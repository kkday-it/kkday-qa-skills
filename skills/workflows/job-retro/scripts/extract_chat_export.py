#!/usr/bin/env python3
"""
extract_chat_export.py — retro a claude.ai / Claude Desktop *chat* conversation
from a data export, since chat history is NOT stored locally (it lives in the
cloud; see references/chat-mode.md). Export is only on the claude.ai WEBSITE
(the Desktop app's Settings has no export option): log in at claude.ai → click
your initials (bottom-left) → Settings → Privacy → "Export data". It emails a
link (not an instant download); the zip holds `conversations.json` (or `.jsonl`)
— an account-level dump of ALL conversations (incl. those made in Desktop).
Authoritative steps: https://support.claude.com/en/articles/9450526

This complements extract_session.py (which reads Claude Code's local
~/.claude/projects/*.jsonl). Same retro signals, different input shape: a chat
export has no tool calls / subagents / files — just human/assistant messages,
so we mine the human turns for the four correction types.

Usage:
  extract_chat_export.py <conversations.json> list
  extract_chat_export.py <conversations.json> <selector> [--json out] [--md out] [--full]

<selector>: conversation index (from `list`), a uuid (full or prefix), or a
substring of the conversation name.
"""
import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

# reuse the shared retro heuristics from the session extractor (same dir)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_session import (  # noqa: E402
    ROLE_SIGNALS, classify_correction, adds_information,
    _role_guess, _trim, fmt_dur, parse_ts,
)


def log(*a):
    print(*a, file=sys.stderr)


def msg_text(m):
    """A chat message has either a flat `text` or a `content` block list."""
    if isinstance(m.get("content"), list):
        parts = [b.get("text", "") for b in m["content"]
                 if isinstance(b, dict) and b.get("type") == "text"]
        joined = "\n".join(p for p in parts if p)
        if joined.strip():
            return joined
    return m.get("text", "") or ""


def msg_has_attachment(m):
    if m.get("attachments") or m.get("files"):
        return True
    if isinstance(m.get("content"), list):
        return any(isinstance(b, dict) and b.get("type") in ("image", "tool_result")
                   for b in m["content"])
    return False


def _as_conv_list(data):
    """Normalise a parsed JSON value into a list of conversation objects."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("conversations", "data"):
            if isinstance(data.get(k), list):
                return data[k]
        # a bare single conversation object (not a wrapper)
        if "chat_messages" in data or "uuid" in data:
            return [data]
    return None


def load_conversations(path):
    # claude.ai exports have shown up as both conversations.json (one big JSON
    # array, or {"conversations":[...]}) and conversations.jsonl (one
    # conversation object per line). Handle all of them.
    raw = open(path, errors="replace").read().strip()
    if not raw:
        sys.exit(f"{path} is empty.")
    try:
        got = _as_conv_list(json.loads(raw))
        if got is not None:
            return got
    except json.JSONDecodeError:
        pass
    # JSON Lines fallback (one conversation per line)
    convs = []
    for line in raw.splitlines():
        line = line.strip().rstrip(",")
        if not line or line in ("[", "]"):
            continue
        try:
            convs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if convs:
        return convs
    sys.exit("Could not parse export as a JSON array or JSON Lines.")


def pick(convs, selector):
    # index
    if selector.isdigit():
        i = int(selector)
        if 0 <= i < len(convs):
            return convs[i]
    # uuid full / prefix
    for c in convs:
        if str(c.get("uuid", "")).startswith(selector):
            return c
    # name substring (case-insensitive)
    low = selector.lower()
    for c in convs:
        if low in str(c.get("name", "")).lower():
            return c
    sys.exit(f"No conversation matched selector {selector!r}. Try `list`.")


def do_list(convs):
    print(f"{'IDX':<4} {'UUID':<9} {'MSGS':>5} {'UPDATED':<20} NAME")
    print("-" * 80)
    rows = []
    for i, c in enumerate(convs):
        rows.append((i, str(c.get("uuid", ""))[:8],
                     len(c.get("chat_messages", []) or []),
                     str(c.get("updated_at", ""))[:19],
                     str(c.get("name", "") or "(untitled)")[:48]))
    # newest first by updated_at string (ISO sorts lexicographically)
    for i, u, n, upd, name in sorted(rows, key=lambda r: r[3], reverse=True):
        print(f"{i:<4} {u:<9} {n:>5} {upd:<20} {name}")


def extract(conv, full=False):
    cap = (lambda n: 10**9) if full else (lambda n: n)
    msgs = conv.get("chat_messages", []) or []

    first_ts = parse_ts(conv.get("created_at"))
    last_ts = parse_ts(conv.get("updated_at"))

    human_prompts, corrections, blob = [], [], []
    n_human = n_assistant = n_attach = 0
    turn = 0  # assistant turns so far (mirror extract_session's turn_index)

    for m in msgs:
        sender = m.get("sender") or m.get("role")
        text = msg_text(m)
        if text:
            blob.append(text.lower())
        if sender == "assistant":
            n_assistant += 1
            turn += 1
            continue
        if sender != "human":
            continue
        # --- human turn ---
        n_human += 1
        t = text.strip()
        if not t and not msg_has_attachment(m):
            continue
        human_prompts.append({"turn": turn, "text": _trim(t, 600)})
        if turn == 0:
            continue  # first ask isn't a correction
        has_att = msg_has_attachment(m)
        if has_att:
            n_attach += 1
        kind = classify_correction(t)
        adds = adds_information(t, has_att)
        if not kind and adds:
            kind = "supplement"
        if kind:
            entry = {"kind": kind, "turn": turn, "text": _trim(t, 400)}
            if adds:
                entry["adds_info"] = True
            if has_att:
                entry["has_attachment"] = True
            corrections.append(entry)

    role_hits = Counter()
    big = "\n".join(blob)
    for role_name, words in ROLE_SIGNALS.items():
        for w in words:
            c = big.count(w)
            if c:
                role_hits[role_name] += c

    dur = (last_ts - first_ts).total_seconds() if first_ts and last_ts else None
    idle = (datetime.now(timezone.utc) - last_ts).total_seconds() if last_ts else None

    return {
        "source": "chat-export",
        "uuid": conv.get("uuid"),
        "name": conv.get("name") or "(untitled)",
        "start": first_ts.isoformat() if first_ts else None,
        "end": last_ts.isoformat() if last_ts else None,
        "duration_seconds": dur,
        "idle_seconds": idle,
        "counts": {
            "messages": len(msgs),
            "human": n_human,
            "assistant": n_assistant,
            "corrections": len(corrections),
            "supplements": sum(1 for c in corrections
                               if c["kind"] == "supplement" or c.get("adds_info")),
            "attachments": n_attach,
        },
        "goal": human_prompts[0]["text"] if human_prompts else None,
        "corrections": corrections[: cap(80)],
        "human_prompts": [{**h, "text": _trim(h["text"], 220)}
                          for h in human_prompts[: cap(60)]],
        "role_signals": dict(role_hits.most_common()),
        "role_guess": _role_guess(role_hits),
    }


def render_md(d):
    tag = {"interrupt": "⟲ INTERRUPT", "redirect": "↪ redirect",
           "question": "? clarify", "supplement": "＋ supplement"}
    c = d["counts"]
    L = [f"# Chat retro digest — {_trim(d['name'], 60)}",
         "",
         f"- **Conversation**: `{str(d['uuid'])[:8]}`  (chat export)",
         f"- **Window**: {d['start']} → {d['end']}  (span {fmt_dur(d['duration_seconds'])})",
         f"- **Role guess**: **{d['role_guess']}**  (signals: {d['role_signals']})",
         f"- **Volume**: {c['messages']} messages · {c['human']} human · "
         f"{c['assistant']} assistant · {c['corrections']} corrections · "
         f"{c['supplements']} supplements · {c['attachments']} with attachment",
         "",
         "## Goal / first ask",
         "> " + ((d["goal"] or "(none)").replace("\n", "\n> ")),
         "",
         "## Corrections & course-changes  ← the 'what got fixed along the way'"]
    if d["corrections"]:
        for x in d["corrections"]:
            flags = ""
            if x.get("adds_info") and x["kind"] != "supplement":
                flags += " [+info]"
            if x.get("has_attachment"):
                flags += " [att]"
            L.append(f"- [{tag.get(x['kind'], x['kind'])} @msg {x['turn']}]{flags} {x['text']}")
        L.append("")
        L.append("> `supplement`/`[+info]` = 中途補的輸入。分「漏給(→前置清單)」vs"
                 "「刻意漸進引導風格(→prompt 技巧)」。")
    else:
        L.append("- (no explicit human corrections detected)")
    L += ["", "## Human prompt sequence"]
    for h in d["human_prompts"]:
        L.append(f"- @msg {h['turn']}: {h['text']}")
    L.append("")
    L.append("> 這是 chat 對話的 retro;沒有工具/subagent/檔案訊號。分析框架同 "
             "references/analysis-framework.md,角色判斷同 references/role-detection.md。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export", help="path to conversations.json (from claude.ai export)")
    ap.add_argument("selector", help="'list' | conversation index | uuid | name substring")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--md", dest="md_out")
    ap.add_argument("--full", action="store_true", help="no truncation caps")
    args = ap.parse_args()

    convs = load_conversations(args.export)
    log(f"[chat] {len(convs)} conversations in {args.export}")
    if args.selector == "list":
        do_list(convs)
        return

    conv = pick(convs, args.selector)
    d = extract(conv, full=args.full)
    md = render_md(d)
    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        log(f"[chat] wrote JSON -> {args.json_out}")
    if args.md_out:
        with open(args.md_out, "w") as f:
            f.write(md)
        log(f"[chat] wrote markdown -> {args.md_out}")
    print(md)


if __name__ == "__main__":
    main()

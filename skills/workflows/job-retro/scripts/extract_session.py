#!/usr/bin/env python3
"""
extract_session.py — turn a Claude Code session transcript into a compact,
structured retro digest so the model never has to read a multi-MB .jsonl.

A "session" here is one continuous AI working session (a background job, a
/goal run, or an interactive session). Sessions are stored as:
  ~/.claude/projects/<project-slug>/<session-id>.jsonl   (the transcript)
  ~/.claude/jobs/<short-id>/state.json                   (job metadata, optional)

Usage:
  extract_session.py <selector> [--json out.json] [--md out.md] [--full]

<selector> may be any of:
  - a transcript path:   /.../<session-id>.jsonl
  - a session id (uuid): 419b2b02-f0f5-4d84-b955-3533d6f59039
  - a job short id:      419b2b02
  - "latest"             most recently modified transcript under the projects dir

By default a markdown digest is printed to stdout. Use --json / --md to also
write files. --full removes the truncation caps (use for a deep retro).

The digest captures the signals a retro cares about:
  - what the session was asked to do (the goal / first prompt)
  - the timeline of phases (user prompt -> what the agent did -> outcome)
  - CORRECTIONS: where the human redirected, interrupted, or the agent
    retried after an error (the "what got fixed along the way" the user wants)
  - tools used, commands run, files touched, subagents spawned
  - role signals (repos, MCP servers, keywords) to classify QA vs engineer
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
PROJECTS_DIR = os.path.join(HOME, ".claude", "projects")
JOBS_DIR = os.path.join(HOME, ".claude", "jobs")

# Words that, when a user sends a message *after* work has started, suggest a
# course-correction rather than a fresh independent instruction. Bilingual
# because this user works in zh-TW + en.
CORRECTION_HINTS = [
    # english
    "no,", "no.", "nope", "not ", "don't", "do not", "stop", "wait", "actually",
    "instead", "revert", "undo", "rollback", "wrong", "mistake", "incorrect",
    "should be", "shouldn't", "should not", "that's not", "isn't right",
    "fix that", "change it", "redo", "again", "but ", "however",
    # zh-TW
    "不對", "不是", "不要", "不該", "錯", "改成", "改一下", "重來", "其實",
    "應該", "不行", "不能", "等一下", "等等",
    "少了", "漏了", "退回", "還原", "重新", "可是", "但是",
]

ROLE_SIGNALS = {
    "qa": [
        "test", "ttest case", "testcase", "regression", "zephyr", "tcms",
        "jira", "bug", "qa-automation", "playwright", "appium", "e2e",
        "assertion", "test run", "test cycle", "coverage", "live bug",
        "smoke", "verify", "驗收", "測試", "測資", "缺陷", "回歸",
    ],
    "engineer": [
        "refactor", "implement", "feature", "endpoint", "migration", "alembic",
        "schema", "deploy", "build", "compile", "dependency", "api", "component",
        "frontend", "backend", "pull request", "merge", "commit", "重構",
        "實作", "部署", "效能",
    ],
    "data_analyst": [
        "mixpanel", "dashboard", "metric", "query", "bigquery", "sql",
        "report", "chart", "funnel", "cohort", "報告", "數據", "儀表板",
    ],
}

# High-precision failure signatures, used only as a fallback when the
# tool_result is_error flag is absent. Anchored / specific so that ordinary
# output that merely *mentions* "error" (e.g. grepping logs) doesn't match.
ERR_SIGNATURE = re.compile(
    r"(?im)^\s*(traceback \(most recent call last\)|fatal:|error:|"
    r"[a-z_]*error:|exception:|panic:|command not found|"
    r"no such file or directory|permission denied|"
    r"npm err!|exit code [1-9]|exited with code [1-9])",
)


def log(*a):
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------------------
# selector resolution
# ---------------------------------------------------------------------------
def resolve_selector(selector):
    """Return (transcript_path, job_state_or_None)."""
    # direct path
    if selector.endswith(".jsonl") and os.path.exists(selector):
        return selector, find_job_for_session(_sid_from_path(selector))

    if selector == "latest":
        path = _latest_transcript()
        if not path:
            sys.exit("No transcripts found under " + PROJECTS_DIR)
        return path, find_job_for_session(_sid_from_path(path))

    # job short id -> state.json -> linkScanPath / sessionId
    job_state_path = os.path.join(JOBS_DIR, selector, "state.json")
    if os.path.exists(job_state_path):
        state = _load_json(job_state_path)
        tp = state.get("linkScanPath")
        if tp and os.path.exists(tp):
            return tp, state
        sid = state.get("sessionId") or state.get("resumeSessionId")
        if sid:
            tp = _find_transcript_by_sid(sid)
            if tp:
                return tp, state

    # treat as a session id (full or prefix)
    tp = _find_transcript_by_sid(selector)
    if tp:
        return tp, find_job_for_session(_sid_from_path(tp))

    sys.exit(f"Could not resolve selector {selector!r} to a transcript.")


def _sid_from_path(path):
    return os.path.splitext(os.path.basename(path))[0]


def _latest_transcript():
    best, best_m = None, -1
    for root, _dirs, files in os.walk(PROJECTS_DIR):
        for f in files:
            if f.endswith(".jsonl"):
                p = os.path.join(root, f)
                try:
                    m = os.path.getmtime(p)
                except OSError:
                    continue
                if m > best_m:
                    best, best_m = p, m
    return best


def _find_transcript_by_sid(sid):
    for root, _dirs, files in os.walk(PROJECTS_DIR):
        for f in files:
            if f.endswith(".jsonl") and f.startswith(sid):
                return os.path.join(root, f)
    return None


def find_job_for_session(sid):
    if not os.path.isdir(JOBS_DIR):
        return None
    for short in os.listdir(JOBS_DIR):
        sp = os.path.join(JOBS_DIR, short, "state.json")
        if os.path.exists(sp):
            st = _load_json(sp)
            if sid and (st.get("sessionId") == sid or st.get("resumeSessionId") == sid):
                return st
    return None


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# transcript parsing
# ---------------------------------------------------------------------------
def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def block_text(content):
    """Flatten a message.content (str or list of blocks) to plain text."""
    if isinstance(content, str):
        return content
    out = []
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text", ""))
    return "\n".join(out)


# Prefixes / substrings that mark a *harness-injected* user-role message rather
# than something the human actually typed. These otherwise masquerade as
# corrections because compaction summaries and task notifications are verbose.
INJECTED_PREFIXES = (
    "<command-", "<system-reminder>", "<task-notification>", "<post-compact",
    "<local-command", "Caveat:", "Shell cwd was reset",
    "This session is being continued from a previous conversation",
    "[Request interrupted by user for tool use]",  # bare interrupt, no instruction
)
INJECTED_SUBSTRINGS = (
    "<local-command-stdout>", "<task-notification>",
    "This session is being continued from a previous conversation",
)


def is_real_user_text(obj, text):
    """True for genuine user-typed text (not tool results, meta, command noise)."""
    if obj.get("isMeta"):
        return False
    if not text or not text.strip():
        return False
    t = text.strip()
    if any(t.startswith(p) for p in INJECTED_PREFIXES):
        return False
    if any(s in t for s in INJECTED_SUBSTRINGS):
        return False
    return True


QUESTION_HINTS = ["?", "？", "怎麼", "為什麼", "為何", "是不是", "會怎樣",
                  "可以嗎", "嗎", "how do", "why ", "what if", "should i"]

# Signals that a mid-session user message is *adding input/context that wasn't
# given up front* — either accidental (forgot to hand over the doc/figma) or a
# deliberate progressive-disclosure technique to steer the output's flavour.
# The retro cares about both; the framework decides which it was.
SUPPLEMENT_HINTS = [
    # english
    "here's", "here is", "attached", "attaching", "fyi", "context:", "spec",
    "prd", "mockup", "figma", "the doc", "background:", "for reference",
    "also note", "additional", "one more thing", "forgot to mention",
    # zh-TW
    "補充", "補一下", "還有", "另外", "附上", "附件", "這是", "資料如下",
    "文檔", "文件", "規格", "需求", "情境", "場景", "參考", "再給",
    "漏給", "忘了給", "忘了說", "背景是", "設計稿", "截圖", "如圖", "如下",
]
# URL / file markers that indicate an input artefact is being handed over.
ARTIFACT_MARKERS = ("figma.com", "docs.google", "drive.google", ".pdf",
                    ".png", ".jpg", ".jpeg", ".xlsx", ".csv", ".md",
                    "confluence", "/browse/")


def adds_information(text, has_image):
    if has_image:
        return True
    low = text.lower()
    if any(h in low for h in SUPPLEMENT_HINTS):
        return True
    if any(m in low for m in ARTIFACT_MARKERS):
        return True
    return False


def classify_correction(text):
    low = text.lower()
    if "[request interrupted" in low:
        return "interrupt"
    is_redirect = any(h in low for h in CORRECTION_HINTS)
    is_question = any(h in low for h in QUESTION_HINTS)
    # a question that carries no redirect verb is a point of confusion, not a
    # correction of the agent's direction — worth tracking separately.
    if is_question and not is_redirect:
        return "question"
    if is_redirect:
        return "redirect"
    if is_question:
        return "question"
    return None


def extract(path, full=False):
    cap = (lambda n: 10**9) if full else (lambda n: n)

    meta = {
        "transcript": path,
        "session_id": _sid_from_path(path),
        "size_bytes": os.path.getsize(path),
    }
    first_ts = last_ts = None
    git_branches = set()
    cwds = set()
    models = set()
    versions = set()

    # main-thread (isSidechain == False) accounting
    user_prompts = []          # real user messages, in order
    assistant_texts = []       # assistant visible text turns
    corrections = []           # {kind, turn_index, text}
    tool_uses = Counter()
    mcp_servers = Counter()
    commands = []              # bash commands
    files_touched = defaultdict(set)  # path -> {Edit/Write/...}
    tool_errors = []           # {tool, snippet, turn_index}
    subagents = []             # Task/Agent spawns with prompts
    result_lines = []          # assistant "result:"/"failed:"/"needs input:" lines

    role_hits = Counter()
    keyword_blob = []

    turn_index = 0  # counts assistant turns on main thread

    with open(path, "r", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue

            ts = parse_ts(obj.get("timestamp"))
            if ts:
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts
            if obj.get("gitBranch"):
                git_branches.add(obj["gitBranch"])
            if obj.get("cwd"):
                cwds.add(obj["cwd"])
            if obj.get("version"):
                versions.add(obj["version"])

            sidechain = obj.get("isSidechain", False)
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            content = msg.get("content")
            if msg.get("model"):
                models.add(msg["model"])

            text = block_text(content)
            if text:
                keyword_blob.append(text.lower())

            # --- user messages (main thread only for corrections) ---
            if role == "user" and not sidechain:
                low_t = (text or "").lower()
                has_image = isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "image" for b in content
                )
                if "[request interrupted" in low_t and turn_index > 0:
                    # the human pulled the cord — strong retro signal even when
                    # no instruction follows the interrupt marker
                    corrections.append({
                        "kind": "interrupt", "turn": turn_index,
                        "text": _trim(text.strip(), 400) or "(interrupted, no message)",
                    })
                elif is_real_user_text(obj, text):
                    user_prompts.append({"turn": turn_index, "text": text.strip()})
                    if turn_index > 0:
                        kind = classify_correction(text)
                        adds = adds_information(text, has_image)
                        # a pure info-drop (no redirect/question) is itself a
                        # signal: input the human gave late instead of up front.
                        if not kind and adds:
                            kind = "supplement"
                        if kind:
                            entry = {
                                "kind": kind, "turn": turn_index,
                                "text": _trim(text.strip(), 400),
                            }
                            # flag redirects/questions that ALSO carry new input,
                            # so the retro can spot "should've been front-loaded".
                            if adds:
                                entry["adds_info"] = True
                            if has_image:
                                entry["has_image"] = True
                            corrections.append(entry)
                # tool_result errors live inside user messages
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            if _is_error_result(b):
                                snip = _result_snippet(b)
                                tool_errors.append({
                                    "turn": turn_index, "snippet": _trim(snip, 300),
                                })

            # --- assistant messages ---
            elif role == "assistant":
                if not sidechain:
                    turn_index += 1
                    vis = block_text(content).strip()
                    if vis:
                        assistant_texts.append({"turn": turn_index, "text": vis})
                        for m in re.finditer(r"(?im)^(result|failed|needs input)\s*:\s*(.+)$", vis):
                            result_lines.append({
                                "turn": turn_index, "kind": m.group(1).lower(),
                                "text": _trim(m.group(2).strip(), 300),
                            })
                if isinstance(content, list):
                    for b in content:
                        if not isinstance(b, dict) or b.get("type") != "tool_use":
                            continue
                        name = b.get("name", "?")
                        tool_uses[name] += 1
                        inp = b.get("input", {}) or {}
                        if name.startswith("mcp__"):
                            mcp_servers["__".join(name.split("__")[:2])] += 1
                        if name == "Bash":
                            cmd = inp.get("command", "")
                            if cmd and not sidechain:
                                commands.append(_trim(cmd.replace("\n", " "), 200))
                        elif name in ("Edit", "Write", "NotebookEdit"):
                            fp = inp.get("file_path") or inp.get("notebook_path")
                            if fp:
                                files_touched[fp].add(name)
                        elif name in ("Task", "Agent"):
                            subagents.append({
                                "turn": turn_index,
                                "type": inp.get("subagent_type") or inp.get("agentType") or "?",
                                "desc": _trim(inp.get("description")
                                              or inp.get("prompt", ""), 160),
                            })

    # role scoring
    blob = "\n".join(keyword_blob)
    for role_name, words in ROLE_SIGNALS.items():
        for w in words:
            c = blob.count(w)
            if c:
                role_hits[role_name] += c

    duration_s = None
    if first_ts and last_ts:
        duration_s = (last_ts - first_ts).total_seconds()

    idle_s = None
    if last_ts:
        idle_s = (datetime.now(timezone.utc) - last_ts).total_seconds()

    digest = {
        "meta": meta,
        "start": first_ts.isoformat() if first_ts else None,
        "end": last_ts.isoformat() if last_ts else None,
        "duration_seconds": duration_s,
        "idle_seconds": idle_s,
        "stopped": (idle_s is not None and idle_s > 24 * 3600),
        "cwds": sorted(cwds),
        "git_branches": sorted(git_branches),
        "models": sorted(models),
        "cli_versions": sorted(versions),
        "counts": {
            "assistant_turns": turn_index,
            "user_prompts": len(user_prompts),
            "corrections": len(corrections),
            "supplements": sum(1 for c in corrections
                               if c["kind"] == "supplement" or c.get("adds_info")),
            "tool_errors": len(tool_errors),
            "subagents": len(subagents),
            "files_touched": len(files_touched),
            "commands": len(commands),
        },
        "goal": user_prompts[0]["text"] if user_prompts else None,
        "user_prompts": [
            {**u, "text": _trim(u["text"], 600)} for u in user_prompts[: cap(60)]
        ],
        "corrections": corrections[: cap(80)],
        "tool_errors": tool_errors[: cap(60)],
        "result_lines": result_lines[: cap(40)],
        "tool_usage": dict(tool_uses.most_common()),
        "mcp_servers": dict(mcp_servers.most_common()),
        "commands": commands[: cap(80)],
        "files_touched": {k: sorted(v) for k, v in sorted(files_touched.items())},
        "subagents": subagents[: cap(60)],
        "role_signals": dict(role_hits.most_common()),
        "role_guess": _role_guess(role_hits),
    }
    return digest


def _role_guess(role_hits):
    if not role_hits:
        return "unknown"
    top = role_hits.most_common(1)[0]
    total = sum(role_hits.values())
    share = top[1] / total if total else 0
    label = top[0]
    if share < 0.45 and len(role_hits) > 1:
        second = role_hits.most_common(2)[1][0]
        return f"{label}+{second} (mixed)"
    return label


def _is_error_result(block):
    # the explicit flag is authoritative
    if block.get("is_error"):
        return True
    # fallback only for strong, specific failure signatures in the first lines
    txt = _result_snippet(block)
    return bool(ERR_SIGNATURE.search(txt[:400]))


def _result_snippet(block):
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for b in c:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    return ""


def _trim(s, n):
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------------------
# markdown rendering
# ---------------------------------------------------------------------------
def fmt_dur(sec):
    if sec is None:
        return "?"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m}m"
    if m:
        return f"{m}m{s}s"
    return f"{s}s"


def render_md(d, job_state=None):
    L = []
    m = d["meta"]
    L.append(f"# Session retro digest — `{m['session_id'][:8]}`")
    L.append("")
    if job_state:
        L.append(f"- **Job name**: {job_state.get('name','?')}  "
                 f"(`{job_state.get('daemonShort','?')}`, state=`{job_state.get('state','?')}`)")
        if job_state.get("intent"):
            L.append(f"- **Intent**: {_trim(job_state['intent'], 300)}")
    L.append(f"- **Transcript**: `{m['transcript']}` ({m['size_bytes']//1024} KB)")
    L.append(f"- **Window**: {d['start']} → {d['end']}  (active {fmt_dur(d['duration_seconds'])})")
    L.append(f"- **Idle**: {fmt_dur(d['idle_seconds'])}  "
             f"→ {'STOPPED (>24h idle)' if d['stopped'] else 'recent / live'}")
    L.append(f"- **CWD(s)**: {', '.join(d['cwds']) or '?'}")
    if d["git_branches"]:
        L.append(f"- **Git branches**: {', '.join(d['git_branches'])}")
    L.append(f"- **Role guess**: **{d['role_guess']}**  (signals: {d['role_signals']})")
    c = d["counts"]
    L.append(f"- **Volume**: {c['assistant_turns']} assistant turns · "
             f"{c['user_prompts']} user prompts · {c['corrections']} corrections · "
             f"{c['tool_errors']} tool errors · {c['subagents']} subagents · "
             f"{c['files_touched']} files touched")
    L.append("")

    # the job's recorded intent (the /goal text) is the most reliable goal;
    # fall back to the first real user prompt.
    goal = (job_state or {}).get("intent") or d["goal"]
    L.append("## Goal / first ask")
    L.append("> " + (_trim(goal, 1000).replace("\n", "\n> ") if goal else "(none captured)"))
    if job_state and job_state.get("intent") and d["goal"] and d["goal"][:60] not in job_state["intent"]:
        L.append("")
        L.append(f"_First in-transcript message:_ {_trim(d['goal'], 300)}")
    L.append("")

    L.append("## Corrections & course-changes  ← the 'what got fixed along the way'")
    tagmap = {"interrupt": "⟲ INTERRUPT", "redirect": "↪ redirect",
              "question": "? clarify", "supplement": "＋ supplement"}
    if d["corrections"]:
        for c in d["corrections"]:
            tag = tagmap.get(c["kind"], c["kind"])
            flags = ""
            if c.get("adds_info") and c["kind"] != "supplement":
                flags += " [+info]"
            if c.get("has_image"):
                flags += " [img]"
            L.append(f"- [{tag} @turn {c['turn']}]{flags} {c['text']}")
        L.append("")
        L.append("> `supplement` / `[+info]` = 人類中途才補的輸入。"
                 "判斷是「該一開始就給卻漏給」(→ front-load 教訓) "
                 "還是「刻意漸進式引導輸出風格」(→ 記成 prompt 技巧)。")
    else:
        L.append("- (no explicit human corrections detected)")
    L.append("")

    if d["tool_errors"]:
        L.append("## Tool errors (retries / friction)")
        for e in d["tool_errors"][:30]:
            L.append(f"- @turn {e['turn']}: {e['snippet']}")
        if len(d["tool_errors"]) > 30:
            L.append(f"- … +{len(d['tool_errors'])-30} more")
        L.append("")

    L.append("## Tool usage")
    L.append("- " + ", ".join(f"`{k}`×{v}" for k, v in d["tool_usage"].items()) or "- (none)")
    if d["mcp_servers"]:
        L.append("- MCP: " + ", ".join(f"`{k}`×{v}" for k, v in d["mcp_servers"].items()))
    L.append("")

    if d["subagents"]:
        L.append("## Subagents spawned")
        for s in d["subagents"][:30]:
            L.append(f"- `{s['type']}` @turn {s['turn']}: {s['desc']}")
        L.append("")

    if d["files_touched"]:
        L.append("## Files touched")
        for fp, ops in list(d["files_touched"].items())[:60]:
            L.append(f"- {fp}  ({','.join(ops)})")
        L.append("")

    if d["result_lines"]:
        L.append("## Completion signals (result:/failed:/needs input:)")
        for r in d["result_lines"]:
            L.append(f"- **{r['kind']}** @turn {r['turn']}: {r['text']}")
        L.append("")

    L.append("## User prompt sequence (the negotiated path)")
    for u in d["user_prompts"]:
        L.append(f"- @turn {u['turn']}: {_trim(u['text'], 220)}")
    L.append("")

    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("selector", help="transcript path | session id | job short id | 'latest'")
    ap.add_argument("--json", dest="json_out", help="also write JSON digest here")
    ap.add_argument("--md", dest="md_out", help="also write markdown digest here")
    ap.add_argument("--full", action="store_true", help="no truncation caps (deep retro)")
    args = ap.parse_args()

    path, job_state = resolve_selector(args.selector)
    log(f"[extract] transcript: {path}")
    d = extract(path, full=args.full)
    md = render_md(d, job_state)

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        log(f"[extract] wrote JSON -> {args.json_out}")
    if args.md_out:
        with open(args.md_out, "w") as f:
            f.write(md)
        log(f"[extract] wrote markdown -> {args.md_out}")

    print(md)


if __name__ == "__main__":
    main()

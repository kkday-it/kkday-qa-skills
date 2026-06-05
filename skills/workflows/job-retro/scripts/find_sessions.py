#!/usr/bin/env python3
"""
find_sessions.py — list AI sessions/jobs so the retro can pick a target.

Joins three sources:
  ~/.claude/jobs/<short>/state.json   (job name, intent, state, sessionId)
  ~/.claude/projects/*/<sid>.jsonl    (the transcript, for size + mtime)

Prints, newest-activity first:
  short  sid8  state  idle   size   role-ish-name / intent

A session is treated as "stopped" when its transcript has been idle >24h
(the user's working definition of a finished job).

Usage:
  find_sessions.py                 # all jobs + orphan transcripts
  find_sessions.py --stopped       # only sessions idle >24h
  find_sessions.py --cwd <path>    # only sessions whose cwd is under <path>
  find_sessions.py --json          # machine-readable
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
PROJECTS_DIR = os.path.join(HOME, ".claude", "projects")
JOBS_DIR = os.path.join(HOME, ".claude", "jobs")
IDLE_STOP = 24 * 3600


def load_json(p):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return {}


def transcript_index():
    """sid -> (path, mtime, size)."""
    idx = {}
    if not os.path.isdir(PROJECTS_DIR):
        return idx
    for root, _dirs, files in os.walk(PROJECTS_DIR):
        for f in files:
            if not f.endswith(".jsonl"):
                continue
            sid = f[:-6]
            p = os.path.join(root, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            idx[sid] = (p, st.st_mtime, st.st_size)
    return idx


def fmt_idle(sec):
    if sec is None:
        return "?"
    sec = int(sec)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m}m"
    return f"{m}m"


def collect():
    tidx = transcript_index()
    now = datetime.now(timezone.utc).timestamp()
    rows = []
    claimed = set()

    if os.path.isdir(JOBS_DIR):
        for short in os.listdir(JOBS_DIR):
            sp = os.path.join(JOBS_DIR, short, "state.json")
            if not os.path.exists(sp):
                continue
            st = load_json(sp)
            sid = st.get("sessionId") or st.get("resumeSessionId") or ""
            tinfo = tidx.get(sid)
            if not tinfo and st.get("linkScanPath"):
                p = st["linkScanPath"]
                if os.path.exists(p):
                    s = os.stat(p)
                    tinfo = (p, s.st_mtime, s.st_size)
            if sid:
                claimed.add(sid)
            mtime = tinfo[1] if tinfo else None
            idle = (now - mtime) if mtime else None
            rows.append({
                "short": short,
                "sid": sid,
                "name": st.get("name", ""),
                "state": st.get("state", ""),
                "intent": (st.get("intent") or "")[:160],
                "cwd": st.get("cwd", ""),
                "transcript": tinfo[0] if tinfo else None,
                "size": tinfo[2] if tinfo else 0,
                "mtime": mtime,
                "idle": idle,
                "stopped": (idle is not None and idle > IDLE_STOP),
                "source": "job",
            })

    # orphan transcripts (interactive sessions without a job dir)
    for sid, (p, mtime, size) in tidx.items():
        if sid in claimed:
            continue
        idle = now - mtime
        rows.append({
            "short": sid[:8],
            "sid": sid,
            "name": "",
            "state": "",
            "intent": "",
            "cwd": os.path.basename(os.path.dirname(p)),
            "transcript": p,
            "size": size,
            "mtime": mtime,
            "idle": idle,
            "stopped": idle > IDLE_STOP,
            "source": "session",
        })

    rows.sort(key=lambda r: (r["mtime"] or 0), reverse=True)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stopped", action="store_true", help="only idle >24h")
    ap.add_argument("--cwd", help="filter to sessions whose cwd is under this path")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    rows = collect()
    if args.stopped:
        rows = [r for r in rows if r["stopped"]]
    if args.cwd:
        c = os.path.abspath(args.cwd)
        rows = [r for r in rows if r["cwd"] and os.path.abspath(r["cwd"]).startswith(c)]

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    if not rows:
        print("No sessions matched.", file=sys.stderr)
        return
    print(f"{'SELECTOR':<10} {'SID':<9} {'STATE':<7} {'IDLE':<7} {'SIZE':>7}  NAME / INTENT")
    print("-" * 100)
    for r in rows:
        sel = r["short"] if r["source"] == "job" else r["sid"][:8]
        label = r["name"] or r["intent"] or "(no name)"
        size = f"{r['size']//1024}K" if r["size"] else "-"
        flag = "■" if r["stopped"] else " "
        print(f"{sel:<10} {r['sid'][:8]:<9} {r['state']:<7} "
              f"{fmt_idle(r['idle']):<7} {size:>7} {flag} {label[:60]}")
    print("\n■ = stopped (idle >24h).  Pass SELECTOR to extract_session.py.")


if __name__ == "__main__":
    main()

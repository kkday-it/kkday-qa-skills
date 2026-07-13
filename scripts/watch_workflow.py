#!/usr/bin/env python3
"""盯一個 workflow run 的進度 —— 給 Claude Code Monitor 當 command 用，或獨立跑。

report 三種訊號（每行一個事件）：
  [進度]   一個 agent 完成（附 caseid / delivered / 各平台狀態 / gate 缺）
  [活動]   每 ~90s 心跳：幾個 agent 在跑、transcript 多大、最新動作幾秒前
  [⚠️卡住]  transcript 超過 180s 沒更新（stall 或長測試）

用法：
  python3 scripts/watch_workflow.py <run-id | transcript-dir> [--timeout-sec 1700] [--heartbeat 90]

  run-id 例 `wf_73ab49a2-953`；會在 ~/.claude/projects/**/subagents/workflows/ 下找。
  也可直接給 transcript dir 絕對路徑。

不讀 subagent 完整 transcript（會爆 context）——只讀 journal.jsonl（精簡）與檔案 mtime/大小。
"""
import argparse
import glob
import json
import os
import sys
import time


def _find_dir(arg: str):
    if os.path.isdir(arg):
        return arg
    for pat in (
        os.path.expanduser(f"~/.claude/projects/**/subagents/workflows/{arg}"),
        os.path.expanduser(f"~/.claude/projects/**/subagents/workflows/{arg}*"),
    ):
        hits = sorted(glob.glob(pat, recursive=True))
        if hits:
            return hits[-1]
    return None


def main() -> int:
    p = argparse.ArgumentParser(description="watch a workflow run's progress")
    p.add_argument("run", help="run id (wf_xxx) 或 transcript dir 絕對路徑")
    p.add_argument("--timeout-sec", type=int, default=1700)
    p.add_argument("--heartbeat", type=int, default=90, help="活動心跳間隔秒")
    p.add_argument("--stall-sec", type=int, default=180, help="idle 超過此秒數警示卡住")
    a = p.parse_args()

    d = _find_dir(a.run)
    if not d:
        print(f"[盯哨] 找不到 run：{a.run}", flush=True)
        return 1
    journal = os.path.join(d, "journal.jsonl")
    seen = 0
    last_act = 0.0
    stall_warned = False
    print(f"[盯哨] 開始盯 {os.path.basename(d)}", flush=True)
    end = time.time() + a.timeout_sec
    while time.time() < end:
        try:
            rows = [json.loads(ln) for ln in open(journal) if ln.strip()]
        except Exception:
            rows = []
        done = [r for r in rows if r.get("result")]
        if len(done) > seen:
            for r in done[seen:]:
                res = r.get("result")
                if isinstance(res, dict):
                    cid = res.get("caseid", "?")
                    dv = res.get("delivered")
                    pp = res.get("per_platform") or []
                    plat = " ".join(f"{x.get('platform')}={x.get('status')}" for x in pp)
                    gm = res.get("gate_missing")
                    line = f"[進度] {cid} | delivered={dv}"
                    if plat:
                        line += f" | {plat}"
                    if gm:
                        line += f" | gate缺={gm}"
                    print(line[:220], flush=True)
                else:
                    print("[進度] 一個 agent 完成（text 回傳）", flush=True)
            seen = len(done)
            stall_warned = False

        files = glob.glob(os.path.join(d, "agent-*.jsonl"))
        if files:
            idle = time.time() - max(os.path.getmtime(f) for f in files)
            total = sum(os.path.getsize(f) for f in files) // 1024
            if time.time() - last_act > a.heartbeat:
                print(f"[活動] {len(files)} 個 agent，transcript 共 {total}K，最新動作 {int(idle)}s 前", flush=True)
                last_act = time.time()
            if idle > a.stall_sec and not stall_warned:
                print(f"[⚠️卡住] idle {int(idle)}s（stall 或長測試），transcript {total}K", flush=True)
                stall_warned = True
        time.sleep(20)
    print(f"[盯哨] 收工（共見 {seen} 個 agent 完成；最終結果以 workflow 完成通知為準）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

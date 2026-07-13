#!/usr/bin/env python3
"""從 qatest.log 客觀 parse「某 case 某 platform 最近一次跑的 pass/fail」——不靠 automator 自報。

qatest.log 每行格式：`ts-[pid][thread]|[LEVEL]|[file:line][func]|msg`。
一次跑（同 pid+thread，from `before_case Start running` 到下一個）：
  - `before_case|Start running <caseid>`     → 這次跑的 case
  - `Using platform from config: <platform>`  → 這次跑實際用的平台
  - 過程若出現 `_handle_fail_case` + `TestCase failed` → 這次跑 fail；否則 pass
caseid 比對容忍 -N 後綴（KQT-T37934 ↔ KQT-T37934-1）。同 case×platform 有多次跑時取最新一次。

用法：
  parse_qatest_log.py --log ~/Documents/QATest_Output/qatest.log --caseid KQT-T37934 --platform mweb
回 JSON：{caseid, platform, result: "pass"|"fail"|"not-run", run_count, last_ts}
"""
import argparse
import json
import re
import sys

LINE = re.compile(
    r"^(?P<ts>[\d\-]+ [\d:,]+)-\[(?P<pid>\d+)\]\[(?P<thread>\d+)\]\|\[(?P<lvl>\w+)\]\|[^|]*\|(?P<msg>.*)$"
)


def _base(cid):
    return re.sub(r"-\d+$", "", cid or "")


def parse(log_path, caseid, platform):
    runs = []          # 收尾的每次跑
    cur = {}           # (pid,thread) -> 進行中的跑
    with open(log_path, errors="replace") as f:
        for raw in f:
            m = LINE.match(raw.rstrip("\n"))
            if not m:
                continue
            key = (m.group("pid"), m.group("thread"))
            msg = m.group("msg")
            ts = m.group("ts")
            if "before_case" in raw and "Start running" in msg:
                if key in cur:
                    runs.append(cur[key])
                cid = msg.split("Start running", 1)[1].strip()
                cur[key] = {"caseid": cid, "platform": None, "failed": False, "ts": ts}
            elif key in cur:
                if "Using platform from config:" in msg:
                    cur[key]["platform"] = msg.split("config:", 1)[1].strip()
                if "_handle_fail_case" in raw and "TestCase failed" in msg:
                    cur[key]["failed"] = True
                if "_handle_case_result" in raw:
                    cur[key]["ts"] = ts
    runs.extend(cur.values())

    matched = [
        r for r in runs
        if _base(r["caseid"]) == _base(caseid) and r["platform"] == platform
    ]
    if not matched:
        return {"caseid": caseid, "platform": platform, "result": "not-run", "run_count": 0}
    last = matched[-1]
    return {
        "caseid": caseid,
        "platform": platform,
        "result": "fail" if last["failed"] else "pass",
        "run_count": len(matched),
        "last_ts": last["ts"],
    }


def main():
    p = argparse.ArgumentParser(description="parse qatest.log for a case×platform run result")
    p.add_argument("--log", required=True)
    p.add_argument("--caseid", required=True)
    p.add_argument("--platform", required=True)
    a = p.parse_args()
    print(json.dumps(parse(a.log, a.caseid, a.platform), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

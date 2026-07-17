#!/usr/bin/env python3
"""Test rot 偵測（#5）—— 交付綠的 case「後來壞了 / 不再跑了」。

問題：case 被 batch flow 判綠交付後，產品改版會讓它慢慢壞；或它被人從回歸套件拿掉、
悄悄沒在跑。沒人回頭看＝rot。這支比對「交付時的綠基準」（case_delivery.jsonl ledger）
vs「最近一次回歸跑的結果」（qatest.log），把偏離浮出來。

前置：要餵一份**新的回歸 log**（把 ledger 裡的 case 重跑一輪產生的 qatest.log）才有意義；
      對著舊 log 跑只會把所有 case 都判成 not-in-run。建議接在夜間回歸之後。

每個 交付綠的 case×platform 分三類：
  - stable     ：log 裡仍 pass
  - rotted     ：log 裡變 fail（green→red，最該修）
  - not-in-run ：log 裡找不到（被拿掉/沒排進這輪跑 → 可能悄悄沒在保護了）

用法：
  python3 detect_test_rot.py --ledger ~/.claude/harness/case_delivery.jsonl \
      --qatest-log ~/Documents/QATest_Output/qatest.log [--json]
"""
import argparse
import json
import os
import sys


def load_delivered(ledger_path):
    """讀 ledger，回 {caseid: set(platforms)}——只收 delivered=true 的最新一筆 per case。"""
    latest = {}  # caseid -> (ts, platforms)
    if not ledger_path or not os.path.isfile(ledger_path):
        return {}
    with open(ledger_path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            cid = r.get("caseid")
            if not cid or not r.get("delivered", True):
                continue
            ts = r.get("ts", 0) or 0
            plats = [str(p).lower() for p in (r.get("platforms") or []) if p]
            if cid not in latest or ts >= latest[cid][0]:
                latest[cid] = (ts, plats)
    return {cid: set(v[1]) for cid, v in latest.items()}


def classify(delivered_platforms, current_result):
    """純判定（好測）：交付綠的某平台 vs 現在 log 的結果。
    current_result: 'pass' | 'fail' | 'not-run'。
    回 'stable' | 'rotted' | 'not-in-run'。"""
    if current_result == "pass":
        return "stable"
    if current_result == "fail":
        return "rotted"
    return "not-in-run"


def detect(ledger_path, qatest_log):
    delivered = load_delivered(ledger_path)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from parse_qatest_log import parse as _parse
    except Exception:
        _parse = None

    rotted, not_in_run, stable = [], [], []
    for caseid, platforms in sorted(delivered.items()):
        for plat in sorted(platforms):
            res = "not-run"
            if _parse and qatest_log and os.path.isfile(qatest_log):
                res = _parse(qatest_log, caseid, plat).get("result", "not-run")
            verdict = classify(platforms, res)
            item = {"caseid": caseid, "platform": plat, "current": res}
            if verdict == "rotted":
                rotted.append(item)
            elif verdict == "not-in-run":
                not_in_run.append(item)
            else:
                stable.append(item)
    return {"delivered_cases": len(delivered), "rotted": rotted,
            "not_in_run": not_in_run, "stable_count": len(stable)}


def main():
    p = argparse.ArgumentParser(description="Test rot 偵測（交付綠 vs 最近回歸）")
    p.add_argument("--ledger", default=os.path.expanduser("~/.claude/harness/case_delivery.jsonl"))
    p.add_argument("--qatest-log", default=os.path.expanduser("~/Documents/QATest_Output/qatest.log"))
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    rep = detect(args.ledger, args.qatest_log)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    print(f"=== Test rot 偵測（交付綠 {rep['delivered_cases']} case）===")
    print(f"stable {rep['stable_count']}｜rotted {len(rep['rotted'])}｜not-in-run {len(rep['not_in_run'])}")
    if rep["rotted"]:
        print("\n🔴 ROTTED（交付時綠、現在 fail，最該修）：")
        for x in rep["rotted"]:
            print(f"  - {x['caseid']} [{x['platform']}] → {x['current']}")
    if rep["not_in_run"]:
        print("\n🟠 NOT-IN-RUN（交付過但這輪回歸找不到，可能悄悄沒在保護）：")
        for x in rep["not_in_run"]:
            print(f"  - {x['caseid']} [{x['platform']}]")
    # rotted 有東西 → exit 1，讓排程 job 可據此告警
    return 1 if rep["rotted"] else 0


if __name__ == "__main__":
    sys.exit(main())

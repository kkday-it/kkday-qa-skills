#!/usr/bin/env python3
"""Escaped-defect 橋接（#5）—— 線上出事時，反查是哪個「交付綠的 case」給了假信心。

問題：bug 綠了卻漏到線上（escaped defect）。harness 已有一套歸因迴路
（false_confidence.jsonl → issue-attributor → tune），但那是接在 **PR-impact 決策**上；
batch flow 撰寫/交付的 case 沒接進去。這支就是那條線：

  incident 進來 → 反查 case_delivery.jsonl，找「宣稱涵蓋這塊、且當時判綠」的 case
             → 標成 false-confidence 候選（這些 case 綠了卻沒擋住）
             → 印出 handoff：spawn issue-attributor 對相關 PR 跑 6 層歸因

比對方式（任一即命中）：
  - --case KQT-T#####   直接指定漏網的 case
  - --pr <url>          incident 對應 PR；找 ledger 裡同 pr_url 的交付 case
  - --keyword <字>      對 caseid/assertions/traceability 子字串比對（模糊）

輸出寫到 false_confidence 風格記錄（--out，預設 ~/.claude/harness/escaped_defects.jsonl），
不直接改 harness 的 false_confidence.jsonl（避免跨檔 race；由 issue-attributor 決定如何併入）。

用法：
  python3 link_escaped_defect.py --incident "付款頁優惠碼沒套用" --pr https://github.com/kkday-it/.../pull/123
  python3 link_escaped_defect.py --case KQT-T37931 --incident "landing 搜尋錯 tab"
  python3 link_escaped_defect.py --keyword coupon --json
"""
import argparse
import json
import os
import sys
import time

DEFAULT_LEDGER = os.path.expanduser("~/.claude/harness/case_delivery.jsonl")
DEFAULT_OUT = os.path.expanduser("~/.claude/harness/escaped_defects.jsonl")


def _load_ledger(path):
    rows = []
    if not path or not os.path.isfile(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except Exception:
                continue
    return rows


def _hay(row):
    """把一筆交付記錄攤成可模糊比對的字串。"""
    parts = [row.get("caseid", ""), row.get("assertions", ""),
             row.get("traceability", ""), row.get("pr_url", "")]
    return " ".join(str(x) for x in parts).lower()


def match_deliveries(rows, case=None, pr=None, keyword=None):
    """純比對（好測）：回命中的交付記錄清單。case/pr 精準，keyword 模糊；多條件為 OR。"""
    hits, seen = [], set()
    for r in rows:
        if not r.get("delivered", True):
            continue
        ok = False
        if case and r.get("caseid") == case:
            ok = True
        if pr and r.get("pr_url") and r.get("pr_url") == pr:
            ok = True
        if keyword and keyword.lower() in _hay(r):
            ok = True
        if ok:
            key = (r.get("caseid"), r.get("pr_url"))
            if key not in seen:
                seen.add(key)
                hits.append(r)
    return hits


def main():
    p = argparse.ArgumentParser(description="Escaped-defect 反查交付 case（橋接 issue-attributor）")
    p.add_argument("--incident", default="", help="incident 描述（記進輸出，供人看）")
    p.add_argument("--case", default="", help="漏網的 case id（精準）")
    p.add_argument("--pr", default="", help="incident 對應 PR url（精準，對 ledger pr_url）")
    p.add_argument("--keyword", default="", help="對 caseid/assertions/traceability 模糊比對")
    p.add_argument("--ledger", default=DEFAULT_LEDGER)
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if not (args.case or args.pr or args.keyword):
        print("ERROR: 至少給 --case / --pr / --keyword 其一", file=sys.stderr)
        return 2

    rows = _load_ledger(args.ledger)
    hits = match_deliveries(rows, args.case or None, args.pr or None, args.keyword or None)

    record = {
        "ts": int(time.time()),
        "incident": args.incident,
        "query": {"case": args.case, "pr": args.pr, "keyword": args.keyword},
        "false_confidence_candidates": [
            {"caseid": h.get("caseid"), "platforms": h.get("platforms"),
             "pr_url": h.get("pr_url"), "commit": h.get("commit"),
             "delivered_ts": h.get("ts")}
            for h in hits
        ],
    }
    # fail-safe append（輸出到 escaped_defects.jsonl，不直接動 false_confidence.jsonl）
    try:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass

    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0

    if not hits:
        print(f"查無對應交付 case（ledger {len(rows)} 筆）。可能：此區塊還沒被 batch flow 自動化，"
              f"或 ledger 沒記到 → 這本身就是覆蓋缺口，值得補 case。")
        return 0

    print(f"⚠️ 找到 {len(hits)} 個『交付綠卻可能沒擋住』的 case（false-confidence 候選）：")
    prs = set()
    for h in hits:
        print(f"  - {h.get('caseid')} 平台={h.get('platforms')} PR={h.get('pr_url') or '（未記）'}")
        if h.get("pr_url"):
            prs.add(h["pr_url"])
    print("\n→ 下一步（橋接 harness）：對這些 PR spawn issue-attributor 跑 6 層歸因：")
    for pr in (prs or {"<這些 case 對應的 PR>"}):
        print(f'   Agent(subagent_type="issue-attributor", '
              f'prompt="{args.incident or "escaped defect"} related_pr={pr}")')
    print(f"\n已記到 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

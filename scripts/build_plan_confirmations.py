#!/usr/bin/env python3
"""Plan 確認防呆（#8 橡皮圖章的技術摩擦）—— 高風險 case 禁「一鍵全確認」。

問題：意圖確認只在人真的細看時有效；10 個計畫連點 confirm，錯的解讀照樣過。純靠文化擋不住。
技術能做的摩擦：**把「可以一次全確認」這個 affordance 對高風險 case 拿掉**——
Critical/High 的計畫必須「逐案」確認，且每案把 specific 斷言 + 已帶假設攤到眼前，
逼人針對「這個 case 到底要驗什麼」做一個非讀不可的選擇；Medium/Low 才准批次一次過。

這支只做**確定性分流 + 產每案確認題**（不自己問人——問人由主對話用 AskUserQuestion 逐案跑）：
  吃 mode=plan 的回傳 → 分 high_risk（必須逐案確認）/ batchable（可批次）
  → high_risk 每案附一題「請確認關鍵斷言」的確認 prompt。

用法：
  python3 build_plan_confirmations.py --infile plans.json [--json]
  （plans.json = workflow mode=plan 的回傳，或 {"plans":[{caseId, plan:{...}}]} / [...] ）
"""
import argparse
import json
import re
import sys

HIGH_RISK_RE = re.compile(r"critical|high|\bRAT\b|\bFAST\b", re.I)


def classify_priority(priority, plan_text):
    """純判定（好測）：回 'high' | 'normal'。與 workflow 的 isHighRisk 同準（Critical/High/RAT/FAST）。"""
    pr = str(priority or "")
    if HIGH_RISK_RE.search(pr):
        return "high"
    if not pr and plan_text and HIGH_RISK_RE.search(str(plan_text)):
        return "high"
    return "normal"


def _plan_obj(item):
    """從各種輸入形狀取出 (caseid, priority, plan_text)。"""
    plan = item.get("plan", item) if isinstance(item, dict) else {}
    if isinstance(plan, str):
        return item.get("caseId") or item.get("caseid"), "", plan
    caseid = (plan.get("caseid") or item.get("caseId") or item.get("caseid"))
    return caseid, plan.get("priority", ""), plan.get("plan", "")


def build(plans):
    high_risk, batchable = [], []
    for item in plans:
        caseid, priority, plan_text = _plan_obj(item)
        if not caseid:
            continue
        level = classify_priority(priority, plan_text)
        if level == "high":
            high_risk.append({
                "caseid": caseid,
                "priority": priority or "(依計畫文字判定為高風險)",
                "plan": plan_text,
                "confirm_question": (
                    f"[{caseid}] 高風險 case，請逐案確認：這個 case 的**關鍵 specific 斷言**"
                    f"是否正確對到你要測的 expected？（不是問『可不可以跑』，是問『驗的是不是對的東西』）"
                    f"如有假設不對請直接改計畫。"
                ),
            })
        else:
            batchable.append({"caseid": caseid, "priority": priority})
    return {"high_risk": high_risk, "batchable": batchable}


def main():
    p = argparse.ArgumentParser(description="Plan 確認防呆分流（#8）")
    p.add_argument("--infile", required=True, help="mode=plan 回傳 JSON")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    try:
        with open(args.infile, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"ERROR: 讀不到/解析不了 {args.infile}: {e}", file=sys.stderr)
        return 2
    plans = data.get("plans", data) if isinstance(data, dict) else data
    if not isinstance(plans, list):
        print("ERROR: 找不到 plans 陣列", file=sys.stderr)
        return 2

    out = build(plans)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    hr, bt = out["high_risk"], out["batchable"]
    print(f"=== Plan 確認防呆（高風險 {len(hr)}｜可批次 {len(bt)}）===")
    if hr:
        print("\n🔴 高風險——**必須逐案確認**（禁一鍵全確認），主對話對每案各跑一次 AskUserQuestion：")
        for x in hr:
            print(f"  - {x['caseid']}（{x['priority']}）：{x['confirm_question']}")
    if bt:
        print("\n🟢 可批次一次確認：" + ", ".join(x["caseid"] for x in bt))
    return 0


if __name__ == "__main__":
    sys.exit(main())

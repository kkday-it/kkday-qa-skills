#!/usr/bin/env python3
"""build_plan_confirmations 純函式測試（#8 橡皮圖章防呆）。

跑法：python3 scripts/test_build_plan_confirmations.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_plan_confirmations import classify_priority, build  # noqa: E402


def test_classify_by_priority():
    assert classify_priority("Critical", "") == "high"
    assert classify_priority("High", "") == "high"
    assert classify_priority("Medium", "") == "normal"
    assert classify_priority("Low", "") == "normal"


def test_classify_by_plan_text_when_no_priority():
    # 沒 priority 欄，但計畫文字提到 RAT/FAST → high
    assert classify_priority("", "此為 RAT 級核心付款流程") == "high"
    assert classify_priority("", "一般 TOFT 檢查") == "normal"


def test_priority_wins_over_text():
    # 有 priority 欄就以它為準
    assert classify_priority("Medium", "提到 critical 字眼但只是描述") == "normal"


def test_build_splits_high_and_batch():
    plans = [
        {"caseId": "KQT-T1", "plan": {"caseid": "KQT-T1", "priority": "Critical", "plan": "付款"}},
        {"caseId": "KQT-T2", "plan": {"caseid": "KQT-T2", "priority": "Low", "plan": "footer"}},
        {"caseId": "KQT-T3", "plan": {"caseid": "KQT-T3", "priority": "High", "plan": "登入"}},
    ]
    out = build(plans)
    assert [x["caseid"] for x in out["high_risk"]] == ["KQT-T1", "KQT-T3"]
    assert [x["caseid"] for x in out["batchable"]] == ["KQT-T2"]
    # 每個高風險 case 都要有逐案確認題
    assert all(x["confirm_question"] for x in out["high_risk"])


def test_build_handles_string_plan():
    plans = [{"caseId": "KQT-T9", "plan": "RAT 級：付款頁優惠碼"}]
    out = build(plans)
    assert [x["caseid"] for x in out["high_risk"]] == ["KQT-T9"]


def test_build_skips_no_caseid():
    assert build([{"plan": {"priority": "Critical"}}]) == {"high_risk": [], "batchable": []}


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f" FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())

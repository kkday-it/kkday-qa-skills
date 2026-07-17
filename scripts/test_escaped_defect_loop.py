#!/usr/bin/env python3
"""#5 escaped-defect 迴路純函式測試：detect_test_rot.classify / link_escaped_defect.match_deliveries。

跑法：python3 scripts/test_escaped_defect_loop.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from detect_test_rot import classify  # noqa: E402
from link_escaped_defect import match_deliveries  # noqa: E402


# ── test-rot 分類 ──────────────────────────────────────────────
def test_classify_stable():
    assert classify({"web"}, "pass") == "stable"


def test_classify_rotted():
    assert classify({"web"}, "fail") == "rotted"


def test_classify_not_in_run():
    assert classify({"web"}, "not-run") == "not-in-run"


# ── escaped-defect 反查 ────────────────────────────────────────
LEDGER = [
    {"caseid": "KQT-T111", "platforms": ["web"], "pr_url": "https://x/pull/1",
     "assertions": "優惠碼 coupon 折扣正確", "delivered": True},
    {"caseid": "KQT-T222", "platforms": ["ios"], "pr_url": "https://x/pull/2",
     "assertions": "landing 搜尋錨定門票 tab", "delivered": True},
    {"caseid": "KQT-T333", "platforms": ["web"], "pr_url": "https://x/pull/1",
     "assertions": "會員登入", "delivered": False},  # 未交付，不該被反查到
]


def test_match_by_case():
    hits = match_deliveries(LEDGER, case="KQT-T111")
    assert [h["caseid"] for h in hits] == ["KQT-T111"]


def test_match_by_pr():
    # pull/1 有兩筆但一筆 delivered=False → 只回 delivered 的那筆
    hits = match_deliveries(LEDGER, pr="https://x/pull/1")
    assert [h["caseid"] for h in hits] == ["KQT-T111"]


def test_match_by_keyword():
    hits = match_deliveries(LEDGER, keyword="coupon")
    assert [h["caseid"] for h in hits] == ["KQT-T111"]


def test_match_undelivered_excluded():
    # 直接指定未交付 case → 不命中（沒交付就沒給假信心）
    assert match_deliveries(LEDGER, case="KQT-T333") == []


def test_match_or_semantics_dedup():
    # case 與 pr 同時命中同一筆 → 去重不重複
    hits = match_deliveries(LEDGER, case="KQT-T111", pr="https://x/pull/1")
    assert len(hits) == 1


def test_match_none():
    assert match_deliveries(LEDGER, keyword="不存在的關鍵字") == []


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

#!/usr/bin/env python3
"""check_platform_delivery.platform_verdict 純函式測試（flaky 防護核心）。

跑法：python3 -m pytest scripts/test_check_platform_delivery.py
或：   python3 scripts/test_check_platform_delivery.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_platform_delivery import platform_verdict  # noqa: E402


def test_not_run():
    assert platform_verdict([], 1) == "not-run"
    assert platform_verdict([], 3) == "not-run"


def test_min_runs_1_keeps_last_run_semantics():
    # min_runs<=1 維持舊行為：只看最後一次
    assert platform_verdict(["pass"], 1) == "pass"
    assert platform_verdict(["fail"], 1) == "fail"
    # 先 fail 後 pass，舊行為＝看最後一次＝pass（不做 flaky）
    assert platform_verdict(["fail", "pass"], 1) == "pass"
    # min_runs=0 也視為 <=1
    assert platform_verdict(["pass"], 0) == "pass"


def test_stable_pass_over_n():
    # 最近 N 次全 pass → pass
    assert platform_verdict(["pass", "pass", "pass"], 3) == "pass"
    # 更早有 fail 但最近 N 次全 pass（automator 重跑後轉穩）→ pass（只看尾端窗口）
    assert platform_verdict(["fail", "pass", "pass", "pass"], 3) == "pass"


def test_flaky_detected():
    # 最近 N 次窗口內有 fail → flaky（一次過≠穩定過）
    assert platform_verdict(["pass", "fail", "pass"], 3) == "flaky"
    assert platform_verdict(["pass", "pass", "fail"], 3) == "flaky"
    # 全 fail 也算 flaky（窗口內有 fail）
    assert platform_verdict(["fail", "fail", "fail"], 3) == "flaky"


def test_insufficient_runs():
    # 全 pass 但跑不足 N 次 → insufficient（沒跑滿，不算過）
    assert platform_verdict(["pass"], 3) == "insufficient"
    assert platform_verdict(["pass", "pass"], 3) == "insufficient"


def test_insufficient_with_fail_is_flaky():
    # 跑不足 N 次且其中有 fail → flaky（fail 優先於 insufficient）
    assert platform_verdict(["fail"], 3) == "flaky"
    assert platform_verdict(["pass", "fail"], 3) == "flaky"


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

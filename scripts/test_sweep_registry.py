#!/usr/bin/env python3
"""sweep_registry.is_stale 純函式測試（#7 registry sweep 核心）。

跑法：python3 scripts/test_sweep_registry.py
"""
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sweep_registry import is_stale, _parse_ts  # noqa: E402

NOW = datetime(2026, 7, 17, tzinfo=timezone.utc)
RECENT = (NOW - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
OLD = (NOW - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def test_status_stale_always_removed():
    assert is_stale({"status": "stale", "last_verified": RECENT}, "flow", NOW, 90)[0] is True
    assert is_stale({"status": "deprecated", "last_verified": RECENT}, "locator", NOW, 90)[0] is True


def test_flow_function_gone_is_stale():
    # function 已不在 repo（func_exists=False）→ stale，即使日期還新
    stale, reason = is_stale({"name": "foo", "last_verified": RECENT}, "flow", NOW, 90, func_exists=False)
    assert stale is True and "foo" in reason


def test_flow_function_missing_repo_falls_back_to_age():
    # func_exists=None（沒法 grep）→ 不因查不到而刪；新的就留
    assert is_stale({"name": "foo", "last_verified": RECENT}, "flow", NOW, 90, func_exists=None)[0] is False
    # 但 age 超標仍該刪
    assert is_stale({"name": "foo", "last_verified": OLD}, "flow", NOW, 90, func_exists=None)[0] is True


def test_age_over_limit_is_stale():
    assert is_stale({"last_verified": OLD}, "locator", NOW, 90)[0] is True
    assert is_stale({"last_verified": RECENT}, "locator", NOW, 90)[0] is False


def test_no_timestamp_not_stale_by_age():
    # 沒 last_verified → age 判不了 → 不因此刪（保守）
    assert is_stale({"name": "foo"}, "locator", NOW, 90)[0] is False


def test_func_exists_true_and_recent_kept():
    assert is_stale({"name": "foo", "last_verified": RECENT}, "flow", NOW, 90, func_exists=True)[0] is False


def test_parse_ts_formats():
    assert _parse_ts("2026-07-09T00:00:00Z") is not None
    assert _parse_ts("2026-07-16T00:00:00+00:00") is not None
    assert _parse_ts("2026-07-09") is not None
    assert _parse_ts("garbage") is None
    assert _parse_ts(None) is None


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

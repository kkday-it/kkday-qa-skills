#!/usr/bin/env python3
"""
get_verified_locator 的最小單元測試（不需 playwright / 不打網路）。

只驗「回寫預設」這條底層邏輯：--emit 省略時要落在 DEFAULT_EMIT_PATH，
且該路徑與 Stop hook send_locator_registry.py 的 --infile 對齊；傳空字串可停用。

跑法：python3 scripts/test_get_verified_locator.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import get_verified_locator as gvl  # noqa: E402


def test_emit_defaults_when_flag_omitted():
    args = gvl._build_parser().parse_args(["--flow", "things-to-do-search"])
    assert args.emit == gvl.DEFAULT_EMIT_PATH, (
        f"省略 --emit 時應落在預設路徑，實際 {args.emit!r}"
    )


def test_emit_can_be_disabled_with_empty_string():
    args = gvl._build_parser().parse_args(["--flow", "x", "--emit", ""])
    assert args.emit == "", "傳 --emit '' 應能停用回寫"


def test_emit_explicit_override_wins():
    args = gvl._build_parser().parse_args(["--flow", "x", "--emit", "/tmp/custom.jsonl"])
    assert args.emit == "/tmp/custom.jsonl"


def test_default_emit_path_matches_stop_hook_infile():
    # Stop hook（~/.claude/settings.json）固定讀 /tmp/locator_results.jsonl；
    # 預設 emit 必須對齊，否則寫了也不會被送。
    assert gvl.DEFAULT_EMIT_PATH == "/tmp/locator_results.jsonl"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} passed")

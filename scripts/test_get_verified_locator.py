#!/usr/bin/env python3
"""
get_verified_locator 的最小單元測試（不需 playwright / 不打網路）。

驗兩條底層邏輯：
1. 回寫預設：--emit 省略時落在 DEFAULT_EMIT_DIR 下的 per-process 檔（並行安全），
   空字串可停用；且該目錄與 Stop hook 的 --indir 對齊。
2. _source_case：source 是 dict / str / None 都能安全取出 case id。

跑法：python3 scripts/test_get_verified_locator.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import get_verified_locator as gvl  # noqa: E402


def test_emit_flag_defaults_to_none():
    # parser 層預設 None（sentinel＝用 per-process 預設）；解析交給 _resolve_emit
    args = gvl._build_parser().parse_args(["--flow", "things-to-do-search"])
    assert args.emit is None, f"未帶 --emit 時 parser 應為 None，實際 {args.emit!r}"


def test_resolve_emit_none_gives_per_process_path():
    p = gvl._resolve_emit(None)
    assert p.startswith(gvl.DEFAULT_EMIT_DIR + os.sep), f"預設應落在 DEFAULT_EMIT_DIR：{p!r}"
    assert p.endswith(".jsonl")
    assert str(os.getpid()) in p, "檔名應含 pid，確保 per-process 唯一"


def test_resolve_emit_unique_per_call():
    # 同一 process 連兩次仍應不同（含微秒時間戳），避免同批多次呼叫互覆
    assert gvl._resolve_emit(None) != gvl._resolve_emit(None)


def test_resolve_emit_empty_disables():
    assert gvl._resolve_emit("") == "", "傳 --emit '' 應停用回寫"


def test_resolve_emit_explicit_override_wins():
    assert gvl._resolve_emit("/tmp/custom.jsonl") == "/tmp/custom.jsonl"


def test_default_emit_dir_matches_stop_hook_indir():
    # Stop hook（sync_hooks.py）掃 /tmp/locator_results.d；預設目錄必須對齊，否則寫了不會被送。
    assert gvl.DEFAULT_EMIT_DIR == "/tmp/locator_results.d"


def test_source_case_from_dict():
    assert gvl._source_case({"case": "KQT-T37931", "origin": "x"}) == "KQT-T37931"


def test_source_case_from_str_does_not_crash():
    assert gvl._source_case("KQT-T37931") == "KQT-T37931"


def test_source_case_from_none():
    assert gvl._source_case(None) == ""


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} passed")

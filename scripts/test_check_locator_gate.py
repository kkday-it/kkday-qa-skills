#!/usr/bin/env python3
"""
check_locator_gate 的單元測試：emit 證據比對 + cleanup-on-pass 只刪本次通過且不含他人 case 的檔。

跑法：python3 scripts/test_check_locator_gate.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_locator_gate as g  # noqa: E402


def _emit(d, fname, rows):
    with open(os.path.join(d, fname), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_evidence_present_passes():
    with tempfile.TemporaryDirectory() as d:
        _emit(d, "p1.jsonl", [{"source": "KQT-1", "platform": "web", "status": "verified"}])
        pairs, sources = g._load_evidence(d)
        assert g._has_evidence("KQT-1", "web", pairs, sources) is True


def test_missing_evidence_fails():
    with tempfile.TemporaryDirectory() as d:
        _emit(d, "p1.jsonl", [{"source": "KQT-1", "platform": "web"}])
        pairs, sources = g._load_evidence(d)
        # 沒有 KQT-2 的 emit → 無證據
        assert g._has_evidence("KQT-2", "web", pairs, sources) is False
        # 平台不符也算缺
        assert g._has_evidence("KQT-1", "android", pairs, sources) is False


def test_platform_none_matches_any():
    with tempfile.TemporaryDirectory() as d:
        _emit(d, "p1.jsonl", [{"source": "KQT-1", "platform": "android"}])
        pairs, sources = g._load_evidence(d)
        assert g._has_evidence("KQT-1", None, pairs, sources) is True


def test_app_selector_evidence_counts():
    # app 收成（resource-id / status verified）同樣算證據
    with tempfile.TemporaryDirectory() as d:
        _emit(d, "harvest.jsonl", [{"source": "KQT-9", "platform": "ios",
                                    "selectors": [{"type": "accessibility-id", "value": "searchBar"}],
                                    "status": "verified"}])
        pairs, sources = g._load_evidence(d)
        assert g._has_evidence("KQT-9", "ios", pairs, sources) is True


def test_stale_emit_still_counts_as_valve_ran():
    # valve 回 remine → emit status=stale，仍代表「valve 有跑」→ 算證據，不擋
    with tempfile.TemporaryDirectory() as d:
        _emit(d, "p.jsonl", [{"source": "KQT-1", "platform": "web", "status": "stale"}])
        pairs, sources = g._load_evidence(d)
        assert g._has_evidence("KQT-1", "web", pairs, sources) is True


def test_cleanup_removes_only_all_passed_files():
    with tempfile.TemporaryDirectory() as d:
        _emit(d, "mine.jsonl", [{"source": "KQT-1", "platform": "web"}])
        _emit(d, "other.jsonl", [{"source": "KQT-OTHER", "platform": "web"}])   # 別 session
        _emit(d, "mixed.jsonl", [{"source": "KQT-1"}, {"source": "KQT-OTHER"}])  # 混到別人
        g._cleanup_passed(d, {"KQT-1"})
        assert not os.path.exists(os.path.join(d, "mine.jsonl")), "本次通過且純本 case 的檔應刪"
        assert os.path.exists(os.path.join(d, "other.jsonl")), "別 session 的檔不可刪"
        assert os.path.exists(os.path.join(d, "mixed.jsonl")), "混到別人 case 的檔要保留"


def test_no_source_row_gives_no_evidence():
    with tempfile.TemporaryDirectory() as d:
        _emit(d, "p.jsonl", [{"platform": "web", "status": "verified"}])  # 無 source
        pairs, sources = g._load_evidence(d)
        assert g._has_evidence("KQT-1", "web", pairs, sources) is False


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} passed")

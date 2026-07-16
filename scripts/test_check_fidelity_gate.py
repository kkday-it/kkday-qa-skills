#!/usr/bin/env python3
"""
check_fidelity_gate 的單元測試：目錄模式讀取 + 逐 case 判定 + cleanup-on-pass 只刪本次 claimed。

跑法：python3 scripts/test_check_fidelity_gate.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_fidelity_gate as g  # noqa: E402


def _wf(d, name, obj):
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def test_load_fidelity_reads_all_files_in_dir():
    with tempfile.TemporaryDirectory() as d:
        _wf(d, "KQT-1__web.jsonl", {"case_id": "KQT-1", "platform": "web", "recommend": "pass"})
        _wf(d, "KQT-2__web.jsonl", {"case_id": "KQT-2", "platform": "web", "recommend": "needs-fix"})
        rows, hard = g._load_fidelity(d)
        assert hard is False
        assert len(rows) == 2, rows


def test_load_fidelity_empty_dir_is_hard_error():
    with tempfile.TemporaryDirectory() as d:
        rows, hard = g._load_fidelity(d)
        assert hard is True and rows == []


def test_evaluate_all_pass_vs_needs_fix():
    with tempfile.TemporaryDirectory() as d:
        _wf(d, "KQT-1__web.jsonl", {"case_id": "KQT-1", "platform": "web", "recommend": "pass"})
        rows, _ = g._load_fidelity(d)
        # 命中 pass
        res = g._evaluate([("KQT-1", "web")], rows)
        assert res[0][2] is True
        # needs-fix 不過
        rows2 = [{"case_id": "KQT-1", "platform": "web", "recommend": "needs-fix"}]
        assert g._evaluate([("KQT-1", "web")], rows2)[0][2] is False


def test_cleanup_on_pass_only_removes_claimed():
    with tempfile.TemporaryDirectory() as d:
        _wf(d, "KQT-1__web.jsonl", {"case_id": "KQT-1", "platform": "web", "recommend": "pass"})
        _wf(d, "KQT-9__web.jsonl", {"case_id": "KQT-9", "platform": "web", "recommend": "pass"})  # 別的 session
        g._cleanup_passed(d, [("KQT-1", "web")])
        assert not os.path.exists(os.path.join(d, "KQT-1__web.jsonl")), "本次 claimed 應被刪"
        assert os.path.exists(os.path.join(d, "KQT-9__web.jsonl")), "別 session 的檔不可被刪"


def test_cleanup_none_platform_globs_all_platforms():
    with tempfile.TemporaryDirectory() as d:
        _wf(d, "KQT-1__web.jsonl", {"case_id": "KQT-1", "platform": "web", "recommend": "pass"})
        _wf(d, "KQT-1__mweb.jsonl", {"case_id": "KQT-1", "platform": "mweb", "recommend": "pass"})
        g._cleanup_passed(d, [("KQT-1", None)])
        assert not os.path.exists(os.path.join(d, "KQT-1__web.jsonl"))
        assert not os.path.exists(os.path.join(d, "KQT-1__mweb.jsonl"))


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} passed")

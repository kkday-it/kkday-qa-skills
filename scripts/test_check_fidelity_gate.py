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


def _read_ledger(path):
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def test_write_delivery_ledger_explicit_platforms():
    # #5 根治：明確 claim web+mweb → 一筆 case、聚合兩平台、confidence 不重複
    with tempfile.TemporaryDirectory() as d:
        rows = [
            {"case_id": "KQT-1", "platform": "web", "recommend": "pass", "confidence": 0.9},
            {"case_id": "KQT-1", "platform": "mweb", "recommend": "pass", "confidence": 0.8},
        ]
        passed = [("KQT-1", "web", True, "pass"), ("KQT-1", "mweb", True, "pass")]
        led = os.path.join(d, "ledger.jsonl")
        n = g._write_delivery_ledger(led, passed, rows)
        assert n == 1
        recs = _read_ledger(led)
        assert recs[0]["caseid"] == "KQT-1"
        assert recs[0]["platforms"] == ["mweb", "web"]
        assert recs[0]["delivered"] is True and recs[0]["source"] == "fidelity_gate"
        assert recs[0]["fidelity_confidence"] == [0.9, 0.8]  # 不重複


def test_write_delivery_ledger_explicit_excludes_unclaimed_platform():
    # 只 claim/pass web，fidelity 另有 mweb 列（本輪沒 claim）→ 不可把 mweb 記成交付
    with tempfile.TemporaryDirectory() as d:
        rows = [
            {"case_id": "KQT-1", "platform": "web", "recommend": "pass"},
            {"case_id": "KQT-1", "platform": "mweb", "recommend": "pass"},
        ]
        passed = [("KQT-1", "web", True, "pass")]
        led = os.path.join(d, "ledger.jsonl")
        g._write_delivery_ledger(led, passed, rows)
        assert _read_ledger(led)[0]["platforms"] == ["web"]


def test_write_delivery_ledger_wildcard_harvests_platforms():
    # platform=None（wildcard）→ 從 fidelity 補全平台
    with tempfile.TemporaryDirectory() as d:
        rows = [
            {"case_id": "KQT-1", "platform": "web", "recommend": "pass"},
            {"case_id": "KQT-1", "platform": "mweb", "recommend": "pass"},
        ]
        passed = [("KQT-1", None, True, "pass")]
        led = os.path.join(d, "ledger.jsonl")
        g._write_delivery_ledger(led, passed, rows)
        assert _read_ledger(led)[0]["platforms"] == ["mweb", "web"]


def test_write_delivery_ledger_empty_passed():
    with tempfile.TemporaryDirectory() as d:
        led = os.path.join(d, "ledger.jsonl")
        assert g._write_delivery_ledger(led, [], []) == 0
        assert not os.path.exists(led)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} passed")

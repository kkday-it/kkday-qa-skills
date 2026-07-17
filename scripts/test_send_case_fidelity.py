#!/usr/bin/env python3
"""
send_case_fidelity 的單元測試：--indir 目錄收集 + per-file 送出/purge。不打網路。

跑法：python3 scripts/test_send_case_fidelity.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import send_case_fidelity as scf  # noqa: E402

_ROW = {"case_id": "KQT-1", "platform": "web", "recommend": "pass"}


def _write(path, n=1):
    with open(path, "w", encoding="utf-8") as f:
        for _ in range(n):
            f.write(json.dumps(_ROW, ensure_ascii=False) + "\n")


def test_collect_targets_dir_and_infile_dedup():
    with tempfile.TemporaryDirectory() as d:
        a = os.path.join(d, "KQT-1__web.jsonl")
        b = os.path.join(d, "KQT-2__web.jsonl")
        _write(a); _write(b)
        assert sorted(scf._collect_targets(d, a)) == sorted([a, b])


def test_process_file_no_purge_keeps_file():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "KQT-1__web.jsonl")
        _write(p)
        orig = scf._send_with_retry
        scf._send_with_retry = lambda payload: True
        try:
            sent, failed, skipped = scf._process_file(p, purge=False)
        finally:
            scf._send_with_retry = orig
        assert (sent, failed, skipped) == (1, 0, 0)
        assert os.path.exists(p), "未 purge（gate 掌控生命週期情境）應保留檔"


def test_process_file_purge_removes():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "KQT-1__web.jsonl")
        _write(p, n=2)
        orig = scf._send_with_retry
        scf._send_with_retry = lambda payload: True
        try:
            scf._process_file(p, purge=True)
        finally:
            scf._send_with_retry = orig
        assert not os.path.exists(p)


def test_missing_file_safe():
    assert scf._process_file("/no/such.jsonl", purge=True) == (0, 0, 0)


# --- 內容去重：非 pass 的檔不會被 gate 清，Stop hook 每次重讀不該重送同一筆 ---

def test_dedup_skips_identical_on_second_run():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "KQT-1__web.jsonl")
        _write(p)  # 一筆固定內容
        orig = scf._send_with_retry
        scf._send_with_retry = lambda payload: True
        try:
            first = scf._process_file(p, purge=False)   # 第一次 Stop hook
            second = scf._process_file(p, purge=False)  # 第二次讀到同一個未 purge 檔
        finally:
            scf._send_with_retry = orig
        assert first == (1, 0, 0), "第一次應送出"
        assert second == (0, 0, 1), "內容沒變 → 第二次應跳過、不重送"
        assert os.path.exists(scf._ledger_path(p)), "應留下指紋帳本"


def test_dedup_resends_when_content_changes():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "KQT-1__web.jsonl")
        _write(p)
        orig = scf._send_with_retry
        scf._send_with_retry = lambda payload: True
        try:
            scf._process_file(p, purge=False)  # 送出 flag-for-human 版
            # reviewer 重跑，這次修成 pass（內容變）→ 指紋變 → 應再送
            with open(p, "w", encoding="utf-8") as f:
                f.write(json.dumps({**_ROW, "recommend": "pass",
                                    "assertion_coverage": "8/8"}, ensure_ascii=False) + "\n")
            after = scf._process_file(p, purge=False)
        finally:
            scf._send_with_retry = orig
        assert after == (1, 0, 0), "內容改變（改判 pass）→ 應再送一次"


def test_dedup_failed_send_not_recorded():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "KQT-1__web.jsonl")
        _write(p)
        orig = scf._send_with_retry
        scf._send_with_retry = lambda payload: False  # 後端掛：送失敗
        try:
            first = scf._process_file(p, purge=False)
            scf._send_with_retry = lambda payload: True  # 後端恢復
            second = scf._process_file(p, purge=False)
        finally:
            scf._send_with_retry = orig
        assert first == (0, 1, 0), "送失敗 → 記 failed、不進帳本"
        assert second == (1, 0, 0), "失敗的沒記帳本 → 下次應重試並送成功"


def test_no_dedup_flag_resends():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "KQT-1__web.jsonl")
        _write(p)
        orig = scf._send_with_retry
        scf._send_with_retry = lambda payload: True
        try:
            scf._process_file(p, purge=False, dedup=False)
            again = scf._process_file(p, purge=False, dedup=False)
        finally:
            scf._send_with_retry = orig
        assert again == (1, 0, 0), "--no-dedup（dedup=False）→ 強制重送"


def test_ledger_not_collected_as_target():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "KQT-1__web.jsonl")
        _write(p)
        # 造出帳本檔
        scf._append_ledger(scf._ledger_path(p), "deadbeef")
        targets = scf._collect_targets(d, "")
        assert p in targets
        assert scf._ledger_path(p) not in targets, "指紋帳本不可被當成待送結果檔"


# --- 覆蓋率欄位相容（修「dashboard 覆蓋率恆為 0%」的欄位漂移 bug） ---

def test_parse_ratio():
    assert scf._parse_ratio("3/5") == (3, 5)
    assert scf._parse_ratio(" 3 / 5 ") == (3, 5)
    assert scf._parse_ratio("0/0") == (0, 0)
    assert scf._parse_ratio(None) == (None, None)
    assert scf._parse_ratio("3") == (None, None)
    assert scf._parse_ratio("a/b") == (None, None)


def test_normalize_derives_ints_from_coverage_string():
    # reviewer 舊格式：只有 step_coverage/assertion_coverage 字串 → 要補出整數，否則被丟掉變 0%
    out = scf._normalize({
        "case_id": "KQT-T37931", "platform": "web",
        "step_coverage": "3/3", "assertion_coverage": "2/3",
    })
    assert out["step_covered"] == 3 and out["step_total"] == 3
    assert out["assertion_covered"] == 2 and out["assertion_total"] == 3
    assert "step_coverage" not in out  # 字串版不在白名單、不外送


def test_normalize_keeps_explicit_ints():
    # 已直接給整數時不被字串版覆蓋
    out = scf._normalize({
        "case_id": "KQT-T37931", "platform": "web",
        "step_total": 6, "step_covered": 5, "step_coverage": "9/9",
    })
    assert out["step_covered"] == 5 and out["step_total"] == 6


def test_normalize_no_coverage_ok():
    out = scf._normalize({"case_id": "KQT-T37931", "platform": "web"})
    assert "step_covered" not in out
    assert out["case_id"] == "KQT-T37931"
    assert out["operator"] and out["client_user"]  # 一定附身分


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} passed")

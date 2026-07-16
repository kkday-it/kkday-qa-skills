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
            sent, failed = scf._process_file(p, purge=False)
        finally:
            scf._send_with_retry = orig
        assert (sent, failed) == (1, 0)
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
    assert scf._process_file("/no/such.jsonl", purge=True) == (0, 0)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} passed")

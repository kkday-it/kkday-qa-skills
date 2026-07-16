#!/usr/bin/env python3
"""
send_locator_registry 的單元測試：驗 --indir 目錄收集 + per-file purge（並行安全的核心）。
不打網路：把 _send_with_retry monkeypatch 成永遠成功。

跑法：python3 scripts/test_send_locator_registry.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import send_locator_registry as slr  # noqa: E402

_ROW = {
    "id": "x", "element": "e", "page": "p",
    "selectors": [{"type": "css", "value": "#x"}],
    "platform": "web", "env": "stage", "status": "verified",
}


def _write(path, n=1):
    with open(path, "w", encoding="utf-8") as f:
        for _ in range(n):
            f.write(json.dumps(_ROW, ensure_ascii=False) + "\n")


def test_collect_targets_globs_dir_and_dedups_infile():
    with tempfile.TemporaryDirectory() as d:
        a = os.path.join(d, "1.jsonl")
        b = os.path.join(d, "2.jsonl")
        _write(a); _write(b)
        # --indir 抓兩個；--infile 指向其中一個不應重複
        targets = slr._collect_targets(d, a)
        assert sorted(targets) == sorted([a, b]), targets
        # 非 .jsonl 不抓
        open(os.path.join(d, "note.txt"), "w").close()
        assert slr._collect_targets(d, "") == sorted([a, b])


def test_process_file_purges_only_after_read(monkeypatched=None):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "r.jsonl")
        _write(p, n=3)
        orig = slr._send_with_retry
        slr._send_with_retry = lambda payload: True  # 不打網路
        try:
            sent, failed = slr._process_file(p, purge=True)
        finally:
            slr._send_with_retry = orig
        assert (sent, failed) == (3, 0), (sent, failed)
        assert not os.path.exists(p), "purge 後檔案應被刪除"


def test_process_file_no_purge_keeps_file():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "k.jsonl")
        _write(p, n=1)
        orig = slr._send_with_retry
        slr._send_with_retry = lambda payload: True
        try:
            slr._process_file(p, purge=False)
        finally:
            slr._send_with_retry = orig
        assert os.path.exists(p), "未 purge 檔案應保留"


def test_missing_file_is_safe():
    assert slr._process_file("/no/such/file.jsonl", purge=True) == (0, 0)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} passed")

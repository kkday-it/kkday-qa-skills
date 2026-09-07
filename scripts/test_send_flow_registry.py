#!/usr/bin/env python3
"""
send_flow_registry 的單元測試：驗 --indir 目錄收集 + purge 語意。
不打網路：把 _send_with_retry monkeypatch 成固定成功／失敗。

重點是 `test_process_file_keeps_file_when_any_row_failed`：這支掛在 Stop hook 背景跑，
retry 全失敗沒有人看得到，若無條件 purge 就會靜默丟掉還沒送出的收成。

跑法：python3 scripts/test_send_flow_registry.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import send_flow_registry as sfr  # noqa: E402

_ROW = {
    "name": "create_order_by_app", "kind": "setup_flow", "platform": "app",
    "location": "QATest/src/test_steps/kkday/app/bookings/booking.py:372",
    "status": "verified",
}


def _write(path, n=1, row=None):
    with open(path, "w", encoding="utf-8") as f:
        for _ in range(n):
            f.write(json.dumps(row or _ROW, ensure_ascii=False) + "\n")


def _with_sender(fn, result):
    orig = sfr._send_with_retry
    sfr._send_with_retry = lambda payload: result
    try:
        return fn()
    finally:
        sfr._send_with_retry = orig


def test_collect_targets_globs_dir_and_dedups_infile():
    with tempfile.TemporaryDirectory() as d:
        a = os.path.join(d, "1.jsonl")
        b = os.path.join(d, "2.jsonl")
        _write(a); _write(b)
        assert sorted(sfr._collect_targets(d, a)) == sorted([a, b])
        open(os.path.join(d, "note.txt"), "w").close()
        assert sfr._collect_targets(d, "") == sorted([a, b])


def test_process_file_purges_after_all_sent():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "ok.jsonl")
        _write(p, n=3)
        sent, failed = _with_sender(lambda: sfr._process_file(p, purge=True), True)
        assert (sent, failed) == (3, 0), (sent, failed)
        assert not os.path.exists(p), "全部送成功才 purge"


def test_process_file_keeps_file_when_any_row_failed():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "fail.jsonl")
        _write(p, n=2)
        sent, failed = _with_sender(lambda: sfr._process_file(p, purge=True), False)
        assert (sent, failed) == (0, 2), (sent, failed)
        assert os.path.exists(p), "有列沒送成功時必須留檔，下輪 Stop 再送（後端 upsert 冪等）"


def test_process_file_no_purge_keeps_file():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "keep.jsonl")
        _write(p, n=1)
        _with_sender(lambda: sfr._process_file(p, purge=False), True)
        assert os.path.exists(p)


def test_row_without_name_is_skipped():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "bad.jsonl")
        _write(p, n=1, row={"kind": "helper", "platform": "app"})
        sent, failed = _with_sender(lambda: sfr._process_file(p, purge=True), True)
        assert (sent, failed) == (0, 0), (sent, failed)
        assert not os.path.exists(p), "沒有任何列失敗（只是被跳過）→ 照樣 purge"


def test_missing_file_is_safe():
    assert sfr._process_file("/no/such/file.jsonl", purge=True) == (0, 0)


def test_normalize_defaults():
    out = sfr._normalize({"name": "x"})
    assert out["kind"] == "setup_flow"
    assert out["platform"] == "any"
    assert out["repo"] == "kkday-QA-automation"
    assert out["status"] == "verified"
    assert "operator" in out and "client_user" in out


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} passed")

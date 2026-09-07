#!/usr/bin/env python3
"""
check_registry_read_gate 的測試：走 CLI（那是 Stop hook 實際用的介面），驗三態與 fail-closed。

為什麼要有這支：這道 gate 原本一支測試都沒有，結果「擋一次就自己失效」（沿用別支 gate 的
claimed 檔、被那支 pass 時刪掉）是靠人問「你有先驗證嗎」才發現的。gate 的錯誤模式是**靜默放行**
——長得跟通過一模一樣，所以只能靠測試釘住。

跑法：python3 scripts/test_check_registry_read_gate.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "check_registry_read_gate.py")


def _run(claimed=None, receipt_dir=None, caseids=None):
    cmd = [sys.executable, GATE]
    if claimed:
        cmd += ["--claimed", claimed]
    if caseids:
        cmd += ["--caseids", caseids]
    if receipt_dir:
        cmd += ["--receipt-dir", receipt_dir]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def _claim(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for case, plat in rows:
            row = {"case_id": case}
            if plat is not None:
                row["platform"] = plat
            f.write(json.dumps(row) + "\n")


def _receipt(d, case, platform="", n=0):
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{case}-{platform or 'na'}.jsonl")
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "flow", "case": case, "platform": platform,
                            "n": n, "hit": bool(n)}) + "\n")
    return p


def test_no_claim_passes():
    with tempfile.TemporaryDirectory() as d:
        rc, out = _run(claimed=os.path.join(d, "missing.jsonl"), receipt_dir=os.path.join(d, "r"))
        assert rc == 0, out


def test_claim_without_receipt_blocks():
    with tempfile.TemporaryDirectory() as d:
        c = os.path.join(d, "claimed.jsonl")
        _claim(c, [("KQT-T1", "ios")])
        rc, out = _run(claimed=c, receipt_dir=os.path.join(d, "r"))
        assert rc == 1, out
        assert "KQT-T1" in out
        # 擋下訊息要給得出下一步指令，否則人只知道被擋、不知道怎麼過
        assert "fetch_locator_registry.py" in out and "--case" in out


def test_claim_with_receipt_passes():
    with tempfile.TemporaryDirectory() as d:
        c, r = os.path.join(d, "claimed.jsonl"), os.path.join(d, "r")
        _claim(c, [("KQT-T1", "ios")])
        _receipt(r, "KQT-T1", "ios", n=3)
        rc, out = _run(claimed=c, receipt_dir=r)
        assert rc == 0, out


def test_empty_result_still_counts_as_read():
    """收據記的是「有沒有去問」——registry 還沒資料的新流程一樣要過，
    否則第一個開路的人被永久擋死。"""
    with tempfile.TemporaryDirectory() as d:
        c, r = os.path.join(d, "claimed.jsonl"), os.path.join(d, "r")
        _claim(c, [("KQT-T1", "android")])
        _receipt(r, "KQT-T1", "android", n=0)   # hit=False
        rc, out = _run(claimed=c, receipt_dir=r)
        assert rc == 0, out


def test_platformless_receipt_covers_platform_claim():
    """沒帶 --platform 的讀取撈的是跨平台候選，仍算讀過。"""
    with tempfile.TemporaryDirectory() as d:
        c, r = os.path.join(d, "claimed.jsonl"), os.path.join(d, "r")
        _claim(c, [("KQT-T1", "ios")])
        _receipt(r, "KQT-T1", "", n=1)
        rc, out = _run(claimed=c, receipt_dir=r)
        assert rc == 0, out


def test_receipt_of_other_case_does_not_count():
    """按 case 比對，不能拿別的 case 的收據混過去。"""
    with tempfile.TemporaryDirectory() as d:
        c, r = os.path.join(d, "claimed.jsonl"), os.path.join(d, "r")
        _claim(c, [("KQT-T1", "ios")])
        _receipt(r, "KQT-T999", "ios", n=5)
        rc, out = _run(claimed=c, receipt_dir=r)
        assert rc == 1, out


def test_partial_coverage_blocks():
    with tempfile.TemporaryDirectory() as d:
        c, r = os.path.join(d, "claimed.jsonl"), os.path.join(d, "r")
        _claim(c, [("KQT-T1", "ios"), ("KQT-T2", "android")])
        _receipt(r, "KQT-T1", "ios", n=1)
        rc, out = _run(claimed=c, receipt_dir=r)
        assert rc == 1, out
        assert "KQT-T2" in out and "KQT-T1" not in out.split("BLOCKED")[-1]


def test_broken_claimed_line_fails_closed():
    with tempfile.TemporaryDirectory() as d:
        c, r = os.path.join(d, "claimed.jsonl"), os.path.join(d, "r")
        with open(c, "w", encoding="utf-8") as f:
            f.write('{"case_id":"KQT-T1","platform":"ios"}\n')
            f.write("not json at all\n")
        _receipt(r, "KQT-T1", "ios", n=1)
        rc, out = _run(claimed=c, receipt_dir=r)
        assert rc == 1, out  # 清單不可信 → 擋下，不是放行


def test_missing_case_id_fails_closed():
    with tempfile.TemporaryDirectory() as d:
        c = os.path.join(d, "claimed.jsonl")
        with open(c, "w", encoding="utf-8") as f:
            f.write('{"platform":"ios"}\n')
        rc, out = _run(claimed=c, receipt_dir=os.path.join(d, "r"))
        assert rc == 1, out


def test_no_args_fails_closed():
    rc, out = _run()
    assert rc == 1, out


def test_caseids_form():
    with tempfile.TemporaryDirectory() as d:
        r = os.path.join(d, "r")
        _receipt(r, "KQT-T1", "ios", n=1)
        assert _run(caseids="KQT-T1:ios", receipt_dir=r)[0] == 0
        assert _run(caseids="KQT-T1:ios,KQT-T2:web", receipt_dir=r)[0] == 1
        # 不帶平台 → 該 case 有任何收據就算
        assert _run(caseids="KQT-T1", receipt_dir=r)[0] == 0


def test_prune_removes_only_stale_receipts():
    with tempfile.TemporaryDirectory() as d:
        c, r = os.path.join(d, "claimed.jsonl"), os.path.join(d, "r")
        _claim(c, [("KQT-T1", "ios")])
        fresh = _receipt(r, "KQT-T1", "ios", n=1)
        old = _receipt(r, "KQT-T-OLD", "ios", n=1)
        os.utime(old, (time.time() - 30 * 86400,) * 2)
        rc, out = _run(claimed=c, receipt_dir=r)
        assert rc == 0, out
        assert os.path.exists(fresh), "還在保留期內的收據不可刪"
        assert not os.path.exists(old), "超過保留天數的收據應被 prune"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} passed")

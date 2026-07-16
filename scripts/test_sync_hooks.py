#!/usr/bin/env python3
"""
sync_hooks 的單元測試：確認冪等、自動 migrate 舊 hook、不動別人的 hook。

跑法：python3 scripts/test_sync_hooks.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sync_hooks as sh  # noqa: E402

REPO = "/home/alice/kkday-qa-skills"


def _stop_cmds(cfg):
    return [h["command"] for grp in cfg["hooks"]["Stop"] for h in grp["hooks"]]


def test_fresh_install_adds_indir_locator_hook():
    cfg = sh.sync({}, REPO)
    cmds = _stop_cmds(cfg)
    sl = next(c for c in cmds if "send_locator_registry.py" in c)
    assert "--indir /tmp/locator_results.d" in sl
    # send_locator 不可帶 --purge（生命週期交給 locator gate；後端 upsert 冪等）
    assert "--purge" not in sl
    assert not any("send_locator_registry.py" in c and "--infile" in c for c in cmds)


def test_locator_gate_runs_after_send_locator():
    cmds = _stop_cmds(sh.sync({}, REPO))
    send_i = next(i for i, c in enumerate(cmds) if "send_locator_registry.py" in c)
    gate_i = next(i for i, c in enumerate(cmds) if "locator_gate_stop_hook.sh" in c)
    assert send_i < gate_i, "send_locator 應在 locator gate 之前（先送，gate 才在 pass 清）"


def test_migrates_old_infile_locator_hook():
    # 既有安裝：舊的 --infile 版 locator hook
    old = f'python3 "{REPO}/scripts/send_locator_registry.py" --infile /tmp/locator_results.jsonl --purge'
    cfg = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": old}]}]}}
    sh.sync(cfg, REPO)
    cmds = _stop_cmds(cfg)
    assert old not in cmds, "舊 --infile locator hook 應被 migrate 掉"
    assert any("--indir /tmp/locator_results.d" in c for c in cmds), "應換成 --indir 版"


def test_does_not_touch_foreign_hooks():
    foreign = 'bash "/opt/other-tool/hook.sh"'
    cfg = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": foreign}]}]}}
    sh.sync(cfg, REPO)
    assert foreign in _stop_cmds(cfg), "非本 repo 的 hook 不可被動到"


def test_send_case_fidelity_runs_before_gate():
    # 順序有意義：send_case_fidelity 必須在 gate 之前（先送遙測，gate 才在 pass 時刪結果）
    cfg = sh.sync({}, REPO)
    cmds = _stop_cmds(cfg)
    send_i = next(i for i, c in enumerate(cmds) if "send_case_fidelity.py" in c)
    gate_i = next(i for i, c in enumerate(cmds) if "fidelity_gate_stop_hook.sh" in c)
    assert send_i < gate_i, f"send_case_fidelity({send_i}) 應在 gate({gate_i}) 之前"


def test_send_case_fidelity_no_purge_uses_indir():
    cmds = _stop_cmds(sh.sync({}, REPO))
    scf = next(c for c in cmds if "send_case_fidelity.py" in c)
    assert "--indir /tmp/case_fidelity_results.d" in scf
    assert "--purge" not in scf, "send_case_fidelity 不可帶 --purge（生命週期交給 gate）"


def test_idempotent():
    cfg = sh.sync({}, REPO)
    first = _stop_cmds(cfg)
    sh.sync(cfg, REPO)  # 再跑一次
    second = _stop_cmds(cfg)
    assert first == second, "重跑不應產生重複或變動"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} passed")

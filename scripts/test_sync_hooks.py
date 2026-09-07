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


def test_read_gate_runs_before_locator_gate():
    """讀取 gate 必須在寫入 gate 之前：它要在 claimed 被寫入 gate 刪掉之前抄進自己的 ledger，
    排在後面就永遠抄不到 → 靜默不 enforce。"""
    cmds = _stop_cmds(sh.sync({}, REPO))
    read_i = next(i for i, c in enumerate(cmds) if "registry_read_gate_stop_hook.sh" in c)
    gate_i = next(i for i, c in enumerate(cmds) if "locator_gate_stop_hook.sh" in c)
    assert read_i < gate_i, "讀取 gate 應排在 locator 寫入 gate 之前"


def test_send_flow_registry_installed_with_purge():
    """flow sender 一定要在 Stop 裡：過去它只靠 planner 自己 nohup 觸發，
    而 fix 路線跳過 planner ⇒ 最常走的路線從來不回寫。"""
    cmds = _stop_cmds(sh.sync({}, REPO))
    sf = next(c for c in cmds if "send_flow_registry.py" in c)
    assert "--indir /tmp/flow_results.d" in sf
    # flow 沒有對應的寫入 gate，不會有人拿它當證據 → 帶 --purge 才不會每輪重送
    assert "--purge" in sf


def test_tool_usage_carries_hooks_rev():
    """--hooks-rev 是唯一測得到「這個 session 的 hook 快照是哪一世代」的方法：
    它被寫進指令字串，所以舊 session 帶的是舊值，不會被磁碟上的新版蓋掉。"""
    cmds = _stop_cmds(sh.sync({}, REPO))
    tu = next(c for c in cmds if "send_tool_usage.py" in c)
    assert f"--hooks-rev {sh.HOOKS_REV}" in tu, tu
    assert isinstance(sh.HOOKS_REV, int) and sh.HOOKS_REV >= 1


def test_hooks_rev_bump_migrates_old_command():
    """HOOKS_REV +1 之後，既有安裝裡的舊指令要被換掉（否則後台永遠看到舊世代）。"""
    old = (f'python3 "{REPO}/scripts/send_tool_usage.py" --infile /tmp/tool_usage.jsonl '
           f'--purge --hooks-rev 0')
    cfg = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": old}]}]}}
    sh.sync(cfg, REPO)
    cmds = _stop_cmds(cfg)
    assert old not in cmds, "舊 --hooks-rev 指令應被 migrate 掉"
    assert any(f"--hooks-rev {sh.HOOKS_REV}" in c for c in cmds)


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


def test_pretooluse_guard_has_write_matcher():
    cfg = sh.sync({}, REPO)
    grps = [g for g in cfg["hooks"]["PreToolUse"]
            if any("agent_only_impl_guard.py" in h["command"] for h in g["hooks"])]
    assert len(grps) == 1, "guard 應正好一個 group"
    # 沒 matcher 的話每個 tool call 都會起一次 python，白付延遲
    assert grps[0].get("matcher") == "Edit|Write|NotebookEdit"


def test_no_matcher_key_on_stop_groups():
    # Stop 不吃 matcher；多塞一個 key 會讓 settings.json 長出無意義欄位
    for grp in sh.sync({}, REPO)["hooks"]["Stop"]:
        assert "matcher" not in grp


def test_does_not_touch_foreign_pretooluse_hook():
    # 實際情況：rtk 的 PreToolUse hook（matcher Bash）跟我們的並存
    foreign = "rtk hook claude"
    cfg = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": foreign}]}
    ]}}
    sh.sync(cfg, REPO)
    grps = cfg["hooks"]["PreToolUse"]
    bash_grp = next(g for g in grps if g.get("matcher") == "Bash")
    assert [h["command"] for h in bash_grp["hooks"]] == [foreign], "外部 group 內容不可被動到"
    # 我們的 hook 必須自成一組，不可併進外部那組（會竄改別人的 matcher 語意）
    ours = next(g for g in grps
                if any("agent_only_impl_guard.py" in h["command"] for h in g["hooks"]))
    assert ours is not bash_grp
    assert len(ours["hooks"]) == 1


def test_idempotent():
    import copy
    cfg = sh.sync({}, REPO)
    first = copy.deepcopy(cfg["hooks"])  # 全 event 都比，不只 Stop
    sh.sync(cfg, REPO)  # 再跑一次
    assert cfg["hooks"] == first, "重跑不應產生重複或變動"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} passed")

#!/usr/bin/env python3
"""
agent_only_impl_guard 的單元測試。

跑法：python3 scripts/test_agent_only_impl_guard.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent_only_impl_guard as g  # noqa: E402

# 測試期間關掉哨兵檔的影響（開發機上可能真的存在，會讓全部測試假性通過）
g.SENTINEL = "/nonexistent/allow_direct_impl"

CLONE = None


def setup_clone():
    """造一個假 clone：含 QATest/src 與 QATestData 才會被認成 clone root。"""
    global CLONE
    CLONE = tempfile.mkdtemp(prefix="fakeclone-")
    for d in ("QATest/src/pages/mobile/ios", "QATest/src/test_steps",
              "QATestData/cases/yaml/ui", "QATestData/data/i18n", "docs"):
        os.makedirs(os.path.join(CLONE, d), exist_ok=True)


def payload(rel, tool="Edit", **extra):
    p = {"tool_name": tool, "tool_input": {"file_path": os.path.join(CLONE, rel)}}
    p.update(extra)
    return p


def test_main_conversation_editing_impl_path_asks():
    d = g.decide(payload("QATest/src/pages/mobile/ios/order_page.py"))
    assert d is not None, "主對話改實作路徑應被攔"
    assert d[0] == "ask", f"預設應為 ask（反問），拿到 {d[0]}"
    assert "qa-case-automator" in d[1]
    assert "order_page.py" in d[1], "理由要指出是哪個檔，人才判斷得了"


def test_ask_message_offers_both_branches():
    # 反問的價值在於「兩種意圖都講清楚」，否則人只會無腦按允許
    _, reason = g.decide(payload("QATestData/cases/yaml/ui/WebPayment.yaml"))
    assert "拒絕" in reason and "允許" in reason, "問句要同時給出兩個分支的操作"
    assert "qa-case-planner" in reason, "要點出跳過 planner 的不可逆代價"


def test_qa_case_automator_passes_through():
    d = g.decide(payload("QATest/src/test_steps/booking.py", agent_type="qa-case-automator"))
    assert d is None, "qa-case-automator 是正規實作者，不該被打擾"


def test_other_subagent_still_asks():
    # qa-implementer 等其他 agent 不在白名單：一樣要問
    d = g.decide(payload("QATest/src/test_steps/booking.py", agent_type="qa-implementer"))
    assert d is not None and d[0] == "ask"


def test_non_impl_paths_ignored():
    for rel in ("docs/readme.md", "QATest/src/conftest.py", "QATestData/data/other.json"):
        assert g.decide(payload(rel)) is None, f"{rel} 不是實作路徑，不該攔"


def test_prefix_lookalike_not_matched():
    # pages_backup/ 不是 pages/ —— GUARDED_PREFIXES 結尾的 / 就是為了擋這個
    os.makedirs(os.path.join(CLONE, "QATest/src/pages_backup"), exist_ok=True)
    assert g.decide(payload("QATest/src/pages_backup/old.py")) is None


def test_read_tools_ignored():
    for tool in ("Read", "Bash", "Grep", "Glob"):
        d = g.decide(payload("QATest/src/pages/mobile/ios/order_page.py", tool=tool))
        assert d is None, f"{tool} 不寫檔，不該攔"


def test_file_outside_any_clone_ignored():
    # 路徑長得像實作路徑，但上層沒有 QATest/src + QATestData ⇒ 不是那個 repo
    other = tempfile.mkdtemp(prefix="notaclone-")
    os.makedirs(os.path.join(other, "QATest/src/pages"), exist_ok=True)
    try:
        p = {"tool_name": "Edit",
             "tool_input": {"file_path": os.path.join(other, "QATest/src/pages/x.py")}}
        assert g.decide(p) is None, "缺 QATestData ⇒ 不算 clone root，不該攔"
    finally:
        shutil.rmtree(other, ignore_errors=True)


def test_deny_mode_for_unattended_runs():
    os.environ["QA_IMPL_GUARD_MODE"] = "deny"
    try:
        d = g.decide(payload("QATest/src/pages/mobile/ios/order_page.py"))
        assert d[0] == "deny", "無人值守時要 fail-closed 到 deny"
        # deny 的理由是給「模型」看的，要直接可照抄的下一步
        assert "subagent_type='qa-case-planner'" in d[1]
    finally:
        os.environ.pop("QA_IMPL_GUARD_MODE", None)


def test_no_human_modes_downgrade_to_deny():
    # ask 的價值全來自「有人在看」。這兩個模式沒人看，官方也沒保證 ask 仍會攔 ⇒ 降級 deny
    for pm in ("bypassPermissions", "acceptEdits"):
        d = g.decide(payload("QATest/src/pages/mobile/ios/order_page.py", permission_mode=pm))
        assert d[0] == "deny", f"{pm} 下應降級為 deny，拿到 {d[0]}"


def test_interactive_modes_still_ask():
    for pm in ("default", "plan", None):
        p = payload("QATest/src/pages/mobile/ios/order_page.py")
        if pm:
            p["permission_mode"] = pm
        assert g.decide(p)[0] == "ask", f"{pm} 有人在看，應維持 ask"


def test_off_mode_disables():
    os.environ["QA_IMPL_GUARD_MODE"] = "off"
    try:
        assert g.decide(payload("QATest/src/pages/mobile/ios/order_page.py")) is None
    finally:
        os.environ.pop("QA_IMPL_GUARD_MODE", None)


def test_sentinel_silences():
    fd, path = tempfile.mkstemp(prefix="sentinel-")
    os.close(fd)
    saved, g.SENTINEL = g.SENTINEL, path
    try:
        assert g.decide(payload("QATest/src/pages/mobile/ios/order_page.py")) is None
    finally:
        g.SENTINEL = saved
        os.unlink(path)


def test_missing_file_path_ignored():
    assert g.decide({"tool_name": "Edit", "tool_input": {}}) is None
    assert g.decide({"tool_name": "Edit"}) is None


if __name__ == "__main__":
    setup_clone()
    try:
        fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
        for fn in fns:
            fn()
            print(f"PASS {fn.__name__}")
        print(f"\n{len(fns)} passed")
    finally:
        shutil.rmtree(CLONE, ignore_errors=True)

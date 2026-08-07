#!/usr/bin/env python3
"""
case_impl_router 的單元測試。重點在「該注入時注入、不該注入時閉嘴」——
誤判把「跑 case」當成「實作 case」比漏判更煩人。

跑法：python3 scripts/test_case_impl_router.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import case_impl_router as r  # noqa: E402


def test_impl_with_full_case_id_injects():
    ctx = r.build_context("KQT-T63751 iOS 實作")
    assert ctx is not None
    assert "qa-case-planner" in ctx and "qa-case-automator" in ctx
    assert "case=KQT-T63751" in ctx
    assert "platform=ios" in ctx


def test_bare_case_id_injects():
    # 使用者常常只打 T63751
    ctx = r.build_context("T63751 web 實作")
    assert ctx is not None and "KQT-T63751" in ctx and "platform=web" in ctx


def test_run_request_not_hijacked():
    # 這是本 repo 真的發生過的歧義：`KQT-T63751 web` 在 qa-test-runner 語法裡是「跑」
    for text in ("KQT-T63751 web", "KQT-T63751", "跑 KQT-T63751 web", "T63751 android 執行一次"):
        assert r.build_context(text) is None, f"「{text}」是跑 case，不該被路由成實作"


def test_tcms_side_updates_not_hijacked():
    """「更新 KQT-Txxx Case」在 KKday QA 的講法裡是更新 **TCMS 上的 case**，不是改自動化。

    那類請求屬於 zephyr-case / gherkin-to-tcms 的地盤。若有人日後把「更新 / 修改 / 調整」
    這類裸動詞加進 IMPL_RE，本測試會擋下來——覆蓋率不會增加（真要改自動化的講法本來就
    含「自動化 / 實作」），只會把不碰 code 的請求拖進 agent 流程。
    """
    for text in ("更新 KQT-T12345 Case", "修改 KQT-T12345", "調整 KQT-T12345 的步驟",
                 "KQT-T12345 spec 改了，跟著更新", "clone KQT-T12345"):
        assert r.build_context(text) is None, f"「{text}」是 TCMS 端，不該路由到自動化 agent"


def test_automation_side_updates_do_route():
    # 對照組：真的要改自動化時，講法本來就帶「自動化 / 實作 / automation」
    for text in ("更新 KQT-T12345 的自動化", "修改 KQT-T12345 的實作",
                 "重寫 KQT-T12345 的 automation"):
        assert r.build_context(text) is not None, f"「{text}」是自動化端，應該路由"


def test_impl_word_without_case_id_ignored():
    assert r.build_context("幫我實作一個新的 helper") is None
    assert r.build_context("這段自動化怎麼寫") is None


def test_short_bare_t_not_matched():
    # T1 / T2 常出現在別的語境（表格欄、版本號），裸寫需 4 碼以上
    assert r.build_context("T1 實作") is None
    assert r.build_context("T99 自動化") is None
    assert r.find_cases("KQT-T12") == ["KQT-T12"], "有 KQT- 前綴就不受 4 碼限制"


def test_platform_missing_flags_it():
    ctx = r.build_context("KQT-T63751 實作")
    assert "<向使用者確認>" in ctx
    assert "先問清楚" in ctx, "沒指定平台要明講，不要讓 planner 自己猜"


def test_multiple_cases_listed_and_dedup():
    ctx = r.build_context("實作 KQT-T111 KQT-T222 KQT-T111 web")
    assert "KQT-T111、KQT-T222" in ctx
    assert "一個一個" in ctx


def test_truncation_is_announced():
    # 不做無聲截斷：超過上限要在訊息裡講
    ids = " ".join(f"KQT-T{1000 + i}" for i in range(r.MAX_CASES + 3))
    ctx = r.build_context(f"實作 {ids} web")
    assert "另有 3 個未列出" in ctx


def test_escape_line_present():
    # 誤判時要讓模型自己走得回去
    ctx = r.build_context("KQT-T63751 web 實作")
    assert "只是要「跑」" in ctx


def test_case_insensitive():
    assert r.find_cases("kqt-t63751") == ["KQT-T63751"]
    assert r.build_context("kqt-t63751 IMPLEMENT on iOS") is not None


def test_extract_prompt_tries_candidates():
    assert r.extract_prompt({"prompt": "hi"}) == "hi"
    assert r.extract_prompt({"user_prompt": "hi"}) == "hi"
    # 找不到就回空字串（fail-open），不能爆
    r.DEBUG_KEYS = "/nonexistent/dir/keys.txt"  # 連寫 debug 檔失敗也不能爆
    assert r.extract_prompt({"session_id": "x"}) == ""


def test_no_case_no_impl_is_silent():
    assert r.build_context("今天天氣如何") is None
    assert r.build_context("") is None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} passed")

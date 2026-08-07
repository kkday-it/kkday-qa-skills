#!/usr/bin/env python3
"""
UserPromptSubmit hook：使用者輸入「KQT-T1234 實作」這類請求時，注入明確指示，讓主對話
走 qa-case-planner → qa-case-automator → qa-case-fidelity-reviewer，而不是自己 inline 寫。

## 這是「路由」層，不是「攔截」層

兩層分工（缺一不可）：

    本檔（UserPromptSubmit）  在對的時機把正確做法講清楚 —— 讓正確路徑先發生
    agent_only_impl_guard.py（PreToolUse）  真的動手寫實作檔時反問 —— 接住漏掉的

為什麼需要注入而不是只寫在 CLAUDE.md：CLAUDE.md 在長對話裡會被稀釋，而且它講的是通則；
注入是**貼著那一則 prompt**出現的具體指令（連 case id / platform 都填好了），時機與具體度
都不一樣。

## 誠實的限制：這是 best-effort，不是保證

**沒有任何 hook 能強迫模型呼叫 Agent tool。** UserPromptSubmit 的輸出只有 `decision: block`
與 `additionalContext` 兩種，前者只能擋、後者只能建議。所以本檔提高的是「照走」的機率，
真正接住漏網的是 PreToolUse 那層。兩層都不是密不透風——這點不要對外宣稱成「強制」。

## 觸發條件（兩個都要滿足，避免誤判）

    有 case id：  KQT-T63751      或裸寫 T63751（裸寫要 4 碼以上，避免 T1/T2 誤中）
    有實作意圖：  實作 / 實做 / 自動化 / implement / automate ...

**只有 case id 不注入**——因為 `KQT-T63751 web` 在 qa-test-runner 的語法裡是「跑這個 case」，
不是實作。這個歧義是真的（本 repo 的 session 就發生過），所以動詞是必要條件。

注入文字最後會註明「如果只是要跑，忽略本提示」，讓模型在少數誤判時能自己走回正確的路。

## fail-open

任何例外（stdin 壞掉、找不到 prompt 欄位）一律不注入、exit 0。這支只是加提示，
壞掉時最差的結果是回到現狀，不該擋住任何人的 prompt。
"""
import json
import os
import re
import sys

# KQT-T63751 / kqt-t63751；裸寫 T63751（限 4 碼以上，避免 T1、T2 這類誤中）
CASE_RE = re.compile(r"\b(?:KQT-T(\d+)|T(\d{4,}))\b", re.IGNORECASE)

# 關鍵字挑的是「動作 + 自動化這個受詞」，不是單純的動作。
#
# 刻意**不收** 更新 / 修改 / 調整 這類裸動詞：在 KKday QA 的講法裡，「更新 KQT-T12345
# Case」通常指**更新 TCMS 上的 case 內容**，不是改自動化程式。收了會把一票根本不碰
# code 的請求拖進 agent 流程。
#
# 而真正要更新自動化的講法（「更新 KQT-T12345 的自動化」「修改 KQT-T12345 的實作」）
# 本來就含「自動化 / 實作」，靠下面這串已經接得到——加裸動詞只增誤判、不增覆蓋。
IMPL_RE = re.compile(
    r"實作|實做|自動化|寫成\s*auto|做成\s*case|implement|automate|automation",
    re.IGNORECASE,
)

PLATFORM_RE = re.compile(r"\b(mweb|web|ios|android)\b", re.IGNORECASE)

MAX_CASES = 10  # 超過就截斷，但**會在訊息裡講明**（不做無聲截斷）

DEBUG_KEYS = os.path.join(
    os.path.expanduser("~"), ".claude", "harness", "router_unknown_payload.txt"
)

TEMPLATE = """\
[case-impl-router] 偵測到 TCMS case 自動化【實作／更新】請求：{cases}{platform_note}

這類任務要走 agent 流程，**不要在主對話 inline 改**，也不要交給 qa-implementer：

1. Agent(subagent_type='qa-case-planner', prompt='case={first} platform={platform}')
2. 把 planner 的計畫拿給使用者確認（這一步不能跳）
3. Agent(subagent_type='qa-case-automator', prompt='case={first}')
4. Agent(subagent_type='qa-case-fidelity-reviewer', prompt='case={first}')

多個 case 就一個一個各跑一輪，不要一次丟給同一個 agent。

為什麼：planner 的規劃只在動手**之前**有意義（前置怎麼建真實資源、關鍵斷言驗什麼、
會不會動到共用 step 而影響其他 case），inline 改完就永遠補不回來；而 fidelity / locator
兩個 Stop gate 是 qa-case-automator 寫 claimed 檔才會 arm，沒經過它 ⇒ gate 直接放行，
**沒驗過卻長得跟驗過一模一樣**。更新既有實作尤其危險：它本來就是綠的、也有驗收紀錄。

若使用者其實只是要「跑」這些 case、或只是要看/改 TCMS 上的 case 內容本身（不動自動化
程式），忽略本提示。"""


def extract_prompt(payload: dict) -> str:
    """取使用者輸入。官方文件沒列 UserPromptSubmit 的 event-specific 欄位，故試多個候選 key。"""
    for key in ("prompt", "user_prompt", "message", "text", "content"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val
    # 一個都沒中：把 key 記下來，讓下次能對症修，而不是再猜一輪
    try:
        os.makedirs(os.path.dirname(DEBUG_KEYS), exist_ok=True)
        with open(DEBUG_KEYS, "w", encoding="utf-8") as f:
            f.write(",".join(sorted(payload.keys())) + "\n")
    except Exception:
        pass
    return ""


def find_cases(text: str):
    """回正規化後的 case id 清單（保持出現順序、去重）。"""
    out = []
    for m in CASE_RE.finditer(text):
        cid = "KQT-T" + (m.group(1) or m.group(2))
        if cid not in out:
            out.append(cid)
    return out


def build_context(text: str):
    """該注入就回字串，否則 None。"""
    if not IMPL_RE.search(text):
        return None  # 只有 case id 而無實作動詞 ⇒ 多半是「跑」，不插手
    cases = find_cases(text)
    if not cases:
        return None
    shown, extra = cases[:MAX_CASES], len(cases) - MAX_CASES
    cases_str = "、".join(shown)
    if extra > 0:
        cases_str += f"（另有 {extra} 個未列出，同樣逐案處理）"
    pm = PLATFORM_RE.search(text)
    platform = pm.group(1).lower() if pm else "<向使用者確認>"
    note = "" if pm else "\n（prompt 未指明 platform，planner 開跑前先問清楚 web/mweb/ios/android）"
    return TEMPLATE.format(
        cases=cases_str, first=shown[0], platform=platform, platform_note=note
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        ctx = build_context(extract_prompt(payload))
    except Exception as exc:
        print(f"[case_impl_router] 略過（{exc}）", file=sys.stderr)
        return 0
    if ctx:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": ctx,
                }
            },
            sys.stdout,
            ensure_ascii=False,
        )
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

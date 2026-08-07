#!/usr/bin/env python3
"""
PreToolUse hook：主對話要直接改 kkday-QA-automation 的「實作路徑」時，**反問使用者**
是否該改走 qa-case-automator。

## 為什麼需要這支

TCMS case 自動化的正規流程是 qa-case-planner（先規劃）→ qa-case-automator（實作，並
arm fidelity / locator 兩個 claimed 檔）→ qa-case-fidelity-reviewer（忠實度驗收）。
但**主對話常常自己 inline 就把 case 實作掉**，agent 一個都沒叫到。後果不是「少一層包裝」：

  - qa-case-planner 被跳過 —— 規劃發生在動手「之前」（決定要建哪些真實前置、關鍵斷言驗
    什麼）。一旦 inline 寫完，這個時機就永遠過去了，事後任何 gate 都補不回來。
  - 兩個 Stop gate 靜默失效 —— claimed 檔由 qa-case-automator 寫，沒 agent 就沒 claimed，
    gate 第一行 `[ -f "$CLAIMED" ] || exit 0` 直接放行。fidelity 沒驗、locator 沒回寫，
    而且**看起來跟真的驗過一模一樣**。

軟性辦法（SKILL.md 寫規範、UserPromptSubmit 塞提醒）都擋不住：UserPromptSubmit 的輸出
只有 block / additionalContext，**沒有任何 hook 能強迫模型呼叫 Agent tool**。

## 為什麼是 ask 不是 deny

同一個寫入動作有兩種完全合法的意圖，**從路徑看不出來**：

    「實作 KQT-T1234 iOS」                → 該走 planner → automator → reviewer
    「這行 typo 改一下」「解 merge conflict」「pre-commit 的 lint」 → 主對話直接改才合理

硬 deny 會把後者也擋掉，逼人開逃生門、最後乾脆關掉 hook。所以這支**不自己裁決意圖，把
決定權交回人**：`permissionDecision: "ask"` 會跳出權限詢問，人選「允許」＝這是小修、
選「拒絕」＝去 spawn agent。人是唯一知道自己意圖的人，這比任何路徑規則都準。

結構性沒有變弱：模型**無法靜默地 inline 實作**，寫入一定經過人的一次決定。

但這只在「真的有人在看」時成立。`bypassPermissions` / `acceptEdits` 模式下沒人會看到問句
（見 NO_HUMAN_MODES），那裡一律降級為 deny。

## 判定範圍

只管 Edit / Write / NotebookEdit 對「實作路徑」的寫入：

    <clone>/QATest/src/pages/**
    <clone>/QATest/src/test_steps/**
    <clone>/QATestData/cases/yaml/**
    <clone>/QATestData/data/i18n/**

<clone> = 從目標檔往上找到、同時含 `QATest/src` 與 `QATestData` 的目錄（支援多 clone /
worktree，不寫死路徑）。其他 repo、其他路徑（pyproject、README、scripts…）一律不管。

subagent 判定用 hook input 的 `agent_type`：**這個欄位只有在 subagent 內才會出現**，
主對話沒有。`agent_type == "qa-case-automator"` ⇒ 直接放行、不打擾。

## 模式與靜音

    QA_IMPL_GUARD_MODE=ask   （預設）跳出詢問，人決定
    QA_IMPL_GUARD_MODE=deny  直接擋。給無人值守的 harness / 自主模式用——沒人可問時，
                             fail-closed 到「必須走 agent」比放行安全
    QA_IMPL_GUARD_MODE=off   完全停用

    touch ~/.claude/harness/allow_direct_impl   哨兵檔：整段大改期間免問（記得刪掉）

## fail-OPEN（與 Stop gate 的 fail-closed 相反，這是刻意的）

Stop gate 擋的是「turn 結束」，擋錯頂多多跑一輪。這支擋的是「寫檔」，誤擋會**連修好這支
hook 本身都做不到**（要改 scripts/ 也得寫檔）——那是死鎖。所以任何例外狀況（stdin 壞掉、
路徑算不出來）一律放行並印 stderr。
"""
import json
import os
import sys

# 這些 subagent 動實作路徑是正常的，不打擾。planner / fidelity-reviewer 唯讀，不需列。
ALLOWED_AGENT_TYPES = {"qa-case-automator"}

# clone 相對路徑前綴（用 / 結尾，避免 pages_backup 這種前綴誤命中）
GUARDED_PREFIXES = (
    "QATest/src/pages/",
    "QATest/src/test_steps/",
    "QATestData/cases/yaml/",
    "QATestData/data/i18n/",
)

WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}

# 刻意**不**管 Bash。`sed -i` / `cat >` 確實能繞過這支，但要偵測就得比對 command 字串，
# 實測 5 個唯讀命令會誤判 4 個（`grep -rn foo QATest/src/test_steps/ > /tmp/out` 這種
# 日常用法也中）。一個常誤跳的守衛會被關掉，關掉的守衛防護力是零 —— 留洞比留假警報好。
# 要真的堵這條，該做的是讓 Stop gate 用 git diff 當證據（見 docs/），不是在這裡猜。

# 這些 permission_mode 下「不會有人真的看到問句」——bypassPermissions 直接跳過權限流程，
# acceptEdits 對檔案編輯自動放行。官方文件只保證 hook 照跑，**沒有**保證 ask 仍會攔下來。
# 與其賭未定義行為，這些模式一律降級為 deny：ask 的整個價值來自「有人在看」，沒人看時
# 它就只是一個會被自動點掉的裝飾。
NO_HUMAN_MODES = {"bypassPermissions", "acceptEdits"}

SENTINEL = os.path.join(
    os.path.expanduser("~"), ".claude", "harness", "allow_direct_impl"
)

# 給「人」看的問句：兩個分支都講清楚，讓人一眼能判斷自己要哪個。
ASK_REASON = (
    "主對話正要直接修改自動化實作檔（{rel}）。\n"
    "\n"
    "• 如果這是 TCMS case 的自動化實作 → 請【拒絕】，改走正規流程：\n"
    "  qa-case-planner（先規劃並與你確認）→ qa-case-automator（實作、arm 兩個 gate）\n"
    "  → qa-case-fidelity-reviewer（驗忠實度）。\n"
    "  跳過 planner 等於永久錯過規劃時機，事後 gate 補不回來。\n"
    "\n"
    "• 如果只是小修（typo / merge conflict / pre-commit lint / 跨 case tech-debt）\n"
    "  → 直接【允許】即可。"
)

DENY_REASON = (
    "自動化實作檔（{rel}）只能由 qa-case-automator 修改。請走："
    "Agent(subagent_type='qa-case-planner', prompt='case=<KQT-T…> platform=<web|mweb|ios|android>')"
    " → Agent(subagent_type='qa-case-automator', prompt='case=<KQT-T…>')"
    " → Agent(subagent_type='qa-case-fidelity-reviewer', prompt='case=<KQT-T…>')。"
)


def find_clone_root(path: str):
    """從 path 往上找同時含 QATest/src 與 QATestData 的目錄；找不到回 None。"""
    d = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.isdir(os.path.join(d, "QATest", "src")) and os.path.isdir(
            os.path.join(d, "QATestData")
        ):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def guarded_relpath(file_path: str):
    """是實作路徑就回 clone 內相對路徑，否則 None。"""
    if not file_path:
        return None
    root = find_clone_root(file_path)
    if not root:
        return None
    rel = os.path.relpath(os.path.abspath(file_path), root).replace(os.sep, "/")
    return rel if rel.startswith(GUARDED_PREFIXES) else None


def decide(payload: dict):
    """回 (permissionDecision, reason)；放行則回 None。"""
    mode = os.environ.get("QA_IMPL_GUARD_MODE", "ask").lower()
    if mode == "off" or os.path.exists(SENTINEL):
        return None
    if payload.get("tool_name") not in WRITE_TOOLS:
        return None
    rel = guarded_relpath((payload.get("tool_input") or {}).get("file_path", ""))
    if not rel:
        return None
    # agent_type 只在 subagent 內出現；主對話沒有這個欄位
    if payload.get("agent_type") in ALLOWED_AGENT_TYPES:
        return None
    if mode == "deny" or payload.get("permission_mode") in NO_HUMAN_MODES:
        return "deny", DENY_REASON.format(rel=rel)
    return "ask", ASK_REASON.format(rel=rel)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        verdict = decide(payload)
    except Exception as exc:
        # fail-OPEN：這支擋的是寫檔，誤擋會連修自己都做不到（見檔頭）
        print(f"[agent_only_impl_guard] 略過（{exc}）", file=sys.stderr)
        return 0
    if verdict:
        decision, reason = verdict
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                }
            },
            sys.stdout,
            ensure_ascii=False,
        )
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

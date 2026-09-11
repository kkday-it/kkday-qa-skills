#!/usr/bin/env python3
"""
Stop hook：派工任務沒有自報結案就不准結束這一輪。

## 為什麼需要這支

ai-studio 的「AI 派工中心」把失敗的 case 丟給 agent 修。agent 講完一輪、claude CLI 吐出
`result` 事件的當下，runner 就認定整件任務結束 —— 接著 `shutil.rmtree(工作區)`。

線上發生過兩次「卡片寫著完成、事情其實還在跑」：

    24bafbd3：「（沒有程式碼變動）測試正在跑（每張卡片約 8.5 秒），等背景完成通知後再判讀結果。」
    5a333ec0：「待重跑完成通知，暫不重複輪詢。」

那個「完成通知」永遠不會來：工作區已經沒了，背景 shell 也隨著 claude 收到 EOF 一起被帶走。
測試不是還在跑，是被砍掉了 —— 而使用者看到「完成」就不會再看一眼。

## 契約

agent 每一輪的**最後一行**只能是這三者之一：

    TASK_DONE                 結果已經拿到手
    TASK_NO_FIX: <原因>       查清楚了，但不該改 code（產品端真的壞了、案例本身該失敗）
    TASK_BLOCKED: <原因>      做不完，說清楚卡在哪

沒有那一行 ⇒ 這一輪不算收尾。**fail-closed**：漏講的代價是被推回去再做一次，誤判成完成
的代價是使用者不知道事情沒做完 —— 兩邊不對稱。

## `TASK_NO_FIX` 是後來補的，補之前踩過一次

這支原本只收前兩種，而 ai-studio 的 system prompt（`agent_runner._FINISH_RULE`）講的是
三種。任務 58540035 因此走成這樣：agent 查出來是 stage 端間歇 5xx、重跑即過，寫了
`TASK_NO_FIX` —— 被這支擋下來，擋回去那句話還明確要求「只能是 TASK_DONE 或
TASK_BLOCKED」。它照做改寫成 `TASK_BLOCKED`，並在報告裡註明「結案類型實為
TASK_NO_FIX，但 gate 只收下列兩種」。runner 看到 `TASK_BLOCKED` 就判沒做完，卡片變成
紅色的失敗 —— 一個判斷完全正確、還附了五張證據圖的任務。

所以這裡的關鍵字清單跟 `agent_runner` 那份是同一份契約，少一個就會把正確答案逼成錯的。

## 為什麼要這支（規則已經寫在 system prompt 裡了）

那是「用講的」，會忘。runner 那邊的 `_read_verdict` 是「事後判定」，判得出來但一輪已經
燒完了。這支是**當下就不讓它結束回合**，是三層裡唯一在正確時機動作的一層。

## 條件式：只在派工 session 生效

靠環境變數 `AGENT_TASK_REQUIRE_VERDICT`（由 ai-studio 的 `agent_runner._agent_env` 設定）。
沒有這個旗標就直接放行 —— 無條件擋的話，每個人在自己筆電上隨便聊一句都會被要求寫
TASK_DONE，那種 hook 會被關掉，而關掉的守衛防護力是零。

## fail-OPEN（跟 fidelity gate 相反，這是刻意的）

fidelity gate 是唯一的把關者，所以它壞掉時必須擋。這支不是：runner 的 `_unfinished_reason`
會獨立再判一次，**hook 壞掉不可能造成假的「完成」**，最壞情況只是少擋一次。

反過來，這支誤擋的代價很實在：每一輪都燒使用者自己的訂閱額度。讀不到 transcript、格式
不認得的時候放行才是對的 —— 錯誤留在 stderr，進終端機給人看。

同一個理由也是 `MAX_BLOCKS` 存在的原因：推不動就放手，交給 runner 判定不算完成。
"""
import json
import os
import sys

DONE_MARK = "TASK_DONE"
NO_FIX_MARK = "TASK_NO_FIX"
BLOCKED_MARK = "TASK_BLOCKED"
VERDICT_MARKS = (DONE_MARK, NO_FIX_MARK, BLOCKED_MARK)

# 同一輪最多擋幾次。跟 runner 的 `AGENT_TASK_MAX_UNFINISHED_NUDGE` 同一個精神：
# 推不動就放手，不能為了要一行關鍵字把人家的訂閱額度燒光。
MAX_BLOCKS = int(os.environ.get("AGENT_TASK_VERDICT_MAX_BLOCKS", "2"))

BLOCK_REASON = (
    f"你還沒有回報結案。這一輪的最後一行必須是 `{DONE_MARK}`、"
    f"`{NO_FIX_MARK}: 原因` 或 `{BLOCKED_MARK}: 原因`，前後不要再加任何字。\n"
    "\n"
    "注意這不只是格式問題：你這一輪講完話，派工系統就會判定整件任務結束並"
    "**立刻刪掉整個工作區** —— 沒有人會替你等背景執行的指令或還沒收到的通知，"
    "它們會連同工作區一起消失。\n"
    "\n"
    "所以：測試、build 這種要等的指令一律在前景跑（timeout 直接設足夠長），"
    f"等到結果真的拿到手再寫 `{DONE_MARK}`；查清楚了但判定不該改 code（產品端真的"
    f"壞了、案例本身就該失敗）寫 `{NO_FIX_MARK}: 原因` —— 那是正常結案，不要為了"
    f"寫得出 `{DONE_MARK}` 去硬改不該改的東西；真的做不完就寫 "
    f"`{BLOCKED_MARK}: 原因`，那也是一種正當的收尾。"
)


def _counter_path(payload: dict) -> str:
    """擋下次數記在哪。

    優先用 `AGENT_TASK_ID`（一件任務就是一個計數），退回 session id —— 同機並發的
    派工不能共用一個計數器，不然 A 被擋兩次就會讓 B 直接放行。
    """
    key = os.environ.get("AGENT_TASK_ID") or payload.get("session_id") or "shared"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(key))
    return os.path.join("/tmp", f"agent_task_verdict_blocks.{safe}")


def read_last_assistant_text(transcript_path: str) -> str:
    """transcript 裡最後一則**主對話**助理訊息的文字。

    跳過 `isSidechain`：subagent 的訊息寫在同一份 transcript 裡，拿到它等於在檢查
    別人有沒有寫關鍵字 —— 主對話明明沒收尾卻被放行，或反過來被冤枉。

    也跳過沒有 text block 的（那一則只有 tool_use）：那不是「講完話」，是還在做事。
    """
    last = ""
    with open(transcript_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue  # 寫到一半的行，跳過就好
            if row.get("type") != "assistant" or row.get("isSidechain"):
                continue
            texts = [
                b.get("text", "")
                for b in (row.get("message") or {}).get("content") or []
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            joined = "\n".join(t for t in texts if t.strip())
            if joined.strip():
                last = joined
    return last


def has_verdict(text: str) -> bool:
    """最後一個非空行是不是結案關鍵字。

    **只認最後一行。** 「我等測試跑完就回 TASK_DONE 給你」不是回報 —— 認整段的話，
    這種句子會直接把 gate 關掉。

    容忍 markdown 包裝（`**TASK_DONE**`、`` `TASK_DONE` ``）：模型很習慣加粗，那不是
    沒照做。這裡跟 ai-studio 的 `agent_runner._read_verdict` 是同一套判定，改動要兩邊
    一起改，不然會出現「hook 放行、runner 判沒做完」的鬼打牆。
    """
    lines = [ln.strip() for ln in (text or "").strip().splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    if not lines:
        return False
    bare = lines[-1].strip("*`_ ").strip().upper()
    return any(bare.startswith(mark) for mark in VERDICT_MARKS)


def decide(payload: dict):
    """要擋就回 reason 字串，放行回 None。"""
    if not os.environ.get("AGENT_TASK_REQUIRE_VERDICT"):
        return None  # 不是派工 session，別打擾
    transcript = payload.get("transcript_path") or ""
    if not transcript or not os.path.exists(transcript):
        # 讀不到就不知道它到底寫了沒。fail-OPEN，交給 runner 判（見檔頭）
        raise FileNotFoundError(f"找不到 transcript：{transcript or '(空)'}")

    counter = _counter_path(payload)
    if has_verdict(read_last_assistant_text(transcript)):
        # 收尾了就把計數清掉：同一件任務可能被 `--resume` 接回來再跑好幾輪，
        # 留著的話第二輪一開始就只剩 0 次額度。
        try:
            os.unlink(counter)
        except OSError:
            pass
        return None

    try:
        with open(counter, encoding="utf-8") as f:
            blocked = int(f.read().strip() or 0)
    except (OSError, ValueError):
        blocked = 0
    if blocked >= MAX_BLOCKS:
        # 推不動就放手。runner 那邊會把這一輪判成「沒做完」（不是完成），
        # 使用者看到的是 failed + 原因，不是一張騙人的「完成」卡片。
        print(
            f"[task_verdict_gate] 已擋 {blocked} 次仍沒有結案關鍵字"
            f"（{'／'.join(VERDICT_MARKS)}），放手交給 runner 判定",
            file=sys.stderr,
        )
        return None
    with open(counter, "w", encoding="utf-8") as f:
        f.write(str(blocked + 1))
    return BLOCK_REASON


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        reason = decide(payload)
    except Exception as exc:  # noqa: BLE001 - fail-OPEN，理由留在 stderr
        print(f"[task_verdict_gate] 略過（{exc}）", file=sys.stderr)
        return 0
    if reason:
        json.dump({"decision": "block", "reason": reason}, sys.stdout, ensure_ascii=False)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

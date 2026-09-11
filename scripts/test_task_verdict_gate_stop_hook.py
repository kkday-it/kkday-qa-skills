#!/usr/bin/env python3
"""
task_verdict_gate_stop_hook 的單元測試。

守的是三件會讓這支 gate 變成裝飾品的事：

1. **一般 session 被打擾** → 沒有 AGENT_TASK_REQUIRE_VERDICT 就必須放行。會擾民的
   hook 會被關掉，關掉的守衛防護力是零。
2. **關鍵字認錯** → 認整段的話，「我等測試跑完就回 TASK_DONE 給你」這種句子會直接
   把 gate 關掉；只認最後一行才擋得住。
3. **無限擋** → 燒的是使用者自己的訂閱額度，推不動要放手。
4. **關鍵字少一個** → 三種結案關鍵字要跟 ai-studio `agent_runner` 那份同步。少收一種
   的話，寫對答案的 agent 會被擋回去、改寫成別的關鍵字，然後被判成失敗（58540035）。

跑法：python3 scripts/test_task_verdict_gate_stop_hook.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import task_verdict_gate_stop_hook as g  # noqa: E402

TMP = None

# 線上真的發生過的兩則結論（ai-studio 任務 24bafbd3 / 5a333ec0）。用字完全不一樣，
# 正是「不能靠字面比對語氣」的證據 —— 這兩句都必須被擋下來。
REAL_UNFINISHED = [
    "（沒有程式碼變動）測試正在跑（每張卡片約 8.5 秒），等背景完成通知後再判讀結果。",
    "待重跑完成通知，暫不重複輪詢。",
]


def setup():
    global TMP
    TMP = tempfile.mkdtemp(prefix="verdict-gate-")
    os.environ["AGENT_TASK_REQUIRE_VERDICT"] = "TASK_DONE"
    # 每個測試自己一個計數器，不然前一條擋過的次數會漏到下一條
    os.environ["AGENT_TASK_ID"] = ""


def transcript(*rows) -> str:
    """寫一份 transcript，回路徑。"""
    fd, path = tempfile.mkstemp(dir=TMP, suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def assistant(text: str, sidechain: bool = False) -> dict:
    row = {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }
    if sidechain:
        row["isSidechain"] = True
    return row


def payload(path: str, task_id: str) -> dict:
    # 一件任務一個計數器，測試之間才不會互相污染
    os.environ["AGENT_TASK_ID"] = task_id
    return {"transcript_path": path, "session_id": "s1", "stop_hook_active": False}


def test_不是派工_session_一律放行():
    saved = os.environ.pop("AGENT_TASK_REQUIRE_VERDICT")
    try:
        # 連 transcript 都不用給 —— 第一行就該回去了
        assert g.decide({"transcript_path": "/nonexistent"}) is None
    finally:
        os.environ["AGENT_TASK_REQUIRE_VERDICT"] = saved


def test_有自報結案就放行():
    path = transcript(assistant("測試三條都綠。\nTASK_DONE"))
    assert g.decide(payload(path, "t-ok")) is None


def test_做不完也算收尾():
    # TASK_BLOCKED 是正當的結束方式，不能跟「忘了寫」一起擋。
    path = transcript(assistant("這條 case 缺測試帳號。\nTASK_BLOCKED: 沒有帳號"))
    assert g.decide(payload(path, "t-blocked")) is None


def test_查清楚了不該改也算收尾():
    # 任務 58540035：agent 查出是 stage 端間歇 5xx、重跑即過，寫了 TASK_NO_FIX ——
    # 這支當時只收兩種，把它擋回去，它改寫成 TASK_BLOCKED，runner 於是判成失敗。
    # 一個判斷完全正確的任務被逼成紅色卡片。
    path = transcript(assistant("stage 間歇 5xx，重跑即過。\nTASK_NO_FIX: 產品端問題"))
    assert g.decide(payload(path, "t-nofix")) is None


def test_擋回去那句話要把三種都講出來():
    # 只列兩種的話，agent 會以為 TASK_NO_FIX 不被接受而改寫成 TASK_BLOCKED ——
    # 58540035 就是這樣發生的，它還在報告裡註明了「gate 只收下列兩種」。
    path = transcript(assistant(REAL_UNFINISHED[0]))
    reason = g.decide(payload(path, "t-三種"))

    for mark in g.VERDICT_MARKS:
        assert mark in reason, mark


def test_線上那兩則都會被擋下來():
    for i, summary in enumerate(REAL_UNFINISHED):
        path = transcript(assistant(summary))
        reason = g.decide(payload(path, f"t-real{i}"))
        assert reason, summary
        assert "TASK_DONE" in reason, "要講清楚少了哪一行"
        assert "刪掉整個工作區" in reason, "要講後果，不然它不知道為什麼不能丟背景"


def test_關鍵字在句子中間不算收尾():
    # 認整段的話這句就直接把 gate 關掉了。
    path = transcript(assistant("我等測試跑完就回 TASK_DONE 給你。\n先這樣。"))
    assert g.decide(payload(path, "t-mid")) is not None


def test_markdown_包起來也算():
    for line in ("**TASK_DONE**", "`TASK_DONE`", "**TASK_NO_FIX: 產品端壞了**"):
        path = transcript(assistant(f"修好了\n{line}"))
        assert g.decide(payload(path, "t-md")) is None, line


def test_subagent_的訊息不算數():
    # subagent 寫在同一份 transcript 裡。拿它當主對話的收尾等於放行一個沒收尾的回合。
    path = transcript(
        assistant("我來查一下。"),
        assistant("查完了\nTASK_DONE", sidechain=True),
    )
    assert g.decide(payload(path, "t-side")) is not None


def test_只有工具呼叫的那一則不算講完話():
    path = transcript(
        assistant("做完了\nTASK_DONE"),
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "Bash", "input": {}}]
            },
        },
    )
    assert g.decide(payload(path, "t-tool")) is None


def test_擋到上限就放手():
    # 燒的是使用者自己的訂閱額度。推不動由 runner 判定「沒做完」，不是無限迴圈。
    path = transcript(assistant(REAL_UNFINISHED[0]))
    p = payload(path, "t-cap")
    hits = [g.decide(p) for _ in range(g.MAX_BLOCKS + 2)]

    assert all(hits[: g.MAX_BLOCKS]), "上限之內要照擋"
    assert not any(hits[g.MAX_BLOCKS :]), "超過上限要放手"


def test_收尾之後計數歸零():
    # 同一件任務會被 --resume 接回來再跑好幾輪，留著計數的話第二輪一開始就沒額度了。
    unfinished = transcript(assistant(REAL_UNFINISHED[1]))
    done = transcript(assistant("好了\nTASK_DONE"))
    p_unfinished = payload(unfinished, "t-reset")
    assert g.decide(p_unfinished)

    assert g.decide(payload(done, "t-reset")) is None
    # 歸零了，所以下一輪還有滿額的機會被推回去
    hits = [g.decide(p_unfinished) for _ in range(g.MAX_BLOCKS)]
    assert all(hits), "收尾之後計數要重來"


def test_transcript_讀不到是_fail_open():
    # 這支不是唯一的把關者（runner 的 _unfinished_reason 會再判一次），所以壞掉時
    # 放行才對 —— 誤擋每一輪都在燒額度。decide 丟例外、main 吞掉。
    try:
        g.decide({"transcript_path": "/nonexistent/x.jsonl"})
        raise AssertionError("讀不到 transcript 應該丟例外，交給 main 吞成放行")
    except FileNotFoundError:
        pass


def test_寫壞的行不會讓整支掛掉():
    # transcript 是邊跑邊寫的，最後一行可能只寫到一半。
    fd, path = tempfile.mkstemp(dir=TMP, suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(assistant("好了\nTASK_DONE"), ensure_ascii=False) + "\n")
        f.write('{"type": "assis')

    assert g.decide(payload(path, "t-partial")) is None


if __name__ == "__main__":
    setup()
    try:
        fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
        for fn in fns:
            fn()
            print(f"PASS {fn.__name__}")
        print(f"\n{len(fns)} passed")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
        for name in os.listdir("/tmp"):
            if name.startswith("agent_task_verdict_blocks.t-"):
                os.unlink(os.path.join("/tmp", name))

#!/usr/bin/env bash
# Stop hook：條件式「registry 讀取」硬 gate（team-wide，由 sync_hooks 掛進 user-level settings）。
#
# 目的：擋掉「交付了 case 卻沒先讀共享 registry」。這個漏跟寫入側那個一樣是**靜默**的
# ——沒讀不會報錯、後端看起來也正常（只是永遠只進不出），於是實測兩個月 flow registry
# stale 率恆為 0.0，同一件事被不同人各寫一套差不多的 step。靠軟指令必漏，所以用死程式擋。
#
# 條件式（只在「這輪交付了 UI case」時 enforce）：
#   - 沒有 locator_claimed 檔、也沒有自己的 ledger → 這輪沒交付 → 放行（exit 0）。
#   - 有 → 跑 check_registry_read_gate.py 對讀取收據逐筆驗：
#       過 → 刪掉**自己的 ledger**，放行；claimed 與收據都不動（claimed 的所有權在 locator
#            寫入 gate；收據若在這裡清掉，接著被寫入 gate 擋下就會變成「claimed 還在、
#            收據沒了」的假性卡死）。
#       不過 → {"decision":"block",...} 逼補打一次讀取端點（帶 --case）。
#
# 🔴 為什麼要自己一份 ledger，而不是直接讀 locator_claimed（實測踩過）：
# 兩支 gate 在**同一個 Stop 事件**都會跑，本 gate 擋下之後 locator 寫入 gate 仍會繼續執行，
# 它一 pass 就把 claimed 刪掉 → 下一輪本 gate 看不到 claim → **靜默放行**。實測序列：
#   ① 本 gate block ② 寫入 gate pass 並刪 claimed ③ 本 gate rc=0 無輸出（收據仍然沒有）
# 也就是「擋一次就自己失效」，繞過方法只是再按一次結束。所以 claim 一出現就先抄進自己的
# ledger，之後只認 ledger、只有自己 pass 時才清 —— 別人刪 claimed 不影響本 gate 的記憶。
#
# 排序：仍必須排在 locator_gate_stop_hook.sh **之前**——要在 claimed 被刪掉前抄到它。
set -u

# session 隔離（同其他 gate）：claimed 檔名帶 CLAUDE_CODE_SESSION_ID；拿不到退 "shared"（fail-closed）。
_SID="${CLAUDE_CODE_SESSION_ID:-shared}"
CLAIMED="${LOCATOR_CLAIMED:-/tmp/locator_claimed.$_SID.jsonl}"
# 本 gate 自己的 claim ledger（見上面「為什麼要自己一份」）。同樣帶 SID 做 session 隔離。
LEDGER="${REGISTRY_READ_CLAIMED:-/tmp/registry_read_claimed.$_SID.jsonl}"
RECEIPT_DIR="${REGISTRY_READ_DIR:-/tmp/registry_reads.d}"
# gate 路徑一律從 $BASH_SOURCE 推（不用 CLAUDE_PROJECT_DIR）：user-level hook 的工作目錄是
# **使用者當下的專案**（多半是 kkday-QA-automation），不是本 repo。用 CLAUDE_PROJECT_DIR 會在
# 別的專案裡找不到 gate → fail-closed 擋死，而且沒有任何補救動作能解。
GATE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/check_registry_read_gate.py"

# SID 拿不到時會退到全機共用的 "shared" ledger，那份可能跨 session 累積 → 加齡防呆：
# 超過 7 天沒更新的 ledger 視為前世遺留，清掉（per-session 的那份本來就隨 session 消失）。
[ -f "$LEDGER" ] && [ -n "$(find "$LEDGER" -mtime +7 2>/dev/null)" ] && rm -f "$LEDGER"

# claim 一出現就先抄進 ledger（去重）——要在 locator 寫入 gate 把 claimed 刪掉之前抄到。
if [ -f "$CLAIMED" ]; then
  cat "$CLAIMED" "$LEDGER" 2>/dev/null | grep -v '^[[:space:]]*$' | sort -u > "$LEDGER.tmp" && mv "$LEDGER.tmp" "$LEDGER"
fi

# 這輪沒交付 case，且沒有欠驗的舊 claim → 放行。
[ -s "$LEDGER" ] || exit 0

# in-flight aware（同其他 gate，省 token）：session 背景 subagent 檔近 15 分鐘內仍被更新
# ＝task 真的在跑 → 放行 turn 結束、等 completion 通知，不忙等。hung/dead task 停止寫檔
# → 15 分內自動恢復 enforce。找不到活動 → 照常 enforce（fail-closed）。
if [ -n "${CLAUDE_CODE_SESSION_ID:-}" ]; then
  for _sd in "$HOME"/.claude/projects/*/"$CLAUDE_CODE_SESSION_ID"/subagents; do
    [ -d "$_sd" ] || continue
    if [ -n "$(find "$_sd" -name '*.jsonl' -mmin -15 2>/dev/null | head -1)" ]; then
      exit 0
    fi
  done
fi

# 守門 fail-CLOSED：gate 跑不成（腳本遺失等）也要擋，否則把關能被刪腳本繞過。
if [ ! -f "$GATE" ]; then
  printf '{"decision":"block","reason":"registry 讀取 gate 腳本找不到（%s），為避免把關被繞過，擋下結束。請確認 scripts/check_registry_read_gate.py 存在。"}\n' "$GATE"
  exit 0
fi

OUT="$(python3 "$GATE" --claimed "$LEDGER" --receipt-dir "$RECEIPT_DIR" 2>&1)"; RC=$?

# 過關：只清自己的 ledger（本輪已驗畢）。claimed 歸寫入 gate、收據按齡自清，都不動。
if [ "$RC" -eq 0 ]; then
  rm -f "$LEDGER"
  exit 0
fi

DETAIL="$(printf '%s' "$OUT" | tr '\n\r\t' '   ' | sed 's/"/'"'"'/g')"
printf '{"decision":"block","reason":"registry 讀取 gate 未過：交付 case 之前沒有讀過共享 registry，這是「大家各寫一套差不多的 step」的根因。%s"}\n' "$DETAIL"
exit 0

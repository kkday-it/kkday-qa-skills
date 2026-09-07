#!/usr/bin/env bash
# Stop hook：條件式 locator 硬 gate（team-wide，由 sync_hooks 掛進 user-level settings）。
#
# 目的：擋掉「交付了 UI case 卻沒真的把 locator 驗證/收成回寫」——這個漏是靜默的
# （後端讀空看起來像『還沒資料』），靠軟指令必漏。這支死程式在 turn 結束時把關。
#
# 條件式（只在「這輪交付了 UI case」時 enforce）：
#   - 沒有 locator_claimed 檔 → 這輪沒 UI 交付 → 放行（exit 0）。
#   - 有 → 跑 check_locator_gate.py 對 emit 證據逐筆驗：
#       過 → 刪 claimed（emit 檔由 gate --cleanup-on-pass 清），放行。
#       不過 → {"decision":"block",...} 逼補跑 valve / 收成，claimed 保留。
#
# 契約：automator 交付**UI** case×平台時 arm $LOCATOR_CLAIMED（純 API case 不 arm）；
# locator emit（valve 或收成）寫進 $LOCATOR_EMIT_DIR。
set -u

# session 隔離（同 fidelity gate）：claimed 檔名帶 CLAUDE_CODE_SESSION_ID，並發的多個 session 互不干擾。
# arm 端（qa-case-automator）用同一組 SID；拿不到退回 "shared"（fail-closed）。
_SID="${CLAUDE_CODE_SESSION_ID:-shared}"
CLAIMED="${LOCATOR_CLAIMED:-/tmp/locator_claimed.$_SID.jsonl}"
EMIT_DIR="${LOCATOR_EMIT_DIR:-/tmp/locator_results.d}"
# gate 路徑從 $BASH_SOURCE 推，不用 CLAUDE_PROJECT_DIR：user-level hook 的工作目錄是使用者
# 當下的專案（多半是 kkday-QA-automation），不是本 repo。用 CLAUDE_PROJECT_DIR 的話，隊友在
# 別的專案裡開 session 就會找不到 gate → fail-closed 擋死，而且沒有任何補救動作能解開。
GATE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/check_locator_gate.py"

# 這輪沒交付 UI case（沒有 claimed 檔）→ 放行。
[ -f "$CLAIMED" ] || exit 0

# in-flight aware（同 fidelity gate，省 token）：用「心跳」主動訊號——session 背景 subagent 檔
# （*.jsonl）近 15 分鐘內仍被更新＝task 真的在跑 → 放行 turn 結束、等 completion 通知，不忙等。
# hung/dead task 停止寫檔 → 15 分內自動恢復 enforce，不會被「output 永遠 size-0」繞過。
# 找不到活動 → 落到下面照常 enforce（fail-closed）。
if [ -n "${CLAUDE_CODE_SESSION_ID:-}" ]; then
  for _sd in "$HOME"/.claude/projects/*/"$CLAUDE_CODE_SESSION_ID"/subagents; do
    [ -d "$_sd" ] || continue
    if [ -n "$(find "$_sd" -name '*.jsonl' -mmin -15 2>/dev/null | head -1)" ]; then
      exit 0
    fi
  done
fi

# 🔴 舊 session 補洞：registry 讀取 gate 有自己的 Stop hook entry，但 hook 清單是
# **session 啟動時的快照** —— 在「新增那支 hook 之前就已經開著」的 session 裡，它永遠不會被
# 觸發，而且沒有任何症狀（跟通過長得一模一樣）。sync_hooks 改寫 settings.json 也救不了：
# 那份快照不會被重讀，得等對方重開 session。
# 但已經在快照裡的 hook（本檔就是，自 eab3919 起全隊都有）觸發時是**去磁碟執行 script**，
# 所以在這裡順手把讀取 gate 也叫一次，舊 session 立刻補上、不必等重開。
# 呼叫的是那支 hook 本身（不是直接跑 python），語意/ledger/清理權完全交給它，不複製一份邏輯。
# 冪等：新 session 會跑兩次——它唯讀、只清自己的 ledger，重跑無副作用。
# 它擋下時**要直接把它的 JSON 吐出來並結束**：一次 hook 呼叫只能有一份 decision，
# 再往下跑會印出第二個 JSON 而破壞協定。
READ_HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/registry_read_gate_stop_hook.sh"
if [ -f "$READ_HOOK" ]; then
  RG_OUT="$(bash "$READ_HOOK" 2>/dev/null)"
  case "$RG_OUT" in
    *'"decision":"block"'*)
      printf '%s\n' "$RG_OUT"
      exit 0
      ;;
  esac
else
  # fail-CLOSED（同下面 $GATE 的處理）：腳本不見了就擋，否則把關能被刪檔繞過。
  printf '{"decision":"block","reason":"registry 讀取 gate 腳本找不到（%s），為避免把關被繞過，擋下結束。請在 kkday-qa-skills clone 跑 git pull 確認 scripts/registry_read_gate_stop_hook.sh 存在。"}\n' "$READ_HOOK"
  exit 0
fi

# 守門 fail-CLOSED：gate 跑不成（腳本遺失等）也要擋，否則把關能被刪腳本繞過。
if [ ! -f "$GATE" ]; then
  printf '{"decision":"block","reason":"locator gate 腳本找不到（%s），為避免把關被繞過，擋下結束。請確認 scripts/check_locator_gate.py 存在。"}\n' "$GATE"
  exit 0
fi

OUT="$(python3 "$GATE" --claimed "$CLAIMED" --emit-dir "$EMIT_DIR" --cleanup-on-pass 2>&1)"; RC=$?

if [ "$RC" -eq 0 ]; then
  rm -f "$CLAIMED"      # 本輪已驗畢（emit 檔已由 gate --cleanup-on-pass 清）
  exit 0
fi

DETAIL="$(printf '%s' "$OUT" | tr '\n\r\t' '   ' | sed 's/"/'"'"'/g')"
printf '{"decision":"block","reason":"locator 回寫 gate 未過，不准把 UI case 當交付就結束。%s  → 對缺證據的 case 真的跑 locator_valve.py valve（web/mweb）或測試通過後收成 emit（app/from-scratch），不是讀 registry.json 冒充。"}\n' "$DETAIL"
exit 0

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
GATE="${CLAUDE_PROJECT_DIR:-.}/scripts/check_locator_gate.py"

# 這輪沒交付 UI case（沒有 claimed 檔）→ 放行。
[ -f "$CLAIMED" ] || exit 0

# in-flight aware（同 fidelity gate，省 token）：本 session 仍有背景 task 在跑（tasks/*.output size 0
# 且 <120min）→ 這批還在處理中，放行 turn 結束、等 completion 通知，不每回合 block 忙等。
# 找不到 tasks 目錄 → 落到下面照常 enforce（fail-closed）。
if [ -n "${CLAUDE_CODE_SESSION_ID:-}" ]; then
  for _td in /private/tmp/claude-*/*/"$CLAUDE_CODE_SESSION_ID"/tasks /tmp/claude-*/*/"$CLAUDE_CODE_SESSION_ID"/tasks; do
    [ -d "$_td" ] || continue
    if [ -n "$(find "$_td" -maxdepth 1 -name '*.output' -size 0 -mmin -120 2>/dev/null | head -1)" ]; then
      exit 0
    fi
  done
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

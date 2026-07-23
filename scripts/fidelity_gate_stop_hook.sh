#!/usr/bin/env bash
# Stop hook：條件式忠實度硬 gate（team-wide，放 checked-in .claude/settings.json 呼叫）。
#
# 目的：結構上擋掉「主對話漏跑 qa-case-fidelity-reviewer 就把 case 當『過』」——這個漏
# 在真實 session 反覆發生，靠記憶必漏。這支死程式在每次 turn 結束時把關。
#
# 條件式（只在「這輪跑了 TCMS 批次」時 enforce，不干擾一般對話）：
#   - 沒有 claimed 檔（/tmp/case_fidelity_claimed.<session>.jsonl）→ 這輪這 session 沒交付 TCMS → 放行（exit 0）。
#   - 有 claimed 檔 → 跑 check_fidelity_gate.py 對 fidelity 結果逐筆驗：
#       過（exit 0）  → 刪掉 claimed 檔（本輪已驗畢），放行。
#       不過（exit 1）→ 輸出 {"decision":"block","reason":...} 逼主對話補跑 review；
#                       claimed 檔保留，下次想結束仍會被擋，直到全部 pass。
#
# 主對話契約：把「這輪聲稱跑過的 case×平台」逐行寫進 $CASE_FIDELITY_CLAIMED
# （每行 JSON 至少含 case_id，platform 選填），fidelity 結果寫進 $CASE_FIDELITY_RESULTS。
set -u

# session 隔離：claimed 檔名帶 CLAUDE_CODE_SESSION_ID，讓同機並發的多個 session 互不干擾——
# 沒在跑 agent 的 session 沒有自己的 claimed 檔 → 這支直接放行；有跑的 session 只被自己的 claim 擋。
# arm 端（qa-case-automator）用同一組 SID 寫入（主 session 與其子流程共用同一個 SID，已實測）。
# 拿不到 SID 時退回 "shared"（fail-closed：寧可全機共用一個，也不要用空 SID 產生永不匹配的路徑而讓 gate 靜默不 enforce）。
_SID="${CLAUDE_CODE_SESSION_ID:-shared}"
CLAIMED="${CASE_FIDELITY_CLAIMED:-/tmp/case_fidelity_claimed.$_SID.jsonl}"
# 結果改為「目錄」：reviewer per case×平台 各寫一檔（每輪覆寫）。生命週期由本 gate 掌控——
# 送遙測的 send_case_fidelity **不 purge**（見 sync_hooks Stop 順序），只有本 gate 在 pass 時才刪，
# 否則 gate 擋下時被 sender 刪掉輸入 → 下輪變「找不到結果」的假性卡死。相容舊單一檔路徑。
FID="${CASE_FIDELITY_RESULTS:-/tmp/case_fidelity_results.d}"
GATE="${CLAUDE_PROJECT_DIR:-.}/scripts/check_fidelity_gate.py"
# #5 根治：過 gate 時把交付記錄寫進 ledger（交付＝過 gate 的副產品，不靠主對話記得跑
# send_case_delivery）。供 detect_test_rot / link_escaped_defect 回查「後來壞了 / 綠了卻出事」。
DELIVERY_LEDGER="${CASE_DELIVERY_LEDGER:-$HOME/.claude/harness/case_delivery.jsonl}"

# 這輪不是 TCMS 批次（沒有 claimed 檔）→ 放行。這不是繞過：本來就沒有批次要擋。
[ -f "$CLAIMED" ] || exit 0

# in-flight aware（省 token）：本 session 仍有背景 task「真的在跑」時，這批 claim 還在處理中，不是
# 「收尾時漏驗」——放行 turn 結束、安靜等 completion 通知，避免每回合 block 造成忙等迴圈狂燒 token。
# 用「心跳」主動訊號（非被動的 output 存在）：session 的背景 subagent 檔（agent/journal *.jsonl）
# 近 15 分鐘內是否仍被更新。task 真的在跑才會持續寫檔；**hung/dead task 停止寫檔 → 15 分內本 gate
# 自動恢復 enforce**，不會像「output 永遠 size-0」那樣被誤判 in-flight 而繞過守門。
# 找不到活動（無法判定 in-flight）→ 落到下面照常 enforce（fail-closed，安全優先）。
if [ -n "${CLAUDE_CODE_SESSION_ID:-}" ]; then
  for _sd in "$HOME"/.claude/projects/*/"$CLAUDE_CODE_SESSION_ID"/subagents; do
    [ -d "$_sd" ] || continue
    if [ -n "$(find "$_sd" -name '*.jsonl' -mmin -15 2>/dev/null | head -1)" ]; then
      exit 0
    fi
  done
fi

# 到這裡代表「這輪真的跑了 TCMS 批次」。守門一律 fail-CLOSED：任何讓 gate 跑不成的狀況
# （腳本遺失、路徑錯、python 掛）都必須擋下，否則把關能被「刪掉/改名腳本」輕易繞過。
if [ ! -f "$GATE" ]; then
  printf '{"decision":"block","reason":"忠實度 gate 腳本找不到（%s），為避免把關被繞過，擋下結束。請確認 scripts/check_fidelity_gate.py 存在且路徑正確後再繼續。"}\n' "$GATE"
  exit 0
fi

# --cleanup-on-pass：通過時由 gate 只刪「本次 claimed 的 case×平台」結果檔（目錄模式），
# 不 rm 整個目錄，避免誤刪同機其他 session 正在驗的結果。
OUT="$(python3 "$GATE" --claimed "$CLAIMED" --fidelity "$FID" --cleanup-on-pass --delivery-ledger "$DELIVERY_LEDGER" 2>&1)"; RC=$?

if [ "$RC" -eq 0 ]; then
  # 本輪已驗畢：清掉 claimed（結果檔已由 gate --cleanup-on-pass 逐筆刪）
  rm -f "$CLAIMED"
  exit 0
fi

# 擋下：gate 輸出壓成單行、把 " 換成 ' 以免破壞 JSON
DETAIL="$(printf '%s' "$OUT" | tr '\n\r\t' '   ' | sed 's/"/'"'"'/g')"
printf '{"decision":"block","reason":"忠實度 gate 未過，不准把 case 當過就結束。%s  → 對不合格 case 補跑 qa-case-fidelity-reviewer（needs-fix 丟回 qa-case-automator 重修再 review），全部 pass 後再結束。"}\n' "$DETAIL"
exit 0

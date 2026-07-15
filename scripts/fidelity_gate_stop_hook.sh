#!/usr/bin/env bash
# Stop hook：條件式忠實度硬 gate（team-wide，放 checked-in .claude/settings.json 呼叫）。
#
# 目的：結構上擋掉「主對話漏跑 qa-case-fidelity-reviewer 就把 case 當『過』」——這個漏
# 在真實 session 反覆發生，靠記憶必漏。這支死程式在每次 turn 結束時把關。
#
# 條件式（只在「這輪跑了 TCMS 批次」時 enforce，不干擾一般對話）：
#   - 沒有 claimed 檔（/tmp/case_fidelity_claimed.jsonl）→ 這輪不是 TCMS 批次 → 放行（exit 0）。
#   - 有 claimed 檔 → 跑 check_fidelity_gate.py 對 fidelity 結果逐筆驗：
#       過（exit 0）  → 刪掉 claimed 檔（本輪已驗畢），放行。
#       不過（exit 1）→ 輸出 {"decision":"block","reason":...} 逼主對話補跑 review；
#                       claimed 檔保留，下次想結束仍會被擋，直到全部 pass。
#
# 主對話契約：把「這輪聲稱跑過的 case×平台」逐行寫進 $CASE_FIDELITY_CLAIMED
# （每行 JSON 至少含 case_id，platform 選填），fidelity 結果寫進 $CASE_FIDELITY_RESULTS。
set -u

CLAIMED="${CASE_FIDELITY_CLAIMED:-/tmp/case_fidelity_claimed.jsonl}"
FID="${CASE_FIDELITY_RESULTS:-/tmp/case_fidelity_results.jsonl}"
GATE="${CLAUDE_PROJECT_DIR:-.}/scripts/check_fidelity_gate.py"

# 這輪不是 TCMS 批次（沒有 claimed 檔）→ 放行。這不是繞過：本來就沒有批次要擋。
[ -f "$CLAIMED" ] || exit 0

# 到這裡代表「這輪真的跑了 TCMS 批次」。守門一律 fail-CLOSED：任何讓 gate 跑不成的狀況
# （腳本遺失、路徑錯、python 掛）都必須擋下，否則把關能被「刪掉/改名腳本」輕易繞過。
if [ ! -f "$GATE" ]; then
  printf '{"decision":"block","reason":"忠實度 gate 腳本找不到（%s），為避免把關被繞過，擋下結束。請確認 scripts/check_fidelity_gate.py 存在且路徑正確後再繼續。"}\n' "$GATE"
  exit 0
fi

OUT="$(python3 "$GATE" --claimed "$CLAIMED" --fidelity "$FID" 2>&1)"; RC=$?

if [ "$RC" -eq 0 ]; then
  rm -f "$CLAIMED"
  exit 0
fi

# 擋下：gate 輸出壓成單行、把 " 換成 ' 以免破壞 JSON
DETAIL="$(printf '%s' "$OUT" | tr '\n\r\t' '   ' | sed 's/"/'"'"'/g')"
printf '{"decision":"block","reason":"忠實度 gate 未過，不准把 case 當過就結束。%s  → 對不合格 case 補跑 qa-case-fidelity-reviewer（needs-fix 丟回 qa-case-automator 重修再 review），全部 pass 後再結束。"}\n' "$DETAIL"
exit 0

#!/usr/bin/env bash
# 自動把本 repo 同步到最新 master，讓 symlink 進 ~/.claude 的 skills/agents/prompt「用之前」
# 就是最新——不必靠使用者記得手動 git pull。
#
# 掛兩個 hook（見 .claude/settings.json）：
#   SessionStart      —— 開 session 時跑一次（無 throttle，開場一定同步）。
#   UserPromptSubmit  —— 每次送訊息時跑，但帶 throttle：長 session 不重開也會「定期」同步，
#                        又不會每則訊息都 pull 拖慢。
#
# 用法：session_autopull.sh [throttle_seconds]
#   無參數        → 一定嘗試 pull（SessionStart 用）。
#   throttle 秒數 → 距上次 pull 未達該秒數就跳過（UserPromptSubmit 用，預設建議 600）。
#
# 安全 guard（auto git pull 有雷，做足才動）：
#   - 只在 branch==master 才 pull（feature branch 開發 skill 時不干擾）。
#   - --ff-only：只快轉、不製造 merge commit / 衝突；拉不動就放棄。
#   - fail-safe：非 git 目錄 / 無網路 / 任何錯 → 靜默 exit 0，絕不擋 session / 訊息。
set -u

THROTTLE="${1:-0}"
# 目標一律是「本 script 所屬的 clone」，不是 CLAUDE_PROJECT_DIR。用當下專案會踩兩個雷：
# (1) session 開在別的 repo（多半是 kkday-QA-automation）時，pull 的是那個 repo，qa-skills
#     根本沒被同步 → 夥伴永遠不知道有更新；(2) 順手 auto-pull 別人的產品 repo 不是本意。
D="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -d "$D/.git" ] || exit 0

b=$(git -C "$D" rev-parse --abbrev-ref HEAD 2>/dev/null)
[ "$b" = "master" ] || exit 0

STAMP="$D/.git/.claude_autopull_stamp"
# throttle：距上次 pull 未達 THROTTLE 秒就跳過（用 stamp 檔 mtime 判斷；非 git 追蹤檔）
if [ "$THROTTLE" -gt 0 ] 2>/dev/null && [ -f "$STAMP" ]; then
  now=$(date +%s 2>/dev/null || echo 0)
  last=$(stat -f %m "$STAMP" 2>/dev/null || stat -c %Y "$STAMP" 2>/dev/null || echo 0)
  [ $((now - last)) -lt "$THROTTLE" ] && exit 0
fi

before=$(git -C "$D" rev-parse --short HEAD 2>/dev/null)
if git -C "$D" pull --ff-only --quiet 2>/dev/null; then
  : > "$STAMP" 2>/dev/null || true          # 更新 throttle 時間戳
  after=$(git -C "$D" rev-parse --short HEAD 2>/dev/null)
  if [ "$before" != "$after" ]; then
    # 有更新才重跑 hook 同步：hook 是絕對路徑快照，光 pull 不會更新指令字串（改過的 flag/路徑）。
    # sync_hooks.py 冪等 + 自動 migrate + fail-safe，只在版本真的變動時跑，不干擾每則訊息。
    [ -f "$D/scripts/sync_hooks.py" ] && python3 "$D/scripts/sync_hooks.py" >/dev/null 2>&1 || true
    printf '{"systemMessage":"qa-skills repo 已自動同步 %s → %s（skills/agents/hook 為最新）"}\n' "$before" "$after"
  fi
fi

# 補 symlink：放在 pull 之外、且**不分有無更新**都跑。理由：
#   - 只在「有更新」時補，救不到「HEAD 早就最新、但當初安裝時上游還沒有那個 skill/agent」的人
#     ——symlink 只讓已連上的檔案跟著更新，新增檔案永遠不會自己出現（qa-case-planner 就是這樣漏的）。
#   - 放在 pull 之外，離線 / pull 失敗時也照樣自我修復。
# link_assets.sh 冪等、不連網、只做幾個 ln，成本可忽略；輸出全丟掉以免污染 hook 的 stdout 協定。
bash "$D/scripts/link_assets.sh" --quiet >/dev/null 2>&1 || true

# 🔴 舊 session 補洞：flow 寫回有自己的 Stop hook entry（sync_hooks 裡的 send_flow_registry），
# 但 hook 清單是 session 啟動時的快照——在那支 hook 加進來之前就開著的 session 永遠不會觸發它，
# automator 收成的可重用 step 就爛在 /tmp 裡沒人送。本檔是最老的錨點（564e041 起全隊都有），
# 且已裝好的 hook 觸發時是去磁碟執行 script，所以在這裡補送一次，舊 session 立刻生效。
# 只有真的有檔才起 python（多數 prompt 是空目錄，成本≈一次 ls）；丟背景不擋使用者送訊息。
# 冪等：新 session 的 Stop hook 也會送，但後端是 upsert、sender 逐檔 purge，重複送無害。
if [ -n "$(ls -A /tmp/flow_results.d 2>/dev/null)" ]; then
  nohup python3 "$D/scripts/send_flow_registry.py" --indir /tmp/flow_results.d --purge \
    >/dev/null 2>&1 &
fi
exit 0

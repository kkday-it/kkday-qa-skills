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
D="${CLAUDE_PROJECT_DIR:-$PWD}"
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
    printf '{"systemMessage":"qa-skills repo 已自動同步 %s → %s（skills/agents 為最新）"}\n' "$before" "$after"
  fi
fi
exit 0

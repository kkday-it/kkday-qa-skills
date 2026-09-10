#!/usr/bin/env bash
# symlink 本 repo 的 skills（tools + workflows）與 agents 進 ~/.claude ——【單一來源】。
#
# 為什麼獨立成一支（跟 sync_hooks.py 同樣理由）：install.sh 一次性安裝、session_autopull.sh
# 每個 session 也會跑。上游**新增**skill/agent 時，光 git pull 不會產生新的 symlink（symlink 只
# 讓「已連上的」檔案跟著更新），所以 autopull 必須跟 install 共用同一份 link 邏輯，否則
# 「早裝的人永遠拿不到後來新增的 agent」（qa-case-planner 就是這樣漏掉的）。
#
# 用法：link_assets.sh [--quiet]
#   --quiet → 不印任何 log（hook 用；hook 的 stdout 有協定，不可污染）。
# 冪等：重複跑安全（symlink 重指、非 symlink 的既有檔案不覆蓋）。
#
# ## 「不覆蓋」為什麼要留紀錄
#
# 撞到非 symlink 的既有檔案時本檔選擇不覆蓋（那可能是人家自己寫的東西），這是對的。
# 錯的是**只用 say 講**：每個 session 跑的是 `--quiet`，那行訊息永遠不會出現。結果是
# 本機那份**永久遮蔽** repo 版，而且沒有任何地方看得到——eden 的 `tcms-create-case`
# 就這樣跟大家跑不同版本跑了兩個月，是靠人工 `ls -la ~/.claude/skills` 才發現的。
#
# 所以衝突另外寫進 CONFLICTS 檔（quiet 也寫），由 `telemetry_identity.py` 讀進
# `skills_version` 的 `!N` 後綴送上 dashboard——把「我這台跟別人不一樣」變成看得見的數字，
# 而不是要每個人自己想到去 ls。**每次執行整檔重寫**：它描述的是當下狀態，不是歷史；
# 人把遮蔽的目錄移掉之後，下一個 session 就該自動恢復乾淨。
set -u

QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1
say() { [ "$QUIET" -eq 1 ] || echo "$@"; }

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
mkdir -p "$CLAUDE_DIR/skills" "$CLAUDE_DIR/agents"

CONFLICTS="$CLAUDE_DIR/harness/link_conflicts.txt"
conflicts=""

link_one() {  # $1=src $2=dstdir
  local src="$1" dst="$2/$(basename "$1")"
  if [ -L "$dst" ]; then
    ln -sfn "$src" "$dst"; say "  ~ relink $(basename "$1")"
  elif [ -e "$dst" ]; then
    say "  ! skip $(basename "$1")（已存在且非 symlink，不覆蓋——如要接管請先手動移除）"
    conflicts="$conflicts$dst"$'\n'
  else
    ln -s "$src" "$dst"; say "  + link $(basename "$1")"
  fi
}

say "[link] skills:"
# tools 先於 workflows；同名 skill（如 qa-test-runner 兩處都有）以 tools 版為準（first-wins），
# 用字串 seen 清單去重（相容 macOS 內建 bash 3.2，不用 assoc array）。
seen=" "
for s in "$REPO"/skills/tools/*/ "$REPO"/skills/workflows/*/; do
  [ -f "$s/SKILL.md" ] || continue
  name="$(basename "${s%/}")"
  case "$seen" in *" $name "*) say "  = skip ${name}（同名已裝，tools 版優先）"; continue;; esac
  seen="$seen$name "
  link_one "${s%/}" "$CLAUDE_DIR/skills"
done
say "[link] agents:"
for a in "$REPO"/agents/*.md; do
  [ -f "$a" ] && link_one "$a" "$CLAUDE_DIR/agents"
done

# 寫入不能讓本檔失敗（它是每個 session 都跑的 hook 的一部分）。寫不進去就算了，
# 頂多回到「看不見」的現狀，不該因此擋住任何人的 symlink 或 prompt。
if mkdir -p "$(dirname "$CONFLICTS")" 2>/dev/null; then
  printf '%s' "$conflicts" > "$CONFLICTS" 2>/dev/null || :
fi
[ -n "$conflicts" ] && say "[link] ⚠ 有 $(printf '%s' "$conflicts" | grep -c '') 個被本機檔案遮蔽，清單：$CONFLICTS"
exit 0

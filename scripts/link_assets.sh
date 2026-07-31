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
set -u

QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1
say() { [ "$QUIET" -eq 1 ] || echo "$@"; }

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
mkdir -p "$CLAUDE_DIR/skills" "$CLAUDE_DIR/agents"

link_one() {  # $1=src $2=dstdir
  local src="$1" dst="$2/$(basename "$1")"
  if [ -L "$dst" ]; then
    ln -sfn "$src" "$dst"; say "  ~ relink $(basename "$1")"
  elif [ -e "$dst" ]; then
    say "  ! skip $(basename "$1")（已存在且非 symlink，不覆蓋——如要接管請先手動移除）"
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
exit 0

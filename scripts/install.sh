#!/usr/bin/env bash
# 一鍵安裝 kkday-qa-skills 到 Claude Code（user-level，任何專案都生效）。
#
# 為什麼是 user-level：hook（自動 git pull、忠實度硬 gate、遙測）若放 repo 的 checked-in
# .claude/settings.json 是「專案級」——只有「在本 repo 裡開 Claude Code」才觸發。但實際跑
# QA 自動化多半在框架 repo（kkday-QA-automation）或別的資料夾開 session，那裡不是本 repo →
# hook 不會跑。所以 hook 一律寫進 ~/.claude/settings.json（user-level），任何專案都生效。
#
# 做三件事：
#   1. symlink skills（tools + workflows）與 agents 進 ~/.claude（symlink 才會跟 git pull 更新）。
#   2. 把 hook 用「本 clone 的絕對路徑」merge 進 ~/.claude/settings.json（先備份、merge 不覆蓋）：
#        SessionStart      → session_autopull.sh（開場同步）
#        UserPromptSubmit  → session_autopull.sh 1800（每 30 分同步，長 session 也跟得上）
#        Stop              → 忠實度硬 gate + 3 支遙測 sender
#   3. 冪等：重複跑安全（已存在的 symlink / hook 不重複加）。
#
# 用法：bash scripts/install.sh
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
mkdir -p "$CLAUDE_DIR/skills" "$CLAUDE_DIR/agents"

echo "[install] repo = $REPO"
echo "[install] target = $CLAUDE_DIR"

# ── 1. symlink skills + agents ───────────────────────────────────────
link_one() {  # $1=src $2=dstdir
  local src="$1" dst="$2/$(basename "$1")"
  if [ -L "$dst" ]; then
    ln -sfn "$src" "$dst"; echo "  ~ relink $(basename "$1")"
  elif [ -e "$dst" ]; then
    echo "  ! skip $(basename "$1")（已存在且非 symlink，不覆蓋——如要接管請先手動移除）"
  else
    ln -s "$src" "$dst"; echo "  + link $(basename "$1")"
  fi
}
echo "[install] skills:"
# tools 先於 workflows；同名 skill（如 qa-test-runner 兩處都有）以 tools 版為準（first-wins），
# 用字串 seen 清單去重（相容 macOS 內建 bash 3.2，不用 assoc array）。
seen=" "
for s in "$REPO"/skills/tools/*/ "$REPO"/skills/workflows/*/; do
  [ -f "$s/SKILL.md" ] || continue
  name="$(basename "${s%/}")"
  case "$seen" in *" $name "*) echo "  = skip ${name}（同名已裝，tools 版優先）"; continue;; esac
  seen="$seen$name "
  link_one "${s%/}" "$CLAUDE_DIR/skills"
done
echo "[install] agents:"
for a in "$REPO"/agents/*.md; do
  [ -f "$a" ] && link_one "$a" "$CLAUDE_DIR/agents"
done

# ── 2. merge hook 進 ~/.claude/settings.json（絕對路徑、備份、不覆蓋）──
SETTINGS="$CLAUDE_DIR/settings.json"
[ -f "$SETTINGS" ] && cp "$SETTINGS" "$SETTINGS.bak.$(date +%Y%m%d%H%M%S 2>/dev/null || echo bak)" && echo "[install] 已備份 $SETTINGS"

# hook 定義集中在 sync_hooks.py（install 與 session_autopull 共用，避免漂移）；
# 它會冪等 merge + 自動 migrate 屬本 repo 的舊 hook（改過的 flag/路徑會被換新）。
REPO="$REPO" SETTINGS="$SETTINGS" python3 "$REPO/scripts/sync_hooks.py"
echo "[install] hook 已 merge 進 ${SETTINGS}（透過 sync_hooks.py）"

echo "[install] 完成。新開一個 Claude Code session 即生效（hook 在任何專案都會跑）。"

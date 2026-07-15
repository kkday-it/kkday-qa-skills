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
  case "$seen" in *" $name "*) echo "  = skip $name（同名已裝，tools 版優先）"; continue;; esac
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

REPO="$REPO" SETTINGS="$SETTINGS" python3 - <<'PY'
import json, os
repo = os.environ["REPO"]; path = os.environ["SETTINGS"]
try:
    with open(path) as f: cfg = json.load(f)
except Exception:
    cfg = {}
hooks = cfg.setdefault("hooks", {})

def cmd(c): return {"type": "command", "command": c}
want = {
    "SessionStart":     [cmd(f'bash "{repo}/scripts/session_autopull.sh"')],
    "UserPromptSubmit": [cmd(f'bash "{repo}/scripts/session_autopull.sh" 1800')],
    "Stop": [
        cmd(f'bash "{repo}/scripts/fidelity_gate_stop_hook.sh"'),
        cmd(f'python3 "{repo}/scripts/send_case_fidelity.py" --infile /tmp/case_fidelity_results.jsonl --purge'),
        cmd(f'python3 "{repo}/scripts/send_locator_registry.py" --infile /tmp/locator_results.jsonl --purge'),
        cmd(f'python3 "{repo}/scripts/send_tool_usage.py" --infile /tmp/tool_usage.jsonl --purge'),
    ],
}
for event, cmds in want.items():
    arr = hooks.setdefault(event, [])
    # 收集本 event 現有的所有 command 字串（去重用）
    existing = {h.get("command") for grp in arr for h in grp.get("hooks", [])}
    # 只加還沒有的；本 repo 的 command 都含 repo 路徑，重跑不會重複
    new_cmds = [c for c in cmds if c["command"] not in existing]
    if new_cmds:
        arr.append({"hooks": new_cmds})

with open(path, "w") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2); f.write("\n")
print(f"[install] hook 已 merge 進 {path}（events: {', '.join(want)}）")
PY

echo "[install] 完成。新開一個 Claude Code session 即生效（hook 在任何專案都會跑）。"

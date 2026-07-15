#!/usr/bin/env bash
# 為 kkday-QA-automation 的 git worktree 準備執行期 .env。
#
# qatest 一 import 就會初始化 ZephyrAPI → util.get_secret()，需要兩個 env 才開得了機：
#   SERVICE_URL      內部 secret 服務 URL（非機密）
#   AUTOMATION_TOKEN master token（真機密，用來去 secret 服務撈 zephyr_token 等）
# （JIRA_TOKEN / OPENAI_API_KEY 只有 Jira/AI 功能才用到，純 web/mweb/app UI case 不需要。）
#
# 新 worktree 預設沒有 .env → automator 會卡在 import。過去靠 automator 自己 cp 一份含機密的
# .env 進 worktree（機密被複製、有被 git add 的風險）。這支把「provision .env」變成建 worktree 的
# 固定步驟，且**不複製機密**：
#   1. 找得到參考 .env（同 repo 的主 checkout / 其他 worktree）→ symlink 過去（最省事、不複製機密）。
#   2. 都找不到 → 生一份**只含非機密**的骨架 .env（SERVICE_URL/HEADLESS/AGENT 填好），
#      AUTOMATION_TOKEN 留空 + 註解，並印訊息請人補**那一個** token（機密不可捏造）。
#
# 用法：provision_worktree_env.sh <worktree_path>
set -u

WT="${1:-}"
[ -n "$WT" ] && [ -d "$WT" ] || { echo "[provision-env] 用法：$0 <worktree_path>"; exit 2; }
DST="$WT/.env"

if [ -e "$DST" ] || [ -L "$DST" ]; then
  echo "[provision-env] ${DST} 已存在，不動。"
  exit 0
fi

# 1) 找參考 .env：同 repo 的其他 worktree（主 checkout 通常是第一個）
REF=""
while read -r line; do
  d="${line%% *}"                      # worktree 路徑
  [ "$d" = "$WT" ] && continue
  if [ -f "$d/.env" ]; then REF="$d/.env"; break; fi
done < <(git -C "$WT" worktree list 2>/dev/null)

if [ -n "$REF" ]; then
  ln -s "$REF" "$DST"
  echo "[provision-env] 已 symlink ${DST} -> ${REF} (不複製機密)"
  exit 0
fi

# 2) 沒有參考 .env → 生非機密骨架，AUTOMATION_TOKEN 留空請人補
{
  echo "# 由 provision_worktree_env.sh 自動生成的最小骨架（找不到參考 .env）。"
  echo "# 非機密欄位已填；AUTOMATION_TOKEN 是 master 機密，無法自動產生，請貼上你的 token。"
  echo "SERVICE_URL=http://autotest-service.sit.kkday.com"
  echo "AGENT=$(hostname 2>/dev/null || echo mac.local)"
  echo "HEADLESS=1"
  echo "AUTOMATION_TOKEN="
} > "$DST"
echo "[provision-env] 找不到參考 .env，已生骨架 ${DST}。"
echo "[provision-env] ⚠️ 請補上 AUTOMATION_TOKEN（master token）才能跑 qatest；其餘（JIRA_TOKEN/OPENAI_API_KEY）web/app UI case 不需要。"
exit 0

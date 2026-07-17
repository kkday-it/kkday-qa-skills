// Registry stale sweep 每週 dry-run 報告（#7：registry 只長不清 → 定期浮出 stale）
//
// ★ 用法：貼進 Jenkins job → Pipeline → 「Pipeline script from SCM」，指向本 repo。
//   （與 healthcheck 的自包含 Jenkinsfile 不同——sweep 要讀 repo 內的 registry.json，故走 SCM checkout。）
//
// 設計（比照 CLAUDE.md：destructive 需人審）：
//   - 排程只跑 **dry-run**，把「哪些 entry 該清、為什麼」發到 Slack；**絕不自動刪**。
//   - 人看過報告後，自己在本機跑 `sweep_registry.py --apply`（會先寫 .bak）才真的清。
//   - 沒有 stale → 不吵（不發訊息），避免每週雜訊；有 stale 才發。
//
// ★ Slack token 同 healthcheck：只填在 Jenkins job 設定，別 commit。
// 前置：agent 有 python3 + git；FRAMEWORK_REPO 若不存在則跳過 flow 的 function 存在性檢查（只用 age）。

pipeline {
    agent any

    triggers { cron('H 10 * * 1') }   // 每週一約 10:00

    options {
        timeout(time: 10, unit: 'MINUTES')
        disableConcurrentBuilds()
    }

    environment {
        SLACK_CHANNEL   = 'C0BGPQ2FX5Z'
        SLACK_BOT_TOKEN = 'xoxb-REPLACE-ME'   // 只留在 Jenkins job 設定，別 commit
        MAX_AGE_DAYS    = '90'
        FRAMEWORK_REPO  = ''                   // 有框架 repo 路徑才填；填了才做 flow function 存在性檢查
    }

    stages {
        stage('Sweep (dry-run)') {
            steps {
                checkout scm
                sh '''set -e
REPO_ARG=""
if [ -n "$FRAMEWORK_REPO" ] && [ -d "$FRAMEWORK_REPO" ]; then REPO_ARG="--repo-path $FRAMEWORK_REPO"; fi
python3 scripts/sweep_registry.py --registry both --max-age-days "$MAX_AGE_DAYS" $REPO_ARG --json > sweep_report.json
cat sweep_report.json

python3 - <<'PY'
import json, os, sys, urllib.request
rep = json.load(open("sweep_report.json", encoding="utf-8"))
reports = rep.get("reports", [])
stale = [(r["registry"], x) for r in reports for x in r.get("removed", [])]
if not stale:
    print("[sweep] 無 stale entry，不發 Slack")
    sys.exit(0)

lines = [":broom: *Registry sweep — 發現 %d 筆 stale entry（dry-run，未刪）*" % len(stale)]
for reg, x in stale[:40]:
    lines.append("• `%s` [%s]：%s" % (x.get("id"), reg, x.get("reason")))
if len(stale) > 40:
    lines.append("…還有 %d 筆" % (len(stale) - 40))
lines.append("\\n確認後在本機跑 `python3 scripts/sweep_registry.py --apply`（會先寫 .bak）才真的清。")
text = "\\n".join(lines)

TOKEN = os.environ["SLACK_BOT_TOKEN"]; CHANNEL = os.environ["SLACK_CHANNEL"]
body = json.dumps({"channel": CHANNEL, "text": text}).encode()
req = urllib.request.Request("https://slack.com/api/chat.postMessage", data=body, method="POST",
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json; charset=utf-8"})
try:
    with urllib.request.urlopen(req, timeout=8) as r:
        res = json.loads(r.read().decode())
    if not res.get("ok"):
        print("[error] Slack ok=false: %s" % res.get("error"), file=sys.stderr)
except Exception as e:
    print("[error] Slack 送失敗: %s" % e, file=sys.stderr)
PY
'''
            }
        }
    }
}

// Test-rot 偵測排程（#5-A：交付綠的 case「後來壞了 / 不再跑了」要有人管，不靠人記得看）
//
// ★ 用法：貼進 Jenkins job → Pipeline script from SCM，指向本 repo。
//   建議接在**夜間回歸之後**跑（要有一份剛跑完、涵蓋 ledger 裡 case 的 qatest.log 才有意義；
//   對著舊 log 跑只會把所有 case 判成 not-in-run）。
//
// ⚠️ 已知限制（誠實）：交付 ledger 是 local-first（在跑批次那台的 ~/.claude/harness/case_delivery.jsonl）。
//   中央 Jenkins agent 看不到別台的 ledger——所以本 job 要跑在「同時有 ledger + 跑回歸」的那台 agent
//   （用 label 綁定），或等後端 case-delivery route 部署把交付記錄集中後改讀後端。
//   在那之前，這支就是把「定期檢查 rot」變成排程、不靠人記得的那一步。
//
// 設計：有 rotted（交付時綠、現在 fail）→ 🔴 Slack 告警 + build 失敗；
//       只有 not-in-run（沒排進這輪跑）→ 🟠 提醒；全 stable → 不吵。

pipeline {
    agent { label 'has-qa-ledger' }   // 綁定「有交付 ledger 且跑回歸」的那台；沒有就改 agent any 並自備路徑

    triggers { cron('H 6 * * *') }    // 每天約 06:00（夜間回歸之後）

    options {
        timeout(time: 15, unit: 'MINUTES')
        disableConcurrentBuilds()
    }

    environment {
        SLACK_CHANNEL   = 'C0BGPQ2FX5Z'
        SLACK_BOT_TOKEN = 'xoxb-REPLACE-ME'   // 只填在 Jenkins job 設定，別 commit
        LEDGER          = "${HOME}/.claude/harness/case_delivery.jsonl"
        QATEST_LOG      = "${HOME}/Documents/QATest_Output/qatest.log"
    }

    stages {
        stage('Detect test rot') {
            steps {
                checkout scm
                sh '''set -e
python3 scripts/detect_test_rot.py --ledger "$LEDGER" --qatest-log "$QATEST_LOG" --json > rot_report.json || true
cat rot_report.json

python3 - <<'PY'
import json, os, sys, urllib.request
rep = json.load(open("rot_report.json", encoding="utf-8"))
rotted = rep.get("rotted", [])
not_in_run = rep.get("not_in_run", [])
if not rotted and not not_in_run:
    print("[test-rot] 全 stable，不發 Slack"); sys.exit(0)

lines = []
if rotted:
    lines.append(":red_circle: *Test rot — %d 個交付綠的 case×平台現在 FAIL（green→red）*" % len(rotted))
    for x in rotted[:30]:
        lines.append("• `%s` [%s] → %s" % (x.get("caseid"), x.get("platform"), x.get("current")))
if not_in_run:
    lines.append(":large_orange_circle: *%d 個交付過但這輪回歸找不到（可能悄悄沒在保護）*" % len(not_in_run))
    for x in not_in_run[:20]:
        lines.append("• `%s` [%s]" % (x.get("caseid"), x.get("platform")))
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

# rotted（真的壞了）→ 讓 build 失敗，逼人處理；只有 not-in-run 不算失敗
sys.exit(1 if rotted else 0)
PY
'''
            }
        }
    }
}

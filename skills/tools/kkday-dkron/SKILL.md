---
name: kkday-dkron
description: 安全操作 kkday Dkron 排程的「會改 server 的動作」— 手動觸發 / 啟用 / 停用 / 修改某個 cron job 的 command 或 schedule。**只在使用者明確指示要對某個具體 job 執行動作時觸發**,例如:「幫我跑 api-vtrans-xxx」「stage 部署完要觸發 import-price」「把 svc-vtrans-yyy enable 起來跑一次」「改 zzz 的 keys 跑一下」,或貼出 dkron.\*.kkday.com 的 job URL 並表達想執行/啟用/停用該 job。**不要在純資訊查詢時觸發**:列 job、搜尋 job、看 spec、看 executions、解釋 Dkron 是什麼、比較 Dkron vs Airflow 等都直接用 curl 回答即可,別載這個 skill。
---

# kkday Dkron 操作指引

這個 skill 處理 **會修改 Dkron server 狀態的動作** — 手動觸發 job、enable/disable、改 command 或 schedule。重點是知道 *什麼動作會踩雷*、*在 prod 觸發前需要哪些確認*、以及 *為什麼 kkday 的 UI 用 GET→PUT 而不是 toggle*。

> 純讀取(列 job、查 spec、看 executions)直接用 `curl + jq` 就好,不需要這個 skill。如果在 skill 載入後發現使用者只想看資訊,直接給 curl 答案、不用走 skill 裡的「停下來等同意」流程。

---

## 環境 & Base URL

| 環境 | Base URL | 認證 |
|---|---|---|
| stage | `https://dkron.stage.kkday.com` | 目前**無認證**,GET/POST/PUT 直通 |
| prod | `https://dkron.kkday.com` (UI: `https://dkron.kkday.com/ui/#/`) | 預期需要(SSO 或 X-Dkron-Token);若 401/403 請使用者提供 |

寫 script 前先設變數,後面所有指令都用變數:

```bash
BASE="https://dkron.stage.kkday.com/v1/jobs"   # 或 prod
NAME="api-vtrans-manual-clear-redis-keys"      # 你要操作的 job
```

---

## API 端點對照

| 動作 | Method | Path | Body |
|---|---|---|---|
| 列所有 job | `GET` | `/v1/jobs` | — |
| 看單一 job spec | `GET` | `/v1/jobs/{name}` | — |
| 看 job 歷史執行 | `GET` | `/v1/jobs/{name}/executions` | — |
| 手動觸發 job | `POST` | `/v1/jobs/{name}` 或 `/v1/jobs/{name}/run` | — |
| 更新 job(enable/disable/改 command/改 schedule) | `PUT` | `/v1/jobs/{name}` | **完整** job JSON |
| 翻轉 enable/disable(救火 shortcut) | `POST` | `/v1/jobs/{name}/toggle` | — |

關鍵事實(來自 dkron `api.go` 原始碼):
- `jobRunHandler` **不檢查** `disabled` 欄位 → server 端可以直接觸發 disabled job,UI Run 按鈕灰掉是前端 gating
- PUT/POST 更新是 **upsert(整份蓋掉)**,沒有 PATCH,缺欄位就被清空
- 觸發成功回 `202 Accepted` + `Location` header

---

## 🚨 安全規則(嚴格遵守)

### 1. 預設 read-only
GET 類動作直接執行不囉嗦。但任何 **修改 spec** 或 **觸發 run** 的動作,必須:

1. 把 **目標 job 名稱** 和 **預期變更**(diff 或 command)清楚列給使用者
2. 等到使用者明確同意(「OK」「跑吧」「yes」「好」)才執行

### 2. prod 環境兩道關
任何指向 `dkron.kkday.com`(prod) 的 POST/PUT 都要二次確認,並提醒這次 run 會打到 prod DB / Redis / 真實使用者資料。

### 3. Placeholder command 警告
kkday 有些 manual job 的 command 寫死 placeholder,例如:
- `cache:clear-redis-keys keys=key1,key2,key3`
- `sync:product-to-search product_oid=id1,id2,id3`

如果看到 `key1,key2`、`id1,id2`、`xxx`、`example` 之類字樣,**不要直接觸發**,先請使用者給真實值,改完 command 跑完之後 **務必還原成 placeholder**(避免下個人手滑直接 run 跑到剛剛的真實值)。

### 4. PUT 一定先 GET
任何更新 spec 都走 GET → 在 client 端 jq 改 → PUT 整份。**不要憑記憶體裡的舊 spec 送 PUT**,因為:
- spec 可能被別人剛改過(`success_count` / `last_success` 也算欄位)
- 漏帶任何欄位就會被清空(`tags`、`processors`、`executor_config`、`concurrency`...)

### 5. 觸發完要回報結果
POST run 之後:
- 顯示 HTTP code + `Location` header
- 建議使用者跑 `GET /v1/jobs/{name}/executions` 看執行結果
- 如果 1 分鐘內 `success_count` 沒增加、`error_count` 增加 → 提醒去看 log

---

## 工作流 1:查 job 狀態

最常用,純 read-only,可以直接跑。

```bash
BASE="https://dkron.stage.kkday.com/v1/jobs"

# 抓全部 job 一次(約 800KB),後續用 jq 過濾
curl -s "$BASE" -o /tmp/dkron_jobs.json
wc -c /tmp/dkron_jobs.json

# 依 prefix 搜尋(例如 svc-vtrans / api-vtrans)
PATTERN="svc-vtrans"
jq --arg p "$PATTERN" '[.[] | select(.name | test($p)) | .name]' /tmp/dkron_jobs.json

# 摘要清單(本 skill 標準格式)
jq --arg p "$PATTERN" '[.[] | select(.name | test($p)) | {
  name, displayname, schedule, disabled,
  success_count, error_count,
  last_success, last_error,
  command: .executor_config.command
}]' /tmp/dkron_jobs.json
```

**摘要回報給使用者時建議用 markdown 表格**,欄位至少包含:
- 名稱、顯示名、schedule、disabled?
- 成功 / 失敗次數、最後成功 / 失敗時間
- 用一個 emoji 標健康度:🟢 enabled 且 error_count 低 / 🟡 disabled cron / 🔴 enabled 但 error 遠多於 success

如果某 job `success_count == 0` 而 `error_count` 巨大,**標出來提醒使用者**(這通常是長期破掉沒人管的 cron)。

---

## 工作流 2:stage 手動觸發 manual job(最常見)

跟 UI 操作完全一致:GET → 翻 disabled → PUT enable → POST run → 等執行 → PUT disable 回去。

```bash
BASE="https://dkron.stage.kkday.com/v1/jobs"
NAME="api-vtrans-manual-clear-redis-keys"

# 1) 取目前 spec
JOB=$(curl -s "$BASE/$NAME")
echo "$JOB" | jq '{name, disabled, schedule, command: .executor_config.command}'
```

**👉 這裡停下來,把 spec 摘要給使用者看,等明確同意才繼續。**

如果 command 含 placeholder,先處理工作流 3 才回到這裡。

```bash
# 2) enable(把 disabled 翻成 false 後 PUT 回去)
JOB_ENABLED=$(jq '.disabled = false' <<<"$JOB")
curl -s -X PUT -H 'content-type: application/json' \
  --data "$JOB_ENABLED" "$BASE/$NAME" \
  -o /dev/null -w "enable: %{http_code}\n"

# 3) 觸發
curl -s -i -X POST "$BASE/$NAME" | head -5
# 預期: HTTP/2 202, Location: /v1/jobs/{name}

# 4) 等 5~10 秒看執行結果
sleep 8
curl -s "$BASE/$NAME/executions" | jq '[.[] | {started_at, finished_at, success, node_name, output: (.output[0:300])}] | sort_by(.started_at) | reverse | .[0:3]'

# 5) disable 回去(再 GET 一次取最新 spec,因為 success_count 等欄位剛被更新)
JOB_LATEST=$(curl -s "$BASE/$NAME")
JOB_DISABLED=$(jq '.disabled = true' <<<"$JOB_LATEST")
curl -s -X PUT -H 'content-type: application/json' \
  --data "$JOB_DISABLED" "$BASE/$NAME" \
  -o /dev/null -w "disable: %{http_code}\n"
```

### 為什麼不用 `/toggle` shortcut

`POST /v1/jobs/{name}/toggle` 可以一行翻 disabled,但:
- 不符 kkday UI audit pattern(網路 log 看起來跟 UI 動作不一致,他人 trace 困惑)
- 沒辦法在同一個動作裡改其他欄位
- 部署 script 還是建議走 GET→PUT;救火、急切時才用 toggle

---

## 工作流 3:改寫 command 後再跑

當 job command 含 placeholder,或要臨時加 flag(如 `--no-window-guard`):

```bash
BASE="https://dkron.stage.kkday.com/v1/jobs"
NAME="api-vtrans-manual-clear-redis-keys"

# 1) GET 並顯示目前 command
JOB=$(curl -s "$BASE/$NAME")
ORIGINAL_CMD=$(jq -r '.executor_config.command' <<<"$JOB")
echo "Original command:"
echo "  $ORIGINAL_CMD"
```

**👉 停下來,把 original command 給使用者看,讓他指定要把哪段換成什麼。把 placeholder 替換值列清楚等同意。**

```bash
# 2) 改 command(假設使用者要把 keys=key1,key2,key3 改成 keys=real:cache:1,real:cache:2)
NEW_CMD="/usr/bin/php /data/web-project/application/artisan cache:clear-redis-keys keys=real:cache:1,real:cache:2"

# 用 jq 改完整份 PUT 回去
JOB_NEW=$(jq --arg cmd "$NEW_CMD" '.executor_config.command = $cmd | .disabled = false' <<<"$JOB")
curl -s -X PUT -H 'content-type: application/json' \
  --data "$JOB_NEW" "$BASE/$NAME" \
  -o /dev/null -w "update: %{http_code}\n"

# 3) 觸發 + 等結果(同工作流 2 的 step 3-4)
curl -s -i -X POST "$BASE/$NAME" | head -5
sleep 8
curl -s "$BASE/$NAME/executions" | jq '[.[] | {started_at, success, output: (.output[0:500])}] | sort_by(.started_at) | reverse | .[0]'

# 4) 還原 command + disable(重要!避免下次有人手滑跑到剛剛的真實值)
JOB_LATEST=$(curl -s "$BASE/$NAME")
JOB_RESTORE=$(jq --arg cmd "$ORIGINAL_CMD" '.executor_config.command = $cmd | .disabled = true' <<<"$JOB_LATEST")
curl -s -X PUT -H 'content-type: application/json' \
  --data "$JOB_RESTORE" "$BASE/$NAME" \
  -o /dev/null -w "restore: %{http_code}\n"

# 5) 確認還原成功
curl -s "$BASE/$NAME" | jq '{disabled, command: .executor_config.command}'
```

> ⚠️ 第 4 步漏做的話,placeholder 留著真實 key,下個值班的人在 UI 按 Run 會直接打到那些 key。**還原這步在部署 SOP 裡寫成 checklist 項目**。

---

## kkday 慣例 / 命名 hint

- `svc-*` → service-level job(微服務內部排程,如 `svc-vtrans-*`)
- `api-*` → admin/api-level job(後台或 cron,如 `api-vtrans-*`、`api-vtrans-backend-job`)
- `*-manual-*` → 設計上要人工觸發,通常 `disabled: true` + `schedule: @manually`(或 cron schedule 但 disabled)
- `*-once-*` → 一次性資料修補,跑完應該繼續放 disabled
- command 幾乎都是 `/usr/bin/php /data/web-project/application/artisan <command>`(Laravel artisan)
- `tags.service` 通常是 `<service-name>:1`,代表這個 job 只在 tag 是 `service=<svc>:1` 的 node 上執行(Dkron 的 affinity)

---

## 故障排除

| 症狀 | 可能原因 | 怎麼確認 |
|---|---|---|
| POST run 回 404 | job 名打錯 | `GET /v1/jobs` 確認名字 |
| POST run 回 202 但 `executions` 沒新筆 | 沒有 node 配對到 `tags` | 看 `tags`,跟 Dkron node list 對 |
| PUT 之後欄位被清空 | body 漏帶欄位 | 一定 GET → jq 改 → PUT 整份 |
| `error_count` 一直增加 | command 本身炸 | `GET /v1/jobs/{name}/executions` 看 `.output` |
| stage 401/403 | 之後可能加了 SSO | 問使用者怎麼帶 auth |

---

## 跟使用者溝通的建議

- 列 job 結果用 markdown 表格(欄位:name、schedule、disabled、✅/❌、最後成功、command)
- 任何「會打 server」的步驟,先用 1-2 句說「接下來會做 X,影響 Y」,等同意才動作
- 觸發後一定附 `executions` 結果摘要,**不要只說「已觸發,回 202」就結束** — 使用者要的是「跑完了嗎、成不成功」
- prod 環境額外加一句「這會打 prod,影響真實使用者 / 真實資料,確定?」

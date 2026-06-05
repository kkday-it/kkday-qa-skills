---
name: kkday-dkron
description: |
  安全操作 kkday Dkron 排程的「會改 server 的動作」：手動觸發 / 啟用 / 停用 / 修改某個 cron job 的 command 或 schedule。

  適用情境（只在使用者明確要對某個具體 job 執行動作時觸發）：
  - 「幫我跑 api-vtrans-xxx」、「stage 部署完要觸發 import-price」
  - 「把 svc-vtrans-yyy enable 起來跑一次」、「改 zzz 的 keys 跑一下」
  - 貼出 dkron.*.kkday.com 的 job URL 並表達想執行 / 啟用 / 停用

  不要觸發（純資訊查詢直接用 curl 回答即可，別載這個 skill）：列 job、搜尋 job、看 spec、看 executions、解釋 Dkron 是什麼、比較 Dkron vs Airflow。

  必要工具：curl、jq、bash
---

# kkday Dkron 操作指引

處理**會修改 Dkron server 狀態的動作**:手動觸發 job、enable/disable、改 command/schedule。純讀取(列 job、查 spec、看 executions)直接用 `curl + jq` 就好,不需要這個 skill。

三個工作流的完整逐步 runbook 在 [`references/workflows.md`](references/workflows.md);本檔是決策骨架與安全規則。

## 環境 & Base URL

| 環境 | Base URL | 認證 |
|---|---|---|
| stage | `https://dkron.stage.kkday.com` | 目前**無認證**,GET/POST/PUT 直通 |
| prod | `https://dkron.kkday.com` (UI: `/ui/#/`) | 預期需要(SSO 或 X-Dkron-Token);401/403 就請使用者提供 |

所有指令先設變數:

```bash
BASE="https://dkron.stage.kkday.com/v1/jobs"   # 或 prod
NAME="api-vtrans-manual-clear-redis-keys"      # 要操作的 job
```

## API 端點對照

| 動作 | Method | Path |
|---|---|---|
| 列所有 job / 看單一 spec / 看歷史執行 | `GET` | `/v1/jobs` · `/v1/jobs/{name}` · `/v1/jobs/{name}/executions` |
| 手動觸發 job | `POST` | `/v1/jobs/{name}` |
| 更新(enable/disable/改 command/schedule) | `PUT` | `/v1/jobs/{name}`(body = **完整** job JSON) |
| 翻轉 enable/disable(救火 shortcut) | `POST` | `/v1/jobs/{name}/toggle` |

關鍵事實(dkron `api.go`):觸發 handler **不檢查** `disabled`(server 端可直接觸發 disabled job,UI Run 灰掉只是前端 gating);PUT 是 **upsert 整份蓋掉**、沒有 PATCH,漏欄位即清空;觸發成功回 `202` + `Location`。

## 🚨 安全規則(唯一真相,嚴格遵守)

1. **預設 read-only**。GET 直接做。任何**改 spec** 或**觸發 run** 的動作,先把「目標 job 名 + 預期變更(diff/command)」列給使用者,等明確同意(OK / 跑吧 / yes)才執行。
2. **prod 兩道關**。指向 `dkron.kkday.com` 的 POST/PUT 要二次確認,並提醒「這會打 prod DB/Redis/真實使用者資料」。
3. **placeholder 一定還原**。有些 manual job 的 command 寫死假值(`keys=key1,key2,key3`、`id1,id2`、`xxx`、`example`)。看到就**別直接觸發**,先請使用者給真值;跑完**務必把 command 還原成原本的 placeholder**,否則下個人在 UI 按 Run 會打到剛剛的真實值。
4. **PUT 一定先 GET 整份**。更新走 GET → client 端 jq 改 → PUT 整份;**絕不憑記憶體裡的舊 spec 送 PUT**(別人可能剛改過,且漏帶 `tags`/`processors`/`executor_config` 等欄位會被清空)。還原值也要從**當下重新 GET** 取,不要只靠先前的 shell 變數。
5. **觸發後回報結果**。POST run 後顯示 HTTP code + `Location`,並 `GET .../executions` 看結果摘要;**不要只說「已觸發、回 202」就結束**——使用者要的是「跑完沒、成不成功」。若 `success_count` 沒增、`error_count` 增 → 提醒看 log。

## 共用 primitive

每個 workflow 都由這幾個動作組成(先設好 `BASE`/`NAME`):

```bash
get(){ curl -s "$BASE/$NAME"; }                                   # 取最新整份 spec
put_spec(){ curl -s -X PUT -H 'content-type: application/json' \  # PUT 整份,印 http code
  --data "$1" "$BASE/$NAME" -o /dev/null -w "$2: %{http_code}\n"; }
run(){ curl -s -i -X POST "$BASE/$NAME" | head -5; }              # 觸發(預期 202 + Location)
watch(){ sleep 8; curl -s "$BASE/$NAME/executions" \              # 看最近 3 筆執行
  | jq 'sort_by(.started_at)|reverse|.[0:3]|.[]|{started_at,success,node_name,output:(.output[0:300])}'; }
```

`enable` / `disable` / 改 command 都是「`get` → `jq` 改一個欄位 → `put_spec`」。關鍵:每次 `put_spec` 前都重新 `get`(尤其觸發後 `success_count` 會變)。

## 工作流(完整步驟見 references/workflows.md)

- **WF1 查 job 狀態**(read-only,可直接跑):`get` 全部 → `jq` 依 prefix 過濾 → markdown 表格回報(name/schedule/disabled/✅❌/最後成功/command)。`success_count==0` 但 `error_count` 大的標出來(長期破掉的 cron)。
- **WF2 stage 手動觸發 manual job**(最常見):`get` 給 spec ⏸️**等同意** → enable → `run` → `watch` → disable(disable 前重新 `get`)。command 含 placeholder 先走 WF3。
- **WF3 改 command 再跑**:`get` 顯示 original command ⏸️**等使用者指定替換值** → 改 command + enable → `run` → `watch` → **還原 command + disable**。還原值在進入流程時存好 original,並於還原前確認非空(空就中止,不可送出空 command)——對應規則 4。

> 為什麼不用 `/toggle`:雖能一行翻 disabled,但與 kkday UI 的 GET→PUT audit pattern 不一致(他人 trace 困惑)、也無法在同動作改其他欄位。部署 script 走 GET→PUT;只有救火急切才用 toggle。

## kkday 慣例 / 命名 hint

- `svc-*` service-level(微服務內部排程,如 `svc-vtrans-*`);`api-*` admin/api-level(後台或 cron)
- `*-manual-*` 設計上人工觸發,通常 `disabled: true` + `@manually`;`*-once-*` 一次性修補,跑完續留 disabled
- command 幾乎都是 `/usr/bin/php /data/web-project/application/artisan <command>`(Laravel artisan)
- `tags.service` 通常 `<svc>:1`,代表只在該 tag 的 node 上跑(Dkron affinity)

## 故障排除

| 症狀 | 可能原因 | 怎麼確認 |
|---|---|---|
| POST run 回 404 | job 名打錯 | `GET /v1/jobs` 確認名字 |
| POST run 回 202 但 executions 沒新筆 | 沒有 node 配對到 `tags` | 看 `tags` 對 Dkron node list |
| `error_count` 一直增加 | command 本身炸 | `executions` 看 `.output` |
| stage 401/403 | 之後加了 SSO | 問使用者怎麼帶 auth |

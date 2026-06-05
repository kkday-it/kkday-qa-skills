# kkday Dkron 工作流 — 完整 runbook

照著打的逐步版本。先讀 `../SKILL.md` 的 🚨 安全規則;這裡只展開步驟。
每個流程開頭先設變數與 primitive:

```bash
BASE="https://dkron.stage.kkday.com/v1/jobs"   # 或 prod
NAME="api-vtrans-manual-clear-redis-keys"

get(){ curl -s "$BASE/$NAME"; }
put_spec(){ curl -s -X PUT -H 'content-type: application/json' \
  --data "$1" "$BASE/$NAME" -o /dev/null -w "$2: %{http_code}\n"; }
run(){ curl -s -i -X POST "$BASE/$NAME" | head -5; }            # 預期 202 + Location
watch(){ sleep 8; curl -s "$BASE/$NAME/executions" \
  | jq 'sort_by(.started_at)|reverse|.[0:3]|.[]|{started_at,success,node_name,output:(.output[0:300])}'; }
```

---

## 工作流 1:查 job 狀態(read-only,可直接跑)

```bash
# 抓全部 job 一次(約 800KB),後續用 jq 過濾
curl -s "$BASE" -o /tmp/dkron_jobs.json

PATTERN="svc-vtrans"   # 依 prefix 搜尋(svc-vtrans / api-vtrans ...)
jq --arg p "$PATTERN" '[.[] | select(.name|test($p)) | {
  name, displayname, schedule, disabled,
  success_count, error_count, last_success, last_error,
  command: .executor_config.command
}]' /tmp/dkron_jobs.json
```

回報給使用者用 markdown 表格:名稱、顯示名、schedule、disabled?、成功/失敗次數、最後成功/失敗、command。
健康度 emoji:🟢 enabled 且 error 低 / 🟡 disabled cron / 🔴 enabled 但 error ≫ success。
`success_count == 0` 而 `error_count` 巨大 → 標出來(通常是長期破掉沒人管的 cron)。

---

## 工作流 2:stage 手動觸發 manual job(最常見)

跟 UI 一致:GET → enable → run → 等執行 → disable 回去。

```bash
# 1) 取目前 spec
JOB=$(get)
echo "$JOB" | jq '{name, disabled, schedule, command: .executor_config.command}'
```

**👉 停下來**:把 spec 摘要給使用者,等明確同意才繼續。command 含 placeholder 先做工作流 3。

```bash
# 2) enable → 3) 觸發 → 4) 看結果
put_spec "$(jq '.disabled=false' <<<"$JOB")" enable
run
watch

# 5) disable 回去(重新 GET 取最新 spec,因 success_count 等剛被更新)
put_spec "$(jq '.disabled=true' <<<"$(get)")" disable
```

---

## 工作流 3:改寫 command 後再跑

command 含 placeholder,或要臨時加 flag(如 `--no-window-guard`)時用。

```bash
# 1) GET 並存下 original command
JOB=$(get)
ORIGINAL_CMD=$(jq -r '.executor_config.command' <<<"$JOB")
printf 'Original command:\n  %s\n' "$ORIGINAL_CMD"

# 安全檢查:取不到原始 command 就中止,絕不在之後送出空 command(規則 4)
[ -z "$ORIGINAL_CMD" ] || [ "$ORIGINAL_CMD" = "null" ] && { echo "ABORT: 讀不到 original command"; return 1; }
```

**👉 停下來**:把 original command 給使用者看,讓他指定要把哪段換成什麼,列清楚等同意。

```bash
# 2) 改 command + enable(整份 PUT)
NEW_CMD="/usr/bin/php /data/web-project/application/artisan cache:clear-redis-keys keys=real:cache:1,real:cache:2"
put_spec "$(jq --arg c "$NEW_CMD" '.executor_config.command=$c | .disabled=false' <<<"$JOB")" update

# 3) 觸發 + 看結果
run
watch

# 4) 還原 command + disable(重要!避免下次有人手滑跑到剛剛的真實值)
#    從當下重新 GET 取最新 spec,只把 command 換回開頭存的 ORIGINAL_CMD
put_spec "$(jq --arg c "$ORIGINAL_CMD" '.executor_config.command=$c | .disabled=true' <<<"$(get)")" restore

# 5) 確認還原成功
get | jq '{disabled, command: .executor_config.command}'
```

> ⚠️ 第 4 步漏做的話,placeholder 留著真實 key,下個值班的人在 UI 按 Run 會直接打到那些 key。
> **還原這步在部署 SOP 裡寫成 checklist 項目**;若 step 1 的非空檢查沒過,整個流程不要啟動。

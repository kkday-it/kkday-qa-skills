---
name: tcms-create-case
description: 用 TCMS API 程式化建立 KQT-T test case（clone 既有 case、或從 JSON spec 批次建）。**例外工具**——日常建立與修改 case 一律由人直接在 TCMS 網頁操作，只有在「一次要建很多支」「以某支為範本大量複製」這種網頁做起來不合理的情境，且使用者**明確指名本 skill 或明確說要用 API 批次建立**時才觸發。使用者只說「建一支 case」「新增 case」時**不要**用本 skill，請引導他直接在 TCMS 網頁建立。
---

# TCMS Create Case（寫入端，例外用）

```
http://autotest-service.sit.kkday.com:8081/tcms/api/v1
```

須連公司內網 / VPN。

## 什麼時候**不要**用

| 情境 | 該怎麼做 |
| --- | --- |
| 建一支 case、改一支 case 的內容 | **直接在 TCMS 網頁操作**，這是團隊的正常流程 |
| 查 case 的 steps / precondition | 用 `tcms-fetch-cases` |
| 改既有 case 的內容 | 網頁操作。本 skill **刻意不做 update** |
| 刪 case | 網頁操作。刪除屬 destructive，要人審批 |

## 什麼時候才用

- 一次要建**大量** case（網頁一支一支點不合理）
- 以某支既有 case 為範本，**批次**複製出多支
- 使用者明確說「用 API 建」「跑 script 建」

⚠️ **建立 case 就是在定義規格。** case 內容是後續自動化實作與 fidelity 驗收的依據，
讓工具代寫等於規格與實作出自同一個代理人。用本 skill 產出的 case 一律 `lifecycle_status=Draft`，
**務必請人過目確認**再進入自動化流程。

⚠️ **本 script 沒有 dry-run。** 跑下去就是真的建在 TCMS 上，不能「先試試看」。
測試連通性請用 GET（例如 `curl .../cases/KQT-T63751`），不要拿 `clone` 當探針。

## Auth

| Header | 值 |
| --- | --- |
| `Authorization` | `Bearer <token>` |
| `X-User-Id` | 見下方衝突說明 |
| `Content-Type` | `application/json` |

GET 端點權限寬鬆（placeholder token 也通）；**POST / PUT / DELETE 必須真 token**，否則 401。

### Token 從哪來

正本在 **Secret Data Management**（UI 掛在 `:8080`，取值 API 在 `:8000`）。script 自動處理，解析順序：

1. `$TCMS_TOKEN`
2. `~/.cache/tcms_token`（快取，chmod 600）
3. Secret service → 寫回快取

```bash
curl -s "http://autotest-service.sit.kkday.com:8000/api/v1/data/\
?env=stage&service=tcms_skill_token&key=tcms_skill_token_stage" \
  -H "authorization: Bearer $AUTOMATION_TOKEN"
```

回 `[{"environment":…,"id":…,"key":…,"service":…,"value":"{\"token\": \"…\"}"}]` ——
注意 **`value` 是字串包 JSON**，要 parse 兩層才拿得到 `token`。

**401 時 script 會自動回 secret service 重取一次再重試**（快取過期的常見情況），一輪只換一次，
不會無限重試。若換過還是 401，代表 secret service 上的 token 本身過期，或 `X-User-Id` 不對 ——
這時到 `/tcms/account#api-tokens`（若 404 試 `/tcms/settings#api-tokens`）人工產一個寫進
`~/.cache/tcms_token`。該頁也 401 就走 `/api/v1/users/google-oauth/start` 重登，攔 fetch 拿真
Authorization header。

### ⚠️ `X-User-Id` 有兩個版本，尚未實測釐清

| 值 | 依據 |
| --- | --- |
| `ml09h4qj-l7bsikcns5m` | 既有工具打 `POST /cases/` 實際在用的值。但這個 id 其他文件記載是 **ai-studio backend** 的身分（用途是戳 `/api/admin/*`），跟 TCMS 未必同一套 |
| `2` | 另一份文件明寫「TCMS 用這個，2 = Eden Lai」 |

OpenAPI 把 `x-user-id` 標成 **optional**，所以帶錯很可能**不噴錯，只是建立者掛到錯的人**。
script 預設用前者（唯一在這支 endpoint 上實際跑過的），要換：

```bash
TCMS_USER_ID=2 python3 tcms_create_case.py ...
```

**有人實測確認後請回來把這段改成定論。**

## 欄位規格（`POST /cases/` = `TestCaseCreate`，OpenAPI 3.1 / KK TCMS 1.5.0）

| 欄位 | 型別 | 必填 | 預設 |
| --- | --- | --- | --- |
| `title` | string | ✅ | — |
| `suite_id` | int | ✅ | — |
| `steps` | array\<TestStepCreate\> | | `[]` |
| `status` | string | | `Active` |
| `lifecycle_status` | string | | `Draft` |
| `severity` | string | | `Normal` |
| `priority` | string | | `Not Set` |
| `behavior` | string | | `Not Set` |
| `automation_status` | string | | `Manual` |
| `is_flaky` / `muted` | bool | | `false` |
| `description` / `type` / `layer` | string | | null |
| `preconditions` / `postconditions` | string | | null |
| `tags` / `labels` / `jira_keys` | string | | null |
| `external_id` | string | | null |
| `default_owner_id` | int | | null |

`TestStepCreate`：`action`（✅必填）、`data`、`expected_result`、`order`（預設 1）。

### 五個會踩的坑

1. **`tags` / `labels` 是「字串包 JSON array」**，不是 list：
   `"labels": "[\"Automated_PC\", \"UI\"]"`、`"tags": "[\"Platform / Service:Web\", \"Platform_New:mWeb,PC\"]"`
   本 script 的 spec 收 list 會自動轉，直接打 API 不會。
2. **`external_id` 不要指定** —— 留空後端才自動生 `KQT-Txxx`；自己填會撞 409。
3. **`case_ref` 吃 `KQT-T63751`**，不必先換內部 int id。但 `POST /cases/batch-clone` 只吃**內部 int** `case_ids`，且不能改名 / 換 suite —— 所以本 script 的 `clone` 走 GET + POST 而不是 batch-clone。
4. **clone 時 steps 要剝掉 `id` / `test_case_id`**，那是來源 case 的；帶過去會被當成更新目標。script 已處理。
5. **`suite_id` 從 TCMS 網址列抓**：`/tcms/repository?suite=XXXX`。**不要猜，不要沿用前面對話貼過的**，正式推之前再跟人確認一次。

### `priority` 值

canonical 四選一：`Critical` / `High` / `Medium` / `Low`（可省，省了會變 `Not Set`）。

若來源 case 是從 Zephyr 匯入的（`POST /cases/import/zephyr`），它的 priority 是內部術語，
後端 `priority_normalizer.py` 的 `_ALIASES` 負責 normalize；沒對到會變 `NOT_SET`：

| 匯入來源值 | TCMS canonical |
| --- | --- |
| `RAT` | `Critical` |
| `FAST` | `High` |
| `TOFT` | `Medium` |
| `FET` | `Low` |

### `tags` 決定平台範圍 —— 建 case 時就要標對

**一個 TCMS case ID 涵蓋它 tag 標的所有平台**，不是 web / mweb 各自獨立 ID。
tag 標 `FE (Web/mWeb/Android/iOS)` ⇒ 後續自動化四個平台都要做才算完成。

所以 tag 標多標少會直接放大／縮小後面的實作範圍，**要跟人確認過再送**。

## 用法

```bash
S=~/.claude/skills/tcms-create-case/scripts/tcms_create_case.py

# Clone 一支（沿用來源 suite，強制 Draft）
python3 $S clone KQT-T63751 --name "Stripe 付款（繁中 / JPY 幣別）" --draft

# 換 suite + 覆寫 priority
python3 $S clone KQT-T63751 --name "..." --suite-id 1005 --priority High

# 從 spec 批次建
python3 $S create /tmp/cases.json --suite-id 1005
```

spec JSON —— 單支或整批都收：

```json
{
  "suite_id": 1005,
  "labels": ["Automated_PC", "UI"],
  "tags": ["Platform / Service:Web"],
  "cases": [
    {
      "title": "Stripe 付款（繁中語系 / INR 幣別）",
      "priority": "Critical",
      "preconditions": "語系設定 zh-tw，幣別選擇 INR",
      "automation_status": "Manual",
      "steps": [
        {"order": 1, "action": "進入結帳頁", "expected_result": "顯示 Stripe 付款區塊"},
        {"order": 2, "action": "輸入測試卡號送出", "expected_result": "導向訂單完成頁"}
      ]
    }
  ]
}
```

頂層的 `labels` / `tags` 會套到每一支；個別 case 可覆寫。

### 環境變數

| 變數 | 預設 | 用途 |
| --- | --- | --- |
| `TCMS_TOKEN` | — | 直接指定 token，跳過快取與 secret service |
| `TCMS_USER_ID` | `ml09h4qj-l7bsikcns5m` | `X-User-Id`，見上方衝突說明 |
| `AUTOMATION_TOKEN` | 內建 | 打 secret service 的 Bearer |
| `SECRET_SERVICE_HOST` | `http://autotest-service.sit.kkday.com` | secret service host |
| `TCMS_SECRET_ENV` / `_SERVICE` / `_KEY` | `stage` / `tcms_skill_token` / `tcms_skill_token_stage` | 換環境時用 |

## 注意

- 純 stdlib（`urllib`），不 import kkday-QA-automation 任何模組，可在任何 cwd 執行
- 新建一律 `lifecycle_status=Draft`（clone 用 `--draft`），等人確認再改狀態
- 非 401 的失敗直接印 status code + body 前 400 字並中止，不吞錯
- 批次建到一半失敗會中止，**已建的不會回滾** —— 已建的 id / external_id 會印在中止前的輸出裡
---
name: kibana-api-log
description: |
  從 sit / stage Kibana 撈某支 API 的實際請求與回應（含 headers、request body、response），
  可依平台（iOS / Android / APP 全體）、時間窗、帳號、裝置收斂。免認證（anonymous provider）。

  適用情境：
  - 「幫我抓 stage kibana log，`v2.2/payment/booking/channels` 這隻 API，Android 打的」
  - 「剛剛那 2 分鐘打了哪些 endpoint」「這支 API 收到的 body 長怎樣」
  - APP（iOS / Android 原生）有 SSL pinning、抓不到封包，要反推 API contract
  - 某個 case 失敗，想看當下那支 API 實際送了什麼、回了什麼

  不適用：
  - 網頁（b2c web）的即時封包 → 用 `qa-sniff-api-with-playwright`（Playwright 攔截）

  🚨 prod 也撈得到，但**撈之前一定要先問過使用者**（那是真實客戶資料）——見「prod」段。
---

# Kibana API Log

撈 `new-kklog-*` 裡的 API 請求／回應。走 anonymous provider
（`POST /internal/security/login` 拿 `sid` cookie → 打 `/internal/search/es`），
所以**不需要帳密**：sit（`kibana.sit.kkday.com`）、stage（`kibana.stage.kkday.com`）、
prod（`kibana.kkday.com`）三座都進得去。

## 🚨 prod：撈之前先問人

**anonymous 在 prod 一樣過**（實測 login 200、拿得到 `sid`），所以「撈不到」不會幫你踩煞車——
唯一的煞車是你自己。**沒有使用者明確同意，不要對 prod 下任何一筆查詢。**

prod 的 log 是真實客戶的資料：headers 有 `member-uuid` / token / 裝置識別 / IP，body 有姓名、
email、電話、訂單、金流。sit / stage 撈錯頂多白忙一場；prod 撈錯是把客戶個資拉進終端機、
對話紀錄跟截圖裡，收不回去。而且那座 Kibana 是線上監控在用的，寬窗查詢會影響正在處理事故的人。

所以：

1. **先講清楚再撈**——你要 prod 的什麼、為什麼 sit / stage 不夠，等使用者明確同意。
2. 同意之後才加 `--allow-prod`（沒有這個 flag，script 會直接擋下並退出；`--env auto` 也永遠
   不會自己選到 prod）。自己手寫 ES query 時這道關卡不存在，規矩一樣要遵守。
3. 撈的時候：時間窗壓到**分鐘級**、能用 aggs 就不要拉 hits、不要順手 `--detail`
   （`--detail` 現在會把 headers / body 原文整串印出來，prod 那是真的客戶資料）。
4. 撈完：**不要把 headers / body 原文貼進報告、PR、Slack**。要引用就只留結論與統計，
   需要指認特定 token／帳號時用指紋（sha1 前 8 碼），不要貼原值。

## 怎麼跑

整套都在這個 skill 資料夾裡（`api_from_kibana.py` ＋ 它的兩個同層相依
`kibana_client.py` / `device_registry.py`），**不需要 QA framework clone，也不用它的 venv**，
`python3` ＋ `requests` 就能跑，在哪個目錄跑都行：

```bash
S=~/.claude/skills/kibana-api-log/api_from_kibana.py
python3 "$S" --env stage --platform android \
  --route v2.2/payment/booking/channels --minutes 3 --device none --detail
```

唯一要 framework 的是 `--email auto`（那把帳號設定在框架裡）。有 clone 的話設
`QA_FRAMEWORK_PATH=<clone>` 就會自動接上；沒有就改用 `--email <addr>` 明寫。

輸出（`--detail`，最近 3 筆）：

```
=== contract samples: 3

━━━ POST api/v2/points/estimate
    time         : 2026-09-17 16:11:50.975  (本地時間)
    route        : api/v2/points/estimate
    request.uuid : 6eda0469-…   <- 整條 trace 的 id（見下方「注意」）
    ── request headers
       mixpanel-id       : be0f311e-…
       device-model      : SM-A5560
       member-uuid       : be0f311e-…
       token             : <redacted>
       x-req-source      : ANDROID
       …（照 log 裡的原順序全印，一個不漏）
    ── request body
       {
         "cart_amount": 0,
         "currency": "TWD",
         "ui_elements": [
           "productBanner"
         ]
       }
    ── response  HTTP 200 130ms  0000 Success
       {
         "metadata": { "status": "0000", "desc": "Success" },
         "data": { "ui_elements": { "product_banner": { "title": "白金會員", … } } }
       }
```

範例裡的 `…` 是**這份文件在省略**，不是輸出在省略：headers 逐行全印，request / response body
是 JSON 就自動排版、整串給。`available_channels` 那種幾 KB 的清單也不截——contract 的重點常常
就在最後幾個欄位（`pay_endpoint`、`accepted_card_types`、`setting.tap_pay`），截掉就白撈了。
洗版洗不下去時用 `--truncate 300` 自己收，它會標明「截斷，共 N 字」。

headers **不做分類也不挑重點**，順序就是 log 裡的原順序。挑重點得維護一份欄位清單，而那份
清單一定會過期——換一支 API、或 App 新加一個 header，最需要看的那個就被排到看不見的地方。

`response` 那兩行是 script 自己去配對的（用 uuid ＋ route ＋ `log_label: RESPONSE`），
**不是**取樣那筆文件身上帶的——取樣取的是 REQUEST，REQUEST 沒有 response 欄位。
印不出來時會明講「配對不到」，不會靜靜省略。

不帶 `--detail` 則是「這個時間窗打了哪些 route、各幾筆」的清單，適合先看全貌再收斂。

## 參數

| 參數 | 說明 |
| --- | --- |
| `--env` | `sit` / `stage`。預設 `auto`（拿裝置指紋逐一環境試撈，只會試這兩座）—— **人已經講明環境時一律明寫** |
| `--allow-prod` | 確認要撈 prod。**先得到使用者明確同意才加**，沒加會被擋下（見上面「prod」段） |
| `--route` | endpoint 片段，`match_phrase`，不用帶完整 path（`v2.2/payment/booking/channels` 就夠） |
| `--platform` | `ios` / `android`；不給就是 APP 全體 |
| `--minutes` | 相對時間窗，預設 15 |
| `--from` / `--to` | 絕對時間，**本地時區**：`now-2h` / `14:30` / `'2026-09-14 14:30'` |
| `--device` | 收斂到某台機器的 header 指紋。預設 `auto` 會去抓「連著的實機」 |
| `--ad-id` | 直接指定裝置識別（同型號多台時的手動入口） |
| `--email` / `--member-uuid` | 收斂到某個帳號；`--email auto` 取該平台的框架預設帳號 |
| `--list-devices` | 只列該時段出現過的裝置指紋，不做其他查詢 |
| `--detail` | 印完整 contract（headers / body / response）而不是 route 統計 |
| `--truncate N` | 把 headers / body / response 各截到 N 字。**預設 0＝不截**，截到時會標明共幾字 |

## 撈不到東西時，照這個順序查

1. **`--device none` 忘了帶。** `--device` 預設是 `auto`，會去偵測「插在這台電腦上的實機」，
   然後把查詢收斂到那台。只是想看「這支 API 誰打的」時這層會把結果清空 —— 而且它清空的樣子
   跟「這段時間沒人打」完全一樣。**沒有要鎖特定機器就一定要 `--device none`。**
2. **env 撈錯。** 撈錯環境不會報錯：不是 0 筆，就是回同時段別人的流量。人講 stage 就寫 stage，
   別靠 `auto`。
3. **時間窗。** 人給的時間一律當**本地時間**（台北 +08）用，不用自己換算成 UTC ——
   `--from 13:20 --to 13:30` 就對了，script 會補時區給 ES。只有自己手寫 ES query 時才要記得
   帶 offset（`2026-09-17T13:20:00+08:00`），因為 `@timestamp` 存的是 UTC，把本地時間當 UTC
   直送會差 8 小時、回空的 —— 而空的跟「那段沒流量」長得一模一樣。
4. `--platform` 的大小寫不用自己處理：log 裡 `iOS/IOS/ios`、`ANDROID/Android` 都有，script
   已經把變體全列進 `terms` 查詢。
5. 查無資料時它會自動補印該時段的裝置清單，用來分辨是「env 錯」還是「型號字串對不上」。

## 自己下查詢（統計／時間軸這類 script 做不到的形狀）

script 只回「最近 N 筆的 contract」。要問的是「這支 API 今天成功幾次失敗幾次」「這個帳號的
token 什麼時候換的」「iOS 有沒有一樣的症狀」時，直接拿同一個 `KibanaClient` 下 ES query：

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "/Users/eden.lai/.claude/skills/kibana-api-log")
from kibana_client import KibanaClient
c = KibanaClient.for_env("stage")
rv = c.search({"size": 0, "query": {"bool": {"must": [
    {"range": {"@timestamp": {"gte": "2026-09-17T13:00:00+08:00",
                              "lte": "2026-09-17T13:30:00+08:00"}}},
    {"term": {"log_label.keyword": "RESPONSE"}},
    {"term": {"request.route.keyword": "api/v2/token/refresh"}}]}},
    "aggs": {"s": {"terms": {"field": "response.http_status", "size": 10}}}})
print([(b["key"], b["doc_count"]) for b in rv["aggregations"]["s"]["buckets"]])
PY
```

### 🚨 時間窗開窄一點 —— Kibana 會被打掛

這座 Kibana 是**大家共用的**，`new-kklog-*` 又是全站 API 的 log。時間窗開太寬（跨天、跨週）
的查詢會把整個 cluster 拖垮，受害的是所有在查 log 的人，不是只有自己等久一點。

- 預設就用**幾十分鐘**的窗，先把形狀問出來。
- 真的要看趨勢再放大，而且放大時**一律 `size: 0` + aggs**（`terms` / `date_histogram`），
  讓 ES 在自己那邊數完只回統計值。**絕對不要**用寬時間窗配 `size: 200` 去拉 hits 回本地自己數。
- 一支 route、一個帳號這種條件先加好再送 —— 條件愈早收斂，掃到的 shard 愈少。
- **自己下 query 時 prod 的門是不存在的**：`KibanaClient.for_env("prod")` 直接就通。
  script 那道 `--allow-prod` 擋不到這條路，所以「先問過人」這件事得自己記得。

### 欄位

| 欄位 | 用途 |
| --- | --- |
| `custom_api-b2c.source.keyword` | `ANDROID` / `iOS` / `IOS` …（**比 platform 有用**，拿來比對雙平台） |
| `custom_api-b2c.platform.keyword` | `APP` / web |
| `custom_api-b2c.member_uuid.keyword` | 會員，只有會員域 endpoint 有 |
| `request.route.keyword` | 完整 route（`term` 用這個；`match_phrase` 才用不帶 `.keyword` 的） |
| `response.http_status` | 數值，直接 `terms` agg |

⚠️ **agg 的欄位一定要帶 `.keyword`。** `custom_api-b2c.source` 拿去做 terms agg 回的是**空
bucket 清單**，不是報錯 —— 跟「這段時間沒流量」長得一模一樣，很容易就據此下錯結論。

### 常用形狀

- **雙平台對照**：同一組 query 換 `{"terms": {"custom_api-b2c.source.keyword": [...]}}`，
  iOS 要列 `["iOS","IOS","ios"]` 三種拼法。實測有「某支 API 只有 Android 在打」這種事，
  這時 iOS 不是沒壞而是**根本沒走那條路**，別當成「只有 Android 有 bug」。
- **時間軸**：`{"size": 200, "sort": [{"@timestamp": "asc"}]}` 撈 REQUEST，把
  `request.headers` 那串 JSON（單 key dict 的 array）攤平成 dict 取 `ad-id` / `member-uuid`，
  再用 `request.uuid` 回撈 RESPONSE 對狀態。
- **token / 敏感值**：不要印出來，一律 `hashlib.sha1(v.encode()).hexdigest()[:8]` 當指紋。
  指紋足以回答「換了沒／是不是同一張」，而那通常就是真正要問的事。

## 注意

- `token` / `b2c-token1` / `authorization` / `password` 這類 header 與欄位輸出前會被 `<redacted>`。
- `request.headers` 是字串，只能全文比對 → `--device` / `--ad-id` 這兩個過濾**只留得住 REQUEST**
  （RESPONSE 沒有 headers）。要串回應就用輸出裡那個 `request.uuid`。
- ⚠️ **`request.uuid` 是整條 trace 的 id，不是單一請求的**。同一個 uuid 底下會有前端那支，
  加上它往下打的 `svc-member` / `api-product` / `payment` …十幾筆 REQUEST/RESPONSE/OUTBOUND。
  只用 uuid 撈 RESPONSE 會拿到某個下游服務的回應（實測拿到 member info，看起來完全像是
  這支 API 回的）。**一定要同時鎖 route 與 log_label**：

  ```python
  {"bool": {"must": [
      {"match_phrase": {"request.uuid": uuid}},
      {"term": {"request.route.keyword": "api/v2.2/payment/booking/channels"}},
      {"term": {"log_label.keyword": "RESPONSE"}}]}}
  ```

  `log_label` 有 `REQUEST` / `RESPONSE` / `OUTBOUND` / `TRACE` 四種；HTTP 狀態與耗時在
  `response.http_status` / `response.time`，業務狀態在 body 的 `metadata.status`
  （`0000` 才是成功，例如 `M001` = unauthorized-user 會配 401）。
- `--member-uuid` 只有會員域 endpoint 帶得到，商品／搜尋那些不帶，單靠它會漏掉大半 trace。

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
  - prod 的 log —— 這支走 anonymous 登入，只有 sit / stage 開
  - 網頁（b2c web）的即時封包 → 用 `qa-sniff-api-with-playwright`（Playwright 攔截）
---

# Kibana API Log

撈 `new-kklog-*` 裡的 API 請求／回應。背後是 QA framework 的 `KibanaClient`
（`POST /internal/security/login` 拿 anonymous `sid` cookie → 打 `/internal/search/es`），
所以**不需要帳密**，但只有 sit（`kibana.sit.kkday.com`）與 stage（`kibana.stage.kkday.com`）有。

## 怎麼跑

script 在本 repo，但**必須用 QA framework 的 venv 跑**（要 import 框架裡的 `KibanaClient`）：

```bash
FW=/Users/eden.lai/Downloads/qa_test/web/kkday-QA-automation   # app/ test/ 那兩個 clone 也行
cd "$FW" && QA_FRAMEWORK_PATH="$FW" ./venv/bin/python \
  ~/kkday-qa-skills/scripts/app_api_from_kibana.py \
  --env stage --platform android --route v2.2/payment/booking/channels \
  --minutes 3 --device none --detail
```

輸出（`--detail`，最近 3 筆）：

```
=== contract samples: 1

--- POST api/v2.2/payment/booking/channels
    route      : api/v2.2/payment/booking/channels
    request.uuid: 419db3b1-…   <- 用這個串 REQUEST/RESPONSE
    headers    : {… "x-req-source": "ANDROID", "x-req-version": "2.125.0", "ad-id": …}
    body       : {"cart_amount":"100","currency":"HKD","product_oids":"127033", …}
    response   : {…}
```

不帶 `--detail` 則是「這個時間窗打了哪些 route、各幾筆」的清單，適合先看全貌再收斂。

## 參數

| 參數 | 說明 |
| --- | --- |
| `--env` | `sit` / `stage`。預設 `auto`（拿裝置指紋逐一環境試撈）—— **人已經講明環境時一律明寫** |
| `--route` | endpoint 片段，`match_phrase`，不用帶完整 path（`v2.2/payment/booking/channels` 就夠） |
| `--platform` | `ios` / `android`；不給就是 APP 全體 |
| `--minutes` | 相對時間窗，預設 15 |
| `--from` / `--to` | 絕對時間，**本地時區**：`now-2h` / `14:30` / `'2026-09-14 14:30'` |
| `--device` | 收斂到某台機器的 header 指紋。預設 `auto` 會去抓「連著的實機」 |
| `--ad-id` | 直接指定裝置識別（同型號多台時的手動入口） |
| `--email` / `--member-uuid` | 收斂到某個帳號；`--email auto` 取該平台的框架預設帳號 |
| `--list-devices` | 只列該時段出現過的裝置指紋，不做其他查詢 |
| `--detail` | 印完整 contract（headers / body / response）而不是 route 統計 |

## 撈不到東西時，照這個順序查

1. **`--device none` 忘了帶。** `--device` 預設是 `auto`，會去偵測「插在這台電腦上的實機」，
   然後把查詢收斂到那台。只是想看「這支 API 誰打的」時這層會把結果清空 —— 而且它清空的樣子
   跟「這段時間沒人打」完全一樣。**沒有要鎖特定機器就一定要 `--device none`。**
2. **env 撈錯。** 撈錯環境不會報錯：不是 0 筆，就是回同時段別人的流量。人講 stage 就寫 stage，
   別靠 `auto`。
3. **時間窗。** log 的 `@timestamp` 是 UTC，script 會把你給的時間當本地時區換算 —— 對照它印出來
   的那行 `時間 … → …` 確認範圍。
4. `--platform` 的大小寫不用自己處理：log 裡 `iOS/IOS/ios`、`ANDROID/Android` 都有，script
   已經把變體全列進 `terms` 查詢。
5. 查無資料時它會自動補印該時段的裝置清單，用來分辨是「env 錯」還是「型號字串對不上」。

## 注意

- `token` / `b2c-token1` / `authorization` / `password` 這類 header 與欄位輸出前會被 `<redacted>`。
- `request.headers` 是字串，只能全文比對 → `--device` / `--ad-id` 這兩個過濾**只留得住 REQUEST**
  （RESPONSE 沒有 headers）。要串回應就用輸出裡那個 `request.uuid`。
- `--member-uuid` 只有會員域 endpoint 帶得到，商品／搜尋那些不帶，單靠它會漏掉大半 trace。

---
name: qa-sniff-api-with-playwright
metadata:
  requires_repo: kkday-QA-automation
description: |
  使用者手動操作 KKday web/mweb 頁面，由 Playwright sniff script 攔截真實 API call，再從 sniff log 反推 endpoint / payload / response 結構，產生對應 test_step 與 JSON case data。

  適用情境：
  - 使用者說「sniff」、「攔截 API」、「我手動點」、「先看 UI 打什麼」、「不知道哪支 API」、「找對應 endpoint」
  - 要自動化某個 UI 流程但不確定真實 BE endpoint / payload / response
  - 既有 test_step 跑出來與 UI 行為不符，需用真實流量驗證
  - 需要新增「驗證訂單狀態 / 拉訂單列表 / 查詢資料」類 API 但 framework 沒現成 helper

  必要工具：Playwright（pip install playwright）、Bash、Read、Edit
  前置條件：本機需有 kkday-QA-automation repo（產出物 test_step / case_data 寫入 framework；無則 skill 會引導 clone，見 Step 0）。
---

# Sniff and Derive API

把「使用者手動瀏覽器操作 → 抓真實 API call → 反推 test_step 程式碼」整套流程標準化。

## 何時用

- 用戶想自動化某個 UI 流程，但**不確定真實 API endpoint / payload / response 結構**
- 已經寫的 test_step **跑出來跟 UI 行為對不起來**，需要驗證真實 BE 是怎麼打的
- 要新增「驗證訂單狀態 / 拉訂單列表 / 查詢某資料」之類的 API 但 framework 沒現成的 helper
- 用戶說「先 sniff 看一下」、「我手動點給你看」、「不要直接寫，先看 UI」

## 你 (Claude) 要做的事

### Step 0：前置檢查

#### 0a. 確認 framework repo 存在

產出物（test_step、case_data）會寫入 kkday-QA-automation repo，所以 framework 必須存在：

1. **偵測** — 從常見位置找：
   ```bash
   for d in "$HOME/Downloads/qa_test/test/kkday-QA-automation" \
            "$HOME/kkday-QA-automation" \
            "$PWD/kkday-QA-automation" \
            "$PWD"; do
     [ -f "$d/QATest/src/qatest/__init__.py" ] && echo "FOUND: $d" && break
   done
   ```

2. **找不到 → 提示使用者 clone**（**不要無腦自動執行**，先請使用者確認 clone 目標位置與權限）：
   ```bash
   git clone https://github.com/kkday-it/kkday-QA-automation.git <目標目錄>
   cd <目標目錄>
   python -m venv venv && source venv/bin/activate && pip install -r requirements.txt
   ```

#### 0b. 確認 sniff script 已部署

`<framework_path>/scripts/sniff_kkday_api.py` 是否存在？
- **存在** → 用 framework 內版本
- **不存在**（舊版 framework 沒這支 script） → 從 skill 自帶複製過去：
  ```bash
  cp <skill-root>/scripts/sniff_kkday_api.py <framework_path>/scripts/sniff_kkday_api.py
  ```
  其中 `<skill-root>` 是本 SKILL.md 所在目錄。

#### 0c. 依賴檢查

Playwright 必須裝好：
```bash
python -c "import playwright" || pip install playwright && playwright install chromium
```

### Step 1：請用戶啟動 sniff

依場景挑選 sniff 模式：

#### Mode A：standalone sniff script（用戶手動瀏覽器操作）

`sniff_kkday_api.py` 是獨立 Playwright sniff，給「我手動點」場景用。

**注意：你不能在 background 跑這個 script** —— `input()` 會立刻 EOF script 就死掉抓不到東西。**必須請用戶在他自己的 terminal 跑**。

提供完整可複製貼上的指令給用戶（在 Step 0 偵測到的 framework root 執行）：
```bash
source venv/bin/activate && \
  python scripts/sniff_kkday_api.py --output /tmp/kkday_sniff.log --start-url https://www.stage.kkday.com/<相關起始頁>
```

說明：
- 不要寫死 `cd` 到絕對路徑——每台機器路徑不同
- 起始 URL 直接帶到要操作的頁面（如 `/zh-tw/order/orderlist` 拉訂單列表）
- 操作完在 terminal 按 Enter 結束 sniff
- 結束後叫 Claude 讀 log

#### Mode B：跟 UI test run 一起 sniff（觀察實際 case 行為）

`QATest/src/lib/fixtures/playwright.py` 已經有 `API_SNIFF=1` env hook，跑 UI test 時自動把 KKday API call 寫進 `qatest.log`：

```bash
API_SNIFF=1 python -m qatest run --caseid <CASE_ID> --use_driver playwright
```

之後找 qatest output 目錄下該 case 的 log 檔，grep `[api_sniff]` prefix 的 log 行就是攔到的 API。

這個模式適合：
- 已經有 UI test 但寫得不對，想看「UI test 實際打什麼 API」 vs「我自己想像的 API」差在哪
- 觀察 UI 流程的真實順序（特別是「response 鏈」場景：A response 餵 B request）

### Step 2：用戶做完操作說「好了」/「done」

讀 `/tmp/kkday_sniff.log`，**先用 wc -l 跟 head 確認 log 有實際內容**（不是只剩 start/stop 訊息）。

### Step 3：篩相關 API

用 grep + awk 抽出 unique URL：
```bash
grep ">>> " /tmp/kkday_sniff.log | awk '{print $3, $4}' | sort -u | grep -iE "<keyword>"
```

過濾掉 noise：`fetch_member_coupon_count`、`fetch_points_balance`、`exchange_rate`、`ajax_get_member_status`、`api/v1/journey/action`、`tealium`、`recommend` 等。

剩下的就是該頁/該動作真實打的 API。

### Step 4：抓 payload + response 結構

對每支關鍵 API：
1. `grep -B1 -A3 "<endpoint>" /tmp/kkday_sniff.log` 拿 request body
2. response body 短的會 pretty-print；長的只有 status code（要從 b2c-app 同類 API 推 schema 或讓用戶再 sniff 一次調整 truncation 上限）
3. 若 response body 沒抓到細節，提供「再 sniff 一次」選項或從 BE2 doc 推

### Step 5：擴充 sniff 過濾（必要時）

`scripts/sniff_kkday_api.py` 的 `KKDAY_API_PATTERNS` 是 sniff 包含的 URL 子字串。如果用戶頁面 URL 路徑不在裡面（如 `/zh-tw/order/`、`/api/v3/orderInfo/`），先 Edit 加入 pattern 再請用戶 re-sniff。

不要動 `SKIP_PATTERNS`（那是 noise filter，動到反而有風險）。

### Step 6：寫 test_step + JSON

依 sniff 結果產：
- `test_steps/api/kkday/web/<feature>.py` 新 function（遵守 `qa-automation-writer` skill 的 ApiCore + try/finally + Google docstring 規範）
- `case_data/TW/KKdayWeb/WebApi<Name>.json` 對應 collection
- yaml case 把 step 接進去

JSON placeholder、form-urlencoded headers、動態 dict key 等實作細節見 [references/gotchas.md](references/gotchas.md)。

### Step 7：跑 test 驗證

寫完一支馬上單跑驗證：
```bash
source venv/bin/activate && python -m qatest run --caseid <CASE_ID> --use_driver playwright
```

**有 bug 不要一次寫一堆**——每個新 step 寫完跑一次，bug fix 確認 PASS 再做下一個。批量寫然後一次跑會 cascading fail，難 debug。

## 常見坑

CodeIgniter csrf、referer header、assign_data fail、HTML response check、分頁 API 等踩過的坑見 [references/gotchas.md](references/gotchas.md)。

## 參考

`qa-automation-writer` skill 的 coding 規範必須同步遵守。

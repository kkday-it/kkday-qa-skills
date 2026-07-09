---
name: qa-automation-writer
metadata:
  requires_repo: kkday-QA-automation
description: |
  KKday QA 自動化框架（kkday-QA-automation repo）的 coding 規範與撰寫指引，涵蓋 Page Object、Test Step、Case Data、API 測試、Playwright/Appium 等規範。

  適用情境：
  - 使用者在 kkday-QA-automation repo 中撰寫或修改 `pages/`、`test_steps/`、`case_data/` 下的檔案
  - 使用者要求新增測試案例、page object 或 test step
  - Code review 自動化測試 PR 時需要對照規範

  必要工具：Read、Edit、Write（撰寫）。**定稿前的元素驗證階段**會用 playwright MCP（Web/MWeb）、adb（Android）、idb（iOS）抓真實元素樹——這些工具與模擬器若沒裝/沒開，skill 會**自動 bootstrap**（不依賴使用者事先準備，見「撰寫流程 階段 2」）。
  前置條件：本機需有 kkday-QA-automation repo（無則先引導 clone，見「前置」段）。
---

# QA Automation Coding Standards

在 kkday-QA-automation repo 中撰寫或修改自動化測試程式碼時，**必須遵守以下規範**。

## 前置：確認 framework repo 存在

開始套用規範前，先確認使用者所在 repo 是 kkday-QA-automation：

1. **偵測** — 確認當前 cwd 或附近是否為 framework root（看 `QATest/src/qatest/__init__.py` 或 `pages/`、`test_steps/`、`case_data/` 三個目錄是否同時存在）。
2. **不在 framework repo 內** — 提示使用者：
   - 確認當前是否誤跑（規範僅適用於 kkday-QA-automation repo 的程式碼）
   - 若需要 clone：`git clone https://github.com/kkday-it/kkday-QA-automation.git`（**請使用者確認目標位置後再執行**，不要無腦自動 clone）
3. **僅看規範不寫 code** — Code review 等情境可不需 clone，直接套用規範比對即可。

---

## 撰寫流程：判定平台 → 先規劃 → 元素驗證 → 自動執行

**核心原則：locator 一律不准用猜的定稿。** 依 case steps 把整個 case 想完、草擬完，**定稿前必須拿真實頁面/畫面元素驗證所有新增或修改的 locator**，再自動跑一次確認。這段流程凌駕於「先寫先跑」的直覺——寧可多一次驗證，也不要交出猜的 locator。

### 階段 0 — 判定目標平台與步驟切分（先做）

**平台來源**：看 case 的 `labels` / `tags`（`tcms-fetch-cases` 已輸出，兩欄都可能帶、要一起看）。**不要只照 case 內文寫成單一平台。**

1. **解析平台 token**（大小寫容錯）：`Web→web`、`mWeb`/`Mweb→mweb`、`Android→android`、`iOS→ios`。拆包裝與展開：
   - `FE (Web/mWeb/Android/iOS)`、`Platform / Service:FE (...)` → 取括號內全部平台
   - `Web (Web/mWeb)` → `{web, mweb}`；`["Android","iOS","mWeb","Web"]`（拆開列）→ 全取
2. **step 內的平台標記要切分**：case 步驟常在 action 文字帶標記，同一 case 對不同平台有不同操作與 expected_result（如 KQT-T37935 有 `[APP]`/`[M]`/`[PC]` 各自不同）。對映 `[PC]→web`、`[M]→mweb`、`[APP]→native app`（可能再細分 `[iOS]`/`[Android]`）。**每個平台的 auto case 只取「該平台適用」的步驟與 expected_result**；無標記的步驟視為所有平台共用。標記對不上/看不懂 → 當成待確認點（見 4）。
3. 確認平台後，**每個平台各寫一份 auto case**（web/mweb 走 `web_playwright/`；App 走 `mobile/` + `test_steps/kkday/app/`），逐一跑後面的規劃 → 驗證 → 執行。
4. **subagent 不自己拍板、也不 hang、也不直接問人**。碰到需判斷的點（label 混 API 如 `web/API`、多平台是否這輪全做、平台標記對不上、缺 oid 等），一律：**能安全帶預設就帶入並記錄假設**（預設：label 標的所有 UI 平台、環境 stage…），**回報主對話**（候選平台集合／步驟切分結果／已帶入的假設／真正卡住需輸入的點）。真的無法進行的（如缺 oid 又推不出）→ 該平台/該 case 標 `blocked`＋原因、跳過續跑，不 hang。

> **「要不要問使用者」是主 agent 的職責，不是 subagent。** subagent 永遠只「帶預設 + 記錄假設 + 回報待確認點」。主 agent 依模式決定：**互動模式**→ 把待確認點問使用者（這輪做哪些平台？web/API 做哪個？）；**自主/harness 模式**→ 直接套預設繼續、blocked 的排入待人工佇列，全程不停等輸入。

### 階段 1 — 規劃草擬（把整個 case 想完再進下一階段）

讀 case steps，規劃需要哪些 page object element / test step / case data，依下方各項規範草擬。此階段**允許先依經驗寫初版 locator**。
**必須一次把整個 case 規劃/草擬完成**，不要邊寫一個 element 就驗一個——驗證是下一階段「批次」做。

### 階段 2 — 元素驗證（強制、批次）

**Preflight（全自動 bootstrap，不依賴使用者、不詢問，缺什麼補什麼）：**
每次進入驗證前，AI 都要自己把「工具」和「目標裝置」準備好，不要假設使用者已經裝好或開好。這是無條件執行的步驟。

**① 工具：沒裝就自動裝**

| 平台 | 檢查 | 沒裝就自動裝（不問直接跑） |
| --- | --- | --- |
| Web/MWeb | `claude mcp list \| grep -i playwright` | `claude mcp add playwright -- npx @playwright/mcp@latest` |
| Android | `which adb` | `brew install android-platform-tools` |
| iOS | `which idb` | `brew install idb-companion && python3 -m pip install fb-idb` |

**② 目標裝置：沒在線就自動拉起**

| 平台 | 檢查 | 沒有就自動開 |
| --- | --- | --- |
| Web/MWeb | playwright MCP 本身自帶 browser，免裝置 | — |
| Android | `adb devices` 有裝置 | `emulator -list-avds` 取一個 AVD → `emulator -avd <name> -no-snapshot -no-boot-anim &`，再 `adb wait-for-device` |
| iOS | `xcrun simctl list devices booted` 有 booted | `xcrun simctl boot <udid>`（取 `xcrun simctl list devices available` 第一個）→ `open -a Simulator` |

bootstrap 完成後驗可用性（MCP connected / `adb devices` / `idb list-targets` 有 target）。**只有在自動安裝或自動開機都失敗時才停下回報**，並說明卡在哪一步。

工具與裝置就緒後，把「所有新增/修改的 locator」一次列出，逐一對照**真實元素樹**驗證與修正：

- **Web（playwright MCP）**
  1. `browser_navigate` → `https://www.stage.kkday.com/...`（**禁用** `www.kkday.com`）
  2. `browser_snapshot` 取 accessibility tree（或 `browser_evaluate` 跑 `document.querySelector(...)` 驗證命中）
  3. 比對草擬 locator 與實際 DOM，改成能**唯一命中**的 css/xpath

- **MWeb（playwright MCP）— 必須用手機 device profile，不能只縮 viewport**
  kkday 是靠 **User-Agent**（＋`isMobile`/`hasTouch`）決定回 web 還是 mweb DOM，**不是看 viewport**。所以只 `browser_resize`／只設 `--viewport` 仍是桌面 UA → server 回的是 **web 頁**，你就驗到錯的頁。
  1. **先確認 MCP 帶手機 UA**：`browser_evaluate` → `() => navigator.userAgent`，要看到 iPhone/Mobile UA；若是桌面 Chrome UA，代表 MCP 沒設 device profile，**停下**先設定，別在錯的頁上驗 locator。
  2. **設定方式**：Playwright MCP server 啟動要帶行動裝置設定 —— 首選 `--device "iPhone 15"`（＝框架 mweb 用的同一台，見 `QATest/src/lib/fixtures/playwright.py:90` `devices['iPhone 15']`）；或 `--user-agent "<iPhone UA>"` + viewport `375×667`（同檔 :96-97 fallback）。一個 MCP server = 一個 profile，**無法在同一 session 靠 resize 在 web/mweb 間切**；驗 mweb 就用 device=iPhone 15 的 MCP。
  3. UA 確認為手機後，再 `browser_navigate` → stage → `browser_snapshot`/`browser_evaluate` 比對 mweb DOM（mweb 的 class 常與 web 不同，勿照搬 web locator）。

- **App / Android（adb dump uiautomator tree）**
  ```bash
  adb shell uiautomator dump /sdcard/ui.xml && adb pull /sdcard/ui.xml /tmp/android_ui.xml
  ```
  解析 `/tmp/android_ui.xml` 的 hierarchy，優先用 `resource-id` → `content-desc` → `text` 找真實 locator。
  前提：`adb devices` 要有裝置在線。

- **App / iOS（idb dump accessibility tree）**
  ```bash
  idb ui describe-all --json > /tmp/ios_ui.json    # 需 booted 模擬器/實機 + idb companion
  ```
  解析 tree，優先用 `AXIdentifier`（= accessibility id / name）→ `AXLabel` → `type` 找真實 locator。
  前提：`xcrun simctl list devices booted` 有 booted 裝置。

> **抓不到就停** — 對應平台的元素樹拿不到（MCP 未連、Web 進不去頁面、`adb`/`idb` 無裝置在線），**停下回報使用者**，不得憑 case 文字臆測 locator 定稿。

### 階段 3 — 自動執行

locator 驗證修正後，**自動跑一次測試**確認（走 qa-test-runner，或直接 `python -m qatest run`）：
- Web/MWeb：`export HEADLESS=1 && source <venv> && python -m qatest run --caseid KQT-Txxxx --platform web --use_driver playwright`
- App：`python -m qatest run --caseid KQT-Txxxx --platform android`（或 `ios`）

失敗時交給 **qa-test-runner** 的診斷/修復流程（它同樣會用上述元素樹抓取來修 locator）。

### 階段 4 — 產出 step→assertion 可追溯表（供忠實度 review）

**跑過 ≠ 有測對 case。** 定稿時必須產出一張**可追溯表**，把 TCMS case 的每個 step / expected_result 對到實作中的斷言，供 `qa-case-fidelity-reviewer` 比對（也逼自己確認每個 expected 都真的有斷言）。

- 每個 **expected_result 至少對到一個真斷言**（引用到 case 要驗的那個值/元素，非恆真、非「頁面有載入」這種弱檢查）。
- 多平台逐平台各一張（含 `[PC]/[M]/[APP]/[iOS]/[Android]` 步驟切分）。
- 格式範例（放在回報，或 test step docstring）：

  | TCMS step / expected | 實作斷言（file:line） |
  | --- | --- |
  | step2 expected「顯示原價無劃線」 | `category_page.py:142` `assert_that(...strikethrough is None...)` |
  | step4 expected「折扣再現」 | `category_page.py:151` `assert_that(...has_discount...)` |

- **對不到斷言的 expected_result 一律列出**（不要藏），交給 fidelity reviewer / 主對話判。

---

## Page Object 規範

適用於 `pages/` 目錄下的所有檔案。

- 類別命名必須 PascalCase 且以 `Page` 結尾（如 `ShoppingPage`）
- 元素必須用 `@property` + 回傳 `Element` 或 `Elements`，並標註型別 `-> Element`
- 元素命名後綴：`_button`（可點擊）、`_text`/`_input`（輸入欄位）、`_label`（靜態文字）、`_dropdown`（下拉選單）
- Locator 格式為 tuple：`("xpath", "...")` 或 `("css", "...")`
- XPath 優先用 `resource-id`、`data-testid`、`name` 等穩定屬性；禁止用絕對路徑（`/html/body/div[1]/...`）
- 動態元素用 f-string 參數化：`def item_by_name(self, name: str) -> Element`
- pages 檔案**只能定義** `@property` + `Element`/`Elements` 回傳，不能包含業務邏輯、迴圈、判斷或直接呼叫 driver 方法

### Mobile (Appium)

- 必須有 abstract base（`mobile/base/`）+ 具體實作（`android/`、`ios/`），改一邊要確認另一邊
- 定義新元件前先確認 base 是否已有相同元件，避免重複定義
- 元件文字建議用 `t('key', locale=AppConfig.language)` 取多語言，避免寫死中文

### Web/MWeb (Playwright)

- Playwright（`web_playwright/`）element 傳入 `self.driver`：`Element(('css', '...'), self.driver)`
- Selenium/Appium（`mobile/`、`web/`）element 傳入 `self`：`Element(('xpath', '...'), self)`。根據檔案路徑判斷，不要搞混
- Web ↔ MWeb 共用 test step，改一邊的 page object 時要確認另一邊是否需同步
- `__init__.py` 規則：若 `self.xxx = Xxx(driver)` 放在共用區（if/else 外面），則 MWEB 和 Web 兩邊的 import 區塊都必須有對應的 import，否則另一平台會 NameError。若只有一邊有，須放在 `if platform != Platform.MWEB:` 保護下

---

## Test Step 規範

適用於 `test_steps/` 目錄下的所有檔案。

### 必要結構

- 所有函式必須加 `@function_recorder()` 裝飾器
- 參數必須有 type hint（`pages: Pages`、`testcase: TestCase` 等），return 也必須標註型別（如 `-> None`）
- `pages`、`uidriver`、`test_run_config`、`testcase`、`api_request`、`api_response` 等參數由框架 fixture 自動注入，呼叫時不需手動傳入
- Docstring 使用 Google style + 雙引號，包含 Args 和 Returns 區塊

### 命名

- Function 命名用 snake_case，禁止使用數字或中文（專有名詞除外），禁止無意義命名（如 abc/aaa/xyz）
- Playwright function 命名必須以 `_playwright` 為後綴，避免與 Selenium 方法重名

### 操作規範

- 互動前必須呼叫 `.wait()`：`pages.page.element.wait().click()`；禁止直接 `.click()` 不加 wait
- 禁止用變數暫存 page object（如 `page = pages.xxx_page`），必須每次完整寫 `pages.xxx_page.element`
- **禁止在 test_step 內 inline 建構 `Element(...)` / `Elements(...)`**：所有 locator 一律定義在對應 page object 的 `@property`，test_step 只透過 `pages.<page>.<element>` 取用（取 `.center`、`.text`、`.wait()` 等也一樣，先在 page object 定義好 element）。
  ```python
  # ❌ 錯：locator 寫死在 test_step、繞過 page object
  center = Element(("accessibility id", "homeTxtSearch"), pages.home_page).wait().center
  # ✅ 對：element 在 page object 定義，test_step 只取用
  #   pages/.../home_page.py:  @property def search_bar(self)->Element: return Element(("accessibility id","homeTxtSearch"), self)
  center = pages.home_page.search_bar.wait().center
  ```
- 禁止用 `time.sleep()` 或 `driver.page.wait_for_timeout()` 做硬等待，若需硬等待請用 `common.sleep_by_seconds()` 搭配 `TimeoutConstants`
- 斷言必須用 hamcrest：`assert_that(actual, equal_to(expected))`
- 測試資料必須從 `testcase.static_test_data` 或 `testcase.dynamic_test_data` 取得，禁止硬編碼
- iOS/Android 共用同一個 test step 檔案，流程內須用 `match TestRunConfig.platform` 做平台判斷
- 禁止在同一個 function 中混用 Playwright 和 Selenium 寫法

### 禁止直接呼叫底層 driver

除了 `playwright_element.py` / `playwright_elements.py` 以外，**所有檔案**禁止任何 `driver.page.*`、`self.driver.page.*`、`uidriver.page.*` 的直接呼叫。詳細禁令清單與允許例外見 [references/driver-call-rules.md](references/driver-call-rules.md)。

---

## API 測試規範

適用於 `test_steps/api/` 和 `case_data/` 目錄下的檔案。

### API Test Step

- 必須加 `@function_recorder()` 裝飾器
- 參數必須有 type hint：`testcase: 'TestCase'`、`api_request: ApiRequest`，return 標註型別
- 必須遵循固定流程：`ApiCore.initial_api()` → `ApiCore.send_request()` → 驗證 → `ApiCore.deinitial_api()`
- `initial_api()` 的 session 參數只能用 `unused`、`new`、`inherit` 三個值
- `deinitial_api()` 必須在 finally 區塊中呼叫，確保資源釋放
- 至少呼叫 `check_api_response_code()` + `check_api_response_text()` 做回應驗證
- 動態替換 payload 建議先 `testcase.update_dynamic_test_data()` 再 `assign_data_to_api_request()`，也允許直接修改 `api_request.payload` 或 `api_request.headers`
- URL 動態替換必須用 `ApiHelper.substitute_url()`，禁止手動字串拼接
- 禁止硬編碼 URL、帳密或測試資料，應使用 JSON 資料檔的 placeholder
- 認證 token 必須從 `testcase.dynamic_test_data` 取得，不可寫死
- Pages function 應專注做一件事（單一職責），test_steps 負責組合流程

### JSON 資料檔

- 必須有 `$env` 根節點，包含 `api_collection` 和 `test_data`
- 每個 collection 必須有：`url`、`headers`、`payload`、`response_status`、`response_text`
- 動態值必須用 placeholder：`str_to_be_replaced`、`int_to_be_replaced`、`bool_to_be_replaced`、`list_int_to_be_replaced`
- URL 中的環境變數必須用 `$env` 佔位（如 `https://api-gateway.$env.kkday.com/...`）
- `response_schema` 建議加上 JSON Schema 驗證

---

## 通用 Coding Style

縮排、import 排序、命名、pre-commit 等通用規範見 [references/coding-style.md](references/coding-style.md) 與 [Confluence Coding Style](https://kkday.atlassian.net/wiki/spaces/QS/pages/473661593/Coding+Style)。

## 發 PR

用戶要求發 PR 時，必須：
- 跑 `pre-commit run --all-files`
- PR body 套 repo `.github/pull_request_template.md` 五段式範本（**不可**用 `## Summary` + `## Test plan` 簡化格式）：

  ```
  ## Description
  <簡單敘述這個 PR 在幹嘛>

  ## Changes Made
  ### <相對檔案路徑>
  - <因為什麼目的而改>

  ## Testing
  - ✅ KQT-Txxxxx mweb/web/ios/android Pass
  - ✅ pre-commit run --all-files 全 pass

  ## Related Issues
  <修了什麼 Bug or 為了哪些 case 而改>

  ## Checklist
  - [ ] 有改到底層邏輯，請記得加好Unit Test
  - [x] 請指派相對應的Code reviewer
  - [x] 此branch記得先與dev merge過一次 => git merge origin/master

  ---
  **註：** <額外提醒 AI Reviewer 的政策說明，例如 XPath union 保留舊段>

  🤖 Generated with [Claude Code](https://claude.com/claude-code)
  ```

- 指派 reviewer：`angelalin0822,ericsukkday,ethan02872`（若用戶或 PR template 帶入 `Lance-Liu-KKday` 需移除）

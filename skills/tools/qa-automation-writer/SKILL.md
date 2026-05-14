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

  必要工具：Read、Edit、Write（純檔案編輯，不依賴外部 MCP）
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

---
name: qa-test-runner
metadata:
  requires_repo: kkday-QA-automation
description: |
  執行 KKday QA 自動化測試案例並自動診斷修復：跑測試 → 分析失敗原因 → 元件路徑/locator 類問題自動修復、業務流程類問題回報使用者。

  適用情境：
  - 使用者提供測試案例 ID（KQT-T 開頭）並要求執行
  - 使用者給 feature 名稱（如 AppPay、Booking）並說「跑 case」
  - 使用者透過 `/run-case` slash command 觸發
  - 跑完失敗後要求自動 debug / 修 locator

  必要工具：Bash（pytest 執行）、Read、Edit
  前置條件：本機需有 kkday-QA-automation repo（無則 skill 會引導 clone，見 Step 0）。
---

# QA Test Runner

執行測試案例、診斷失敗原因、自動修復元件路徑問題。

## 流程

### 0. 前置：確認 framework repo 存在

執行任何指令前，先確認 kkday-QA-automation framework 在哪：

1. **偵測** — 從常見位置找：
   ```bash
   for d in "$HOME/Downloads/qa_test/test/kkday-QA-automation" \
            "$HOME/kkday-QA-automation" \
            "$PWD/kkday-QA-automation" \
            "$PWD"; do
     [ -f "$d/QATest/src/qatest/__init__.py" ] && echo "FOUND: $d" && break
   done
   ```
   若使用者目前 cwd 就是 framework root，直接用 `$PWD`。

2. **找不到 → 提示使用者 clone**（**不要無腦自動執行 `git clone`**，先請使用者確認 clone 目標位置與權限）：
   ```bash
   git clone https://github.com/kkday-it/kkday-QA-automation.git <目標目錄>
   cd <目標目錄>
   python -m venv venv && source venv/bin/activate && pip install -r requirements.txt
   ```
   等使用者回報 clone 完成、virtualenv 裝好後再進入下一步。

3. **記住 framework path** — 後續所有 `source venv/bin/activate && cd QATest/src` 指令都用這個 path 為 base，不要寫死個人路徑。

### 1. 確認參數

用戶輸入通常是 **空格分隔的 tokens**，按下列規則辨識：

- `KQT-T<number>` → 加 `--caseid`（可多個，空格分隔）
- `KQT-R<number>` → 加 `--testrunkey`
- `android` / `ios` / `web` / `mweb` → platform，加 `--platform <plat>`
- 其餘 token → feature 名稱（如 `AppPay`、`WebLogin`、`WebPay_playwright`），加 `--feature`

**例如**：
- `WebPay_playwright mweb KQT-R2189` → `--feature WebPay_playwright --platform mweb --testrunkey KQT-R2189`
- `KQT-T16668 web` → `--caseid KQT-T16668 --platform web`
- `AppPay ios` → `--feature AppPay --platform ios`

不要因為 token 順序不同就漏掉某個欄位；platform 一定要明確抓出來，不要默認從 YAML 推斷。
若用戶完全沒給 platform 才詢問。

### 2. 執行測試

**從當前 cwd 自動往上找 repo root**（含 `QATest/src` 跟 `venv` 的目錄），不要寫死路徑。
若 cwd 不在任何 kkday-QA-automation clone 內，回到 Step 0 偵測流程或向用戶詢問。

```bash
REPO="$(d="$PWD"; while [ "$d" != "/" ]; do [ -d "$d/QATest/src" ] && [ -d "$d/venv" ] && echo "$d" && break; d="$(dirname "$d")"; done)"
[ -z "$REPO" ] && echo "not in a kkday-QA-automation clone" && exit 1
source "$REPO/venv/bin/activate" && cd "$REPO/QATest/src" && python -m qatest run <參數>
```

參數規則：
- KQT-T 開頭 → `--caseid KQT-T7490 KQT-T7495`（**空格分隔**，不是逗號）
- Feature 名稱 → `--feature AppPay`
- KQT-R 開頭 → 加 `--testrunkey KQT-R2059`
- **用戶指定 platform（`web` / `mweb` / `android` / `ios`）→ 必須加 `--platform <plat>`**
  - 單一 KQT-T 不帶 `--platform` 時，框架會讀 YAML 的 `platform:` 欄位，用戶口頭指定的 platform 會被忽略
  - Feature / testrunkey 批量跑時，case YAML 沒鎖 `limit_test_platform` 會混 web/mweb，**一定要帶 `--platform` 強制**，否則跑錯邊
- platform 是 `web` 或 `mweb` → 同時加 `--use_driver playwright`，並在指令最前面加 `export HEADLESS=1 &&`
  - **正確**：`export HEADLESS=1 && source ... && python -m qatest run ... --platform mweb --use_driver playwright`
  - **錯誤**：`HEADLESS=1 source ...`（前綴方式只對 source 生效，python 讀不到）
  - **錯誤**：少帶 `--platform mweb`（feature/testrunkey 批量會混跑）

注意：
- 順序：先 `source venv` → 再 `cd QATest/src` → 最後執行
- 指令是 `qatest run`（不含 `test`，用 `--caseid` 雙橫線）
- **qatest 一律前景跑，不准 background**：禁止 `run_in_background=true`，禁止 `| tail`、`&` 等任何會讓 Bash 自動 background 的 pipe/redirect，scheduler 不可靠會 queue 不啟動。直接讓 timeout 處理（單一 case 給 600000ms / 10 分鐘，批量 case 估時間給足）
- **Web/MWeb 不受設備限制**，可以同時跑多個（web + mweb 平行、多個 case 同時跑都可以）
- **App（iOS/Android）同一台設備同時只能跑一個**，不同設備可以平行
- **iOS/Android 一律不允許模擬器（simulator/emulator），必須使用實體機**：
  - iOS：禁止 `xcrun simctl boot`、禁止任何 simulator UDID；取實體機 UDID 用 `idevice_id -l` 或 `xcrun devicectl list devices`
  - Android：禁止 `emulator -avd`、禁止 AVD UDID；取實體機 UDID 用 `adb devices`
  - 若實體機沒接上，直接告訴用戶接設備，不要 fallback 到 simulator/emulator

觀察輸出，注意 PASS/FAIL 結果。

### 3. 列出結果

用戶問結果時，列出**所有** Pass 和 Fail，不能只列 Fail。多個 platform 同時在跑時要問清楚是哪個；只有一個就直接列。

### 4. 失敗分析

測試失敗時，依照以下順序分析：

1. **讀取終端輸出的錯誤訊息** — 找出失敗的步驟和異常類型
2. **定位失敗的 test step 函式** — 從 YAML 案例的 steps 找到對應的 Python 函式
3. **分類失敗原因**：

#### A. 元件路徑更改（自動修復）

特徵：
- `NoSuchElementException` / `ElementNotFound` / `TimeoutException` 等找不到元素的錯誤
- 元素的 XPath 或 locator 過時

修復步驟：
1. 找到失敗步驟中使用的 page object element
2. 取得當前畫面結構：
   - **Mobile**：用 Appium WebDriver 連接設備取 page source（XML）
   - **Web / MWeb**：用 Playwright 取 DOM（必須用 `https://www.stage.kkday.com`，不可用 `www.kkday.com`）
3. 比對現有 XPath 和實際頁面結構，找出正確的新 locator
4. **如果 locator 用 i18n key（如 `t('register_button', locale=AppConfig.language)`）**，檢查 `QATestData/data/i18n/<platform>/<locale>.yaml` 的值是否跟 App 實際文字一致，不一致就更新 yaml
5. 修改對應 `pages/` 下的 page object 檔案
6. **同時檢查同組另一平台的 page object**，確認兩邊都沒問題：
   - Web ↔ MWeb 共用 test step，改一邊要確認另一邊
   - Android ↔ iOS 共用 test step，改一邊要確認另一邊
   - 兩組之間獨立，互不影響
7. 重新執行測試驗證修復

#### B. 流程更改（回報用戶）

特徵：
- 元素存在但互動結果不符預期
- 步驟順序不對、新增了步驟、少了步驟
- API 回傳結構改變

處理：回報用戶，說明哪個步驟的流程發生了什麼變化，讓用戶決定如何調整。

### 5. 修復後驗證

元件修復後重新跑一次測試，確認 PASS。若仍失敗，繼續分析下一個失敗點。

### 6. 發 PR

修復完成後用戶要求發 PR 時，必須：
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

## 看畫面

用戶說「看畫面」時，自動執行以下步驟截取 iOS 設備畫面：

1. Kill 佔用 3080 port 的 process：`lsof -ti:3080 | xargs kill -9`
2. 啟動 Appium server：`appium -p 3080 --use-drivers=xcuitest &`，等 8 秒
3. 取得設備 UDID：`idevice_id -l | head -1`
4. 用 Appium 連接截圖：

```python
from appium import webdriver
from appium.options.ios import XCUITestOptions

options = XCUITestOptions()
options.platform_name = "iOS"
options.udid = "<device_udid>"
options.no_reset = True
options.new_command_timeout = 180

driver = webdriver.Remote("http://localhost:3080", options=options)
driver.get_screenshot_as_file("/tmp/ios_current.png")
driver.quit()
```

5. 用 Read 工具顯示截圖
6. 用完後 kill 3080 port

## Appium 連接

取得 Android/iOS 頁面結構用 Appium：

```python
from appium import webdriver
from appium.options.android import UiAutomator2Options

options = UiAutomator2Options()
options.platform_name = "Android"
options.udid = "<device_udid>"  # 用 adb devices 取得
options.no_reset = True
options.new_command_timeout = 180

driver = webdriver.Remote("http://localhost:<port>", options=options)
page_source = driver.page_source  # XML
driver.quit()
```

Port 規則：
- iOS 預設 port：3080
- Android 預設 port：10000
- 用戶可能會指定其他 port
- 如果連線失敗且確認 port 正確，先用以下指令啟動 Appium server 再重試：
  ```bash
  appium -p <port> --use-drivers=xcuitest,uiautomator2 &
  ```

## 參考

專案結構與 Element 定義模式詳見 [project-structure.md](references/project-structure.md)。

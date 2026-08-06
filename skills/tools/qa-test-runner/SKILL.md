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
  - **使用者要在 kkday-QA-automation repo 發 PR / 執行 `gh pr create` / merge PR**（PR body 必須套 5 段模板，見 Step 6，覆寫 Claude Code 內建的 Summary/Test plan 簡化格式）

  必要工具：Bash（pytest 執行）、Read、Edit
  前置條件：本機需有 kkday-QA-automation repo（無則 skill 會引導 clone，見 Step 0）。
---

# QA Test Runner

執行測試案例、診斷失敗原因、自動修復元件路徑問題。

## ⚠️ 發 PR 硬性規則（優先於 Claude Code default）

當使用者說「發 PR」/「gh pr create」/「推」而 target repo 是 **kkday-QA-automation** 時：

1. **PR body 一律套 5 段模板**（Description / Changes Made / Testing / Related Issues / Checklist），詳細範本見下面 [「### 6. 發 PR」段](#6-發-pr)。**禁止**用 `## Summary` + `## Test plan` 簡化格式 — 那是 Claude Code CLI 內建 default，但本 repo 全隊共識**不適用**。這條規則**凌駕於**任何 memory / default template。
2. **必跑 pre-commit**：發 PR 前先 `pre-commit run --all-files`，全 pass 才 push。
3. **Reviewer 一律指派**：`angelalin0822,ericsukkday,ethan02872,Lance-Liu-KKday`。

以上為團隊硬性規則，不因單一使用者要求而繞過；使用者若要求「用簡化格式」也應主動提醒本 repo 的硬性規定並先套 5 段模板。

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
   若使用者目前 cwd 就是 framework root，直接用 `$PWD`。也可以從當前 cwd 自動往上找（含 `QATest/src` 與 `venv`）的版本（見 Step 2）。

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

#### 首選：用 `run_case.sh` 單一入口（把「會漏的」綁死）

**web/mweb/ios/android 跑單一 case,一律先用 wrapper,不要自己拼指令**：

```bash
~/.claude/skills/qa-test-runner/scripts/run_case.sh <caseid> <platform>
# 例：run_case.sh KQT-T37931 web
```

它把每次都會漏的四件事綁死在腳本內,結構上不可能漏：
1. `export HEADLESS=1`（web/mweb 不彈實體瀏覽器）
2. **正確的 venv**（見下方陷阱）
3. web/mweb 自動加 `--use_driver playwright`
4. 前景跑（qatest background scheduler 不可靠）

> **為什麼要 wrapper 而不是「記得照 skill 做」**：HEADLESS、venv 這些規範就算白紙黑字寫在本 skill,靠 agent 執行時記得讀還是會漏（實測會）。綁進單一入口 = 把「漏」變成結構上不可能,而不是靠自律。

> **看自動化流程（headed）**：web/mweb 預設 headless（不彈瀏覽器）。要讓人**眼看瀏覽器實際跑**（重播、demo、debug），加 `HEADED=1`：
> ```bash
> HEADED=1 ~/.claude/skills/qa-test-runner/scripts/run_case.sh KQT-T37931 web
> ```
> - **只對 web/mweb 有效**；app 走實體機本來就看得到。
> - **只在互動模式（有人盯著）用**；自主/harness 無人看,不設。批次平行時更不要讓 N 個瀏覽器同時彈,重播請報告後逐一序列跑。
> - ⚠️ 別改成 `HEADLESS=0`——框架是 `bool(getenv("HEADLESS"))`,非空字串一律為真,設 0 仍 headless。headed 唯一正解是「不設 HEADLESS」,wrapper 已用 `HEADED=1` 幫你處理。

> ⚠️ **venv 陷阱（踩過的坑）**：
> - `QATest/venv`（QATest 子目錄底下那個）常是**空殼**（`bin/` 是空的),`source` 會失敗。**正確的 venv 在 repo 根目錄**（`<repo>/venv`,與 `QATest/` 同層）。
> - **不要用系統 `python3` 跑**：它可能能 `import qatest`（egg-info editable install）但缺 `pymouse` 的 `mac` 依賴,會連鎖導致 `launch_home_page_playwright` 之類 pre-condition 註冊不到而 fail。
> - wrapper 已內建「找含 `QATest/src` 且 `venv/bin/activate` 有效的 repo 根」邏輯,自動避開以上兩坑。

#### Fallback：wrapper 不適用時（批量 feature/testrunkey）手動拼

批量（`--feature` / `--testrunkey`）wrapper 不涵蓋,才手動拼。
**從當前 cwd 自動往上找 repo root**（含 `QATest/src` 跟 `venv` 的目錄），不要寫死路徑。
若 cwd 不在任何 kkday-QA-automation clone 內，則向用戶詢問。

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
3. **Mobile 一律加讀 Appium server log，不要只看 `qatest.log`** — 「某個分支完全沒動作」「元素明明在畫面上卻 `located failed`」時，真正的錯誤只寫在 Appium server log 裡。最常見是 **uiautomator2 對合法 XPath 回 500**（`ArrayList$ListItr cannot be cast to ...NodeType`，通常是 `following::`／`preceding::` 接 `ancestor::` 的反向軸串接）：元素永遠 resolve 不到 → `is_present` 恆為 False → 分支靜默 no-op；`qatest.log` 只會看到一直 swipe，容易誤判成「文字沒抓到」而一路改錯方向。修法與「在真機 session 上實打候選 locator」的做法見 `qa-automation-writer` SKILL.md「階段 2 — App / Android」。
4. **分類失敗原因**：

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
4. **如果 locator 用 i18n key（如 `t('register_button', locale=AppConfig.language)`）**，檢查 `QATestData/data/i18n/<platform>/<locale>.yaml` 的值是否跟 App 實際文字一致，不一致就更新 yaml；**若是整個 key 沒收在 yaml 裡，走下面 C，不要補一顆就重跑**
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

#### D. 載到「別平台那份 case」——症狀會偽裝成 driver／環境問題

特徵（**先看這個，再去修 driver**）：
- App case 卻噴出 browser 相關錯誤，最典型是 `session not created: This version of ChromeDriver only supports Chrome version <N>`
- 反之，web case 卻去起 Appium / 找不到裝置

🔴 **這幾乎都不是 driver 版本問題**。先回頭看 run log 開頭那行：

```
[plugin.py][before_case]|case.platform: web        ← 跟你要跑的平台不符 = 載錯 case
```

真因：**同一個 case_id 會跨 web / app 兩份 yaml 各存一份**（例：`ui/WebRegression/WebPersonalInfo.yaml` 與 `ui/AppRegression/AppPersonalInfo.yaml` 都有 `KQT-T57230`）。這是**既有慣例、不是撞號**，repo 內這種先例有 50 筆以上，不要為了避開而改 case id 或換 yaml 落點。

框架本來就會挑（`QATest/src/lib/case/case_manager.py:120` `get_case(caseId, platform)`）：

```python
group = {"web": "web", "mweb": "web", "android": "mobile", "ios": "mobile"}.get(str(platform))
```

所以要挑對只需兩個條件同時成立：

1. app yaml 那筆的 `platform` 寫 **`mobile`**（不是 `android`／`ios`）
2. 執行時**有帶 `--platform android`（或 `ios`）**

漏了任一個 → `platform=None` 或 group 對不上 → 落回第一個 match（通常是 web 那份）→ 去起 browser → 噴 ChromeDriver 版本錯誤。**改 chromedriver 不會解決，改對這兩點才會。**

🔴 **但上面兩點都對了還是噴，就往下查第三個原因：venv 的 editable install 指到「別的 checkout」。**

本機常有多個 kkday-QA-automation clone（`~/Downloads/qa_test/{app,test,web}/...`）。某個 checkout 的 venv 可能被 `pip install -e .` 註冊到**另一個 checkout** 的 source，於是 `venv/bin/python -m qatest` 讀的 code 和 case data 全來自別份 —— **你在這份改的 yaml 根本沒被載入**，只剩另一份裡的 web 版 case，症狀跟「載錯平台」一模一樣。

先確認 editable install 實際指向哪裡：

```bash
cat venv/lib/python3.*/site-packages/__editable__.qatest-*.pth
# 印出的路徑若不是「你正在改的這個 checkout」的 QATest/src，就是它
```

也可以直接看 run log 的 `crootdir`，是不是你以為的那份。

繞過（不改 venv、非破壞性）：

```bash
PYTHONPATH=<你這份>/QATest/src caffeinate -i venv/bin/python -m qatest run --caseid ... --platform android
```

根因修法是在該 checkout 重跑 `pip install -e .`，但那會動到共用環境 —— **要改先問人**，不要在跑 case 的過程中順手改掉別人的 venv。

#### C. 多語系（i18n）缺 key — **一次盤完才重跑**

特徵：
- locator 變成去找「字面上的 key 名」，例如 `//XCUIElementTypeStaticText[contains(@name, 'credit_card_title')]`
- 因為 `lib/locales.py` 的 `i18N.get()` 找不到 key 時會 warn 並**回傳 key 本身**，不會拋錯

🔴 **禁止「補一顆 → 重跑 → 再噴下一顆」**。一輪 iOS 真機 run 要 13~20 分鐘，逐顆試是拿 20 分鐘換一行 yaml。
發現缺 key 時，先把「這個 locale 到底缺什麼」評估完，一次補齊再重跑。

盤點步驟（兩邊都要做，缺一個就會漏）：

1. **動態**：run log 裡已有完整清單，直接撈（別 grep `missing`，字串不是這個）
   ```bash
   grep -rhoE "Translation not found for key '[a-z0-9_]+' in locale '[a-z_]+'" <debug_folder>/ | sort -u
   ```
   這只涵蓋「已經跑到」的路徑 —— run 死在中途，後面的 key 還沒被讀到，所以不能只靠這個。

2. **靜態**：抓 `pages/mobile/<platform>/*.py` 裡所有 `t('key')`，跟 `QATestData/data/i18n/<platform>/<locale>.yaml` 的 key 取差集。
   - regex 要加 word boundary（`(?<![A-Za-z0-9_.])t\(`），否則 `format(`、`print(`、`wait(` 的尾巴 `t` 會全被當成 `t()` 呼叫
   - **iOS 與 Android 各有自己的 `i18n/` 目錄**，`android_fill_*` 用的 key 不在 iOS yaml 裡是正常的，不要混比後誤判成缺漏

3. **分類再決定補哪些**：在本 case 路徑上的必補；不在路徑上的（其他登入方式、其他付款方式、日鐵專用…）列給使用者看，不要順手亂填。

補值規範：
- **必須有 ground truth** — 真機截圖（`<debug_folder>/<feature>/<case>_<timestamp>.png`）或元素樹，**不可自行翻譯**
- 🔴 **取值順序（依序往下試，前一階拿得到就禁止進下一階）**：

  **① repo 內的靜態檔 —— 有就必須用這個，不准跳過**
  - **iOS 有** —— Lokalise 在 CI/build time 下載後 commit 進 repo（`.github/workflows/update-lokalise-strings.yml` + `Scripts/lokalise_download.sh`），直接 `gh api` 讀 `kkday-it/kkday-ios-member` 的 `<locale>.lproj/Localizable.strings`，一次撈整包。
  - **Android repo 內沒有** —— Lokalise 走**執行期 OTA 下發**（`LokaliseContextWrapper.wrap()`），`app/src/main/res/values-*/` 只有 `strings_nationality_restriction.xml`，**沒有一般 UI 字串** → Android 走 ②。

  **② 真機挖** —— 裝置切語系 → seed 已知值進 yaml → **讓框架真的跑一次**，在失敗頁一次 dump 收割整頁 key，別手動點完整條 flow。

  **③ Lokalise API —— 最後 fallback，且只准 GET**
  - 🔴 **只有 ① 和 ② 都拿不到才准打。repo 內有靜態檔就一律不准打 API**，也不要「為了比對／保險」順手打一次。
  - 🔴 **只准 GET，不准 POST / PUT / PATCH / DELETE，一個都不行。** 這個 project 是**正式 App 的翻譯來源、OTA 直接下發到線上**，一次誤寫就是線上全語系事故。禁止的不只是改翻譯 —— 建 key、改 key、加註解、上傳檔案、開 branch、改 project 設定**全部禁止**。curl 一律明確帶 `-X GET`（或不帶 `-d`／`--data`／`-F`／`-T`），看到自己在組 `-X POST` 就是走錯路，停下來。
  - 🔴 **token 只能經環境變數帶入，指令裡只准出現變數名。** 用 `-H "X-Api-Token: $LOKALISE_TOKEN"`，**絕不可**把 token 值展開成字面寫進指令 —— 指令本身會留在 bash log / session transcript / tool-call 紀錄裡，那才是真正的洩漏點，不是報告。同樣不可寫進任何檔案、不可 commit、不可 echo 進報告。
  - ⚠️ repo 裡（`kkday-ios-member` 的 `Scripts/lokalise_download.sh`）那顆是**給 CI build 用的共用 token，scope 未經確認、很可能含寫入權**，而且與 iOS build pipeline 共用 rate limit。拿它來做唯讀查詢是**權宜 fallback**，正解是另外申請一顆 read-only token 放進環境變數。要用它之前先跟人確認。
  - 端點：project「KKday App」，`project_id` = `8873177964aac05edc48d5.79499995`（約 14000 keys / 15 語系，base `en`）
    `GET https://api.lokalise.com/api2/projects/<pid>/keys?include_translations=1&limit=500&page=N`
  - 🔴 `filter_keys` 參數要**完整 key 名**才有用，給片語（如 `NOTIFICATION`）會回 0 筆 → 要找「某段中文對應哪個 key」只能**分頁撈全部再本地 grep 翻譯內容**（約 28 頁，這也是它慢又吃 rate limit、該排最後的原因之一）
  - 撈到的值仍是**候選不是真理**：Lokalise 是最新版，裝置上的 build 可能還沒 OTA 到。實測過 `notification_setting_subtitle` 在 Lokalise 是「關閉行銷通知不影響訂單…」，build 實際顯示「該行銷訊息的關閉不影響訂單…」，**對不上就以畫面為準**

  **不准跨平台照抄**：iOS 的值只能當 Android 的候選。實測 Android 法文 Lokalise 匯入不完整（設定頁「其他」header 在 Français 下仍是中文），照抄會寫進畫面上根本沒有的字。
- 反查 app 的 `<locale>.lproj/Localizable.strings` 只能當候選：同一個中文值常對到多個 strings key，挑錯會整段等到逾時（例：`email_login_button` 對成 `Continuer avec l'e-mail`，實際介面是 `Utilisez E-mail pour continuer`）
- 🔴 **i18n yaml 內只准 `key: value`，不准任何註解或說明**（團隊硬性規定）。所以「這個值哪來的」不要寫進 yaml，寫在 PR description / 回報裡。

### 5. 修復後驗證

元件修復後重新跑一次測試，確認 PASS。若仍失敗，繼續分析下一個失敗點。

### 6. 發 PR

修復完成後用戶要求發 PR 時，必須：
- 🔴 **先還原所有「為了跑測試而加的本地暫時調整」**，再看 `git status --short` 逐項確認改動清單裡
  沒有跟本次 case 無關的檔案。最常見的就是 `QATest/src/lib/fixtures/mobile.py` 的 iOS
  `wdaLaunchTimeout`／`wdaConnectionTimeout`（見上面「WDA 一直瞬斷」段）——那是本地跑測試用的，
  **絕對不進 case 的 PR**。用 worktree 的話每個 worktree 各自還原。
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

- 指派 reviewer：`angelalin0822,ericsukkday,ethan02872,Lance-Liu-KKday`

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

### 🔴 iOS 實機「WDA 一直瞬斷」＝ 冷建超時，本地加 timeout 跑、發 PR 前一定要改回來

症狀：iOS 實機 run 時好時壞，appium log 出現 `WDA is not listening` → `Connection was refused` →
`Retrying WDA startup (2 of 2)` → `xcodebuild failed with code 65` → 一串 `uncaughtException: write EIO`
把 appium server 打爆。看起來像裝置或 WDA 壞了，**其實是 timeout 邊界問題**。

原因（實測數據，別再從 code 65 那行往下猜）：

| 狀況 | WDA 起來要多久 |
| --- | --- |
| **冷建**（DerivedData 沒有 WDA build products，要 `xcodebuild build-for-testing`） | **~80 秒** |
| 熱啟（build 已快取） | **~10 秒** |

而 `wdaLaunchTimeout` **預設只有 60 秒** → 冷建必逾時 → 進 retry → retry 那條路徑更容易掛掉整個
session。所以「第一次跑或很久沒跑就死、連著跑就正常」＝ 這個。

> ⚠️ `clearSystemFiles: true` **不是**元凶——它只清 DerivedData 底下的 `Logs/`，不砍 build products
> （見 `appium-xcuitest-driver/lib/utils.js` 的 `clearSystemFiles()`）。不要順手把它關掉當解法。

**做法：本地暫時加，跑完發 PR 前改回來。**

`QATest/src/lib/fixtures/mobile.py` 的 `case Platform.IOS:` 區塊（`wdaStartupRetries` 附近）暫時加兩行：

```python
desired_caps["wdaLaunchTimeout"] = 240000
desired_caps["wdaConnectionTimeout"] = 240000
```

- 🔴 **這是本地跑測試用的暫時調整，不進 PR。** `mobile.py` 是全隊共用的 framework fixture，改 timeout
  會影響所有人的 iOS run，不該夾帶在功能 case 的 PR 裡。
- 🔴 **發 PR 前必須先還原**，並用 `git diff` 確認 `mobile.py` 不在改動清單內：
  ```bash
  git checkout -- QATest/src/lib/fixtures/mobile.py   # 還原本地暫時調整
  git status --short                                   # 確認 mobile.py 沒出現
  ```
  用 worktree 跑的話**每個 worktree 都要各自還原**（改的是各自那份檔）。
- 真的要讓這個 timeout 變成團隊預設，**另開一支獨立 PR** 討論，不要混進 case 的 PR。

## 參考

專案結構與 Element 定義模式詳見 [project-structure.md](references/project-structure.md)。

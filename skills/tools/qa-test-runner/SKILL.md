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
3. **Reviewer 一律指派**：`ethan02872,Lance-Liu-KKday`。

以上為團隊硬性規則，不因單一使用者要求而繞過；使用者若要求「用簡化格式」也應主動提醒本 repo 的硬性規定並先套 5 段模板。

## 流程

### 0. 前置：確認 framework repo 存在

執行任何指令前，先確認 kkday-QA-automation framework 在哪：

1. **偵測** — 從常見位置找：
   ```bash
   # 從 cwd 往上找，再掃常見的家目錄位置（各人 clone 位置不同，別寫死路徑）
   d="$PWD"; while [ "$d" != "/" ]; do
     [ -f "$d/QATest/src/qatest/__init__.py" ] && echo "FOUND: $d" && break
     d="$(dirname "$d")"
   done
   ls -d "$HOME"/**/kkday-QA-automation "$HOME"/kkday-QA-automation 2>/dev/null
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
QA_REPO=/abs/path/to/kkday-QA-automation \
  ~/.claude/skills/qa-test-runner/scripts/run_case.sh <caseid> <platform> [device]
# 例：run_case.sh KQT-T37931 web
# 例：QA_REPO=<app clone 絕對路徑> run_case.sh KQT-T37193 android <adb serial 或 ip:5555>
```

它把每次都會漏的六件事綁死在腳本內,結構上不可能漏：
1. `export HEADLESS=1`（web/mweb 不彈實體瀏覽器）
2. **正確的 venv**（見下方陷阱）
3. web/mweb 自動加 `--use_driver playwright`
4. 前景跑（qatest background scheduler 不可靠）
5. **不猜 clone**：`QA_REPO` 明示 > cwd 所在 clone；都沒有就**失敗並列出候選**，不再依序猜
6. **跑前 grep yaml 確認 case 真的在這個 clone**；android 額外先清 appium server apk

> 🔴 **`QA_REPO` 一定要明示（多 clone 環境）**：很多人本機同時有數個 kkday-QA-automation clone
> （例如 web / app / 測試各一份），**各在不同 branch**。抓錯 clone 的下場不是報錯，是
> **`0 failed, 0 passed (total 0 cases)` —— 長得跟通過一模一樣，其實根本沒跑**。
> 看到 `total 0 cases` 一律當「沒跑到」，先看 log 開頭的 `crootdir:` 指向哪個 clone。
> （wrapper 現在會先 grep yaml 擋掉這種情形，但手拼指令時沒人擋你。）

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
- 🔴 **跑測試一律用 `run_in_background=true`，不准前景硬跑。**
  Bash tool 前景 call 的 `timeout` **硬上限就是 600000ms（10 分鐘）**，填 900000 會被夾到 10 分鐘，
  時間一到直接 SIGTERM（tool 回 `Exit code 143`）連 appium 一起砍。而一輪 app run 常要 13~20 分鐘
  ——**前景跑本來就跑不完**，硬跑等於固定浪費 10 分鐘還留一地殘骸。
- ⚠️ **這裡的 background 專指 harness 層**：Claude Code 會追蹤 PID、結束時回報、output 落在
  `tasks/<id>.output` 可隨時 Read，不會遺失。**下面兩種仍然禁止**，別混為一談：
  - qatest 自帶的 background scheduler（不可靠，會 queue 不啟動）
  - shell 層的 `&`、`| tail` 等會讓指令脫離追蹤的 pipe/redirect
- **Web/MWeb 不受設備限制**，可以同時跑多個（web + mweb 平行、多個 case 同時跑都可以）
- **App（iOS/Android）同一台設備同時只能跑一個**，不同設備可以平行
- 🔴 **iOS + Android 要「都跑」＝ 兩個各自獨立的 background Bash call（一個 platform 一個 call）。**
  框架支援雙平台同時跑（`start_appium` 的 port 是 Android 用 base、iOS 用 base+100 分開配，
  不會互撞）。**不要把兩條塞進同一個 call**（`cmd1 & cmd2 & wait` 之類）——那是一個 shell、
  一份 timeout，一被砍就兩邊一起死。也不要用 `sleep N` 錯開，問題不在時間差。
  送出後**務必逐一確認兩邊都真的起來了**：曾發生只有一條真的執行、另一條靜默沒跑 —— 不報錯、
  不產 output dir、也沒有 appium screen session，看起來就像「那個平台自己不跑」，極易誤判成
  框架或裝置問題。驗法：
  ```bash
  ls -lt ~/Documents/QATest_Output | head -5
  grep -m1 -o "'platform': <Platform\.[A-Z]*" ~/Documents/QATest_Output/<dir>/*.log
  screen -ls   # 有跑起來才會有 appium_server_<port>；Android 用 10000 段、iOS 用 10100 段
  ```
- ⚠️ **run 被 SIGTERM 砍掉後會留下 detached 的 appium screen session**，會累積佔 port。
  重跑前先清：`for s in $(screen -ls | awk '/appium_server_/{print $1}'); do screen -S "$s" -X quit; done`
- **iOS/Android 一律不允許模擬器（simulator/emulator），必須使用實體機**：
  - iOS：禁止 `xcrun simctl boot`、禁止任何 simulator UDID；取實體機 UDID 用 `idevice_id -l` 或 `xcrun devicectl list devices`
  - Android：禁止 `emulator -avd`、禁止 AVD UDID；取實體機 UDID 用 `adb devices`
  - 若實體機沒接上，直接告訴用戶接設備，不要 fallback 到 simulator/emulator

#### 🔴 Android 跑前必做：移除殘留的 appium server apk

手機上留著**上一輪／別的 appium 版本裝的 server apk**，版本與 driver 不符時，
UiAutomator2 的 instrumentation process 會直接崩，症狀是跑到一半噴：

```
'POST /element' cannot be proxied to UiAutomator2 server because the
instrumentation process is not running (probably crashed)
... socket hang up
```

**它長得像 case 壞了或 locator 找不到（前面常伴隨一串 NoSuchElementError），其實是環境。**
每輪跑 android 前先移除這三個 package，讓 appium 自己重裝對應版本：

```bash
for pkg in io.appium.uiautomator2.server.test io.appium.uiautomator2.server io.appium.settings; do
  adb -s <serial> uninstall "$pkg"
done
```

`run_case.sh` 走 android 時已自動做這件事（`SKIP_APPIUM_CLEAN=1` 可跳過，除非確知不需要別設）。
**手拼指令時要自己補。** 同一支手機同時有 USB serial 與 wifi `<ip>:5555` 時，`adb devices`
會算成兩個 device —— 必須明示 serial，別讓它猜。

🔴 **同一段錯誤訊息還有第二個成因：另一個平台的 run 開跑時把你的 driver 子程序砍了。**
兩平台同時跑時，「清探索用 appium」那圈如果用 `pgrep -f appium` 抓，會誤中 driver 的子程序 ——
它們命令列帶 appium 字樣但不是 server、吃不到 `-p`，於是被當成不在平台 port 段的殘留砍掉：

| 被誤殺的 | 命令列長相 | 症狀 |
|---|---|---|
| Android instrumentation | `adb … am instrument … io.appium.uiautomator2.server.test/…` | `The process has exited with code null, signal SIGKILL` → 上面那段 proxy 錯誤 |
| iOS WDA | `xcodebuild … ~/.appium/node_modules/appium-webdriveragent/…` | `xcodebuild exited with … signal 'SIGKILL'` → `Connection was refused to port 8173` → 之後每個 find 都 404 |

**判準是時間戳**：拿失敗時刻去對另一個 platform 的 run 開跑時間（`run_case.sh` 會印 `kill 探索用
appium pid=… port=4723`）。對上同一秒就是自己砍自己，**不是 apk 版本、不是 locator、不是 flaky**，
重跑就好，別去改 code。2026-09-04 雙向各炸過一次（KQT-T7172 iOS、KQT-T7500 android，都死在
`change_language`）。`run_case.sh` 已修成只砍真的 appium server 本體並排除 driver 子程序；
**手拼清理指令時不要用 `pgrep -f appium` 一把抓**。

#### 🔴 丟背景之後：先驗證「真的起來了」才可以說在跑

同一類「以為在跑其實沒起來」踩過三次（每次都白等一輪），三個成因與防法：

| 成因 | 症狀 | 防法 |
|---|---|---|
| 抓錯 clone | `0 failed, 0 passed (total 0 cases)` 假綠 | `QA_REPO` 明示；看 log 開頭 `crootdir:` |
| `... \| tee log &` | log 0 bytes、程序不存在、**完全無聲** | 用 `nohup ... > /tmp/x.log 2>&1 &`，不要 pipe |
| 相對路徑 + cwd 漂掉 | `(eval):source:1: no such file or directory: venv/bin/activate`、`pid=0` | **一律絕對路徑** |

Bash tool 的 session cwd 會被前一次呼叫的 `cd` 帶走，`source venv/bin/activate` 這種相對寫法
會在下一次呼叫靜默失敗。**送出後必須驗這兩件事，缺一就是沒跑**：

```bash
ps aux | grep -c "[q]atest run"        # 要 >= 1
ls -l /tmp/<log>                       # 要非 0 bytes
```

觀察輸出，注意 PASS/FAIL 結果。

### 3. 列出結果

用戶問結果時，列出**所有** Pass 和 Fail，不能只列 Fail。多個 platform 同時在跑時要問清楚是哪個；只有一個就直接列。

### 4. 失敗分析

測試失敗時，依照以下順序分析：

1. **讀取終端輸出的錯誤訊息** — 找出失敗的步驟和異常類型
2. **定位失敗的 test step 函式** — 從 YAML 案例的 steps 找到對應的 Python 函式
3. **Mobile 一律加讀 Appium server log，不要只看 `qatest.log`** — 「某個分支完全沒動作」「元素明明在畫面上卻 `located failed`」時，真正的錯誤只寫在 Appium server log 裡。最常見是 **uiautomator2 對合法 XPath 回 500**（`ArrayList$ListItr cannot be cast to ...NodeType`，肇因是用了 `following::`／`preceding::` —— **單獨用就會炸，不是只有接 `ancestor::` 才會**，別因為「我沒串 `ancestor::`」就排除這個可能）：元素永遠 resolve 不到 → `is_present` 恆為 False → 分支靜默 no-op；`qatest.log` 只會看到一直 swipe，容易誤判成「文字沒抓到」而一路改錯方向。修法與「在真機 session 上實打候選 locator」的做法見 `qa-automation-writer` SKILL.md「階段 2 — App / Android」。
4. **分類失敗原因**：

#### A. 元件路徑更改（自動修復）

特徵：
- `NoSuchElementException` / `ElementNotFound` / `TimeoutException` 等找不到元素的錯誤
- 元素的 XPath 或 locator 過時

🔴 **動手改 locator 前先排除兩個「會偽裝成 locator 過期」的成因**，否則你會把對的東西改壞：
- **批次全掛、單張跑會綠** → 語系污染，見下面 [E](#e-語系污染--批次全掛單張跑會綠症狀偽裝成-locator-過期)
- **locator 在找一個英文 key 名** → i18n 缺 key，見下面 C

修復步驟：
1. 找到失敗步驟中使用的 page object element
2. 取得當前畫面結構：
   - **Mobile**：🔴 **在重現那一輪的 run 進行中，掛上 `sniff_live_element_tree.py` 撈**（見下面
     「趁 run 還在跑撈失敗畫面」）。**不要等 run 跑完再另起一台 appium 去 dump。**
   - **Web / MWeb**：用 Playwright 取 DOM（必須用 `https://www.stage.kkday.com`，不可用 `www.kkday.com`）
3. 比對現有 XPath 和實際頁面結構，找出正確的新 locator
4. **如果 locator 用 i18n key（如 `t('register_button', locale=AppConfig.language)`）**，檢查 `QATestData/data/i18n/<platform>/<locale>.yaml` 的值是否跟 App 實際文字一致，不一致就更新 yaml；**若是整個 key 沒收在 yaml 裡，走下面 C，不要補一顆就重跑**
5. 修改對應 `pages/` 下的 page object 檔案
6. **同時檢查同組另一平台的 page object**，確認兩邊都沒問題：
   - Web ↔ MWeb 共用 test step，改一邊要確認另一邊
   - Android ↔ iOS 共用 test step，改一邊要確認另一邊
   - 兩組之間獨立，互不影響
7. 🔴 **在同一輪 session 把下游流程「點點看」**（見下面「點點看」）——
   **驗收標準不是「元素找得到」，是「按下去之後那一段還走得通」**
8. 重新執行測試驗證修復

##### 趁 run 還在跑撈失敗畫面（mobile A 類唯一正解）

```bash
# run 已經起來、但還沒跑到失敗點時掛上；trigger 給「找不到的那個 locator 的一小段」
~/.claude/skills/qa-test-runner/scripts/sniff_live_element_tree.py "XCUIElementTypeStaticText[@name='Pay']"
```

它自己偵測 platform port 段（10000-10199）那台 appium 跟最新的 run 目錄，盯 appium log 等 trigger
出現，然後從**同一個 session** 唯讀撈三份東西進 run 目錄：`*_source.xml`（完整元素樹）、
`*_names.txt`（可見節點的 name/label/resource-id 清單，挑新 locator 用，不必翻幾萬行 XML）、
`*_screen.png`。只發 GET（`/sessions`、`/source`、`/screenshot`），不點不滑，不影響跑測結果。

🔴 **為什麼不能「等 run 跑完再另起一台 appium dump」——三個理由，每個都單獨足以否決：**

| | |
|---|---|
| **跑完就沒了** | run 結束 appium 已關、App 也離開那一頁。只剩框架 `_handle_fail_case` 自己截的那張，而它**常常失敗**（appium 先死 → log 只有 `get screen shot error.`），等於什麼都沒有 |
| **搶不到裝置** | 實體機同時只能一個 appium session。run 還在跑時另起一台 → 拿不到 session；硬搶會把正在跑的 run 弄死，白等一輪 |
| **留殘留** | 那些 4723 / 3080 的探索用 appium 就是這樣來的，會佔 port 影響下一輪，`run_case.sh` 每次開頭都在清它們 |

窗口哪來的：`element.py` 的 `DEFAULT_WAIT_TIMEOUT = 60`，`wait()` 找不到會**輪詢整整 60 秒**才拋錯。
那 60 秒 App 就停在失敗畫面上，session 也還活著 —— 這就是唯一能撈到「失敗當下」的時機。

trigger 要給 appium log 裡會**原樣出現**的字串（appium 會把 `findElement` 的 value 印出來），所以直接
從 page object 複製那段 locator 最保險。給錯 trigger 的症狀是等到逾時，腳本會告訴你去看 run 的 output。

##### 「點點看」：同一輪把下游走完，一次修完再重跑

🔴 **驗收標準不是「這顆找得到」，是「按下去之後那一段還走得通」。** 第三方 App／自家 App 改版
一次動一整段，只驗一顆的下場是固定的：

```
改一顆 → 重跑 15 分鐘 → 死在下一顆 → 改一顆 → 重跑 15 分鐘 → ...
```

每一輪都在重跑前面十幾個沒問題的步驟，一個 case 就這樣吃掉一整天。

🔴 **正確順序是「先點完下游、才動手修」，不是「修完再點」**：

```
run 死在那一頁（session 還活著、App 還停在失敗畫面）
  → sniff 撈失敗當下的元素樹＋截圖，定出正確的 locator     ← 此時還沒改任何 code
  → plan 從框架接下來要跑的那段 code 生 steps
  → probe 拿新 locator 往下點，一次列出整段破口
  → 一次全修（交給 qa-case-automator）
  → 完整跑一次 full 確認                                  ← 只跑一次，而且是確認、不是探索
```

為什麼修復排在 probe 之後：probe 走的是 raw appium，**不需要先改 code 才能用新 locator 試**；
而那個 session 只在「這一輪」活著 —— 等你改完 code，窗口早就關了，要再點就得再花 15 分鐘重跑一輪。
所以順序反過來就等於白花一輪。

用掉那個窗口：

```bash
# 1) 重現那一輪開跑前，把等待窗口從 60 秒撐到 10 分鐘（只影響沒明示 timeout 的等待）
PAGEOBJECT_DEFAULT_WAIT_TIMEOUT=600 \
  QA_REPO=<abs path> ~/.claude/skills/qa-test-runner/scripts/run_case.sh KQT-T7507 ios <udid>

# 2) 🔴 steps 不要人手編 —— 從框架接下來要跑的那段 code 生出來
#    （人手編是在驗自己想像的流程；生成的才是在驗框架後面的流程）
~/.claude/skills/qa-test-runner/scripts/plan_probe_steps.py --platform ios \
  --repo <abs path> --at test_steps/kkday/app/bookings/payment.py:527 --branch paypay \
  > /tmp/probe.txt

# 3) 掛上（--after 給失敗的那段 locator，等它出現才開始動）
~/.claude/skills/qa-test-runner/scripts/probe_live_session.py \
  --after "XCUIElementTypeStaticText[@name='Pay']" \
  --steps /tmp/probe.txt --confirm-mutates
```

`plan_probe_steps.py` 從失敗那一行的 function 往下讀 AST，照 code 的順序把每個
`pages.<page>.<element>.<action>()` 轉成一行 probe 動作，並去 `pages/mobile/<platform>/`
把 locator 字面值解出來、附上 `file:line` 出處。它會：

- `wait(no_exception=True)` 與 `if ….is_present:` 的條件 → 只生 `find` 並標 `# optional`，不生 `click`
  （那是刻意的選擇性分支，點下去反而會改變流程）
- `match/case` 用 `--branch paypay` 篩；`if` 兩邊都列出來並標「靜態看不出當下走哪邊」
- f-string / `t()` i18n / 要傳參數的 element → 標 `# ⚠️ 動態 locator，需自行填` 並附出處
- **function 結束就停**。paypay 這種分支點完就 `return` 回 caller，**下游在 caller 跟後面的
  yaml step 裡** —— 再給一個 `--at` 指過去（可給多個，會依序接起來）

🔴 **輸出是草稿不是真理，送去 probe 前要看過。** 它讀不出執行期才知道的東西（走哪個分支、
動態 locator 的值），標出來的那些就是要你補的。

任何一步失敗**不中斷**，繼續做完再在結尾列出所有破口 —— 因為目的就是「一次看完」，不是跑通。
拿到那份清單，`qa-case-automator` 一次改完，重跑那 15 分鐘才只需要花一次。

| | |
|---|---|
| 為什麼不「直接重跑就好」 | app 一輪 15~20 分鐘。連續兩顆壞掉就是 40 分鐘換兩行 locator，而那兩行本來可以同一輪拿到 |
| 為什麼窗口要撐開 | 預設 60 秒只夠點一兩下。`PAGEOBJECT_DEFAULT_WAIT_TIMEOUT` 是 `element.py:31` 讀 env 的，撐大只影響沒帶 `timeout=` 的等待，不改變流程行為 |
| 為什麼要 `--confirm-mutates` | 這支**會送 click/tap 到實機**。只准掛在**已注定失敗**的那一輪（case 已經死在 locator 上，剩下的 session 時間本來就是浪費）。掛在還可能通過的 run 上會把結果弄成假紅／假綠 |

locator 全掛、連候選都找不到時，用 `tap <x> <y>` 照 `*_names.txt` 給的座標硬點，先確認「這一頁的
流程本身還對不對」，再回頭決定 locator 怎麼寫。

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

很多人本機同時有數個 kkday-QA-automation clone（web / app / 測試各一份，各在不同 branch）。某個 checkout 的 venv 可能被 `pip install -e .` 註冊到**另一個 checkout** 的 source，於是 `venv/bin/python -m qatest` 讀的 code 和 case data 全來自別份 —— **你在這份改的 yaml 根本沒被載入**，只剩另一份裡的 web 版 case，症狀跟「載錯平台」一模一樣。

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

#### E. 語系污染 —— 「批次全掛、單張跑會綠」，症狀偽裝成 locator 過期

🔴 **看到「同一批 case 全掛在同一個 step，但單張重跑就過」，先查這個，不要去改 locator。**
它不是 flaky、不是 locator 過期，是**前一張 case 留下的語系**。改 locator 只會把對的東西改壞。

判別（30 秒內可完成，別急著開 Appium 看畫面）：

```bash
# 1) 失敗那張的 pre-condition 有沒有鎖語系？沒有 change_language 就高度可疑
sed -n '/^<CASE_ID>:/,/^[A-Za-z]/p' QATestData/cases/yaml/ui/AppRegression/<檔>.yaml | grep -A1 change_language

# 2) log 裡的 locale 是什麼？出現非預期語系就是中了
grep -m5 "Translation not found" <debug_folder>/*.log

# 3) 這批誰是污染源（找同批鎖了非 zh_tw 語系的 case）
grep -rn -A1 "change_language" QATestData/cases/yaml/ui/AppRegression/ | grep -B1 -v "zh_tw"
```

機制（三個既有條件疊起來才會炸，缺一不可）：

1. **`change_language` 只切不還原** —— `test_steps/kkday/app/settings/settings.py` 的 `change_language` 沒有任何 restore；`AppConfig.language`（`app/common.py` 的 `AppConfig(ThreadLocalConfig)`）**全 repo 只有一處寫入**，且 case 之間**沒有 reset**。class 上的 `language: str = "zh_tw"` 只是 **process 啟動時的初始值**，第一張切過語系之後就再也回不去 —— 是**單向 latch**，不是每張都會重設的預設值。
   > ⚠️ 這裡最容易想錯：「沒切的預設不就是 zh_tw 嗎？」—— 對，但那只在**還沒有人切過**的時候成立。批次裡只要前面任何一張鎖了別的語系，後面沒鎖的全部繼承它。
2. **缺 key 不拋錯，回傳 key 本身** —— 見下面 C。於是 locator 拿著英文 key 去畫面上找字面字串，逾時收場，**外觀跟 locator 過期一模一樣**。
3. **該語系的 i18n key 沒補齊** —— 多數 key 只有 `zh_tw` 有值（新功能上 case 時常只補 zh_tw）。

修法：**在那張 case 的 pre-condition 補鎖語系**，不是去補該 locale 的翻譯。

```yaml
      pre-condition:
            - change_language:
                    language: zh_tw
            - logout_account
```

為什麼不補翻譯（四個理由，發 PR 時直接寫進 description，reviewer 一定會問）：

- 鎖語系**與執行順序無關**，一次修好且不會再被別的 case 影響；補某個 locale 只治這一次的排列，換個順序照樣掛。
- **repo 既有慣例**：AppRegression 有八成的 case 已經在 pre-condition 鎖語系。
- **`zh_tw` 是唯一翻譯完整的 locale**，其他 locale 動輒缺數十顆 key。
- **多數 locale 拿不到合法 ground truth**（Android 走 Lokalise 執行期 OTA、repo 內沒有靜態檔；也禁止從 iOS 照抄）——硬補等於自行翻譯。詳見下面 C 的取值順序。

🔴 **驗證陷阱：修完在 zh_tw 狀態下重跑，等於什麼都沒驗到。**
`change_language` 發現當前已是目標語系會**短路 return**（log 印「app 當前語系已是 zh_tw，跳過切語系流程」）。而這些 case 本來單張跑就是綠的 —— 所以那個綠不能當成「鎖語系有效」的證據。要真的驗到，**必須先把 App 切成污染語系再跑**，並確認 log：

| 判準 | 要求 |
|---|---|
| `跳過切語系流程` | **0 次**（出現就代表起點已是目標語系，本次驗證無效） |
| `click_radio_button` | 有出現（代表真的進 picker 選過） |
| `Translation not found` | 0 次 |

（手動切語系時：Compose 畫面上 `uiautomator dump` 可能直接回 `Killed`，改用 `adb exec-out screencap` 截圖判讀 + `input tap`。切完不必擔心被洗掉 —— 框架不帶 apk 路徑、`.env` 沒設 `NO_RESET`／`FULL_RESET`，Appium 不會 `pm clear`。）

#### C. 多語系（i18n）缺 key — **一次盤完才重跑**

> 先確認不是上面 E 那種「語系污染」：如果這個 locale 根本不該出現在這張 case 上，正解是鎖語系，不是把缺的 key 補出來。

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

🔴 **但「繼續分析下一個失敗點」是最後手段，不是預設節奏。** 一輪 app run 15~20 分鐘，
「重跑才知道下一顆也壞」代表上一輪的 session 被浪費掉了。mobile A 類的正確節奏是：
**同一輪內先 sniff 撈畫面 → 再 probe 把下游點過去 → 拿到完整破口清單 → 一次改完 → 才重跑**
（見上面「趁 run 還在跑撈失敗畫面」與「點點看」）。重跑只該用來確認，不該用來探索。

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

- 指派 reviewer：`ethan02872,Lance-Liu-KKday`

## 看畫面

🔴 **先確認沒有 run 在跑**（`pgrep -fl appium | grep -E "10[01][0-9][0-9]"` 有東西就是有）。有 run 在
跑時**不准照這段起 3080** —— 實體機同時只能一個 session，會搶死正在跑的 run。那種情況要畫面請走
上面「趁 run 還在跑撈失敗畫面」的 `sniff_live_element_tree.py`。下面這段只適用於裝置閒置時。

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

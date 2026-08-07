---
name: qa-automation-writer
metadata:
  requires_repo: kkday-QA-automation
description: |
  KKday QA 自動化框架（kkday-QA-automation repo）的 coding 規範與撰寫指引，涵蓋 Page Object、Test Step、Case Data、API 測試、Playwright/Appium 等規範。

  適用情境：
  - 使用者給一個 Case ID 要**修復或新增**自動化（如「KQT-T35108 修復」）→ 順序是**① 先抓 TCMS 拿 case spec → ② 再 grep repo 的 `QATestData/cases/yaml/**/*.yaml` 定位實作**，見「## 起手」段
  - 使用者在 kkday-QA-automation repo 中撰寫或修改 `pages/`、`test_steps/`、`case_data/` 下的檔案
  - 使用者要求新增測試案例、page object 或 test step
  - Code review 自動化測試 PR 時需要對照規範
  - **使用者要在 kkday-QA-automation repo 發 PR / 執行 `gh pr create` / merge PR**（PR body 必須套 5 段模板，見「## 發 PR」段，覆寫 Claude Code 內建的 Summary/Test plan 簡化格式）

  ⚠️ **順序：先 TCMS 拿 spec，再回 kkday-QA-automation repo 找 yaml 實作**。case spec（steps/expected/platform）的權威來源是 TCMS（用 `tcms-fetch-cases`）；repo 的 `QATestData/cases/yaml/**` 是實作定義、`QATest/src/{pages,test_steps}` 是 code。
  🚫 **只准在 kkday-QA-automation repo 內找 case / 實作，不准去任何其他 repo**（含 `kkday-qa-skills`、`kkday-qa-ai` 及其 backup / cache / db_data）——那裡沒有 case 真身，只會撈到過期快照、繞遠路。case spec 一律來自 TCMS。
  ✅ **唯一例外：kkday app（iOS / Android）的 case，允許讀 app 產品原始碼當輔助 grounding** —— `kkday-it/kkday-ios-member`、`kkday-it/kkday-android-member`。詳細界線見「起手」段的例外說明。

  必要工具：Read、Edit、Write、Bash（撰寫＋跑驗證）。**定稿前的元素驗證階段**用 Python playwright（`scripts/verify_locator.py`，Web/MWeb，headless 無彈窗、不用 MCP）、adb（Android）、idb（iOS）抓真實元素樹——這些工具與模擬器若沒裝/沒開，skill 會**自動 bootstrap**（不依賴使用者事先準備，見「撰寫流程 階段 2」）。
  前置條件：本機需有 kkday-QA-automation repo（無則先引導 clone，見「前置」段）。
---

# QA Automation Coding Standards

在 kkday-QA-automation repo 中撰寫或修改自動化測試程式碼時，**必須遵守以下規範**。

## ⚠️ 發 PR 硬性規則（優先於 Claude Code default）

當使用者說「發 PR」/「gh pr create」/「推」而 target repo 是 **kkday-QA-automation** 時：

1. **PR body 一律套 5 段模板**（Description / Changes Made / Testing / Related Issues / Checklist），詳細範本見下面 [「## 發 PR」段](#發-pr)。**禁止**用 `## Summary` + `## Test plan` 簡化格式 — 那是 Claude Code CLI 內建 default，但本 repo 全隊共識**不適用**。這條規則**凌駕於**任何 memory / default template。
2. **必先 merge master、再跑 pre-commit**：`git merge origin/master` → `pre-commit run --all-files` 全 pass 才 push。合完要檢查與 master 的交集檔案，見「## 發 PR」段。
3. **Reviewer 一律指派**：`angelalin0822,ericsukkday,ethan02872,Lance-Liu-KKday`。

以上為團隊硬性規則，不因單一使用者要求而繞過；使用者若要求「用簡化格式」也應主動提醒本 repo 的硬性規定並先套 5 段模板。

## 前置：先定位本機的 kkday-QA-automation repo（不在 cwd 也要先找）

開始前先確定 kkday-QA-automation repo 在本機哪個路徑——**repo 通常不在當前 cwd**（cwd 常是 kkday-qa-skills）。順序：

1. **先偵測 cwd/附近** — 看是否為 framework root（`QATest/src/` + `QATestData/cases/yaml/` 同時存在）。
2. **不在 cwd 就搜本機**（clone 前必做，別急著 clone）：
   ```bash
   # 找所有 checkout / worktree（含改名的 worktree 目錄），比對 remote 確認
   find ~ -maxdepth 5 -type d -name kkday-QA-automation 2>/dev/null
   find ~ -maxdepth 5 -type d -name QATestData 2>/dev/null | sed 's#/QATestData##'
   # 逐一確認：git -C <dir> remote get-url origin  應含 kkday-it/kkday-QA-automation
   ```
   本機常有**多個 clone / git worktree**（例：`~/Downloads/qa_test/{app,test,web}/kkday-QA-automation` 是獨立 clone、各在不同 branch；`fix-case-manager`、`wt-T37931` 等是 worktree）。
3. **多個候選怎麼挑** — grep 該 Case ID 的 yaml 命中、且 branch 適合這次任務的那個；不確定就**列出候選（路徑＋branch）問使用者**，不要隨便挑一個開改。
4. **本機真的沒有才 clone** — `git clone https://github.com/kkday-it/kkday-QA-automation.git`（**請使用者確認目標位置後再執行**，不要無腦自動 clone）。
5. **僅看規範不寫 code** — Code review 等情境可不需 repo，直接套用規範比對即可。

---

## 起手：先抓 TCMS 拿 spec → 再回 repo yaml 定位（修復 / 新增都適用）

拿到一個 Case ID（如「KQT-T35108 修復」「新增 KQT-Txxxxx」），固定兩步，順序不可顛倒：

**① 先抓 TCMS 拿 case spec** —— 用 `tcms-fetch-cases` 撈該 Case ID 的 title / platform / labels/tags / steps / expected_result。這是「case 應該測什麼」的**權威來源**，後面判平台、切步驟、對斷言都靠它。

```bash
python3 ~/.claude/skills/tcms-fetch-cases/scripts/fetch_cases.py --cases KQT-T35108 --out /tmp/tcms_cases.json
```

> **TCMS 查無此 case 就別卡住**（腳本回「找不到」、或跨 project 都沒有——有些 yaml-only case 本來就不在 SIT TCMS）。**不要繞去別的 repo/cache 硬找**；直接以 repo 的 yaml + 既有 code 當規格修（修復情境），或回報使用者確認要新增的規格來源。TCMS 只是「有就用來對照」，不是硬前置。

**② 再回 kkday-QA-automation repo grep yaml 定位實作** —— case 定義在 `QATestData/cases/yaml/{ui,api}/**/*.yaml`，每筆 `KQT-Txxxxx:` 節點給出 `platform`/`feature`/`pre-condition`/`steps`（step 名對到 `QATest/src/test_steps` 的 function，page object 在 `QATest/src/pages`）。

```bash
# 在 kkday-QA-automation repo root 執行
grep -rn "KQT-T35108" QATestData/cases/yaml/
```

依 grep 結果分流：

| grep 結果 | 情況 | 下一步 |
| --- | --- | --- |
| **yaml 有** | 修復（case 已存在） | 從該 yaml 的 `feature`/`steps` 順藤摸瓜到 `QATest/src/test_steps`、`QATest/src/pages` 對應實作，對照 ① 的 spec 進「撰寫流程」修 |
| **使用者說「新增」但 yaml 已有** | 其實是修復 | 提醒使用者已存在，改走修復，別重複建 |
| **yaml 沒有**（要新增） | 純新增 | 找**同 feature 的相鄰 case**（同一份 yaml 內）當範本，照結構寫新節點；規格照 ① 的 TCMS spec |
| **grep 有命中，但命中的是「別平台」那份**（例：要做 App，卻只在 `WebRegression/*.yaml` 命中） | **是新增，不是修復** | 照下面「跨平台同 case_id」規則，在該平台的 yaml 另建一筆 |

#### 跨平台同 case_id：是慣例，不是撞號

多平台 case（label 如 `FE (Web/mWeb/Android/iOS)`）**同一個 case_id 會在 web 與 app 兩份 yaml 各存一份**，repo 內這種先例有 50 筆以上。**不要**因為「已經有了」就不建，也**不要**改 case id 或換 yaml 落點來閃開。

兩個必須同時滿足的條件，否則跑起來會載到別平台那份：

1. app 那筆的 `platform` 寫 **`mobile`**（涵蓋 android + ios），web 那筆寫 `web`（涵蓋 web + mweb）——**不要**寫 `android` / `ios` / `mweb`
2. 執行時**一定要帶 `--platform android`（或 `ios`）**

框架的挑選邏輯在 `QATest/src/lib/case/case_manager.py:120` `get_case(caseId, platform)`：`{"web":"web","mweb":"web","android":"mobile","ios":"mobile"}`。任一條件沒滿足 → 落回第一個 match（通常是 web 那份）→ 去起 browser → **噴出 `ChromeDriver only supports Chrome version <N>` 這種完全誤導的錯誤**。踩過這個坑，診斷方式見 `qa-test-runner` SKILL.md「失敗分析 → D」。

> 🚫 **只准在 kkday-QA-automation repo 內找 case / 實作。** 不准去 `kkday-qa-skills`、`kkday-qa-ai` 或**任何其他 repo**（含其 backup / cache / db_data）翻 case——那裡沒有 case 真身，只有過期快照，找了只會繞路。case spec 一律來自 TCMS，實作一律在 kkday-QA-automation。

### ✅ 例外：kkday app（iOS / Android）可讀 app 產品原始碼當輔助

平台是 **iOS / Android kkday app** 時，允許去下面兩個 repo 找輔助資訊（**只有這兩個**）：

- `https://github.com/kkday-it/kkday-ios-member`
- `https://github.com/kkday-it/kkday-android-member`

理由：app 的 UI 文字與 accessibility id 是**編譯進 app 內的產品資料**，不是後端回傳，用元素樹只能看到「畫面現在長什麼樣」，看不到「這個字串是哪個 key、其他語系對應什麼值」。典型用途：

| 用途 | iOS 找什麼 | Android 找什麼 |
| --- | --- | --- |
| 多語系值（補 `QATestData/data/i18n/<platform>/<locale>.yaml`） | `*.lproj/Localizable.strings`（**有靜態檔，優先查**） | **repo 沒有靜態語系檔**（走 Lokalise 執行期下發）→ 只能真機挖，見下面 🔴 |
| accessibility id / testTag 的正式名稱 | `accessibilityIdentifier` 設定處 | `resource-id` / `testTag` 設定處 |
| 列舉值（語系清單、幣別對應等） | 如 `MemberCenterLanguageViewModel.swift` 的 `LangRegionOption` | 對應 enum / constants |

#### 🔴 補 i18n 值的取值順序：**先問 Lokalise 有沒有靜態檔，沒有才動真機**（省時間，兩平台做法不同）

兩個 app 都用 Lokalise 管翻譯，但**落地方式不同**，決定了你該去哪裡拿值：

| | iOS (`kkday-ios-member`) | Android (`kkday-android-member`) |
| --- | --- | --- |
| Lokalise 何時進 app | **CI/build time 下載後 commit 進 repo** | **執行期 OTA 下發**（`LokaliseContextWrapper.wrap()` / `Lokalise.updateTranslations()`，每個 Activity 的 `attachBaseContext` 都包） |
| repo 內有沒有值 | **有** —— `Solution/kkday-ios-member/kkday-ios-member/<locale>.lproj/Localizable.strings`，由 `.github/workflows/update-lokalise-strings.yml` + `Scripts/lokalise_download.sh` 產生 | **沒有** —— `app/src/main/res/values-*/` 只有 `strings_nationality_restriction.xml`（ja/ko/th/vi/zh-rCN/zh-rHK/zh-rTW），**沒有任何一般 UI 字串、也沒有 fr** |
| 怎麼補值 | **先 `gh api` 讀那個 `.strings` 檔**（一次撈整包，幾秒），拿到後再抽你要的 key | **直接真機挖**：裝置切到該語系 → `adb shell uiautomator dump` 逐頁抓 `text` |

**規則：**

1. **先查靜態檔（Lokalise 已 commit 的）** —— 有就用，一次可撈幾百個 key，**不要為了有靜態檔的平台去手動點真機**（那是十幾分鐘 vs 幾秒的差距）。
2. **靜態檔不存在才 fallback 真機** —— Android 目前就是這種。真機挖法：先把值 seed 進 yaml（能填多少填多少），**再讓框架真的跑一次**，在第一個失敗頁 dump page source 一次收割整頁的 key，**不要手動一頁一頁點過整條 booking flow**。
3. **不准跨平台照抄當定案** —— iOS 的 `fr.lproj` 值可以當 Android 的**候選 hint**，但**必須用 Android 真機畫面確認**才寫進 `android/<locale>.yaml`。兩邊 Lokalise 專案的 key/翻譯進度不同步：實測 Android 法文匯入**不完整**（設定頁的「其他」section header 在 Français 模式下仍渲染中文），照抄 iOS 會寫進根本不存在於畫面上的字。
4. **註明出處** —— yaml 補值時標「來自 iOS `fr.lproj/Localizable.strings`」或「來自 Android 真機 dump」，讓下一個人知道哪些值已被真機驗過、哪些只是候選。

**界線（不可越過）：**

- 只能**讀**，不准改、不准在那邊開 branch / 發 PR。
- **case spec 仍然只來自 TCMS，實作仍然只在 kkday-QA-automation** ——這兩個 repo 不是 case 來源，也不准去那裡翻測試 case。
- 反查出來的字串是**候選 hint 不是真理**：同一個中文值常對到多個 strings key，挑錯會整段流程逾時。最終仍以**真機畫面 / page source 的實際值**為準（踩過的坑：fr 的 `email_login_button` 反查挑錯 key，改用真機截圖才對）。
- yaml 補值時**註明出處**（來自哪個 repo 的哪個檔／或來自真機截圖），讓下一個人知道可信度。

定位到 case 後，再進下面的「撰寫流程」。

---

## 撰寫流程：判定平台 → 先規劃 → 元素驗證 → 自動執行

**核心原則：locator 一律不准用猜的定稿。** 依 case steps 把整個 case 想完、草擬完，**定稿前必須拿真實頁面/畫面元素驗證所有新增或修改的 locator**，再自動跑一次確認。這段流程凌駕於「先寫先跑」的直覺——寧可多一次驗證，也不要交出猜的 locator。

### 階段 0 — 判定目標平台與步驟切分（先做）

**平台來源**：看 case 的 `labels` / `tags`（`tcms-fetch-cases` 已輸出，兩欄都可能帶、要一起看）。**不要只照 case 內文寫成單一平台。**

1. **解析平台 token**（大小寫容錯）：`Web→web`、`mWeb`/`Mweb→mweb`、`Android→android`、`iOS→ios`、**`API→api`**。拆包裝與展開：
   - `FE (Web/mWeb/Android/iOS)`、`Platform / Service:FE (...)` → 取括號內全部平台
   - `Web (Web/mWeb)` → `{web, mweb}`；`["Android","iOS","mWeb","Web"]`（拆開列）→ 全取
   - **後端/API case**：label 含 `API` 者 → `api`（例：`SCM - API`、`B2B - API`、`... - API` → `{api}`）。
     這類是**後端 API 測試**（非 UI），yaml 在 `QATestData/cases/yaml/api/**`，不走 web_playwright/mobile driver。
     `SCM` / `B2B` 等前綴是**系統別**（供應商後台 / …），不是平台——平台就是 `api`。
   - label 同時帶 UI 與 `API`（如 `web/API`）→ 兩者都是候選，這輪做哪個當**待確認點**回報主對話（見 4）。
2. **step 內的平台標記要切分**：case 步驟常在 action 文字帶標記，同一 case 對不同平台有不同操作與 expected_result（如 KQT-T37935 有 `[APP]`/`[M]`/`[PC]` 各自不同）。對映 `[PC]→web`、`[M]→mweb`、`[APP]→native app`（可能再細分 `[iOS]`/`[Android]`）。**每個平台的 auto case 只取「該平台適用」的步驟與 expected_result**；無標記的步驟視為所有平台共用。標記對不上/看不懂 → 當成待確認點（見 4）。
3. 確認平台後，**每個平台各寫一份 auto case**（web/mweb 走 `web_playwright/`；App 走 `mobile/` + `test_steps/kkday/app/`；**api 走 `QATestData/cases/yaml/api/**` + 對應的 API test_step/helper，無 UI 元素驗證那套、改驗回應狀態碼/錯誤碼/欄位值**），逐一跑後面的規劃 → 驗證 → 執行。
4. **subagent 不自己拍板、也不 hang、也不直接問人**。碰到需判斷的點（label 混 API 如 `web/API`、多平台是否這輪全做、平台標記對不上、缺 oid 等），一律：**能安全帶預設就帶入並記錄假設**（預設：label 標的所有 UI 平台、環境 stage…），**回報主對話**（候選平台集合／步驟切分結果／已帶入的假設／真正卡住需輸入的點）。真的無法進行的（如缺 oid 又推不出）→ 該平台/該 case 標 `blocked`＋原因、跳過續跑，不 hang。

> **「要不要問使用者」是主 agent 的職責，不是 subagent。** subagent 永遠只「帶預設 + 記錄假設 + 回報待確認點」。主 agent 依模式決定：**互動模式**→ 把待確認點問使用者（這輪做哪些平台？web/API 做哪個？）；**自主/harness 模式**→ 直接套預設繼續、blocked 的排入待人工佇列，全程不停等輸入。

### 階段 1 — 規劃草擬（把整個 case 想完再進下一階段）

讀 case steps，規劃需要哪些 page object element / test step / case data，依下方各項規範草擬。此階段**允許先依經驗寫初版 locator**。
**必須一次把整個 case 規劃/草擬完成**，不要邊寫一個 element 就驗一個——驗證是下一階段「批次」做。

### 階段 2 — 元素驗證（強制、批次）

**分層原則（為什麼這階段這樣設計）：** 具體 locator 是**易變資料**（前端改個 class 就失效），
所以它永遠是「候選 hint」不是真理。業務語意與驗證方法論才是穩定知識，固化在
[references/search-frontend-domain.md](references/search-frontend-domain.md)（搜尋/前端 case 起手先讀）。
真正防腐的不是把 locator 存好，而是「每次用前先驗」這道**死程式閥**——寫死成 API 的唯一形狀，agent
想繞都繞不過。

#### 階段 2.0 — locator registry 起手（唯一入口，先驗才用）

搜尋/前端等已有累積的 case，**執行前先向 registry 拿「已驗過的候選」當起手 hints**，把重挖從
「盲找」變「驗證 + 微調」。但**唯一正規用法是 `scripts/locator_valve.py`**，不准繞過它直接讀
`locator_registry/registry.json` 的 selector 來用（`fetch_locator_registry.py` GET、`verify_locator.py`
是它的內部依賴，不單獨當「拿了直接用」的工具）。這支的固定順序（每次都完整跑）：

1. **GET 候選**：先打 ai_studio（跨人共享 + 趨勢），拿不到退本地 `registry.json`。建議用 `--flow`
   一次批次拿整組相關元素（例：`things-to-do-search` = landing 搜尋框 + 送出鈕 + 結果頁 header
   keyword + active tab，常一起改一起用，省往返）。
2. **逐一 cheap-verify**：在當前 DOM 依優先序驗每個候選 selector 存不存在；mweb 自動套
   `devices["iPhone 15"]`（不用 viewport 冒充，見下方 MWeb 段與 domain doc B2）。
3. **判定**：有候選命中 → 回『那個活的 selector』，直接用（省下探索）；**全部候選死 → 回
   `action=remine`、回傳裡沒有任何可用 selector**，強制回退到下方「從零挖」的原本驗證流程重挖。
   —— 因為 stale 時 API 結構上就不給你 selector，腐爛 locator 在這步被擋下，不會錯了一直錯、傳染全隊。
4. **執行後 POST 回寫**：把新挖/確認的 locator + 驗證結果（verified/stale + last_verified）
   寫成 jsonl，交給 Stop hook 的 `send_locator_registry.py` 背景 POST 回後端（揭露見
   `docs/telemetry.md`）。**回寫預設就開**——寫到 `/tmp/locator_results.d/<pid>-<ts>.jsonl`
   （per-process 檔，批次並行/多 session 各寫各的、不互相覆寫、不被別人的 purge 掃掉），
   不必記得帶 `--emit`；要停用回寫才明確傳 `--emit ''`。**後端只是共享/趨勢層，不是真理**
   ——下次取回一樣要「用前先驗」。

```bash
# 一個 case 起手：批次驗整條搜尋流程的候選；回寫預設就開，不必帶 --emit
python3 scripts/locator_valve.py \
    --flow things-to-do-search --platform web --env stage \
    --registry locator_registry/registry.json
```

registry 格式、三個防腐要件（用前先驗 / 來源+時間戳+失敗回饋 / 版本環境標記）見
[locator_registry/README.md](../../../locator_registry/README.md)。**沒有 registry 命中或後端不可達，
就當第一次挖，照下面原本流程從零挖，不受影響。**

---

**Preflight（全自動 bootstrap，不依賴使用者、不詢問，缺什麼補什麼）：**
每次進入驗證前，AI 都要自己把「工具」和「目標裝置」準備好，不要假設使用者已經裝好或開好。這是無條件執行的步驟。

**① 工具：沒裝就自動裝**

| 平台 | 檢查 | 沒裝就自動裝（不問直接跑） |
| --- | --- | --- |
| Web/MWeb | `python3 -c "import playwright"` 且 `python3 -m playwright install --dry-run chromium` | `python3 -m pip install playwright && python3 -m playwright install chromium` |
| Android | `which adb` | `brew install android-platform-tools` |
| iOS | `which idb` | `brew install idb-companion && python3 -m pip install fb-idb` |

**② 目標裝置：沒在線就自動拉起**

| 平台 | 檢查 | 沒有就自動開 |
| --- | --- | --- |
| Web/MWeb | Python playwright（`verify_locator.py`）自帶 headless browser，免裝置、無彈窗 | 缺 chromium → `python3 -m playwright install chromium` |
| Android | `adb devices` 有裝置 | `emulator -list-avds` 取一個 AVD → `emulator -avd <name> -no-snapshot -no-boot-anim &`，再 `adb wait-for-device` |
| iOS | `xcrun simctl list devices booted` 有 booted | `xcrun simctl boot <udid>`（取 `xcrun simctl list devices available` 第一個）→ `open -a Simulator` |

bootstrap 完成後驗可用性（`verify_locator.py` 能跑 / `adb devices` / `idb list-targets` 有 target）。**只有在自動安裝或自動開機都失敗時才停下回報**，並說明卡在哪一步。

工具與裝置就緒後，把「所有新增/修改的 locator」一次列出，逐一對照**真實元素樹**驗證與修正：

> **automator 一律用 Python playwright（`scripts/verify_locator.py`），禁用 playwright MCP。** 真正原因是**隔離性**（不只是彈窗）：MCP server 是**單一共用瀏覽器、一個 page，沒有 per-call 隔離**，並行時多個 automator driving 同一 page 會互相沖掉 navigation / 登入態；就算 MCP 跑 headless 也一樣互踩。`verify_locator.py` 每次呼叫開**獨立 headless 瀏覽器**（per-process），天然可並行、不彈窗。
>
> **MCP 只給主對話**做一次性探索 grounding（單人用、不並行），**永遠不給 spawn 出去的 automator**。
>
> **🔴 登入後頁面 grounding（禁猜元件型態、禁自建假 case）：**
> - 登入後頁面（如 `/member/basic`）的 locator **必須經真實 logged-in DOM 定案**，禁憑經驗猜元件型態（是 select2？KkSelect？原生 select？——猜錯會整條做壞）。取得登入後 DOM 兩條合法路徑：① 主對話用 MCP 探索後把 ground 好的 recipe（真實 class/屬性）交給 automator；② 用框架既有 `login_with_email_playwright` 跑到登入後 dump `storage_state`，再 `verify_locator.py --storage-state <檔>` 探該頁。
> - **禁止自建假 case ID（如 `KQT-T99001`）塞進 yaml 跑框架來 ground** —— 那會被誤判成亂跑 / 假綠，且暫存檔容易漏刪進 PR。若真要暫存探索檔，用固定前綴且**流程結束強制清除**（不靠記得刪）。

- **Web（Python playwright / `verify_locator.py`）**
  1. 對 `https://www.stage{suffix}.kkday.com/...`（**禁用** prod `www.kkday.com`）逐一驗候選 locator：
     ```bash
     python3 scripts/verify_locator.py --url "https://www.stage.kkday.com/..." \
       --candidate "css:.things-to-do-search-bar__input" --candidate "css:..."
     ```
  2. 它 headless 開頁、回報每個候選有沒有**唯一命中**；沒命中就依真實 DOM 改成能唯一命中的 css/xpath 再驗。

- **MWeb（Python playwright / `verify_locator.py` — 必須用手機 device profile，不能只縮 viewport）**
  kkday 是靠 **User-Agent**（＋`isMobile`/`hasTouch`）決定回 web 還是 mweb DOM，**不是看 viewport**。只縮 viewport 仍是桌面 UA → server 回 **web 頁**，就驗到錯的頁。
  - 加 `--device "iPhone 15"`（＝框架 mweb 用的同一台，見 `QATest/src/lib/fixtures/playwright.py:90` `devices['iPhone 15']`），`verify_locator.py` 會套該 device profile（手機 UA + `isMobile`/`hasTouch`）：
    ```bash
    python3 scripts/verify_locator.py --url "https://www.stage.kkday.com/..." \
      --device "iPhone 15" --candidate "css:..."
    ```
  - mweb 的 class 常與 web 不同，勿照搬 web locator。

- **App / Android（adb dump uiautomator tree）**
  ```bash
  adb shell uiautomator dump /sdcard/ui.xml && adb pull /sdcard/ui.xml /tmp/android_ui.xml
  ```
  解析 `/tmp/android_ui.xml` 的 hierarchy，優先用 `resource-id` → `content-desc` → `text` 找真實 locator。
  前提：`adb devices` 要有裝置在線。

  🔴 **dump 命中 ≠ Appium 打得中：候選 locator 必須在真機 Appium session 上真的 `find_elements` 打一次。**
  dump 是 XML、用 lxml 驗只證明「XPath 語意對」；真正執行的是 uiautomator2 的 XPath2 引擎，**它會對某些
  合法 XPath 直接回 500**，元素永遠 resolve 不到 → `is_present` 恆為 False → 分支靜默 no-op（外層又常有
  `try/except: pass`），只看框架 log 完全看不出原因，會誤判成「文字沒抓到」而一路改錯方向。踩過的實例：
  ```
  # 這條在 dump 上唯一命中，實機回 500：
  //android.widget.TextView[@text='旅遊期間聯絡方式']/following::android.widget.TextView[...][1]/ancestor::android.view.View[@clickable='true'][1]
  # UiAutomator2Exception: java.util.ArrayList$ListItr cannot be cast to ...NodeType
  # 改成只用 descendant 述詞就 n=1：
  //android.view.View[@clickable='true'][.//android.widget.TextView[contains(@text, '請填寫資料')]]
  ```
  → **避免 `following::` / `preceding::` 接 `ancestor::` 的反向軸串接**（尤其再帶位置述詞 `[1]`）；
  改用 `[.//...]` / `[descendant::...]` 述詞直接選中目標容器。

  **實打的做法**（畫面就停在目標頁時，別浪費一輪 13~20 分的 E2E 去試）：自起一個 Appium server，用
  `noReset:true` + `autoLaunch:false` attach 當前畫面，一次把整段互動（點入口 → sheet 內每個元素 →
  存檔 → 閘門是否關閉）都打過再寫進 code。
  ```bash
  nohup appium server -p 10099 --base-path / > /tmp/appium_probe.log 2>&1 &
  ```
  ```python
  caps = {"platformName": "android", "automationName": "UIAutomator2", "udid": "<udid>",
          "noReset": True, "autoLaunch": False, "skipDeviceInitialization": True}
  d = webdriver.Remote("http://127.0.0.1:10099", options=UiAutomator2Options().load_capabilities(caps))
  for xp in CANDIDATES:          # try/except 分開印「n=幾」與「ERR 500」——兩者意義完全不同
      ...
  ```
  驗完 `pkill -f "appium server -p 10099"`，再交回框架跑正式 run。
  （USB 線會擋事時先切 wifi：`adb tcpip 5555` → `adb connect <ip>:5555`，正式 run 用
  `--device <ip>:5555` 綁 transport。）

- **App / iOS（idb dump accessibility tree）**
  ```bash
  idb ui describe-all --json > /tmp/ios_ui.json    # 需 booted 模擬器/實機 + idb companion
  ```
  解析 tree，優先用 `AXIdentifier`（= accessibility id / name）→ `AXLabel` → `type` 找真實 locator。
  前提：`xcrun simctl list devices booted` 有 booted 裝置。

> **抓不到就停** — 對應平台的元素樹拿不到（`verify_locator.py` 進不去頁面、`adb`/`idb` 無裝置在線），**停下回報使用者**，不得憑 case 文字臆測 locator 定稿。

### 階段 3 — 自動執行

locator 驗證修正後，**自動跑一次測試**確認（走 qa-test-runner，或直接 `python -m qatest run`）：
- Web/MWeb：`export HEADLESS=1 && source <venv> && python -m qatest run --caseid KQT-Txxxx --platform web --use_driver playwright`
- App：`python -m qatest run --caseid KQT-Txxxx --platform android`（或 `ios`）

失敗時交給 **qa-test-runner** 的診斷/修復流程（它同樣會用上述元素樹抓取來修 locator）。

> **🔴 失敗（locator located failed）先「看那一頁」，別盲改盲跑（踩過的坑，尤其 App 一次跑 12–18 分）。**
> 失敗的 locator，正確答案就在**失敗當下那一頁**——先看它，再修，別憑猜改 locator 又重跑整個 E2E：
> 1. **框架失敗時已存截圖**：`~/Documents/QATest_Output/<run>/<feature>/<case>_<ts>.png`（App/Web 皆有）——先 Read 這張圖，肉眼確認失敗頁上目標元素長怎樣、值是什麼。
> 2. **再 dump 失敗頁的真實元素**定 locator（**用真實屬性、禁猜元件型態/巢狀**）：
>    - Web/MWeb：`verify_locator.py --snapshot`（登入後頁配 `--storage-state`）。
>    - Android：`adb -s <udid> shell uiautomator dump` 拉 hierarchy（看 resource-id/text/clickable/enabled）。
>    - iOS 實機：idb `describe-all` 不支援 → 起 Appium session（`noReset`+`autoLaunch:false` attach 當前畫面）取 `driver.page_source`（看 name/label/value/enabled；文字多在 **label**）。
> 3. 用 dump 到的真實屬性一次修對，再重跑驗證。**禁「改一個 locator → 重跑 12 分 → 再猜再跑」的盲改迴圈。**
> 平台差異也常在此現形（同一設計 Android 與 iOS 呈現不同，如鎖定：一邊欄位 disabled、一邊值回復）——以失敗頁實況為準，別假設兩平台一致。

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

### 🔴 忠實度紅線：測到 case 真正要驗的那條邏輯（通用，任何平台 / API / case）

「跑得起來、綠了」常常只是**走到某條剛好會過的路徑**，不代表測到 case 要驗的東西。這三條是死線：

1. **前置要求「有效 / 存在的資源」→ 必須真的建立或取得，禁止捏造假 id / 假資料。**
   case 前置若寫「有效的商品編號 / 已存在的訂單 / 某權限帳號 / 已登入憑證…」，就**先用既有 setup flow /
   helper / API 產生真實資源**再拿它的 id 來測；**不准隨手塞假 id 或跳過前置**。捏假的通常會提早撞到
   **另一條錯誤路徑**（「資源不存在」「參數缺失」「未關聯」…），根本沒走到 case 要驗那層邏輯 → 測到假的
   東西還以為過了。

2. **斷言綁 case「明確的預期結果」，禁止用寬鬆 proxy。**
   斷言要對到 expected 講的**特定結果**（特定 HTTP 狀態碼 / 錯誤碼 / 欄位值 / 狀態轉移）；**不准用「只要
   不是成功值就算」「有回應就算」這種寬鬆 proxy**。寬鬆斷言對「錯誤路徑」也會成立 → 假綠。**錯的路徑要能
   讓測試失敗**，斷言才有鑑別力。

3. **先沿用 repo 既有做法，不憑空造第二套。**
   前置怎麼建、資料怎麼備、locator 怎麼取 —— 先 grep 既有 case / test_step / test_tools 看有沒有現成的
   setup flow / helper 可重用；沿用既有才同時「對」又「跟團隊一致」。找不到現成才規劃新建。

> 具體例（**僅示意，非規則本身**）：某「無權限帳號對真實商品送審 → 回 403」的 case，若用假 oid + 沒先建
> 商品，API 會先回「未選供應商」錯誤、根本沒到權限檢查那層；又若只斷言「status ≠ 成功碼」，那個假錯誤也會
> 讓它綠。正解＝用既有建商品流程拿真 oid + 用無權限帳號 + 斷言「特定 403 / 權限錯誤碼 + 商品狀態不變」。

> 這三條在 [`qa-case-planner`](../../../agents/qa-case-planner.md) 規劃階段就先把關（攤計畫給人確認），
> automator 實作、fidelity reviewer 覆核時同樣守。

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

- 🔴 **element 一律三處齊全：`mobile/base/` 宣告 + `android/` 實作 + `ios/` 實作。**
  在 `android/` 或 `ios/` 新增任何 element，**同一輪就要補 base 宣告與另一平台的實作**，不能只改動到的那一邊。
  「另一邊」指的是 base 與另一個平台**兩者都要**，不是只有另一個平台。
  - **base**：`@property` + `@abstractmethod` + `raise NotImplementedError`。
  - **有該元素的平台**：回真正的 `Element`。
  - **沒有該元素的平台**：**寫一個 `return None` 的 `@property`**，加一行註解說明該平台改用什麼方式
    （例：Android 用 `press_device_btn(single_back)` 返回，沒有返回鈕元素）。
    ❌ 不准「base 放非 abstract stub、缺的那邊乾脆不宣告」—— 那樣缺漏不會被擋，誤用時噴
    `NotImplementedError` 而不是可判斷的 `None`。
  - 範例：`base/booking_payment_page.py:23` abstract → `ios:45` 真 Element → `android:64` `return None`。
  - 為什麼要明文寫：**Python 不會擋子類多出 base 沒有的 property**，漏寫 base 宣告時測試照樣全綠，
    唯一的防線就是這條規範（base 用 `@abstractmethod` 才能在建 `Pages` 當下 `TypeError` 擋下缺漏）。
- 定義新元件前先確認 base 是否已有相同元件，避免重複定義
- 元件文字建議用 `t('key', locale=AppConfig.language)` 取多語言，避免寫死中文
- 🔴 **把 i18n 值內插進 XPath 時一律用 `xpath_literal()`（`lib/helpers/string_helper.py`），不准直接寫 `@text='{t(...)}'`。**
  中日韓語系的值不含 `'`，所以 `@text='{...}'` 一直沒出事；但**法文（及其他拉丁語系）到處是撇號** —— `l'itinéraire`、`J'ai compris.`、`d'identité` ——
  XPath 1.0 沒有引號逸出機制，值裡的 `'` 會直接把字面量截斷，Appium 回 `InvalidSelectorException: XPathParserException ... CUP parser error`。
  這不是 locator 寫錯，是**字串組裝**錯，改 locator 沒用。`xpath_literal()` 會依內容自動選 `'...'` / `"..."` / `concat()`。
  正確寫法（`pages/mobile/android/product_page.py` 已是這樣）：
  ```python
  # ❌ 錯：法文值一含撇號就炸
  f"//android.widget.TextView[@text='{t('fill_travel_info', locale=AppConfig.language)}']"
  # ✅ 對：注意 xpath_literal 自帶引號，外面不要再包 '
  f"//android.widget.TextView[@text={xpath_literal(t('fill_travel_info', locale=AppConfig.language))}]"
  ```
  **新增非中日韓 locale 時，先掃一遍該平台 page object**：把 locale yaml 裡含 `'` 的 key 抓出來，比對哪些被 `'{t(...)}'` 內插，一次全改，別等跑到那一步才逐個炸（一輪真機 run 10~20 分鐘）。
- 🔴 **i18n locale yaml 內只准有 `key: value`，不准寫說明性註解。** 出處、取值方法、為什麼略過某些 key、候選值可信度——這些一律寫在 **PR description**，不要塞進 yaml 檔頭。repo 慣例就是這樣（`android/*.yaml`、`ios/*.yaml` 除了少數 1~2 行 section 分隔註解外都是純 key-value），寫一大段檔頭會被退。
- **新增／擴充 locale 檔（`QATestData/data/i18n/<platform>/<locale>.yaml`）時，先靜態盤點該 locale 缺哪些 key，一次補齊再跑**：
  key 缺漏不會拋錯，`i18N.get()` 會回傳 key 本身，locator 變成去找字面上的 key 名，只能靠跑到那一步才發現 —— 一輪真機 run 13~20 分鐘，逐顆試很貴。
  盤點與補值（含 ground truth 要求）的完整做法見 `qa-test-runner` SKILL.md「失敗分析 → C. 多語系（i18n）缺 key」。
  **值從哪裡拿**：先看上面「例外：kkday app 可讀 app 原始碼」段的 [🔴 補 i18n 值的取值順序](#-補-i18n-值的取值順序先問-lokalise-有沒有靜態檔沒有才動真機)
  —— iOS 有 commit 進 repo 的 Lokalise `.strings` 可直接撈；Android 是執行期 OTA、repo 無靜態檔，只能真機挖。**別對兩平台用同一套做法。**
- **iOS XCUITest locator 屬性慣例（踩過的坑，以真機 page source 為準、勿只賭 `@name`）**：
  - StaticText 的文字常在 **`@label`**（`@name` 可能空或被截斷）→ 文字比對要 `@name` 或 `@label` 都查。
  - 顯示值元素**可能是 `Button`（其 `name`/`label` = 值）而非 StaticText**（如國籍欄選值鈕）→ 別硬接 `//XCUIElementTypeStaticText`，直接讀那顆 Button。
  - 輸入框（email/姓名等）文字在 **`TextField` 的 `@value`**（name/label 常空）。
  - **iOS 實機無法用 `idb ui describe-all`**（回 FBAccessibilityCommands 不支援）→ 要 ground 就起 Appium session（`noReset`+`autoLaunch:false` attach 當前畫面）取 `driver.page_source`。

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
- 🔴 **私有 helper 一律禁止放在 module top-level（包含 `_` 開頭的普通函式）。** test_step 檔的 top-level 只能有「加了 `@function_recorder()` 的正式 test step」，不准出現 top-level 的私有 helper。抽出來的斷言/操作，依「被幾個 step 用」歸位：
  - **只被單一 step 用** → 寫成該 step 內的 **nested function**（inner function，靠 closure 直接取用外層注入的 `pages`/`uidriver`，不必自己收 fixture、不用 `_` 前綴），或直接 **inline 進該 step**。
  - **真的被多個 step 共用** → **升格成正式 public test step**（snake_case、**無 `_` 前綴**、加 `@function_recorder()`），當一個正規 step 用，而不是 top-level 私有 helper。
  - **禁止**（兩種都不行）：① top-level `_xxx` 沒 decorator 的普通函式，再從呼叫端手動傳 `pages`/`uidriver`——會噴 `missing positional argument`、也違反「所有函式都要 decorator」；② top-level `_xxx` **就算加了 decorator 也不行**——那不是「私有 helper」的正確歸位，共用就升格成正規 step、單用就 nest 進去。
  - 為什麼：`function_recorder` 靠參數名注入 fixture（見 `lib/decorators/function_recorder.py`）；top-level 私有 helper 會逼你手動傳 fixture、或讓輔助邏輯散落 top-level 難維護。nested function 用 closure 拿 `pages`/`uidriver` 最乾淨。
- Docstring 使用 Google style + 雙引號，包含 Args 和 Returns 區塊

### 命名

- Function 命名用 snake_case，禁止使用數字或中文（專有名詞除外），禁止無意義命名（如 abc/aaa/xyz）
- Playwright function 命名必須以 `_playwright` 為後綴，避免與 Selenium 方法重名

### 操作規範

- 互動前必須呼叫 `.wait()`：`pages.page.element.wait().click()`；禁止直接 `.click()` 不加 wait
- 禁止用變數暫存 page object **或其 element property**，必須每次完整寫 `pages.xxx_page.element`（每次重新 locate）。
  - ❌ `page = pages.xxx_page`（暫存 page 物件）
  - ❌ `native = pages.personal_info_page.nationality_select_native`（暫存 element property；即使只是為了少打字也不行）
  - ✅ 每次用完整 dot chain：`pages.personal_info_page.nationality_select_native.is_disabled`
  - helper 內同理；只有「從 element 取出的**純值**」（如 `text = pages.x.y.text`、`count = pages.x.y.count`）可暫存，因為那已不是 page/element 物件。
  - repo AI reviewer 會擋 element alias；本規範明文收嚴以免每次被退。（既有 code 尚有未清的 alias 屬 tech-debt，碰到再清，勿在不相干 PR 動它。）
- **禁止在 test_step 內 inline 建構 `Element(...)` / `Elements(...)`**：所有 locator 一律定義在對應 page object 的 `@property`，test_step 只透過 `pages.<page>.<element>` 取用（取 `.center`、`.text`、`.wait()` 等也一樣，先在 page object 定義好 element）。
  ```python
  # ❌ 錯：locator 寫死在 test_step、繞過 page object
  center = Element(("accessibility id", "homeTxtSearch"), pages.home_page).wait().center
  # ✅ 對：element 在 page object 定義，test_step 只取用
  #   pages/.../home_page.py:  @property def search_bar(self)->Element: return Element(("accessibility id","homeTxtSearch"), self)
  center = pages.home_page.search_bar.wait().center
  ```
- 禁止用 `time.sleep()` 或 `driver.page.wait_for_timeout()` 做硬等待，若需硬等待請用 `common.sleep_by_seconds()` 搭配 `TimeoutConstants`
- **等元素/等數量一律用 page object element 的既有 wait API「直接呼叫」，讓等不到時自然拋錯——禁止自己用 `try/except` 把 wait 包起來吞逾時。** 等可見用 `.wait()`/`.wait_for_visible()`；等集合數量到位用 `.wait_for_min_count(n)`/`.wait_for_count(n)`（框架既有方法，見 `playwright_elements.py`；既有 code 如 `search_result_page.py` 都是直接呼叫）。逾時＝元素沒出現＝測試本來就該失敗，不准用 `try: ...wait_for_min_count(n)... except Exception: pass` 把它吞掉再讀 `.count` 斷言——那會把「沒等到」的真失敗靜默成假綠、也不是框架慣例。
  ```python
  # ❌ 錯：自建 try/except 吞掉 wait 逾時（非慣例、把真失敗靜默）
  try:
      pages.loyalty_page.benefit_cards.wait_for_min_count(2)
  except Exception:
      pass
  card_count = pages.loyalty_page.benefit_cards.count
  # ✅ 對：直接呼叫，逾時自然拋錯（＝該失敗）
  pages.loyalty_page.benefit_cards.wait_for_min_count(2)
  card_count = pages.loyalty_page.benefit_cards.count
  ```
  真的需要「等不到但不丟錯」時，用框架既有的 `no_exception=True` 參數（如 `Elements.wait(no_exception=True)`），**不要自建 try/except**。框架缺對應能力就先在 `playwright_element.py`/`playwright_elements.py` 擴充（改底層要附 unit test + 回歸既有呼叫者），不要在 test_step 裡繞。
- 斷言必須用 hamcrest：`assert_that(actual, equal_to(expected))`
- 測試資料必須從 `testcase.static_test_data` 或 `testcase.dynamic_test_data` 取得，禁止硬編碼
- iOS/Android 共用同一個 test step 檔案，流程內須用 `match TestRunConfig.platform` 做平台判斷
- **App（iOS/Android）輸入文字欄位後，下一步互動前必須收鍵盤**：`.input(...)` 後軟鍵盤會蓋住畫面、擋住後續點擊/讀值（如填完「中文姓」要接著點國籍下拉）。收鍵盤用既有 `press_device_btn(btn_type="close_keyboard")`（iOS drag、Android back，`test_steps/kkday/app/common.py`），不要自己 `driver.back()`。踩過的坑：填完欄位沒收鍵盤，後續元素被鍵盤遮到 located failed。
- **App 判斷欄位「是否為空」不能只看 `.text`/`.value`**：空的輸入框讀回的是 **placeholder/hint**（Android 如「中文姓 *」、iOS 如「例：陳」），非空字串 → 直接 `not field.text` 會誤判「已填」而跳過必填、導致儲存被擋。判空要把 placeholder 一併視為空（比對已知 hint 字樣 / `例：` 前綴 / label 文字），或乾脆每次都重填該必填欄。踩過的坑：中文姓沒填→儲存鈕沒反應→整條 case 走不到驗證。
- **鎖定/停用等 UI 狀態，斷言要綁「真實狀態屬性」，且逐平台 ground、勿套用單一行為模型**：例如「國籍鎖定」的真實訊號是欄位 `enabled=false`（Android `layout_country`、iOS 選值 Button 皆然）——不要自己發明「改第二次會回復原值」這種**未經真機驗證的行為模型**去斷言（讀值常讀到本地未存的暫值而誤判）。以失敗頁/真機 dump 的實際屬性為準。
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

### 註解精簡（禁大段 essay）

- `#` 註解**只寫「為什麼」的非顯而易見理由**（一兩行），禁止把 grounding 過程、DOM 結構、平台差異寫成整段 essay 塞在 code 裡——那屬回報 / PR 描述 / commit message 的內容，不是 code 註解。
- docstring 保持精簡（pre-commit 要求函式有 docstring，但一句話講清用途即可，不要多段落解釋）。
- 判準：如果一段 `#` 註解超過 2~3 行在解釋「這個元素長怎樣 / 當初怎麼驗出來的」，就是多餘，刪掉或濃縮成一句 why。

## 發 PR

用戶要求發 PR 時，必須：
- 🔴 **先還原「為了跑測試而加的本地暫時調整」**，再 `git status --short` / `git diff --name-only` 逐項確認
  改動清單裡沒有跟本次 case 無關的檔案。典型的是 `QATest/src/lib/fixtures/mobile.py` 的 iOS
  `wdaLaunchTimeout`／`wdaConnectionTimeout`（避開 WDA 冷建 60 秒逾時用的，詳見 `qa-test-runner`
  SKILL.md「WDA 一直瞬斷」段）——那種**共用 framework fixture 的本地調參絕對不能夾帶進 case 的 PR**，
  要改就另開獨立 PR 討論。用 worktree 的話每個 worktree 各自還原。
- 🔴 **先 `git pull` 更新自己這條 branch**（`git pull --ff-only origin <當前 branch>`，遠端還沒有這條 branch 就跳過），**再** merge master。這是兩件事，不能只做後者：
  `git fetch origin master && git merge origin/master` 只把 master 合進來，**完全不會更新你這條 branch 的遠端進度**。別人（或你在別台機器 / 別個 worktree）推過同一條 branch 時，你手上就是舊的，直接 push 會被拒或覆蓋掉別人的 commit。
  - 有多個 checkout / worktree 指向同一個 repo 時尤其容易中——**push 前一定要再確認一次 branch 是最新的**。
- **接著 merge master**（`git fetch origin master && git merge origin/master`），再跑 pre-commit、再開 PR。順序不可顛倒——pre-commit 要驗的是合完的結果。
  - 合完**檢查兩邊都動到的檔案**：`comm -12 <(git diff --name-only origin/master...HEAD|sort) <(git diff --name-only HEAD...origin/master|sort)`。
  - 有交集就**逐一看合併後的完整 function**，不能只信 git 沒報 conflict：文字不衝突不代表語意相容（踩過的坑：master 在 `change_currency` 開頭加了 early return、本地改的是後段 picker，git 合得乾淨，但**本地的實機驗證是在沒有 early return 的舊 code 上跑的**，合完的路徑等於沒測過）。
  - 交集檔案落在測試主要路徑上時，在 PR 的 Testing 段**寫明實測是合併前跑的**，別讓 reviewer 以為合併後也驗過。
- 跑 `pre-commit run --all-files`
- 🔴 **PR body 語言：只有五個 `## ` 標題用英文，內文一律繁體中文**（段落、bullet、Checklist 項目文字都是）。
  下面範本只固定了英文標題、沒寫內文語言，容易被帶著整篇寫成英文——PR body 算團隊文件，適用「對團隊的文件一律繁體中文」。
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

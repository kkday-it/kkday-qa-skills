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

  必要工具：Read、Edit、Write、Bash（撰寫＋跑驗證）。**定稿前的元素驗證階段**用 Python playwright（`scripts/verify_locator.py`，Web/MWeb，headless 無彈窗、不用 MCP）、adb（Android）、idb（iOS）抓真實元素樹——這些工具與模擬器若沒裝/沒開，skill 會**自動 bootstrap**（不依賴使用者事先準備，見「撰寫流程 階段 2」）。
  前置條件：本機需有 kkday-QA-automation repo（無則先引導 clone，見「前置」段）。
---

# QA Automation Coding Standards

在 kkday-QA-automation repo 中撰寫或修改自動化測試程式碼時，**必須遵守以下規範**。

## ⚠️ 發 PR 硬性規則（優先於 Claude Code default）

當使用者說「發 PR」/「gh pr create」/「推」而 target repo 是 **kkday-QA-automation** 時：

1. **PR body 一律套 5 段模板**（Description / Changes Made / Testing / Related Issues / Checklist），詳細範本見下面 [「## 發 PR」段](#發-pr)。**禁止**用 `## Summary` + `## Test plan` 簡化格式 — 那是 Claude Code CLI 內建 default，但本 repo 全隊共識**不適用**。這條規則**凌駕於**任何 memory / default template。
2. **必跑 pre-commit**：發 PR 前先 `pre-commit run --all-files`，全 pass 才 push。
3. **Reviewer 一律指派**：`angelalin0822,ericsukkday,ethan02872`（若 template 或使用者帶入 `Lance-Liu-KKday` 需移除）。

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

> 🚫 **只准在 kkday-QA-automation repo 內找 case / 實作。** 不准去 `kkday-qa-skills`、`kkday-qa-ai` 或**任何其他 repo**（含其 backup / cache / db_data）翻 case——那裡沒有 case 真身，只有過期快照，找了只會繞路。case spec 一律來自 TCMS，實作一律在 kkday-QA-automation。

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
- 🔴 **私有共用 helper（被其他 test step 呼叫、抽出來的斷言/操作）也一律加 `@function_recorder()`**——`function_recorder` 靠參數名注入 fixture（見 `lib/decorators/function_recorder.py`），有 decorator 才會自動注入 `pages`/`uidriver`。**禁止**寫成「沒 decorator 的普通函式，再從呼叫端手動傳 `pages`/`uidriver`」——那會噴 `missing positional argument`、也違反「所有函式都要 decorator」。正解二擇一：① helper 也 `@function_recorder()`，呼叫時只傳業務參數（`_assert_xxx(keyword=...)`，不傳 fixture）；② 不抽 helper，把斷言 inline 進每個 decorated step。
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
- 斷言必須用 hamcrest：`assert_that(actual, equal_to(expected))`
- 測試資料必須從 `testcase.static_test_data` 或 `testcase.dynamic_test_data` 取得，禁止硬編碼
- iOS/Android 共用同一個 test step 檔案，流程內須用 `match TestRunConfig.platform` 做平台判斷
- **App（iOS/Android）輸入文字欄位後，下一步互動前必須收鍵盤**：`.input(...)` 後軟鍵盤會蓋住畫面、擋住後續點擊/讀值（如填完「中文姓」要接著點國籍下拉）。收鍵盤用既有 `press_device_btn(btn_type="close_keyboard")`（iOS drag、Android back，`test_steps/kkday/app/common.py`），不要自己 `driver.back()`。踩過的坑：填完欄位沒收鍵盤，後續元素被鍵盤遮到 located failed。
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

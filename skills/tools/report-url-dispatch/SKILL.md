---
name: report-url-dispatch
description: 使用者貼 ai_studio test-suite report 的 URL（`autotest-service.sit.kkday.com:8081/ai_studio/test-suites/report?...uuid=...`，帶或不帶 `caseid`）時觸發。把 URL 解析成 case + platform + 失敗 log，先分診判斷是不是 flaky，重現確認後才派工給 qa-case-automator 修。這只是多一種入口——手打 `KQT-T7562 android` 仍然照舊走 `qa-test-runner`，兩種並存。
---

# Report URL → 派工

## 這個 skill 解決什麼

以前要修一個跑掛的 case，使用者得自己從 report 頁面讀出 case id、自己知道那個 suite 是哪個平台，
再手打 `KQT-T7562 Android` 給 agent。現在直接貼 URL 就好——platform 從 suite detail 自動推、
失敗 log 從 report 自動撈。

**但不會拿到 URL 就直接改 code。** 失敗有可能是 flaky（或環境／語系問題），盲目修會把對的東西改壞。
流程一定是：解析 → 分診 → 重現確認 → 才修。

## 觸發樣態

🔴 **這個 skill 不排擠既有輸入方式**。使用者打 `KQT-T7562 android`（case id + platform）時
**照舊走 `qa-test-runner`**，不要因為這個 skill 存在就要求對方改貼 URL。兩種入口並存，
差別只在 URL 這條能自動補出 platform 跟失敗 log。

| 使用者貼的 | mode | 處理 |
|---|---|---|
| URL 帶 `&caseid=KQT-T7562` | `single` | 只處理那一個 case |
| URL 只有 `uuid`（可能帶 `reportFail=1`） | `batch-fail` | 處理該 report **所有 Fail** 的 case，**循序**一個一個做 |
| 裸 uuid `a4f60e7d-…` | 同上 | script 也吃 |

## 步驟

### 1. 解析 URL（唯一入口，不要自己拼 API）

```bash
python3 ~/.claude/skills/report-url-dispatch/scripts/resolve_report.py "<貼進來的 URL>"
# 要餵給程式時加 --json；要完整 log 加 --full
```

輸出含：suite title、**platform**、device、environment、run status，以及每個目標 case 的
`fail_function`、步驟鏈、`terminal_output`、runner 上的 `log_file_path`。

解析鏈路（platform 是 report 本身沒有的，必須繞 suite detail 才拿得到）：

```
URL --uuid--> /api/test-suite/cached-report/<run_uuid>  → cases / terminal_output / fail_function
         └--> data.test_suite_uuid
              └--> /api/test-suite/cached-suite-detail/<suite_uuid> → platform / device / environment
```

🔴 **report 還在跑時（`still_running: true`）fail 清單會繼續長**。script 會標警告。
batch 模式遇到這種，先把當下這批做完，做完再重跑一次 script 看有沒有新增，別假設第一次撈到的就是全部。

### 2. 分診——先判斷，不要動 code

拿 `terminal_output` 對照 `qa-test-runner` SKILL.md「失敗分析」的 A~E 分類先下一個假設：

| 類 | 特徵 | 是不是該改 code |
|---|---|---|
| A 元件路徑更改 | `NoSuchElementException` / `TimeoutException` / locator 過期 | 可能，但先排除 C、E |
| B 流程更改 | 元素在、互動結果不符預期、步驟少了或多了 | **不自動修**，回報使用者決定 |
| C i18n 缺 key | locator 在找一個英文 key 名 | 補 yaml，不是改 locator |
| D 載到別平台那份 case | app case 噴 ChromeDriver 錯誤（或反之） | 不是 driver 問題，看 `case.platform` |
| E 語系污染 | **批次全掛、單張跑會綠** | **不改 locator**，改了就是把對的東西改壞 |

另外標記 **flaky 嫌疑**（這是不派工的主要理由）：
- 錯誤是 timeout／等待類，而非「元素完全不存在」
- 同 suite 其他同類 case 都過
- 斷言訊息像時序問題（拿到空值 / 舊值，而不是拿到錯的值）
- 這個 case 前幾天在同 suite 是 Pass 的

### 3. 重現——跑一次確認真的壞

照 `qa-test-runner` 用 script 給的 platform 跑該 case（**不是** report 上跑測機那台，是本機）：

```bash
~/.claude/skills/qa-test-runner/scripts/run_case.sh <caseid> <platform>
```

App（android/ios）記得 qa-test-runner 那邊的前置：清殘留 appium、`export ANDROID_HOME`、
iOS 包 `caffeinate`。丟背景跑要驗證真的起來（`total 0 cases` 是沒跑，不是綠）。

🔴 **分診是 A（找不到元素）且 platform 是 app 時，重現這一輪就要順手掛 sniffer** —— 一輪 app run
要 15~20 分鐘，「先跑一輪確認壞、再跑一輪抓畫面」是拿 20 分鐘換一份本來可以同時拿到的東西：

```bash
# run 起來之後、還沒跑到失敗點時掛上；trigger 從 page object 複製那段 locator
~/.claude/skills/qa-test-runner/scripts/sniff_live_element_tree.py "<失敗 locator 的一小段>"
```

它從正在跑的那個 session 唯讀撈失敗當下的元素樹＋可見節點清單＋截圖（窗口是 `wait()` 的 60 秒
輪詢），存進 run 目錄。**這也是 report 那邊拿不到的東西** —— API 只給 `terminal_output`，沒有畫面。
細節與「為什麼不能等跑完再另起一台 appium」見 `qa-test-runner` SKILL.md「趁 run 還在跑撈失敗畫面」。

🔴 **撈到畫面、看出新 locator 之後，同一輪再把下游「點點看」**，不要只驗那一顆就派工：

```bash
PAGEOBJECT_DEFAULT_WAIT_TIMEOUT=600   # 重現那一輪開跑前 export，把窗口從 60 秒撐到 10 分鐘
~/.claude/skills/qa-test-runner/scripts/probe_live_session.py \
  --after "<失敗 locator 的一小段>" --steps /tmp/probe.txt --confirm-mutates
```

第三方 App 改版一次動一整段，只修一顆的下場是「重跑 15 分鐘 → 死在下一顆 → 再修一顆」。
run 死在那一頁時 session 還活著，那是唯一能一次攤出整段破口的時機。見
`qa-test-runner` SKILL.md「點點看」。

| 重現結果 | 動作 |
|---|---|
| **失敗，且失敗點跟 report 一致** | 確認真壞 → 進第 4 步派工 |
| **失敗，但失敗在別的地方** | report 那個 log 已經過時，用**本機這次**的 log 當派工依據 |
| **Pass** | 判定 **flaky / 環境**，🔴 **不派 automator、不改 code**，回報使用者：case、report 上的錯、本機重現過了。要不要再跑一次確認由使用者決定 |

### 3.5 讀共享 registry（派工前必做，不是可選）

🔴 **重現完、派工前，先讀一次共享 registry**。fix 路線刻意跳過 `qa-case-planner`，而 planner
是唯一被規定要讀 registry 的角色 —— 所以這條路線如果不補這一步，就是**整個流程裡最常走、卻從來
沒讀過共享記憶的那條**。實測後果：locator registry 累積 1600+ 筆，flow registry 的 stale 率
兩個月恆為 0.0（沒人在讀），同一件事被不同人各寫一套差不多的 test step。

```bash
S=~/.claude/.../kkday-qa-skills/scripts
# app：先探索有哪些 flow key（別猜字串），再取候選
python3 $S/fetch_locator_registry.py --case <KQT-T…> --platform <ios|android> --list-flows --q <關鍵字>
python3 $S/fetch_locator_registry.py --case <KQT-T…> --platform <ios|android> --flow <上面挑到的 key>
# web/mweb：走 valve（會順便驗）
python3 $S/locator_valve.py --case <KQT-T…> --platform <web|mweb> --flow <key>
# 順手看有沒有現成可重用的 step/flow（避免 automator 重造）
python3 $S/get_verified_flow.py --case <KQT-T…> --q <關鍵字> --platform <platform> --repo-path <framework repo>
```

- **`--case` 一定要帶**：Stop 的 registry 讀取硬 gate 按 case 比對收據，不帶等於沒讀
  （`scripts/check_registry_read_gate.py`，automator 交付時 arm 的 claimed 檔就是它的 claim 來源）。
- 讀回來是空的**也算過** —— 收據記的是「有沒有去問」。但**不准跳過不問**。
- 撈到的東西要**放進第 4 步的 automator prompt**（既有 locator / 現成 step / 誰在什麼時候驗過），
  不要只是跑完就丟掉：automator 看不到你的終端輸出。

### 4. 派工給 qa-case-automator

這些 case 一定是**既有 case**（跑得出 report 就代表已實作），所以照 CLAUDE.md 走 **fix 路線**：
**跳過 `qa-case-planner`**，直接 automator。重跑 planner 只會產出跟現況打架的計畫。

```
Agent(subagent_type='qa-case-automator',
      prompt='case=<KQT-T…> platform=<platform> 既有實作，最小改。
              report 失敗訊息=<terminal_output 摘要>
              本機重現失敗訊息=<第 3 步的 log 尾段>
              分診假設=<A/B/C/D/E 哪一類>
              registry 已讀=<第 3.5 步撈到的既有 locator / 可重用 step，或「查無」>
              元素樹實證=<sniff 出來的節點，例：該節點已從 StaticText 改為 Button>
              下游探測結果=<probe 的破口清單，或「整段下游走得通」>')
Agent(subagent_type='qa-case-fidelity-reviewer', prompt='case=<KQT-T…>')
```

**不可在主對話 inline 改實作檔**：實作路徑被 `agent_only_impl_guard.py` 綁定只有 automator 能寫，
且兩個 Stop gate 靠它寫 claimed 檔才會 arm。

唯一該回頭找 planner 的情況：根因不是壞掉，而是 case 規格本身變了、或要新增沒覆蓋的平台。

### 5. batch-fail 模式：循序，不要並行

同一個 report 的 fail case **同平台、同一台裝置**，並行會搶 appium / 裝置，全部一起爛。
一次一個：分診 → 重現 → 修 → 下一個。

每做完一個回報一行進度（case、判定、有沒有改 code），全部做完再給總表：

| case | 分診 | 重現 | 處置 |
|---|---|---|---|
| KQT-T7562 | A locator | 重現 | 已修，automator + fidelity 過 |
| KQT-T7556 | flaky 嫌疑 | Pass | 未改 code，待人判斷 |

## 注意

- report 的 `log_file_path` 指的是**跑測機器（qateam1）**上的路徑，本機不存在、服務也沒開檔案下載 API。
  能用的只有 `terminal_output`（已含失敗函式、行號、斷言訊息），多數時候夠分診。
- report API **不提供任何截圖**——case 欄位裡沒有 screenshot，前端也沒有 image/artifact endpoint。
  要畫面只能自己重現時抓，而且要**在重現那一輪進行中**用 `sniff_live_element_tree.py` 撈（見第 3 步）；
  跑完才想抓就來不及了 —— appium 已關、App 已離開失敗頁。
- **一輪重現要同時產出兩份東西**：失敗當下的畫面（sniff）＋下游流程的破口清單（probe）。
  少了第二份，派工只會修到第一顆，第二顆要再花 15 分鐘才會浮出來。
- API 無需認證，內網直接 GET。

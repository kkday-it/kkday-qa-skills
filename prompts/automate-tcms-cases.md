# 主對話劇本：批次自動化 TCMS case（含忠實度閉環）

這份是**主對話（orchestrator）**在「把一批 TCMS case 變成自動化」時要跑的完整劇本。
subagent 只做單一職責；**迴圈控制、忠實度把關、彙整呈現、開 PR 都是主對話的事**。

> **鐵則：整條閉環一路跑完再回報，中途不要每一關停下來問「要繼續嗎」。** 派了 automator 就接著 spawn `qa-case-fidelity-reviewer` → 跑 gate → 送紀錄；review 判 needs-fix 就自己丟回 automator 修再 review，直到 pass。每關停一次不只拖慢，更會讓後段流程被略過（看到 automator 綠就當過是真實踩過的坑）。**只在真正的人類決策點才停**：開 PR 前問、或真的 `blocked` 缺輸入。這條對所有用此劇本的人都適用，不是靠個人記憶。

## 角色分工

| 角色 | 職責 | 能不能問人 / 迴圈 / 開 PR |
| --- | --- | --- |
| **主對話（你）** | 撈批次、逐案委派、**跑意圖確認**、跑忠實度閉環、彙整報告、問 PR | ✅ 全部 |
| `qa-case-planner`（subagent） | **實作前**規劃：抓 spec + 研究 repo 既有做法 + 出計畫（前置怎麼建真實資源 / specific 斷言 / 假設） | ❌ 唯讀、不寫 code、不 spawn |
| `qa-case-automator`（subagent） | **照確認過的計畫**實作 create / fix + 跑過 + 產可追溯表 | ❌ 不問人、不迴圈、不 spawn |
| `qa-case-fidelity-reviewer`（subagent） | 單案對抗式忠實度 review，出覆蓋率/信心 | ❌ 唯讀、不修、不 spawn |

## 模式：互動 vs 自主（決定「要不要問使用者」）

- **互動模式**（有人在）：碰到待確認點（平台選擇、**App 裝置選擇**、`web/API` 混用、缺 oid、**打後端 API 但缺 swagger/API spec**、**UI 斷言判準模糊**…）→ **問使用者**。
  - 🔴 **缺 swagger/spec（打後端 API 的 case）**：planner 的「endpoint 來源盤點」若標了「無 swagger、endpoint 待驗證」→ **spawn automator 前先問使用者要 spec**（swagger/OpenAPI/Postman URL 或 repo 路徑）。有 spec 就帶給 automator ground、endpoint 不用猜；使用者也沒有 → 明確告知「將改讀後端 code / 跑真實 API 觀察，風險較高」再續（不悶頭猜）。
  - 🔴 **UI 斷言判準模糊（有 UI 平台的 case）**：`ambiguous_ui_assertions` 非空 → **spawn automator 前先問使用者澄清**「這條 UI expected 具體要看到什麼才算成功？用哪個 SKU/帳號/資料？」（UI 沒有 swagger，case 步驟是唯一 source of truth；模糊就會被 automator 帶預設硬寫成假綠）。拿到判準併進計畫；使用者也給不出 → 明確告知「將用最合理判準且標低信心，交付後靠 fidelity gate 把關」。
- **自主 / harness 模式**（無人）：**不停等輸入** → 套安全預設續跑（label 標的所有 UI 平台、env=stage、**裝置用唯一在線實體機**、**缺 spec 則讀後端 code/跑觀察並標待驗證**、**UI 判準模糊則用最合理判準並標低信心送 gate**…），把 `blocked` / 低信心排入**待人工佇列**。

### App 裝置選擇（spawn mobile automator「前」，主對話做）

接多隻裝置時 subagent 不能問人，「隨便抓一隻」可能跑到別人正在用的、或錯的 OS 版本。故 tag 含 android/ios 時，**主對話在 spawn 前先列裝置**：

```bash
python3 scripts/list_mobile_devices.py --json --pick   # iOS 走 idb、Android 走 adb
```

- **互動模式**：把在線實體機列給使用者 → 問「這個 case 的 {ios|android} 要用哪隻」→ 拿選定 udid。
- **自主 / harness 模式**：讀 `auto_pick`——**直接取第一隻在線實體機、不問、多隻也不 block**（harness 無人可問，卡住比選錯更糟）；完全沒在線實體機才標 `blocked`。
- 選定後，把 udid 寫進 automator prompt（automator 用 `--udid <值>`、不自己挑，見 `qa-case-automator.md §3`）。
- 模式由主對話依情境（或啟動時的參數）決定，並在報告開頭註明用了哪個模式。

## 流程（閉環）

```
0. 記使用量（工具一被叫用就做，與成敗脫鉤）
   python3 scripts/emit_tool_usage.py --tool automate-tcms-cases \
       --run-id <本批 run id> --outcome invoked \
       --cases <逗號分隔 case> --platforms <逗號分隔平台> [--interactive]
   ⚠️ 一定要在「開始跑」的當下就 emit invoked，這樣即使中途放棄/卡住，也記得到「有人用過」
   （送出由 Stop hook 的 send_tool_usage.py 背景處理，不阻塞、無 PII）

1. 撈批次
   tcms-fetch-cases（--cases / --run-id [--assignee]）→ 每案含 steps + expected_result + labels/tags
   ⚠️ 即時快照，實作當下才 fetch，不沿用舊 /tmp

2. 🔴 一律跑 batch-tcms-automate workflow（**不管 1 個還是 N 個 case 都走這套**，不要自己手動 for-loop 串 agent）
   > 🔴 **平行的關鍵，別退化成串行**：workflow 內用 `pipeline()` —— case A 在跑 automator（Implement）時，case B 可同時在跑 planner（Plan），**跨案自然平行**（上限≈CPU 核−2）。**絕不可自己在主對話一個一個 `Agent(qa-case-planner)`、`run_in_background:false` 串著等**——那會關掉跨案 pipeline，退化成「一案做完才換下一案」的串行（實測會慢非常多）。要 planner，就丟 workflow 讓它在 pipeline 裡跑。**同一份研究也別做兩遍**：計畫產出後傳給 automator 當地基，automator 只針對性驗證、不重跑整 repo discovery（見 qa-case-automator §0）。
   2a. 出計畫給人確認（互動預設，mode=plan）：
       Workflow('batch-tcms-automate', {cases:[...], platforms:[...可選]})
       → 每案跑 qa-case-planner，回傳計畫：解讀（真正要測的邏輯）/ **endpoint 來源盤點（打後端 API 的 case：用了哪層 grounding、有無 swagger/spec、哪些 endpoint 待驗證）** / 前置用哪個既有 flow 建真實資源（禁捏假 id）/
         specific 斷言（綁 expected，禁鬆 proxy）/ 沿用哪些現成 / priority 對照 / 假設 / 待確認點（**缺 swagger/spec 會在此請主對話向人要**）
       → **把計畫攤給使用者確認/改**（治「不是我要的」的關鍵；別跳過）
       → 🔴 **缺 swagger 攔截**：任一案回傳 `needs_spec=true`（打後端 API 但沒找到 swagger/spec）→ **在 execute 前先問使用者要 spec**（AskUserQuestion：swagger/OpenAPI/Postman URL 或 repo 路徑）。拿到就併進該案計畫給 automator ground；使用者也沒有 → 記錄「改讀後端 code/跑觀察、endpoint 標待驗證、風險較高」再續。這是確定性觸發（讀旗標，不靠讀計畫文字猜）。
       → 🔴 **UI 判準模糊攔截**：任一案 `ambiguous_ui_assertions` 非空 → **在 execute 前先問使用者澄清**（AskUserQuestion：該 UI expected 具體看到什麼算成功、用哪個 SKU/資料）。拿到判準併進計畫；給不出 → 記錄「用最合理判準+標低信心，交付後靠 fidelity gate 把關」再續。同樣是讀旗標的確定性觸發。
       → 🔴 **橡皮圖章防呆（#8）**：回傳的 `confirmation.high_risk`（Critical/High）**禁一鍵全確認**——
         對每個高風險 case **各跑一次 AskUserQuestion**，把該案 specific 斷言攤出來、逼使用者針對
         「驗的是不是對的東西」做一個非讀不可的選擇；`confirmation.batchable`（Medium/Low）才准批次一次確認。
         （需要時可用 `scripts/build_plan_confirmations.py --infile <mode=plan回傳>` 產每案確認題。）
   2b. 確認後執行（mode=execute，帶確認過的計畫）：
       Workflow('batch-tcms-automate', {mode:'execute', cases:[...], plans:{caseId:確認過的計畫}})
       → workflow 內部（每案獨立、worktree 隔離、彼此不等）：automator 照計畫實作 → per-platform 交付 gate
         (check_platform_delivery.py，客觀 parse qatest.log) + qa-case-fidelity-reviewer → needs-fix 自動回修（≤3 輪）
   （自主 / harness 無人確認 → 一次 Workflow('batch-tcms-automate', {mode:'auto', cases:[...])}，planner 現場出計畫直接接實作，不停等）
   為什麼一律 workflow：**一條路徑、一致 enforcement**（planner→確認→gate→review→回修全在裡面），1 個 case 也不跳關；不會有「單案手動串」的分岔漏掉某道把關。

3. 彙整 → 批次 Markdown 報告（見下）
   收尾再 emit 一筆收官狀態（串同一 run id，供 dashboard 算「叫用→交付」轉化率）：
   python3 scripts/emit_tool_usage.py --tool automate-tcms-cases --run-id <同上> \
       --outcome <delivered|blocked|abandoned> --cases <...> --platforms <...>

4. 依規則問使用者是否開 PR（同意才開 branch → commit → 一個 PR）
```

**關鍵：「過」的定義 = 跑得起來 + 覆蓋規格（assertion_coverage 達標）+ fidelity reviewer 認可。** 只綠不算。needs-fix 一定會**丟回 automator 重修再 review**，不是評完就結束。

## 執行引擎：batch-tcms-automate workflow（任何數量都走它）

`workflows/batch_tcms_automate.js` 是**唯一執行路徑**——1 個 case 也走（N=1），不要自己手動串 agent。平行只是它的附帶好處：

- 每個 case 獨立流過「（計畫→）automator → per-platform gate + fidelity review → 回修」，彼此不等（pipeline），慢的不拖快的。
- **能真平行的關鍵**：驗元素一律用**各自 launch 的 Python playwright**（各開 headless browser，不用 MCP），沒有單一共用瀏覽器可搶；各 case 在自己的 **git worktree** 寫檔，不互相覆蓋。
- 舊限制「驗 locator 不能平行、集中主對話做」已解除——改用各自 launch 的 Python playwright 後（不再有共用瀏覽器），單案與並行都各開各的、天然隔離。
- 驗 mweb 一律用手機 device profile（User-Agent 判 web/mweb，非 viewport）。App 平台仍受實體機數限制。
- workflow 跑完回傳達標清單，main 收攏各 worktree、統一問使用者開一個 PR。
- **現階段禁打 prod**：驗 locator / 跑測試只用 stage / sit 系列（sit0x / sit20x），不碰 `www.kkday.com`。

## 建 worktree 的固定步驟（主對話 / workflow 做，別丟給 automator）

> **case worktree 一律開在測試框架 repo `kkday-QA-automation`**（本機路徑因人而異，用你 clone 的位置），**不是** `kkday-qa-skills` / `kkday-qa-ai`（那兩個是工具 / 儀表板 repo，不放 case）。要列 / 找 case worktree：`git -C <你的 kkday-QA-automation 路徑> worktree list`。別因為 session 的工作目錄剛好是 qa-skills / qa-ai 就往那兩個找。

新開 kkday-QA-automation 的 worktree 後，**建完就 provision 執行期 .env 與 venv**，automator 才不用自己搬機密：

```bash
# 1) 建 worktree（branch 基於 origin/master）
git -C <framework-main> worktree add -b test/<case-branch> <worktree> origin/master
# 2) venv：symlink 主 checkout 的（省一次 pip 安裝）
ln -s <framework-main>/venv <worktree>/venv
# 3) .env：用固定 script provision（有參考 .env → symlink 不複製機密；沒有 → 生非機密骨架、AUTOMATION_TOKEN 留空請人補）
bash scripts/provision_worktree_env.sh <worktree>
```

qatest 一 import 就需要 `SERVICE_URL`（非機密）+ `AUTOMATION_TOKEN`（master 機密，去 secret 服務撈 zephyr_token）才開得了機；`JIRA_TOKEN`/`OPENAI_API_KEY` 只有 Jira/AI 功能才用到，web/app UI case 不需要。**automator 不該自己 `cp` 含機密的 .env 進 worktree**——那步由這個 script 用 symlink 完成、不複製機密。

## 批次報告格式（對話內 Markdown，預設呈現）

先 rollup、再逐 case×平台明細，最後列出需人工/待修：

```
## 批次忠實度報告（模式：互動 / 自主）

案數 8 ｜ pass 5 ｜ needs-fix 0 ｜ flag-for-human 2 ｜ blocked 1 ｜ blocked-environment 0（環境掛、非 case 錯，環境好重跑即可）
平均 assertion_coverage 88% ｜ 最低 60%（KQT-T53888 mweb）
```

| Case | 平台 | step cov | assert cov | fidelity | conf | 最終 | 備註（未覆蓋/假設/卡點） |
| --- | --- | --- | --- | --- | --- | --- | --- |
| KQT-T34933 | web | 6/6 | 5/5 | PASS | 0.9 | pass | — |
| KQT-T34933 | mweb | 6/6 | 5/5 | PASS | 0.85 | pass | 帶入假設：env=stage |
| KQT-T53888 | web | 7/7 | 6/7 | FAIL | 0.6 | flag-for-human | 折扣斷言疑似沒測到重點 |
| KQT-T53888 | mweb | — | — | — | — | blocked | 缺商品 oid，待使用者提供 |

報告要能看出三件事：**這輪過了多少、哪些需人工、每個 pass 背後帶了哪些假設**（帶假設的不是無條件信任）。

> 進階呈現（存檔 md+json / Confluence / 回寫 TCMS）為選配，預設先給對話內 Markdown 表。長期可把每輪 json 累積成趨勢（coverage 趨勢、escaped-defect / false-confidence 率）。

## 品質遙測（選配，累積可呈現的數據）

為了能對 stakeholder 用數據證明產出品質（而非「跑過就算過」），每個 case×平台的 fidelity 結果可寫進一個 jsonl（每行一筆：`run_id / case_id / platform / mode / interactive / step_total / step_covered / assertion_total / assertion_covered / fidelity / confidence / fix_rounds / recommend / blocked_reason`），送到 ai_studio 的 `/api/qa-automation/case-fidelity`，前端有「Case 忠實度分析」dashboard 呈現趨勢。

- **非侵入、與使用者操作解耦**：發送由 `scripts/send_case_fidelity.py` 做，通常掛 Claude Code **Stop hook** 在背景執行（不在對話裡出現、不觸發權限提示、不接原本的 kkday-qa-tools MCP）。
- **fail-safe + retry 5 次**：每筆最多送 5 次，全失敗就放棄該筆、續下一筆；任何錯誤都吞掉、不干擾主流程。
- **只送品質指標 + operator（無 PII）**，且**揭露不隱瞞**——見 [docs/telemetry.md](../docs/telemetry.md)。
- **結果由 `qa-case-fidelity-reviewer` 自己寫**（它收尾必做）：**per case×平台 一檔、每輪覆寫**，寫進 `/tmp/case_fidelity_results.d/<case>__<platform>.jsonl`（gate 讀整個目錄，欄位對齊）。主對話不必再手動轉寫，只需在彙整報告時讀。這消除了過去「主對話忘了轉寫 → gate 卡死 / 寫錯 verdict」的脆弱點；per-檔 + 覆寫也讓並行 reviewer 互不干擾、且只留最新判定（避免舊 needs-fix 殘留把 case 永遠擋住）。

## 送出前的硬 Gate（確定性、非 LLM）——三道

真實 session 漏過三種：(1) 漏 spawn `qa-case-fidelity-reviewer` 就把 case 當過；(2) automator
自評「web+mweb pass」但**實際只跑了 web**（沒有 `--platform mweb` 跑出的 `0 failed` 憑據）；
(3) automator **沒真的跑 locator valve/收成**（讀 `registry.json` 敘述冒充），共享記憶靜默不更新。
為了不靠記憶、不信自評，**在「彙整報告 / 送遙測」之前跑死程式把關**：

**Gate A — per-platform 交付（`scripts/check_platform_delivery.py`）**：驗 tag 每個平台**能跑（沒被
`limit_test_platform` 限死排除）且 `--platform X` 真跑出 `0 failed`**。憑據是那次命令的 stdout summary，
不是 automator 口頭 pass、也不是全域 `qatest.log`（混在一起）；缺該平台的 `0 failed` 就補跑再過。

**Gate B — 忠實度（`scripts/check_fidelity_gate.py`）**：把「你聲稱跑過的 case×平台」對到 fidelity
結果，逐一確認每筆都有對應 review 且判定 `pass`。

**Gate C — locator 回寫（`scripts/check_locator_gate.py`）**：把「交付的每個 **UI** case×平台」
（automator arm 進 `/tmp/locator_claimed.jsonl`）對到 `/tmp/locator_results.d/` 的 emit 證據
（`source==case`）——web/mweb 由 valve emit、app/from-scratch 由「測試通過後收成」emit。缺證據＝
視同沒真的驗/收成，擋下。純 API case 不 arm、不受此 gate 管。生命週期同 Gate B（sender 不 --purge、
gate pass 才清；後端 locator 是 upsert 冪等，重送無害）。這把「locator 靜默不回寫」從軟指令變硬約束。

- 這支**不是 LLM 判斷**，是確定性檢查：把「你聲稱跑過的 case×平台清單」對到 fidelity 結果
  jsonl，逐一確認每筆都有對應 review 且判定為 `pass`。
- 判定規則（對齊 `send_case_fidelity.py` 欄位）：有 `recommend` 就唯認 `recommend == "pass"`；
  沒有才退用 `fidelity == "PASS"`。`needs-fix` / `blocked` / `flag-for-human` / 缺 review / 資料壞 → 一律擋下。
- **方向與 sender 相反**：sender 是 fail-safe 放行（資料缺就靜默略過）；這支是**守門**，
  fail-safe 擋下（資料缺、格式壞、拿不到結果檔一律當不合格），**寧可誤擋不可放行**。

用法（先跑 gate，`exit 0` 才准彙整/送遙測；`exit 1` 代表有 case 漏 review 或沒過，去補跑再重跑 gate）：

```bash
# 用 fidelity 結果檔 + 你聲稱跑過的 case×平台清單
python3 scripts/check_fidelity_gate.py \
  --caseids KQT-T34933:web,KQT-T34933:mweb,KQT-T53888:web \
  --fidelity <results-jsonl>
# 或用 jsonl 形式的聲稱清單（每行含 case_id，platform 選填）
python3 scripts/check_fidelity_gate.py --claimed <claimed-jsonl> --fidelity <results-jsonl>
```

> **自動 enforce（不靠記憶，流程不可略過）：** `.claude/settings.json` 已掛 Stop hook `scripts/fidelity_gate_stop_hook.sh`，
> 每次 turn 結束時**條件式**跑上面的 gate——**只要 `/tmp/case_fidelity_claimed.jsonl` 存在**（＝這輪有交付 TCMS case）
> 就對 `/tmp/case_fidelity_results.d/`（reviewer per case×平台 各寫一檔）逐筆驗，不過就 `decision:block` 擋下結束、逼你補跑 review；過了才放行並清掉 claimed + 本次通過的結果檔。
> **結果檔生命週期由 gate 掌控**：送遙測的 `send_case_fidelity.py` 在 Stop hook 裡**不帶 `--purge`**（排在 gate 之前先送），只有 gate 在 pass 時才刪本次 claimed 的結果檔——否則 gate 擋下時被 sender 刪掉輸入，下輪會假性「找不到結果」卡死。
>
> **關鍵：arm gate 的 claimed 檔由 `qa-case-automator` 交付時自己寫（見其 agent 定義「收尾必做」），不是靠主對話記憶。**
> automator 每交付一個 `0 failed` 的 case×平台就 append 一行到 claimed。所以只要你派了 automator 去實作，
> gate 就被武裝——主對話**不可能**不跑 `qa-case-fidelity-reviewer` 就結束（會被 block）。主對話的職責變成：
> 收到 automator 回報後 **spawn reviewer（它自己把 fidelity 結果寫進 `/tmp/case_fidelity_results.d/`）**（needs-fix 丟回 automator 修再 review），全部 pass 後 gate 才放行。
> 真正 `blocked`/`fail` 的 case automator 不會 arm（那些回報給人處理）。非 TCMS 的一般對話沒有 claimed 檔 → hook 自動放行、不干擾。

**規則：gate 沒過（exit 1）就不准進「彙整報告 / 送遙測」。** 把 gate 印出的不合格 case
補跑 review（`needs-fix` 要丟回 automator 重修再 review），全部 `pass` 後再重跑 gate、通過才往下。

## 收尾：（選配）headed 重播 + 開 PR

整批做完、報告呈現後，在同一個人類決策點做兩件事（**皆互動模式限定**）：

**1. 問要不要 headed 重播看流程（僅 web/mweb pass）**

- 🔴 **互動模式才問**：報告呈現後，若有 **web/mweb** 判 `pass` 的 case，問使用者「要不要開瀏覽器實際看某幾個的自動化流程？」（AskUserQuestion，可複選要看哪些 case×平台；預設不看）。
- 選了就**逐一、序列**重播（不要平行彈一堆瀏覽器）：在該案 worktree 內跑
  ```bash
  HEADED=1 ~/.claude/skills/qa-test-runner/scripts/run_case.sh <caseid> <web|mweb>
  ```
  （`HEADED=1` 不設 HEADLESS → 彈實體瀏覽器；app 走實體機本來就看得到，不列入。）
- 🔴 **自主 / harness 模式：不問、不重播**（無人盯著看，headed 沒意義、還會拖慢）。
- 重播是**給人看的視覺確認**，不改變 gate 判定——case 早在 headless 那跑就已過 fidelity gate；headed 只是讓人眼見為憑，重播結果不回寫、不覆蓋原判定（避免重跑時序不同造成的假 flaky 動搖已通過的結論）。

**2. 問要不要開 PR**

**主動詢問使用者是否開 PR**（見各 agent 定義的「主對話收齊後先問使用者」）。同意才動 git，統一開一個 PR。

## 相關

- `agents/qa-case-automator.md` — 單案實作（create/fix）
- `agents/qa-case-fidelity-reviewer.md` — 單案忠實度 review
- `skills/tools/tcms-fetch-cases/SKILL.md` — 撈 case（含 labels/tags、新鮮度）
- `skills/tools/qa-automation-writer/SKILL.md` — 撰寫規範（含階段 0 判平台、階段 4 可追溯表）
- `skills/tools/qa-test-runner/SKILL.md` — 跑測試 + 診斷修復

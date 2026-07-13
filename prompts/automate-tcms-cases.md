# 主對話劇本：批次自動化 TCMS case（含忠實度閉環）

這份是**主對話（orchestrator）**在「把一批 TCMS case 變成自動化」時要跑的完整劇本。
subagent 只做單一職責；**迴圈控制、忠實度把關、彙整呈現、開 PR 都是主對話的事**。

## 角色分工

| 角色 | 職責 | 能不能問人 / 迴圈 / 開 PR |
| --- | --- | --- |
| **主對話（你）** | 撈批次、逐案委派、跑忠實度閉環、彙整報告、問 PR | ✅ 全部 |
| `qa-case-automator`（subagent） | 單案實作 create / fix + 跑過 + 產可追溯表 | ❌ 不問人、不迴圈、不 spawn |
| `qa-case-fidelity-reviewer`（subagent） | 單案對抗式忠實度 review，出覆蓋率/信心 | ❌ 唯讀、不修、不 spawn |

## 模式：互動 vs 自主（決定「要不要問使用者」）

- **互動模式**（有人在）：碰到待確認點（平台選擇、`web/API` 混用、缺 oid…）→ **問使用者**。
- **自主 / harness 模式**（無人）：**不停等輸入** → 套安全預設續跑（label 標的所有 UI 平台、env=stage…），把 `blocked` / 低信心排入**待人工佇列**。
- 模式由主對話依情境（或啟動時的參數）決定，並在報告開頭註明用了哪個模式。

## 流程（閉環）

```
1. 撈批次
   tcms-fetch-cases（--cases / --run-id [--assignee]）→ 每案含 steps + expected_result + labels/tags
   ⚠️ 即時快照，實作當下才 fetch，不沿用舊 /tmp

2. 逐案處理（每案一個迴圈；可平行的部分見「平行化」）
   for case in batch:
     判定目標平台（labels/tags + step 內 [PC]/[M]/[APP]/[iOS]/[Android] 切分）
     for platform in 目標平台:
       attempt = 0
       ┌─► a. spawn qa-case-automator（mode 自動判 create/fix）
       │        → 回傳 result + step→assertion 可追溯表 + 假設 + blocked
       │      若 automator 回報「待確認點」：互動→問使用者；自主→套預設/blocked
       │   b. spawn qa-case-fidelity-reviewer
       │        → step_coverage / assertion_coverage / 未覆蓋 / 可疑斷言 / fidelity / confidence / recommend
       │   c. 判 recommend：
       │        - pass          → 收下，跳出迴圈
       │        - needs-fix     → 把「未覆蓋清單 + 可疑斷言」餵回 automator（fix 模式）
       └───────  attempt += 1；attempt < 上限(預設 3) → 回 a 重修再 review
                 - 到達上限仍未過 / flag-for-human → 標記 flag-for-human，收下最後狀態
                 - blocked（缺資訊等）→ 標 blocked，續下一個

3. 彙整 → 批次 Markdown 報告（見下）

4. 依規則問使用者是否開 PR（同意才開 branch → commit → 一個 PR）
```

**關鍵：「過」的定義 = 跑得起來 + 覆蓋規格（assertion_coverage 達標）+ fidelity reviewer 認可。** 只綠不算。needs-fix 一定會**丟回 automator 重修再 review**，不是評完就結束。

## 平行化（MCP 限制）

- 可平行：撈 case、寫 code、跑 `qatest`（各自子程序）。
- **不可平行**：用 Playwright MCP 驗 locator（單一共用瀏覽器）。驗 locator 集中在主對話一次做，其餘 fan-out。
- 驗 mweb 需 device profile 的 MCP（`--device "iPhone 15"`），與 web 的 MCP 分開。

## 批次報告格式（對話內 Markdown，預設呈現）

先 rollup、再逐 case×平台明細，最後列出需人工/待修：

```
## 批次忠實度報告（模式：互動 / 自主）

案數 8 ｜ pass 5 ｜ needs-fix 0 ｜ flag-for-human 2 ｜ blocked 1
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
- 主對話要做的只是：把批次的 fidelity 結果**寫成那個 jsonl**（本來就在產報告）；送出交給 hook。

## 送出前的硬 Gate（確定性、非 LLM）

第 2 步的忠實度 review 靠主對話「記得做」——**這在真實 session 漏過**：漏 spawn
`qa-case-fidelity-reviewer` 就把 case 當過、直接彙整送出。為了不再靠記憶，**在「彙整報告 /
送遙測」之前，一律先跑一支死程式把關**：`scripts/check_fidelity_gate.py`。

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

**規則：gate 沒過（exit 1）就不准進「彙整報告 / 送遙測」。** 把 gate 印出的不合格 case
補跑 review（`needs-fix` 要丟回 automator 重修再 review），全部 `pass` 後再重跑 gate、通過才往下。

## 收尾：開 PR

整批做完、報告呈現後，**主動詢問使用者是否開 PR**（見各 agent 定義的「主對話收齊後先問使用者」）。同意才動 git，統一開一個 PR。

## 相關

- `agents/qa-case-automator.md` — 單案實作（create/fix）
- `agents/qa-case-fidelity-reviewer.md` — 單案忠實度 review
- `skills/tools/tcms-fetch-cases/SKILL.md` — 撈 case（含 labels/tags、新鮮度）
- `skills/tools/qa-automation-writer/SKILL.md` — 撰寫規範（含階段 0 判平台、階段 4 可追溯表）
- `skills/tools/qa-test-runner/SKILL.md` — 跑測試 + 診斷修復

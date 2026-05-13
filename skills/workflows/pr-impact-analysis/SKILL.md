---
name: pr-impact-analysis
description: |
  版本變更精準分析。給 GitHub repo + base/target refs，串 ai-studio backend 的 5 支 API（get-diff / analyze-components / get-version-impact-summary / get-test-cases / ai-analyze-impact），自動分類 P0~P1 必跑 regression case。短任務 chat 內等完解讀重點；長任務丟本地 background script 跑，跑完自動回來解讀。

  適用情境：
  - 使用者說「分析這版改了什麼」「base vs target 差在哪」「這次 release 風險評估」「該跑哪些 regression」「impact summary」
  - 使用者只給平台別名（如 `ios`、`b2c-web`），想列 refs 選 base/target
  - 使用者問「task <id> 跑到哪 / 結果如何」「為什麼這支 case 是 must_run」「這個 commit 誰寫的」

  必要工具：Bash、Read、Edit、Monitor（references/ 內以「Watchdog」稱呼）、本地 `gh` CLI（必須先 `gh auth login`）、Python 3（`requests` 套件）
  前置條件：本機需有 `gh` CLI 並已登入；可連到 ai-studio backend（預設 `http://autotest-service.sit.kkday.com:8081/ai_studio`）。
---

# PR Impact Analysis

把「給 base/target ref → 跑 5 支 ai-studio API → 解讀必跑 regression」整個流程封裝成 skill。

**所有分析交給 ai-studio 後端**（它有 LLM、模組對照表、Jira 整合、cache）。Skill 的價值在後端做不到的事：

1. **Workflow 管理**：解析意圖、列 refs 選 base/target、決定 Wait vs Background、處理 member-ci/b2c-web 配對
2. **結果解讀**：把後端 JSON 變人話 — 風險點、為什麼 risk_level=high、must_run 前 N 支與理由、gap 提示
3. **Follow-up Q&A**：用戶基於結果再問，從 task JSON / GitHub API 撈

**Claude 不自己做分析**（不自己看 commit messages 歸納 module），那會吃 token 且結果跟 web UI 不一致。

## 觸發判斷流程

```
用戶輸入 → 解析意圖
├─ 只給 repo（如 "ios"）→ Mode A：列 refs，等用戶選 base/target
├─ 給 base + target → 走 Pipeline
│   ├─ Step 1 拿 ahead_by + kept_files 數
│   ├─ ahead_by ≤ 30 且 kept_files ≤ 50 → Wait 模式
│   ├─ ahead_by > 300 → 警告太大，建議改 base
│   └─ 其他 → Background 模式
├─ "result <task_id>" → 讀 JSON 進結果解讀
└─ 對既有結果 follow-up → 從 task JSON / GitHub 補答
```

**只有 Wait / Background 兩種模式**，都跑完整 5 支 API、結果一致。Background 模式不上 web UI，本地 script 串 5 支 API，進度與結果寫進 `/tmp/release_impact_<task_id>.json`。

threshold 可調：`commits=N` / `files=N` 覆寫；或 `mode=wait` / `mode=background` 強制。

## Setup（首次必確認）

這個 skill **用本地 `gh` CLI 打 GitHub**（不走後端共用 GITHUB_TOKEN，避免 rate limit + HTTP plaintext token 風險）。

```bash
gh auth status     # 必須有效；失敗 → 提示用戶 gh auth login 並停在此步
gh --version
```

ai-studio backend 健康檢查：

```bash
BACKEND="${BACKEND_URL:-http://autotest-service.sit.kkday.com:8081/ai_studio}"
curl -s -H "X-User-Id: mp0qewxc-idis9qqi1d" "$BACKEND/api/config" | head -c 200
```

`X-User-Id` header 固定值 `mp0qewxc-idis9qqi1d`（UI 才看得到 task）。

## 主要流程

### Mode A — 只給 repo / 別名

用戶只給 repo（如 `ios`、`b2c-web`），**強制跑 `scripts/list_refs.py`**——不要再手寫 gh / GraphQL（會踩雷：first:100 限制、--jq 對 array 輸出 NDJSON、master/main/develop 不在 top-100 commit date ranking）。

```bash
python3 ~/.claude/skills/pr-impact-analysis/scripts/list_refs.py <repo_or_alias>
# 範例：... b2c-api / ... ios --filter rc / ... member-ci（自動 pair b2c-web）
```

⛔ **強制：跑完 list_refs.py 後，必須再 render「推薦 base→target 組合表」（plain text markdown table，C1~C10 編號）**，不能只把 refs 列完就丟給使用者打字選。**不准**用 AskUserQuestion 把選項限縮成 4 個（會塞掉自由輸入）。組合場景：task branch review、master vs develop、master vs test/*、test/* vs master、hotfix vs master、跨 release tag diff。**沒印組合表 = 3.25。**

script 一次處理掉 gh auth check、GraphQL 上限、master/main/develop 補抓、release/* 砍量、b2c-web pairing 等所有 edge case。完整組合表規範 + 參數表 + 解析使用者回覆的邏輯見 [references/mode-a-list-refs.md](references/mode-a-list-refs.md)。

### Mode B — 直接給 base/target

跳過 Mode A 直接跑 Pipeline。參數表 + `result <task_id>` 子命令見 [references/mode-b-params.md](references/mode-b-params.md)。

### Pipeline — 5 支 API

| Step | API | 用途 |
| --- | --- | --- |
| 1 | `get-diff`（帶 `prefetched_compare`） | 探勘大小 + 短路判斷 |
| 2 | `analyze-components`（SSE） | 模組對應 |
| 3 | `get-version-impact-summary`（SSE） | 影響摘要 + risk_level |
| 4 | `get-test-cases`（SSE，每 cycle 跑一次） | 撈 cycle 內 case |
| 5 | `ai-analyze-impact`（SSE，每 cycle 跑一次） | must_run 分類 |
| 6 | `GET /api/v1/testcase`（autotest-service） | 自動化 case_id list（標 ⚙️/🖐） |

Wait 模式 chat 內 SSE stream；Background 模式跑 `scripts/run_pipeline.py`。詳細（payload 格式、模式判斷 threshold、Wait 走法、Background 啟動、awk per-step 去重）見 [references/pipeline.md](references/pipeline.md)。

> ⛔ **強制：Pipeline 啟動後第一個輸出必須是 Unicode 方框進度面板**（不是 task ID、不是 "已啟動 pipeline" 字串），每收到 step 切換通知就**重印整個面板**。範例：

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 📊 PR Impact Analysis — <repo>                                          │
├─────────────────────────────────────────────────────────────────────────┤
│ base    : <base_ref>                                                    │
│ target  : <target_ref>                                                  │
│ size    : ahead=N commits, files=M → Wait/Background 模式               │
│ cycles  : 🔁 KQT-R...  📦 KQT-R...  🚗 KQT-R... (all)                   │
├─────────────────────────────────────────────────────────────────────────┤
│ Step 1  get-diff                  ✅ done                                │
│ Step 2  analyze-components        ⏳ 跑中                                │
│ Step 3  version-impact-summary    ⏸ 等                                  │
│ Step 4  get-test-cases (×N)       ⏸ 等                                  │
│ Step 5  ai-analyze-impact (×N)    ⏸ 等                                  │
│ Step 6  fetch automated_case_ids  ⏸ 等                                  │
└─────────────────────────────────────────────────────────────────────────┘
```

狀態符號約定：`⏸ 等` / `⏳ 跑中` / `✅ done` / `❌ 失敗`。**沒印面板 = 3.25。**

短路：`code_changed=false` 或 `release_only=true && kept_files=[]` → 直接回「本次變更僅版本號 / lock file，無需 regression」。

### 結果解讀（Wait / Background / `result <id>` 共用）

> ⛔ **強制前置動作**：Pipeline 跑完（或 `result <id>` 被呼叫）後，**送出結果輸出之前，必須先用 Read 工具讀完 [references/result-interpretation.md](references/result-interpretation.md)**。憑記憶輸出 = 一定踩雷（漏 `files:` 欄位、漏 P0/P1 標、自動化觸發格式錯、footer 位置錯）。**沒讀完規範就直接出表 = 3.25。**

**強制做 P0/P1 二次審核 + variant 合併**（過程不顯示給用戶，只給最終結果）。核心原則：**RD 跑不完 = 等於沒分類**，跨 cycle 合計上限 20 支（依 cluster 數動態：1 cluster 3~5 / 2~3 cluster 6~12 / 4+ cluster 12~20）。

**送出前自我檢核 checklist**（任一沒過 → 回頭讀 result-interpretation.md 重做）：
- [ ] 已撈真實 `compare/<base>...<target>` changed files（不只看 `diff_meta.kept_files_count`）
- [ ] 每支精選 case 標 `[P0]` 或 `[P1]`（P2/P3 一律不列）
- [ ] 每支精選 case 帶 `files:` 欄位列具體檔名（寫不出來 → 直接丟掉，不要硬掰）
- [ ] 每支標 `⚙️`（在 `automated_case_ids` 內）或 `🖐`
- [ ] AB Test / 純語系 / 分期 / 純 UI case **沒**混進精選清單
- [ ] Variant 已合併（同 cluster 同 user flow 只挑一支代表）
- [ ] footer `> AI 原始 MUST X 支 → 精選 P0/P1 共 Y 支` 放在 gap 區 + JSON 路徑**之後**、自動化觸發詢問**之前**
- [ ] 結尾自動化觸發區塊用 `---` + `### 👉 要直接觸發這 N 支自動化測試嗎？` + inline code 列 case ID +「回 要/yes/觸發/go ...」三件套（**不是** blockquote / plain text / 隨意動詞）
- [ ] iOS/Android repo → 整段自動化觸發**跳過不顯示**
- [ ] 結尾**沒有**任何 token 使用統計 / 多餘總結 / tips

完整規則（P0/P1 分級、永不列 case 類型、信用卡 happy path 挑選優先序、Variant 合併判斷準則、跨 cycle 去重、test/src 同步度檢核、輸出格式 + example、自動化觸發 single test run、iOS/Android 跳過段、Follow-up Q&A）見 [references/result-interpretation.md](references/result-interpretation.md)。**這個檔每次跑完 pipeline 都要重讀，不能憑記憶。**

### Background script

位於 `<skill-root>/scripts/run_pipeline.py`。輸出 JSON 結構（`cycles_resolved` / `test_cases_by_cycle` / `ai_results_by_cycle` / `automated_case_ids` / `progress_log`）與用法見 [references/background-script.md](references/background-script.md)。

只依賴 `requests`，python 標準環境就能跑。

## Repo 別名

第一個位置 token 可用平台別名（`ios` / `android` / `member-ci` / `mobile-member-ci` / `b2c-web` / `b2c-api`）代替 `repo=...`。每個別名對應的 repo 名稱、Regression / Project / Trans cycle ID，**預設一律自動跑全部 cycle**。完整對照表 + member-ci/mobile-member-ci 需同時抓 `kkday-b2c-web` refs 的特殊處理見 [references/repo-aliases.md](references/repo-aliases.md)。

## Endpoint + Edge cases

ai-studio backend prefix `/api/release-impact/`，Automation Platform 在 `autotest-service:8080`。完整 endpoint 表、Edge cases、Token/認證原則（為什麼用本地 `gh` 而非後端共用 GITHUB_TOKEN）見 [references/endpoints.md](references/endpoints.md)。

關鍵 Edge cases：`gh auth status` 失敗 → 停下來請用戶登入；`ahead_by > 300` → 警告太大建議改 base；`code_changed=false` → 短路回無變更；Wait SSE 中斷 → 重試一次後轉 background。

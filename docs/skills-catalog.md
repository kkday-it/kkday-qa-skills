# Skills Catalog

本 repo 所有可用 skill 的一頁式索引。**新加入的人先看這份**，不用一支支點開讀 SKILL.md。

> 補充說明：每支 skill 的 frontmatter `description` 都寫好了觸發條件，使用者**不需要記 skill 名字** — 講話命中關鍵字 Claude 會自動載入。下方表格的「使用時機」是給人類快速理解，不是給 agent 的觸發規則。

## 初次安裝建議

**第一次裝：只接 2 支 tools** —
- `qa-test-report-template`（測試完拿來就用，立即見效）
- `python-pr-quality-checklist`（commit 前自檢，寫 py 的人立刻有感）

**用 1-2 週後再加 meta** —
- `task-input-readiness-check` + `dev-focus-alignment`

為什麼分批？meta skill 的價值在「強迫 agent 動工前停下來確認」，但新手第一次遇到 agent 突然丟 5 段清單要你看，常見反應是「煩」而不是「對」。等踩過幾次「agent 自己腦補做錯方向 / 缺資料硬做」的坑，這時 meta skill 才會被當救星而不是路障。

> 不想記順序也沒關係 — 全部裝上去後 Claude 該觸發哪支自動決定，但對新手而言「先看到正向回饋再加上把關 skill」會比較順。

> 完整安裝步驟看 [getting-started.md](getting-started.md)。

## 三層分類

```
skills/
├── tools/      工具型 — 教 agent 怎麼用某個工具 / 產出某種範本
├── workflows/  流程型 — 跑某個 SOP 的端到端流程
└── meta/       agent 自用 — agent 啟動 / 對焦 / handoff 自己用的機制
```

## Tools — 工具型 skill

| Skill | 使用時機 | 必要工具 |
|---|---|---|
| [jira-bug-query](../skills/tools/jira-bug-query/SKILL.md) | 從 KKday Jira 抓 bug 並做 platform / project / assignee 多維度分析 | Atlassian MCP |
| [qa-test-report-template](../skills/tools/qa-test-report-template/SKILL.md) | 測試完成後產 Pass / Fail 雙範本回寫到開發單 | 無（純文字範本） |
| [python-pr-quality-checklist](../skills/tools/python-pr-quality-checklist/SKILL.md) | Python commit / PR 前自檢 6 條程式品質硬規則（+ 1 條測試 code 專用） | 無（建議搭配 ruff / mypy） |

## Workflows — 流程型 skill

| Skill | 使用時機 | 必要工具 |
|---|---|---|
| [project-retro](../skills/workflows/project-retro/SKILL.md) | 從 Slack channel 完整 dump 討論串 → 交叉比對 PRD / Lesson Learned → 輸出 Confluence retro 報告 | Slack connector + Atlassian connector + (選用) Google Drive |

## Meta — agent 自用 skill

| Skill | 使用時機 | 必要工具 |
|---|---|---|
| [task-input-readiness-check](../skills/meta/task-input-readiness-check/SKILL.md) | 任務啟動前列必要 input 清單，缺任一就停下索取（pre-flight） | 無 |
| [dev-focus-alignment](../skills/meta/dev-focus-alignment/SKILL.md) | 動工前強制對焦：需求 / 檔案 / 方向 / 影響 / 風險五段確認 | 無 |

## 推薦搭配模式

### 模式 A：標準開發任務閉環（meta + tools）

```
1. task-input-readiness-check       ← 確認 input 齊備
2. dev-focus-alignment              ← 對焦清單給人類確認
3. （實際開發 / 改 code）
4. python-pr-quality-checklist      ← commit 前自檢
```

### 模式 B：QA 測試任務閉環（meta + tools）

```
1. task-input-readiness-check       ← 確認 spec / target / env 齊備
2. （執行測試）
3. qa-test-report-template          ← 結構化回報到開發單
```

### 模式 C：Retro 任務閉環（meta + workflow）

```
1. task-input-readiness-check       ← 確認 channel / 期間 / PRD 齊備
2. project-retro                    ← 跑完整 retro 流程
```

## 新增 skill 流程

見 [docs/how-to-add-skill.md](how-to-add-skill.md)。

## 設計原則（從 CLAUDE.md 摘錄）

1. **Progressive Disclosure** — SKILL.md ≤ 5000 字，細節丟 `references/`
2. **Generator-Evaluator 分離** — 實作和驗收用不同 agent
3. **Skill 不是 Tool** — Skill 是「教 agent 怎麼做」，不是「直接做」
4. **繁中為團隊文件主語言** — 對外輸出（Confluence / Slack）用繁中

## 按工作切面搭配（自行對應到你的角色 / agent）

> 這份表格按「**工作切面**」分，不假設你有特定 agent 設置。一個人寫 code 也用得上；用 Claude Code subagent 或自組 multi-agent system 的人，把表格左欄對應到你自己的角色即可。

| 工作切面 | 推薦載入 skill |
|---|---|
| 出題 / 拆任務 / 派工 | `task-input-readiness-check`（派之前先確認素材齊備） |
| 撈資料 / 查 Jira / 跑 SOP | 依任務載入 `jira-bug-query` 等 tools |
| 寫 code / 改設定 / 動手 | `dev-focus-alignment`（動工前對焦）+ `python-pr-quality-checklist`（commit 前自檢） |
| 驗收 / 寫測試報告 / review | `qa-test-report-template`（驗收時對照標準範本） |

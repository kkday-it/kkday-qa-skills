# Skills Catalog

本 repo 所有可用 skill 的一頁式索引。**新加入的人先看這份**，不用一支支點開讀 SKILL.md。

> 補充說明：每支 skill 的 frontmatter `description` 都寫好了觸發條件，使用者**不需要記 skill 名字** — 講話命中關鍵字 Claude 會自動載入。下方表格的「使用時機」是給人類快速理解，不是給 agent 的觸發規則。

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
| [python-pr-quality-checklist](../skills/tools/python-pr-quality-checklist/SKILL.md) | Python commit / PR 前自檢 7 條程式品質硬規則 | 無（建議搭配 ruff / mypy） |

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

## 與 agent 角色的搭配

| 角色 | 推薦載入 skill |
|---|---|
| `qa-planner` | `task-input-readiness-check` + 任務分派時引用對應 workflow |
| `qa-investigator` | 依任務載入 `jira-bug-query` 等 tools |
| `qa-implementer` | `dev-focus-alignment` + `python-pr-quality-checklist` + 對應 workflow |
| `qa-reviewer` | `qa-test-report-template`（驗收時對照標準範本） |
| `qa-evaluator` | 不依賴 skill，純獨立挑剔（見 agents/qa-evaluator.md） |

# KKday QA Skills - Agent Guide

這個檔案是所有使用本 repo 的 agent 的預設 onboarding context。

## Why

KKday QA 團隊用這個 repo 把日常 QA 工作（bug triage、retro、PRD review、release audit）封裝成可重用的 skill 與 agent team。目標是讓「3 天無人介入」的 agent 工作流成為可能。

## What

```
skills/tools/       工具型 skill（Jira、Confluence、Slack、Jenkins...）
skills/workflows/   流程型 skill（retro、PRD review、bug triage...）
skills/meta/        給 agent team 自己用（progress 追蹤、handoff、自評）
mcp_servers/        自架 MCP server（用自然語言呼叫 QA 工具）
agents/             subagent 角色定義
teams/              預組合 team 範本
prompts/            啟動 prompt 範本
```

> 使用者要你**幫他安裝某個 MCP server**時：到 `mcp_servers/<name>/CLAUDE.md` 讀該 server 專屬的安裝 SOP，照著動手做。例如 `mcp_servers/kkday_qa_tools/CLAUDE.md`。

## Non-Negotiable Rules

### 工具呼叫
- **Atlassian MCP**：必須先呼叫 `getAccessibleAtlassianResources` 取得 cloudId 才能用其他工具
  - KKday cloudId：`8b890302-cc52-42ce-a15e-697446426613`
- **Jira MCP**：deferred tool，呼叫前先 `tool_search` 載入定義，避免 `-32601 Method Not Found`
- **Confluence**：寫入時優先用 ADF 格式；markdown 在特殊 block（task list、panel、status lozenge）會回空，需 fallback 到 ADF
- **`updateConfluencePage`**：要先 fetch 現有內容再附加，不能只傳新增段落

### 輸出語言
- 對團隊的文件（Confluence、Slack 訊息、報告）一律 **繁體中文**
- 程式碼註解、commit message、技術文件可用英文
- 與 agent 內部溝通的 progress.txt 可用英文（節省 token）

### 安全與權限
- 任何 destructive 操作（刪除、push to main、改 sharing permission）必須由人類審批
- 不要直接呼叫 Slack `chat.delete`、Jira `deleteIssue`、Confluence `deletePage`
- 不要在報告中放任何個人 email、access token、credential

### Progress 追蹤
- 長任務（預期 >30 分鐘）必須維護 `claude-progress.txt`
  - 開始時：讀取現有內容 + `git log --oneline -20`
  - 結束時：寫回三段 — 完成了什麼 / 下一步 / 卡點
- 詳細規範見 `skills/meta/progress-tracking/SKILL.md`

## How

### 載入 skill 的時機
- 使用者明確說「執行 X skill」→ 載入對應 SKILL.md
- 任務描述符合 skill 的 `description` 觸發條件 → 自動載入
- 不確定時：列出候選 skill，讓使用者選

### Agent 角色的選擇
| 任務類型 | 用哪個 agent |
|---|---|
| 拆任務、寫 plan、分配工作 | `qa-planner` |
| 撈資料、調查、整合背景 | `qa-investigator` |
| 寫 code、改文件、跑測試 | `qa-implementer` |
| 確認交付物符合 acceptance | `qa-reviewer` |
| 獨立挑剔、扮演 critic | `qa-evaluator` |

詳見 `agents/` 目錄下各角色定義。

### 不確定怎麼辦
1. 先看 `docs/lessons-learned.md` 有沒有踩過類似坑
2. 看 `skills/` 下有沒有現成 skill
3. 都沒有：先用單 agent 試跑，不要一開始就組 team
4. 真的卡住：寫到 progress.txt 並停下來等人

## Known Gotchas（踩過的坑）

- **Slack `slack_read_channel`**：`limit=100` + `response_format=concise` 一次撈完，避免分頁
- **Google Sheets**：用 gviz/tq endpoint + `?tqx=out:json`，不用 Sheets API；strip `google.visualization.Query.setResponse(` 包裝
- **Chrome MCP 寫 Sheets**：不可靠，遇到要寫入回歸到手動複製貼上
- **Claude Code `claude mcp add`**：可能默默損壞 JSON config，事後檢查
- **CLAUDE.md 位置**：必須在 repo root；`~/.claude/CLAUDE.md` 是全域

## 參考連結

- 官方 Skills 規格：https://github.com/anthropics/skills
- Agent Teams 文件：https://code.claude.com/docs/en/agent-teams
- Anthropic harness 研究：https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents

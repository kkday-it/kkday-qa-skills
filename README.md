# kkday-qa-skills

KKday QA 團隊的 Agent Skills 與 Agent Teams 共享資產庫。

## 這個 repo 在解決什麼問題

QA 團隊每天都在做相似的工作：抓 Jira bug、查 Slack 討論、寫 retro、審 PRD、跑 release audit。這些隱性知識散在每個人腦中、每個 Confluence 角落、每段 Slack 對話。

這個 repo 把這些工作流程封裝成 **Agent Skills**，讓 Claude Code（或任何相容 SKILL.md 規格的 agent）可以：

- 用一致的方式呼叫工具（Jira、Confluence、Slack、Jenkins...）
- 跑標準化的流程（retro、PRD review、bug triage...）
- 由多 agent 協作完成（planner / implementer / reviewer / evaluator）

## 三層架構

```
skills/
├── tools/      # 「常用工具 skill」— 教 agent 怎麼用某個工具
├── workflows/  # 「專案流程 skill」— 跑某個流程的 SOP
└── meta/       # 給 agent team 自己用的 skill（progress 追蹤、handoff、自我評估）

agents/         # Subagent 角色定義（planner、implementer、reviewer...）
teams/          # 預組好的 team 範本
prompts/        # 啟動 prompt 範本（給人用的）
```

### Skill / Agent / Team 三者怎麼分

| 概念 | 定義 | 例子 |
|---|---|---|
| **Skill** | 知識+流程包，誰都能載入 | `jira-bug-query`、`prd-spec-review` |
| **Agent 角色** | 有特定 system prompt + 工具範圍的 subagent | `qa-planner`、`qa-implementer` |
| **Team** | 預組合的 agent 群 + 任務範本 | `retro-analysis-team` |

一個 agent 可以載入多個 skill。一個 team 由多個 agent 組成。

## 快速開始

### 安裝

**一鍵安裝（推薦）**：

```bash
git clone https://github.com/kkday-it/kkday-qa-skills.git ~/kkday-qa-skills   # 或你慣用的位置
bash ~/kkday-qa-skills/scripts/install.sh
```

`install.sh` 做三件事（冪等，可重跑）：

1. **symlink** skills（tools + workflows，同名以 tools 版優先）與 agents 進 `~/.claude`——用 symlink 才會跟著 `git pull` 更新，不會像 copy 變舊。
2. 把 hook **用本 clone 的絕對路徑 merge 進 `~/.claude/settings.json`（user-level）**：`SessionStart` / `UserPromptSubmit`（自動 `git pull` 保持最新）、`Stop`（忠實度硬 gate + 遙測）。
   > **為什麼 user-level**：hook 若只放本 repo 的 checked-in `.claude/settings.json`（專案級），**只有「在本 repo 裡開 Claude Code」才觸發**；但實際跑 QA 自動化多半在框架 repo 或別的資料夾——那裡不是本 repo，hook 就不會跑。放 user-level 才能**在任何專案**都生效。
3. 會先**備份** `~/.claude/settings.json` 再 merge（不覆蓋既有設定）。

> 裝完**新開一個 session** 才生效（`SessionStart` 在啟動時跑）。之後 skill/agent 靠 symlink + autopull 自動保持最新；但 `install.sh` 本身若有更新（例如加新 hook），要**重跑一次**。

> ⚠️ 註：Claude Code 的 skill discovery 只掃一層 `<root>/<skill-name>/SKILL.md`，所以是逐個 symlink 進 `~/.claude/skills/` 而非連整個 `skills/` 目錄——`install.sh` 已處理。

### 開啟 Agent Teams（實驗性）

```bash
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
```

需要 Claude Code v2.1.32+。

### 跑第一個流程

```
請執行 prd-spec-review，PRD 連結：<confluence URL>
```

Claude 會自動載入 `skills/workflows/prd-spec-review/SKILL.md`，依流程呼叫 `skills/tools/confluence-page-ops` 等工具型 skill。

## 設計原則

1. **Progressive Disclosure**：SKILL.md 控制在 5000 字內，細節丟到 `references/`
2. **Generator-Evaluator 分離**：實作和驗收用不同 agent，避免互相取暖
3. **Skill 不是 Tool**：Skill 是「教 agent 怎麼做」，不是「直接做」
4. **繁中為團隊文件主語言**：對外輸出（Confluence、Slack）用繁中

## 貢獻

新增 skill 流程見 `docs/how-to-add-skill.md`。

## 相容性

所有 skill 遵循 [Agent Skills 開放標準](https://github.com/anthropics/skills)，理論上在 Claude Code、Codex CLI、OpenClaw、Copilot 等都能用。實際以 Claude Code 為主要驗證環境。

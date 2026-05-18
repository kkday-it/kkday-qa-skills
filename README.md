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

完整安裝步驟（含新手建議的「先裝哪 2 支、之後再加哪 2 支」）：[docs/getting-started.md](docs/getting-started.md)

最簡 user-level 安裝（已經知道自己要全裝）：

```bash
git clone https://github.com/kkday-it/kkday-qa-skills.git ~/Documents/kkday-qa-skills
mkdir -p ~/.claude/skills
for s in ~/Documents/kkday-qa-skills/skills/*/*; do ln -s "$s" ~/.claude/skills/; done
```

開**新的** Claude Code session 即可使用。所有 skill 的索引與推薦搭配：[docs/skills-catalog.md](docs/skills-catalog.md)。

### 開啟 Agent Teams（實驗性）

```bash
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
```

需要 Claude Code v2.1.32+。

## 設計原則

1. **Progressive Disclosure**：SKILL.md 控制在 5000 字內，細節丟到 `references/`
2. **Generator-Evaluator 分離**：實作和驗收用不同 agent，避免互相取暖
3. **Skill 不是 Tool**：Skill 是「教 agent 怎麼做」，不是「直接做」
4. **繁中為團隊文件主語言**：對外輸出（Confluence、Slack）用繁中

## 貢獻

新增 skill 流程見 `docs/how-to-add-skill.md`。

## 相容性

所有 skill 遵循 [Agent Skills 開放標準](https://github.com/anthropics/skills)，理論上在 Claude Code、Codex CLI、OpenClaw、Copilot 等都能用。實際以 Claude Code 為主要驗證環境。

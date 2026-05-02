# Retro Analysis Team

預組合的 team 範本，用於跑 `project-retro` skill 的完整流程。

## 適用情境

- 中大型專案的 retro（>1 個產品線、>500 則 Slack 訊息、>30 個相關 ticket）
- 需要交叉比對 PRD、Slack、Jira、Lesson Learned 文件
- 最終產出要發到 Confluence 並通知多個角色

## 角色配置

| 角色 | Agent | 主要 skill |
|---|---|---|
| Lead | `qa-planner` | - |
| 資料調查 | `qa-investigator` | `slack-channel-dump`, `jira-bug-query`, `confluence-page-ops` |
| 實作 | `qa-implementer` | `project-retro`, `confluence-page-ops` |
| 驗收 | `qa-reviewer` | - |
| 獨立評估 | `qa-evaluator` | - |

## 啟動方式

```bash
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
claude --version  # 確認 v2.1.32+
```

在 Claude Code session 中：

```
請啟動 retro-analysis-team 處理以下專案的 retro：

- Slack channel: #project-xxx
- 專案期間: 2026-Q1
- PRD: <Confluence URL>
- Lesson Learned: <Confluence URL>

最終輸出到我的 Confluence personal space (LC)，並通知 PM Ella、UED Chenny。
```

## 預期流程

```
[planner]
  ↓ 寫 requirements.md
[investigator] ←──────────┐
  ↓ 撈 Slack / Jira / Confluence    │
  ↓ 寫 investigation-report.md       │
[implementer]                       │  退回時
  ↓ 套 project-retro skill 產出報告 │
  ↓ 寫成 Confluence draft           │
[reviewer]                          │
  ↓ 對照 criteria                   │
  ↓ ✅ PASS / ❌ FAIL ──────────────┘
  ↓ PASS
[evaluator]
  ↓ 獨立挑剔
  ↓ 🟢 / 🟡 / 🔴
  ↓ 🟢 或 🟡 → 通知人類發布
  ↓ 🔴 → 退回 planner 重新規劃
```

## 預期成本

- Token 用量：約單 agent 的 3-4 倍
- 時間：取決於 Slack channel 大小，預估 30-60 分鐘
- 預算上限：建議設 $20 / 任務（透過 budget hook）

## 關鍵防護

設在 `.claude/settings.json`：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Atlassian:updateConfluencePage|Slack:slack_send_message",
        "command": "scripts/require-approval.sh"
      }
    ]
  }
}
```

destructive 或對外操作必須經人類審批，這條不能省。

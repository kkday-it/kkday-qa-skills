# 如何新增一個 Skill

## 決策樹

```
這個東西每個任務都會用到？
├── 是 → 寫到 CLAUDE.md（不要做成 skill）
└── 否 → 繼續

這個東西是「教 agent 怎麼用某個工具」？
├── 是 → skills/tools/<name>/
└── 否 → 繼續

這個東西是「跑某個流程的 SOP」？
├── 是 → skills/workflows/<name>/
└── 否 → skills/meta/<name>/  （給 agent team 自己用）
```

## 標準流程

### 1. 建立目錄

```bash
mkdir -p skills/tools/my-skill/references
cd skills/tools/my-skill
```

### 2. 寫 SKILL.md

最小範本：

```markdown
---
name: my-skill
description: |
  一句話說這個 skill 做什麼。

  適用情境：
  - 使用者說「...」
  - 任務涉及「...」

  必要工具：<list>
---

# My Skill

## 前置條件
...

## 主要流程
...

## 常見坑
...
```

**重要：description 寫好觸發條件**

Claude 是否自動載入這個 skill 完全靠 description。寫得越具體越好。

❌ 不好：`description: 處理 Jira 相關工作`
✅ 好：`description: 從 KKday Jira 抓取 P0/P1 bug 並做 platform/assignee 分組分析。使用時機：使用者問「這週有哪些 P0/P1」、「哪個 RD 票數最多」`

### 3. 控制 SKILL.md 大小

- 上限 5000 字
- 細節（範例、參考表格、長 SOP）放 `references/`
- 用「詳見 references/xxx.md」連結
- 這樣 Claude 只在需要時才載入細節，省 context

### 4. 補 references（選用）

```
my-skill/
├── SKILL.md
├── references/
│   ├── examples.md
│   ├── error-codes.md
│   └── kkday-conventions.md
└── scripts/         # 可選，放可執行 script
    └── helper.py
```

scripts 內的 code 不會被載入 context（節省 token），只有執行結果會。

### 5. 測試

最簡單的測試方法：開新 Claude Code session，輸入「請執行 my-skill 來處理 X」，看 Claude 是否：
- 正確載入 SKILL.md
- 按照流程執行
- 在卡住時給出合理錯誤訊息

### 6. 更新 README 和相關 team 範本

如果新 skill 會被某個 team 用到，記得更新 `teams/*.md`。

## 命名慣例

- 工具型：`<tool>-<action>`，例：`jira-bug-query`、`confluence-page-ops`
- 流程型：`<process>` 或 `<process>-<scope>`，例：`project-retro`、`bug-triage-weekly`
- Meta 型：`<purpose>`，例：`progress-tracking`、`handoff-between-agents`

## Anti-pattern

- ❌ 一個 skill 包含太多無關功能（拆開）
- ❌ description 太籠統（Claude 不知道何時載入）
- ❌ SKILL.md 寫成 5000 字以上（拆到 references）
- ❌ 把 credentials 寫進 SKILL.md（用環境變數）
- ❌ 互相 import 無限遞迴（A 引用 B，B 引用 A）

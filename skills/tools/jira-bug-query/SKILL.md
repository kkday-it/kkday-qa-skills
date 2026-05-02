---
name: jira-bug-query
description: |
  從 KKday Jira 抓取 bug ticket 並做多維度分析（platform、project、assignee、severity）。

  適用情境：
  - 使用者問「這週有哪些 P0/P1 bug」、「哪個 RD 票數最多」、「KQT 這個 sprint 進度如何」
  - 使用者提供 filter ID 或 JQL 並想做統計分析
  - 流程型 skill（如 bug-triage-weekly、project-retro）需要 bug 資料時呼叫

  必要工具：Atlassian MCP
---

# Jira Bug Query (KKday)

KKday 內部的 Jira 查詢慣例與常用 query。

## 前置條件

每次使用前必須完成：

1. 呼叫 `getAccessibleAtlassianResources` 取得 cloudId
2. 確認 cloudId：`8b890302-cc52-42ce-a15e-697446426613`（KKday）
3. Jira MCP 是 deferred tool，先 `tool_search` 載入定義避免 `-32601 Method Not Found`

## 常用查詢

### 1. P0/P1 bug 全公司視角

```
filter = 20205
```

`references/filter-20205-context.md` 有這個 filter 的完整定義與歷史背景。

### 2. 特定 sprint 的 bug

```
project = KQT AND sprint in openSprints() AND issuetype = Bug
```

⚠️ KQT 用非標準狀態名（`Pending`、`In Development`、`In Process`、`Waiting for QA`），詳見 `references/kqt-status-mapping.md`。

### 3. 依 assignee 分組

用 `searchJiraIssuesUsingJql` 抓完之後在本地端 group by `assignee.displayName`，**不要**靠 JQL 的 GROUP BY（不支援）。

## 輸出慣例

- 對外報告：永遠分組為 platform → project → assignee 三層
- 數字呈現：絕對數字 + 百分比
- 對比基線：用該 team 自己的 Q1 中位數，不跨 team 比較
- 語言：對團隊報告用繁中

## 常見坑

- **status 名不一致**：先用 `getJiraProjectIssueTypesMetadata` 確認該 project 的 status 名
- **JQL 引號**：含中文或特殊字元的值要用雙引號包
- **大量結果**：超過 100 筆時用 `nextPageToken` 分頁，不要一次拉
- **assignee 為空**：unassigned ticket 在統計時要單獨列一類，不要丟掉

## 與其他 skill 的關係

- `bug-triage-weekly` 會呼叫本 skill 取得當週 P0/P1 清單
- `project-retro` 會呼叫本 skill 抓專案期間的 bug 統計
- `dora-metrics-collection` 會呼叫本 skill 取得 change failure rate 計算用的 bug 資料

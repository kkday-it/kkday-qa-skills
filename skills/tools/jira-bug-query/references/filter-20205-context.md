# Jira Filter 20205 — KKday P0/P1 Bug Tracker

## 用途

這是 KKday QA 團隊長期維護的全公司 P0/P1 bug 追蹤 filter，是月會、週會、retro 的標準資料來源。

## JQL 邏輯（概念）

```
priority in (P0, P1)
AND status not in (Done, Closed, "Won't Fix")
AND created >= -90d
```

> 實際 JQL 可能依時段調整，呼叫前先用 `getJiraIssue` 或 filter API 取得最新定義。

## 使用慣例

- **週會用法**：撈出本週新增的 P0/P1，依 platform 分組
- **月會用法**：撈本月所有 P0/P1，看 trend 與 root cause 分類
- **Retro 用法**：依 project label 過濾，看特定專案的 P0/P1 集中度

## 注意事項

- Filter ID 是固定的 `20205`，但 JQL 內容可能被 admin 調整
- 如果結果筆數異常（突然從 30 變 0 或 300），先檢查 filter 是否被改
- 跑統計時要排除 spam ticket（label `spam` 或 reporter 為自動化工具）

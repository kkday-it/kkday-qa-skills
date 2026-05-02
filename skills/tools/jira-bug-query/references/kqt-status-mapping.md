# KQT Project Status Mapping

KQT project 用了非標準 Jira status 名稱，跨工具串接時容易踩雷。

## Status 對照表

| KQT 用的名稱 | 標準 Jira 對應 | 意義 |
|---|---|---|
| `Pending` | `To Do` | 待處理 |
| `In Development` | `In Progress` | RD 開發中 |
| `In Process` | `In Progress`（次階段） | RD 自測 / Code review |
| `Waiting for QA` | `In Review` | 待 QA 接手 |
| `QA Testing` | `In Review`（QA 階段） | QA 驗證中 |
| `Done` | `Done` | 完成 |

## JQL 撰寫注意事項

- 用中文或含空格的 status 必須用雙引號：`status = "Waiting for QA"`
- 不要用 `status = "In Progress"`（不存在於 KQT），會回空結果
- 跨 project 查詢時要分 project 寫條件：

```
(project = KQT AND status in ("In Development", "In Process"))
OR (project = OTHER AND status = "In Progress")
```

## 自動化判斷邏輯

寫 script 時建議：

```python
KQT_IN_PROGRESS = ["In Development", "In Process"]
KQT_PENDING_QA = ["Waiting for QA", "QA Testing"]

def is_kqt_active(status: str) -> bool:
    return status in KQT_IN_PROGRESS + KQT_PENDING_QA
```

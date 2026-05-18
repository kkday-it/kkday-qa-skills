---
name: qa-test-report-template
description: |
  QA 測試完成後產出標準化測試報告（Pass / Fail 雙範本），預期回寫到開發單（Jira / GitLab issue / GitHub PR comment）作為測試閉環。

  適用情境：
  - 完成一輪測試（API / UI / regression）後要回報給 RD / PM
  - 在開發單留下「測試通過 / 不通過 + Bug 描述」的標準紀錄
  - 跨團隊溝通需要結構化的測試結果文件
  - QA agent 跑完自動化後產出 hand-off 文件

  防止：測試結果寫得零散、bug 描述不完整害 RD 看不懂、Pass / Fail 訊號不明確、缺乏可追蹤的 case 列表。
argument-hint: "[ticket id / 測試範圍 / 結果摘要]"
user-invokable: true
---

# QA Test Report Template

QA 測試完成後的標準化回報範本。提供 Pass / Fail 兩套，依測試結果挑用。

## 為什麼要範本化

QA 寫測試結果常見問題：

1. **訊號不明確** — RD 看不出「這個能合併還是不能合併」
2. **case 列表缺失** — 跑了什麼測沒寫清楚，有問題重測也沒參考點
3. **Bug 描述不完整** — 只寫「會壞」，沒寫怎麼觸發、預期 vs 實際是什麼
4. **環境資訊缺漏** — 哪台 stage、哪個 branch 跑的，事後追究找不到

範本強迫把這幾項一次寫齊。

## 共用基本欄位（兩個範本都要）

| 欄位 | 必要？ | 範例 |
|---|---|---|
| 測試日期 | ✅ | `2026-05-04` |
| 測試環境 | ✅ | `STAGE-1`、`SIT-213`、`prod`（用實際的實例編號，不只寫 stage / sit） |
| 測試人員 | ✅ | `qa-engineer@example.com`（人）或 `qa-bot@example.com`（agent） |
| 測試範圍 | ✅ | API endpoint / UI 路徑 / feature 名稱 + 一句話功能描述 |
| Test Case 列表 | ✅ | case ID + 說明 + 結果 |
| 結論 | ✅ | 一行明確訊號（✅ 可合併 / 🔴 不可合併 / 🟡 部分通過待確認） |

## 範本 A：測試通過

```markdown
🧪 QA 測試報告 — {TICKET_ID}

測試日期：{YYYY-MM-DD}
測試環境：{STAGE-1 / SIT-213 / prod}
測試人員：{tester email}

## 測試範圍

{API method + path / UI 路徑 / feature 名稱} — {一句話功能描述}

## 測試案例

| Test Case | 說明 | 結果 |
|-----------|------|------|
| [TC-001]({test_case_link}) | {正向主流程} | ✅ Pass |
| [TC-002]({test_case_link}) | {次要正向} | ✅ Pass |
| [TC-003]({test_case_link}) | {反向 / edge case} | ✅ Pass |

## 結論

測試全部通過，regression 範圍無異常。✅ **可合併**。
```

## 範本 B：測試有 Bug

```markdown
🧪 QA 測試報告 — {TICKET_ID}

測試日期：{YYYY-MM-DD}
測試環境：{STAGE-1 / SIT-213 / prod}
測試人員：{tester email}

## 測試範圍

{API method + path / UI 路徑 / feature 名稱} — {一句話功能描述}

## 測試案例

| Test Case | 說明 | 結果 |
|-----------|------|------|
| [TC-001]({test_case_link}) | {正向主流程} | ✅ Pass |
| [TC-002]({test_case_link}) | {次要正向} | ✅ Pass |
| [TC-003]({test_case_link}) | {反向 / edge case} | ❌ Fail |

## Bug 說明

### TC-003 — {一句話描述 bug}

- **觸發步驟**：{method + path + 關鍵 query / payload params 或 UI 操作步驟}
- **預期**：{expected behavior}
- **實際**：{actual behavior}
- **影響範圍**：{是否阻擋發布 / 影響哪些使用者 / workaround 是否存在}

### TC-???? — {下一個 bug}（如有多個 bug，每個獨立段落，不要合併描述）

...

## 結論

{N} 個 bug 待修復後重測。🔴 **不可合併**。
```

## 撰寫原則

### 環境欄位
- 寫**實際實例編號**（`SIT-213`），不只寫 `sit`
- 如果是 stage / prod 也標清楚是哪個 region / cluster
- 如果跨環境測，列出來（`SIT-213 + STAGE-1`）

### Test Case 列表
- 用**有連結的 case ID**（連到測試管理工具，例：Zephyr / TestRail / Notion）
- 沒有 case 管理工具的話用**短描述 + 結果** 也可以，但每個 case 要分行
- ⚠️ **不要**只寫一行「跑了 5 個 case 都 pass」

### Bug 描述（範本 B）
- **每個 bug 獨立一個段落**，不要合併描述
- 「觸發步驟 / 預期 / 實際」三段缺一不可
- 「影響範圍」幫助 RD 判斷修復優先級

### 結論訊號
- 三選一：✅ 可合併 / 🔴 不可合併 / 🟡 條件性通過（例：通過但需 RD 確認某個邊界行為）
- 不要寫「請 RD 看一下」這種模糊結論

## 載體適配

範本本身是 markdown，依目標載體調整：

| 載體 | 注意事項 |
|---|---|
| Jira comment | Jira 用 ADF（Atlassian Document Format），用 API 寫入時要轉換；超連結用 `inlineCard` 或 `text + link mark` |
| GitHub PR comment | 直接 markdown，連結用 `[text](url)` |
| Slack message | 簡化結論，主表格給 thread 細節；emoji 改用 Slack 支援的 |
| Confluence page | 用 storage format 或 ADF；表格用 Confluence 的 table 格式 |

## 反模式

- ❌ 結論寫「測試完成」— 這不是訊號，是描述
- ❌ Bug 描述只寫「會壞」— 沒有觸發步驟 RD 復現不了
- ❌ 把多個 bug 揉在一段 — 後續修一個關一個的時候沒辦法逐項追蹤
- ❌ 環境欄位寫「sit」— 沒指定實例 = 沒人能驗證
- ❌ 結論說「可合併」但表格有 Fail — 訊號矛盾

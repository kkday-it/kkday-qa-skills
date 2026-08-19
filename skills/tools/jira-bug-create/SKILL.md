---
name: jira-bug-create
description: |
  依 KKday QA 團隊既定模板開 Bug 單到 Jira（預設 KQT project）。

  適用情境：
  - 使用者說「開 bug 單」、「開單請 RD 確認」、「這個要開票」
  - 測試（自動化或手動）發現問題，需要交給 RD 追蹤
  - 其他 skill / agent 查到 defect 後要落單

  必要工具：Atlassian MCP
---

# Jira Bug Create (KKday)

把調查結果填進團隊既定的 Bug 模板並開單。**查 bug 用 `jira-bug-query`，這支是寫的那半。**

## 前置條件

1. 呼叫 `getAccessibleAtlassianResources` 取得 cloudId
2. KKday cloudId：`8b890302-cc52-42ce-a15e-697446426613`
3. Jira MCP 是 deferred tool，先 `tool_search` 載入 `createJiraIssue` / `editJiraIssue`，避免 `-32601 Method Not Found`
4. 預設 `projectKey: KQT`、`issueTypeName: Bug`（id 10007）。開到別的 project 前先問人

## 模板（八段，全部保留，沒內容就寫「無」）

```
### [Description]

### [Platform / Service]

### [Account / Password]

### [Troubleshooting Info]

### [Reproduce Step]

### [Expected Result]

### [Actual Result]

### [Reproduce]
```

需要時可在 `[Troubleshooting Info]` 前插 `### [測資]`，或在最後加 `### QA 端處置`。

## 🔴 格式：markdown，不是 wiki markup

`contentFormat: "markdown"` 時，**Jira 舊版 wiki 語法不會被解析，會原樣印在單子上**。

| 寫法 | 結果 |
|---|---|
| `h4. [Description]` | ❌ 畫面上直接顯示「h4. [Description]」 |
| `### [Description]` | ✅ 正常標題 |
| `{code}...{code}` | ❌ 原樣印出 |
| 三個 backtick 圍起來 | ✅ |

其他兩個已知的轉換行為：

- `[Description]` 存回去會變 `\[Description\]`（markdown 逃逸），**畫面上顯示正常**，不用處理，也不要為此改寫成別的括號
- 粗體不要夾 inline code：`**500 \`9999\` 系統異常**` 會被切成 `**500**` + 裸文字。要嘛整段不加粗，要嘛把 backtick 拿掉

開完單建議用 `getJiraIssue` 回讀一次確認排版，或直接開 webUrl 看。

## 各段要填什麼

**[Description]** — 症狀一句話講完，接著**寫出你的判斷與判斷依據**，最後條列「要 RD 確認什麼」。RD 最需要的是問題清單，不是敘事。

**[Platform / Service]** — 服務名 + 環境 + 實際 host（`b2c-api — stage（api-b2c.stage.kkday.com）`）。只寫「stage」不夠。

**[Account / Password]** — 🔴 **不寫任何憑證**：password、token、`x-auth-token`、`member-uuid`、cookie 一律不進單。寫「需要哪一類帳號」即可，例如「任一 b2c stage 一般會員登入後的 token 即可重現」。真的需要，另循私訊給。這條是團隊硬性規定（見 CLAUDE.md 安全與權限），使用者說「填上去沒關係」也要先確認過才做。

**[測資]**（選填但強烈建議）— 具體到 RD 可以直接複製貼上就重現：環境、資源 oid、payload、header 欄位名（不含值）。自動化發現的 bug，這些直接從 `QATestData/data/case_data/<locale>/<模組>/<CASE_ID>.json` 的 `api_collection` 撈，不要自己回想。前置需要改資料狀態（例如把活動切啟用）也要寫。

**[Troubleshooting Info]** — 放**你做過的對照實驗**，不是 log 貼上來。這段的價值在於幫 RD 排除掉你已經排除的可能性。表格通常比文字好讀。

**[Reproduce Step]** — 含對照組。只給「打這支會壞」不如給「打這支會壞、打那支正常」。

**[Expected Result] / [Actual Result]** — 兩邊都貼實際 response body，不要只寫狀態碼。

**[Reproduce]** — `100%` / `3/5` / `僅特定帳號`。偶發就寫觀察到幾次。

## 下判斷前的兩個檢查（踩過坑）

**① 說「API 壞了」之前，先打一個明知不存在的路徑當對照組。**

wildcard route（`/priority_booking/{oid}`）會把不存在的子路徑吃掉，回的是 500 而不是 404。此時「endpoint 被下架」和「endpoint 壞掉」**症狀完全相同**，而且換各種 payload 都不會變 —— payload 不變性證明不了任何事。

```
POST /api/v2/<prefix>/definitely_not_a_real_route
```

回應與目標 endpoint 一致 → 路由已被移除，該開的是「請確認是否下架 + 原功能搬去哪」，不是「請修 API」。
再打一支同 prefix 已知正常的 endpoint，順便確認 token 還活著。
`PUT` 之類不支援的方法回的 405 會列出 `Supported methods`，可反推是哪條路由在接。

**② 自動化發現的 bug，先抓時間窗再開單。**

```sql
SELECT execute_date, result, fail_reason
FROM automation_case_execute_result
WHERE case_id = '<KQT-Txxxxx>'
ORDER BY execute_date DESC;
```

（需要 `AGENT=mac.local` 與 `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/opt/libpq/lib`，SIP 會清掉 DYLD，見 `run-case-sh-dyld-stripped-by-sip`）

🔴 **要區分「掛在 pre-condition」和「掛在目標步驟」**。前者根本沒跑到問題點，會遮住真正的首次失敗時間，直接算進去會讓 RD 往錯的時間找 commit。把「最後一次成功」「第一次真正失敗」「中間有沒有沒跑的日子」三個都寫進單裡。

## 開單

```
createJiraIssue(
  cloudId="8b890302-cc52-42ce-a15e-697446426613",
  projectKey="KQT",
  issueTypeName="Bug",
  contentFormat="markdown",
  summary="[<模組>] <一句話症狀>，<要 RD 做什麼>",
  description=<填好的模板>,
  additional_fields={"labels": [...]}   # 沒指定就不要自己掛
)
```

summary 寫法：`[B2C API] /api/v2/priority_booking/validate_order 已無回應，請確認是否已下架及限購檢查的新歸屬`。前綴標模組、中間講症狀、後面講要對方做什麼。

## 開單前必須先給人看

開 Jira 單是對外動作，**一律先把填好的模板貼給使用者確認再送**，不要看到「開個 bug 單」就直接建。要確認的至少三件：

1. **assignee** — 不指定就留空，不要自己猜 RD
2. **label** — 不要自作主張掛。使用者沒說就不掛
3. **[Account / Password] 段** — 確認沒有憑證漏進去

改單用 `editJiraIssue`，`fields` 傳要改的欄位；清空 label 傳 `{"labels": []}`。

## 與其他 skill 的關係

- `jira-bug-query` — 查既有 bug；開單前可先查有沒有重複單
- `qa-test-runner` — 測試失敗分析走完、確認是產品問題（B 類：流程更改 / 後端變更）才用本 skill 開單。locator 過期、i18n 缺 key、語系污染那幾類是自動化自己的問題，**不要開給 RD**

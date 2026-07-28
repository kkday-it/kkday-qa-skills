---
name: release-pr-check
description: >
  檢查 KKday Regular Release Confluence 頁面中，指定 section 的所有 Jira tickets
  是否都有 merged PR。當使用者說「幫我查這次 release 的 PR 狀態」、「確認某個 section 的 PR 有沒有 merged」、
  「release check」、提供 Confluence release page URL 並想確認 PR 狀態時，使用此 skill。
  Self-contained — 只需 Python 3.10+ 和 Atlassian API credentials。
---

# Release PR Merged Check Skill

檢查 KKday Regular Release 頁面中各 section 的 Jira tickets，確認每張單是否都有 merged PR。

## 環境需求

- Python 3.10+
- 套件：`requests`（標準 library 之外只需要這一個）
- Atlassian API credentials（見下方「認證」）

**Self-contained：** 此 skill 不依賴 `kkday-qa-tools` repo，可獨立放在 `kkday-qa-skills` 之類的 skill repo 中。腳本只需要 `requests` 套件。

**目錄結構：**

```
release-pr-check/
├── SKILL.md              # 此文件
├── release_pr_check.py   # 主腳本（self-contained）
├── .env.example          # credentials template（commit 到 repo）
├── .env                  # 你的實際 credentials（gitignored，不會 commit）
└── .gitignore
```

## 認證（三種來源，腳本依序嘗試）

### 方式 1：`.env` 檔（推薦給新人，最少摩擦）

```bash
cd <skill_dir>            # release-pr-check/ 目錄
cp .env.example .env
# 編輯 .env，填入你的 email 和 api_token
```

腳本啟動時自動讀取**同目錄**的 `.env`，將 KEY=VALUE 注入 env vars（不覆蓋既有 env）。`.env` 已在 `.gitignore` 中。

### 方式 2：shell 環境變數（CI/CD 友善）

```bash
export ATLASSIAN_EMAIL="your.email@kkday.com"
export ATLASSIAN_API_TOKEN="ATATT3xFf..."   # 申請：https://id.atlassian.com/manage-profile/security/api-tokens
```

### 方式 3：kkday-qa-tools `get_secret` fallback（僅內部）

若 env vars 沒設，腳本會試著 `from library.qa_service.get_secret import get_secret`。需要：
- `kkday-qa-tools` repo 在 `PYTHONPATH` 上
- 在 VPN 或 office 網路（get_secret 打 `autotest-service.sit.kkday.com:8000`）

```python
# get_secret 回傳格式（已驗證）
raw = get_secret("production", "atlassian", "release_atlassian")
secret = json.loads(raw[0]["value"])
# secret = {"email": "...", "api_token": "ATATT3...", "username": "..."}
```

**三者都查不到時的錯誤訊息：**

```
ERROR: No Atlassian credentials found.
  Option A: cp .env.example .env (next to release_pr_check.py) and fill in values
  Option B: export ATLASSIAN_EMAIL and ATLASSIAN_API_TOKEN in your shell
  Option C: add kkday-qa-tools to PYTHONPATH for get_secret fallback
  Get an API token at https://id.atlassian.com/manage-profile/security/api-tokens
```

## 輸入（CLI）

```
release_pr_check.py <confluence_page_url> [-s SECTION] [--no-record] [-r EMAILS]
```

| 參數 | 必填 | 說明 |
|------|------|------|
| `url`（positional） | ✔ | Confluence release page URL |
| `-s` / `--section` | 選填 | 只檢查 heading 包含此字串的 sections（case-insensitive）。**不帶 = 跑全部 sections** |
| `--no-record` | 選填 | 不要建子頁紀錄。**預設會在 release page 底下建一個 `PR Check - <timestamp>` 子頁** |
| `-r` / `--restrict-to` | 選填 | Comma 分隔 emails，限制子頁只給這些人看/編。建立者自己一定會被加進去避免鎖死。Fallback 到 `RESTRICT_TO` env var。**不設定 = space 內所有人可見** |

## 核心流程

### Step 1 — 從 Confluence 取得 JQL

**已驗證：** KKday Regular Release 頁面（如 `2035515447`）使用 Confluence **smart links**（datasource cards），**不是** 傳統的 `jira-issues-macro` 擴充。ADF 裡 extension 通常只有 1 個（無關 JQL）。實際 JQL 嵌在 storage XHTML 的 `data-datasource` 屬性裡（HTML-encoded JSON）。

**三種 JQL 來源，優先順序：**

1. **`data-datasource` JSON（最準）**：smart link 的 runtime JQL，永遠是最新值
2. **`<ac:parameter ac:name="jqlQuery">`**：傳統 jira macro（此頁沒有，但其他頁可能有）
3. **`<a href="...?jql=...">`（不可靠）**：頁面 hyperlink，**可能 stale**。例如 Mweb section 的 href 顯示 `M - v3.40.0`，但 datasource 內實際是 `M - v3.53.0`

```python
import requests, re, html, json
from urllib.parse import urlparse

CLOUD_ID = "8b890302-cc52-42ce-a15e-697446426613"

def extract_page_id_from_url(url: str) -> str:
    parts = urlparse(url).path.split("/")
    return parts[parts.index("pages") + 1]

# 用 storage 格式抓 data-datasource
resp = requests.get(
    f"https://api.atlassian.com/ex/confluence/{CLOUD_ID}/wiki/api/v2/pages/{page_id}",
    params={"body-format": "storage"},
    auth=(email, api_token),
    headers={"Accept": "application/json"},
)
storage = resp.json()["body"]["storage"]["value"]

# 抽出所有 data-datasource JSON，並用前面最近的 <h3>/<strong> 當 section 名稱
```

**Section 對應（取自驗證頁 2035515447）：**

| Section | datasource JQL |
|---------|----------------|
| B2C Web (Nuxt) | `fixVersion = "Nuxt - 20260521" AND (project in ("KKday PD B2C Web") OR project in (Vertical-Multiple)) ORDER BY Type DESC, Key ASC` |
| B2C Web (Mweb) | `fixVersion = "M - v3.53.0" AND (project in ("KKday PD B2C Web") OR ...)` |
| B2C Web (PC) | `fixVersion = "PC - v3.54.0" AND (project in ("KKday PD B2C Web") OR ... OR project in ("KKday BE2-B2C"))` |

### Step 2 — 執行 JQL 取得 tickets

使用新的 enhanced search endpoint `/rest/api/3/search/jql`（舊的 `/rest/api/3/search` 已停用）。需要 pagination（`nextPageToken`）：

```python
def search_issues(jql, auth):
    issues, next_token = [], None
    while True:
        params = {"jql": jql, "fields": "id,key,summary,status", "maxResults": 100}
        if next_token:
            params["nextPageToken"] = next_token
        r = requests.get(
            f"https://api.atlassian.com/ex/jira/{CLOUD_ID}/rest/api/3/search/jql",
            params=params, auth=auth, headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
        issues.extend(data.get("issues", []))
        next_token = data.get("nextPageToken")
        if not next_token or data.get("isLast"):
            break
    return issues
```

### Step 3 — 查 devinfo API 取 PR 狀態

```python
resp = requests.get(
    f"https://kkday.atlassian.net/rest/dev-status/latest/issue/detail",
    params={
        "issueId": issue["id"],          # 數字 ID，非 key
        # 2026-07 起 GitHub 整合改為 OAuth app，applicationType 必須用
        # "oAuth-com.github.integration.production"（舊值 "GitHub" 會回空 detail）
        # 失敗 fallback 依序試 "GitHub"、"githubEnterpriseServer"
        "applicationType": "oAuth-com.github.integration.production",
        "dataType": "pullrequest",
    },
    auth=(email, api_token),
)
prs = resp.json().get("detail", [{}])[0].get("pullRequests", [])
```

**Sub-task 聚合：** 有些 parent ticket（例如 `Task` 類型）本身不會掛 PR，PR 都在 sub-tasks 上。實例：VM-1618 是 Task，PR 分散在 sub-task VM-1619/1620/1621（修在 3 個 repo）。

處理方式：parent 自己查不到 PR 時，遍歷 `fields.subtasks` 並聚合每個 sub-task 的 PR。

```python
def get_prs_for_issue(issue, auth):
    """parent 自己有 PR 就用 parent 的；沒有則 fallback 到 sub-tasks 聚合。"""
    prs = get_prs(issue["id"], auth)
    if prs:
        return prs, None
    subtasks = issue["fields"].get("subtasks") or []
    aggregated, source_keys = [], []
    for st in subtasks:
        st_prs = get_prs(st["id"], auth)
        if st_prs:
            aggregated.extend(st_prs)
            source_keys.append(st["key"])
    return aggregated, (source_keys or None)
```

報告會印出 `↳ PRs aggregated from sub-tasks: VM-1619, VM-1620, VM-1621` 標示來源。

### Step 4 — 判斷邏輯

**PR status 可能值：** `MERGED` / `OPEN` / `DECLINED`。

**核心原則：只要有任一 `MERGED` PR，就視為已上版（PASS）。** 其他 `OPEN` / `DECLINED` PR 視為次要：
- `OPEN`（在已有 MERGED 的情況下）= follow-up / 後續優化，不擋發版
- `DECLINED` = PR 被關閉但未 merge（通常被新 PR 取代），不擋發版

只有「**完全沒 MERGED PR**」才需要關注：若有 OPEN → WARN（追進度），若只剩 DECLINED → FAIL。

**Skip 規則（依序套用）：**

- **`SKIP_PROJECTS`**（依 project key）：某些 project 整體不是 RD 工作（如 PM 協調單），即使沒 PR 也不該 FAIL
- **`SKIP_ISSUETYPES`**（依 issuetype）：Epic 是父單規劃用，實作 PR 掛在 sub-task。Epic 本身查不到 PR 是正常的
- **`SKIP_STATUSES`**（依 status）：已關掉的 ticket 不檢

```python
SKIP_STATUSES = {"Closed", "Won't Fix", "Duplicate", "Cancelled"}
SKIP_PROJECTS = {
    "B2CPM",   # B2C PM coordination tickets — 非 RD 實作
}
SKIP_ISSUETYPES = {
    "Epic",    # Epic 是父單，PR 掛在 sub-task
}

def check(prs, status, issue_key, issuetype=None):
    project = issue_key.split("-")[0] if "-" in issue_key else ""
    if project in SKIP_PROJECTS:
        return "SKIP", f"Project {project} 略過（非 RD 工作）"
    if issuetype in SKIP_ISSUETYPES:
        return "SKIP", f"Issuetype {issuetype} 略過（父單，PR 在 sub-task）"
    if status in SKIP_STATUSES:
        return "SKIP", f"狀態為 {status}，略過"
    if not prs:
        return "FAIL", "No PR found"
    merged = [p for p in prs if p["status"] == "MERGED"]
    pending = [p for p in prs if p["status"] == "OPEN"]
    declined = [p for p in prs if p["status"] == "DECLINED"]
    if merged:
        # 至少 1 PR merged → PASS。OPEN / DECLINED 都當次要 PR 處理
        ignored = []
        if pending: ignored.append(f"{len(pending)} open")
        if declined: ignored.append(f"{len(declined)} declined")
        note = f" ({', '.join(ignored)} ignored)" if ignored else ""
        return "PASS", f"{len(merged)} PR(s) merged{note}"
    if pending:
        return "WARN", f"{len(pending)} PR(s) still OPEN"
    return "FAIL", f"All {len(declined)} PR(s) DECLINED"
```

**注意 In Development 狀態：** 即使狀態是 `In Development`，仍要查 devinfo。實際上有些 In Development 票已經有 merged PR（例如 KB2CW-3490），所以不該直接 SKIP。沒 PR 才算 FAIL。

**Skip 設計理由：**
- **Project skip** 看 ticket key prefix（`B2CPM-861` → `B2CPM`），適用整個 project 都非 RD 工作的情境（如 B2CPM 是 PM 提需求單）
- **Issuetype skip** 適用所有 project 都會有的 ticket 類型（如 Epic），不用為每個 project 重複設定
- 兩者互補：用 issuetype 處理跨 project 通用規則（Epic），用 project 處理單一 project 例外

### Step 5 — 輸出報告（CLI + Confluence 子頁）

**CLI 即時輸出：**

```
[B2C Web] (20 tickets)
JQL: fixVersion = "Nuxt - 20260521" ...

  ✅ KB2CW-3469  [Waiting for QA   ] reCAPTCHA 全局初始化改為按需載入
  ⚠️  KB2CW-4081  [Waiting for QA   ] 主站實現非同步第三方套件載入
      → 1 PR(s) still OPEN: https://github.com/kkday-it/kkday-b2c-web/pull/2293
  ❌ KB2CW-3643  [In Development   ] [PC] 切換目的地後 FAQ 不會刷新
      → No PR found

Summary: 18 passed / 1 failed / 1 warned / 0 skipped
```

**Confluence sub-page（自動建立，除非 `--no-record`）：**

每次跑完，會在 release page 底下建一個子頁 `PR Check - YYYY-MM-DD HH:MM:SS`，內容包含：

- **Summary table** — 各 section 的 Pass/Fail/Warn/Skip 數量 + TOTAL 一行
- **⚠️ WARN — PR 仍 OPEN** — 去重後列出仍 OPEN 的 PR，含 PR 連結
- **❌ FAIL — 查不到 PR** — 去重後列出沒 PR 的 ticket，含 ticket 連結
- **Per-section details (excluding PASS)** — 只列「有問題」的 sections，每個 section 一個 `<expand>` 巨集，展開後表格**只列 FAIL/WARN/SKIP**（PASS 不列以減少 noise；數量在 Summary table 已有）

子頁失敗（403、duplicate title 等）只會 warn，不會中斷 CLI 結果 — 仍會印完整檢查結果到終端。

**權限限制（`--restrict-to` 或 `RESTRICT_TO` env var）：**

可指定 comma 分隔的 emails，限制子頁只給這些人讀/編。腳本會：
1. 用 Jira `/rest/api/3/user/search?query=<email>` 把 email 解析成 `accountId`
2. **自動把當前 API token 持有者（建立者）加進名單**，避免把自己鎖在外面
3. PUT `/wiki/rest/api/content/{id}/restriction` 設定 view + edit 兩種權限

若某個 email 查不到對應的 Atlassian user（例如打錯字、人離職），會印 warn 並跳過該人，不會 abort。

實作位於 `release_pr_check.py`：
- `build_report_html(url, all_sections, when)` — 產生 Confluence storage XHTML
- `create_subpage(parent_id, title, body, auth)` — POST `/wiki/api/v2/pages`（v2 API 要先 GET parent 拿 `spaceId`）
- `lookup_account_ids(emails, auth)` — email → accountId 解析
- `restrict_page(page_id, account_ids, auth)` — PUT v1 restriction API（v2 還沒提供）

## 執行（已驗證）

腳本檔：`release_pr_check.py`（與 SKILL.md 同目錄）。

```bash
# 整頁跑（所有 sections）
python3 release_pr_check.py "<confluence_url>"

# 只跑某個 section（heading 內含字串即可，大小寫不敏感）
python3 release_pr_check.py "<confluence_url>" --section "B2C Web"
python3 release_pr_check.py "<confluence_url>" -s "B2C Web"

# 看 help
python3 release_pr_check.py --help

# 使用 kkday-qa-tools get_secret fallback（沒設 .env 或 env vars 時）
PYTHONPATH=/path/to/kkday-qa-tools python3 release_pr_check.py "<confluence_url>"
```

**腳本架構（參見 `release_pr_check.py` 原始碼）：**

| 函式 | 用途 |
|------|------|
| `load_dotenv()` | 模組載入時自動執行，讀同目錄 `.env` 注入 `os.environ`（不覆蓋既有）。零依賴的迷你 parser |
| `get_auth()` | 解析 credentials（env vars → get_secret fallback），失敗時印 3 個 option 引導 |
| `fetch_storage(page_id, auth)` | 透過 `/wiki/api/v2/pages/{id}?body-format=storage` 拿 Confluence storage XHTML |
| `extract_datasource_jqls(storage)` | 正則掃 `data-datasource="..."` smart link + `<ac:parameter ac:name="jqlQuery">` legacy macro，回傳 `[(heading, jql), ...]` |
| `search_issues(jql, auth)` | `/rest/api/3/search/jql` + nextPageToken 分頁，含 `subtasks` 欄位 |
| `get_prs(issue_id, auth)` | devinfo API；依序試 `oAuth-com.github.integration.production`（2026-07 起 GitHub OAuth 整合）→ `GitHub` → `githubEnterpriseServer` |
| `get_prs_for_issue(issue, auth)` | 先試 parent 自己的 PR；沒有則聚合 sub-tasks 的 PR |
| `check(prs, status, key, issuetype)` | 套用 PR 判斷邏輯（見 Step 4） |
| `run_section(jql, auth, name)` | 跑單一 section，回傳 `{name, jql, counts, rows}` |
| `build_report_html(url, all_sections, when)` | 產生 Confluence storage XHTML 報告 |
| `create_subpage(parent_id, title, body, auth)` | 用 v2 API 在 release page 底下建子頁 |
| `lookup_account_ids(emails, auth)` | email → accountId（Jira user search） |
| `restrict_page(page_id, account_ids, auth)` | 用 v1 API 設 view + edit 權限 |
| `main()` | argparse 接 URL + 選填 `--section` / `--no-record` / `--restrict-to`，跑完後產子頁 + 套權限 |

## 已知常數（已驗證）

| 項目 | 值 |
|------|-----|
| Confluence cloudId | `8b890302-cc52-42ce-a15e-697446426613` |
| Jira domain | `kkday.atlassian.net` |
| Search endpoint | `/rest/api/3/search/jql`（新版，需 pagination） |
| Devinfo endpoint | `/rest/dev-status/latest/issue/detail`（Jira private API） |

## 注意事項

- devinfo API 是 Jira 的 private API，不在 Atlassian MCP 工具集裡，必須直接打 REST
- `issueId` 要用數字 ID（從 JQL 搜尋結果的 `id` 欄位取得），不是 issue key
- `In Development` / `In Progress` 狀態的 ticket 仍可能有 merged PR（例如 KB2CW-3490），**不要直接 SKIP**，要查 devinfo
- **PR 邏輯：只要有任一 MERGED PR 就算 PASS**。剩下 OPEN（follow-up）/ DECLINED（被取代）都不擋發版。實例：KF-4299 有 #1692 + #1703 merged + #1704 open → PASS
- **Skip 機制三層**：`SKIP_STATUSES`（狀態）、`SKIP_PROJECTS`（B2CPM 等 PM 協調 project）、`SKIP_ISSUETYPES`（Epic 父單，PR 在 sub-task）。預設都已涵蓋常見情境，要擴充時直接在對應 set 加值即可
- API token 申請：https://id.atlassian.com/manage-profile/security/api-tokens
- 此 skill self-contained，可放在團隊 skill repo (`kkday-qa-skills`) 不依賴 `kkday-qa-tools`

## 驗證結果（2026-05-18，page 2035515447, B2C Web Nuxt - 20260521）

跑出 20 張 tickets（符合預期）：
- 18 PASS
- 1 FAIL：KB2CW-3643（In Development, No PR found）
- 1 WARN：KB2CW-4081（PR #2293 still OPEN）

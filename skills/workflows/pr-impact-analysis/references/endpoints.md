# 後端 Endpoint 速查 + Edge cases + Token

## ai-studio backend

prefix: `/api/release-impact/`（base URL 可用 `BACKEND_URL` env / `backend=` 參數覆寫，預設 `http://autotest-service.sit.kkday.com:8081/ai_studio`）

| Endpoint | 用途 | 階段 |
| --- | --- | --- |
| ~~`POST /get-github-refs`~~ | ~~列 tags / branches~~ | **改本地 `gh` CLI**，後端 endpoint 仍存在但 skill 不再用 |
| `POST /get-diff` | 接受 `prefetched_compare` 跳過 GitHub call | Pipeline Step 1 |
| `POST /analyze-components` (SSE) | 模組對應 | Step 2 |
| `POST /get-version-impact-summary` (SSE) | 影響摘要 + 風險 | Step 3 |
| `POST /get-test-cases` (SSE) | 撈 cycle 內 case | Step 4 |
| `POST /ai-analyze-impact` (SSE) | must_run 分類 | Step 5 |

進階（用戶明確要求才打）：

| Endpoint | 用途 |
| --- | --- |
| `POST /write-back-jira` | 把 must_run 寫回 cycle |
| `POST /save-automation-record` | 存歷史 |
| `POST /generate-impact-test-cases` | AI 產生新 case |

打 API 時必須帶 header `X-User-Id: mp0qewxc-idis9qqi1d`（固定值，UI 才看得到自己 task）。

如果 curl 走 rtk 被截斷，改 `rtk proxy curl ...` 拿原始輸出。

## Automation Platform（不在 ai-studio backend 底下）

base: `http://autotest-service.sit.kkday.com:8080`

| Endpoint | 用途 | 階段 |
| --- | --- | --- |
| `GET /api/v1/testcase?case_id=` | 全平台自動化 case 列表（source of truth） | Pipeline Step 6 |
| `POST /api/v1/automation/run` | Single test run 觸發 | 結果解讀後使用者手動執行 |

## Edge cases 完整表

| 情境 | 行為 |
| --- | --- |
| Backend 連不上 | 提示確認 ai-studio backend，給 `BACKEND_URL` 設定方式 |
| `gh auth status` 失敗 | 停下來請使用者跑 `gh auth login`，不繼續 |
| `gh api` 回 403 / 404 | 確認 token 有 repo scope；private repo 還要有對應權限 |
| `code_changed=false` | 短路回「無程式碼變更」，不打後續 |
| `cycle=none` | script 跳 step4-5，只給 impact |
| `cycle` 給錯 / 找不到 case | step4 會 log 錯誤，script `errors` 紀錄；Claude 解讀時告知用戶 |
| `ahead_by > 300` | 強烈警告：「太大了，建議改 base / 用前一個 release tag」 |
| Wait 模式 SSE 中斷 | 重試一次；仍失敗轉 background 模式 |
| Background script crash | 讀 `errors` 講原因，問要不要 retry |
| `repo` 寫成 URL（`https://github.com/foo/bar`） | 截取成 `bar`（去 org 前綴），警告一次 |
| `result <task_id>` 找不到檔 | 列 `/tmp/release_impact_*.json` 最近的幾個給用戶選 |
| member-ci / mobile-member-ci 走 background | 目前 script 不支援 b2c-web 配對，警告並建議用 `mode=wait` 或先處理 b2c-web pairing |
| Step 6 拿不到 automated_case_ids | task JSON `automated_case_ids=[]`，輸出時跳過 ⚙️/🖐 標記，結尾加一行 `> 自動化平台暫時拿不到清單，本次未標記` |

## Token / 認證

**這個 skill 一律本地 `gh` CLI 打 GitHub**，token 不離開使用者機器、不會走進後端 plaintext header：

- GitHub token：使用者本地 `gh auth`（`~/.config/gh/hosts.yml`）— 必須先 `gh auth login`
- Jira / Zephyr token：後端 `.env`（後端讀，不在 skill 範圍）

**為什麼不再用後端共用 GITHUB_TOKEN**：
1. 後端 base URL 是 HTTP（plaintext），token 經 header 上去有資安風險
2. 共用 token 跑大量 compare 會碰 GitHub rate limit
3. 使用者不一定有 ai-studio 平台帳號，後端反推不到使用者個人 token

**後端 web UI 行為不變**：UI 走 `/get-diff` 不帶 `prefetched_compare` → 走原本後端用共用 GITHUB_TOKEN 打 GitHub 的舊邏輯。

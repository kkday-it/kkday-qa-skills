# kkday-qa-tools MCP — 安裝指引（給 AI 助手讀）

> **這個檔案是寫給 AI 助手（Claude Code / Cursor / Claude Desktop / Zed…）看的。**
> 當使用者說「幫我裝 kkday-qa-tools MCP」「照這個 repo 幫我加上這個 MCP」之類的話時，
> **直接照下面 SOP 動手做**，不要只是把步驟複製貼給使用者要他自己弄。

功能總覽、27 個 tool 一覽、使用範例、Troubleshooting 都在同目錄 `README.md`。
這裡只講一件事：**你該怎麼幫使用者把它裝起來。**

---

## 你的任務

把本目錄的 MCP server 加進使用者的 MCP client 設定，讓他能用自然語言呼叫 KKday QA 測試環境工具（會員／點數／coupon／tier／訂單…）。

---

## 安裝 SOP（照順序做）

### 1. 確認前置工具 `uv`

```bash
uv --version
```

- 有 → 下一步。
- 沒有 → 幫使用者裝（先徵得同意）：`curl -LsSf https://astral.sh/uv/install.sh | sh`

### 2. 跟使用者要一個值：他自己的 admin `user_id`

- 呼叫 backend `/api/tools/*` 需要 **admin 權限**的 `X-User-Id`。
- **一定要問使用者他本人的 admin user_id，不要沿用 README 範例裡的值**（那是別人的，填了會用錯身分寫入 history）。
- 其他 env 都有合理預設，不用問。

### 3. 寫入設定

**Claude Code**（優先用指令，比手動改 JSON 安全）：

```bash
claude mcp add-json kkday-qa-tools '{
  "command": "uvx",
  "args": [
    "--from",
    "git+https://github.com/kkday-it/kkday-qa-skills.git#subdirectory=mcp_servers/kkday_qa_tools",
    "kkday-qa-tools-mcp"
  ],
  "env": {
    "KKDAY_TOOLS_BASE": "http://autotest-service.sit.kkday.com:8081/ai_studio",
    "KKDAY_TOOLS_USER_ID": "<填使用者本人的 admin user_id>",
    "KKDAY_TOOLS_USER_NAME": "kkday_qa_mcp"
  }
}'
```

**Cursor / Claude Desktop / Zed**：把上面同一段 JSON 放進各 client 的 `mcpServers` 區塊（設定檔路徑見 README 安裝章節）。

> ⚠️ 寫入後**務必再讀一次設定檔確認 JSON 合法**。`claude mcp add` 有默默弄壞 `~/.claude.json` 的前例——寫完就驗，別假設成功。

### 4. 驗證

- 請使用者**重啟 client**，跑 `/mcp`。
- 看到 `kkday-qa-tools ✓ connected` + 27 個 tools 就成功。
- 沒連上 → 對照 README 的 **Troubleshooting** 逐項排查（uv 沒裝／路徑錯／401 admin 權限）。

---

## 邊界（不要越權）

- **不要**把使用者的 `user_id` 寫進任何會 commit 或外傳的檔案。
- GMBE / PG 帳密相關 endpoint **刻意**不在此 MCP，不要嘗試自己補上——那些一律走 ai-studio UI。
- 裝的是 **SIT** 環境（`KKDAY_TOOLS_BASE` 預設）；要換 stage／prod 由使用者明說再改，不要自作主張。

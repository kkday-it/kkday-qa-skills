# kkday-qa-platform MCP Server

把 **QA Test Platform**（`autotest-service` 上的 test data tool）**web 上有開放的**功能包成
**MCP tools**，讓任何支援 MCP 的 LLM client（Claude Code / Claude Desktop / Cursor / Zed 等）
用自然語言直接呼叫，取代開網頁點 Test Data Tool 一個個做的流程。

底層一律 proxy 到平台的 `POST /testtool/run`（同步）或 `/testtool/run_async` + `/testtool/progress`
（長時間 tool 走非同步 + 進程內輪詢）。**所有業務邏輯留在平台端**，本 server 只做「翻譯 schema →
打 API → 回傳」，越薄越好；平台改邏輯，MCP 不用動。

> 這支跟隔壁的 `kkday_qa_tools`（包 ai-studio Tools 面板）是**不同後端**：
> - `kkday_qa_tools` → ai-studio（`:8081/ai_studio`）：會員 / 點數 / 優惠券 / 等級 …
> - `kkday_qa_platform`（本 server）→ QA Test Platform（`:8080/api/v1`）：建/複製商品、下單、兌換、月曆

**範例對話**：
> User：「幫我在 sit206 用 voucher 換商品 9468 的 459106 套餐」
> LLM：（呼叫 `redeem_voucher`）✓ 已兌換

> User：「把 sit213 的商品 102908 複製到 sit206」
> LLM：（先 `copy_product_preview` → 回報哪些供應商目標環境沒有 → 問你要 fallback 還是指定 → `copy_product`）

---

## Tools 一覽（6 個 feature + 2 個說明工具）

| 分類 | Tools |
|---|---|
| **說明** | `help` — 全部 tool 一覽<br>`describe_tool(name)` — 指定 tool 的欄位說明 + 範例 |
| **商品** | `create_product`（20 型別，約 3 分鐘）<br>`copy_product_preview` +`copy_product`（跨環境複製，**兩段式**，見下） |
| **訂單/兌換** | `create_order`, `redeem_voucher` |
| **月曆** | `extend_item_calendar`, `batch_extend_item_calendar`（不支援 OCBT 子母單） |

**刻意不提供**：平台上的個人 / 診斷工具（`scan_product_config`、`kibana_discover`、
`explain_root_cause`、`account_pool` 等）—— 只開放 qatest-web `toolMapping.ts` 有列的 feature。

### copy_product 為什麼是兩段式

複製商品時，來源環境的供應商在目標環境可能不存在，需要**人**決定要用 fallback 還是指定替代。
所以拆成：

1. `copy_product_preview(target_env, source_env, prod_oid)` → 檢查供應商 + 回傳 `confirm_token`
2. `copy_product(..., confirm_token=...)` → 帶著 token 實際執行

`copy_product` **沒有有效 `confirm_token` 會被拒絕**，藉此保證「一定先 preview、供應商有經過覆核」
（否則平台端會把所有供應商靜默替換成 fallback）。token 為 MCP 進程內的一次性 nonce，
單次使用、15 分鐘自清、綁定 (target_env, source_env, prod_oid)。

---

## 安裝

### A. uvx from git（推薦，零手動 setup）

在 `~/.claude.json` 貼下面這段（**需先裝 `uv`**：`curl -LsSf https://astral.sh/uv/install.sh | sh`）：

```json
{
  "mcpServers": {
    "kkday-qa-platform": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/kkday-it/kkday-qa-skills.git#subdirectory=mcp_servers/kkday_qa_platform",
        "kkday-qa-platform-mcp"
      ],
      "env": {
        "KKDAY_QA_PLATFORM_BASE": "http://autotest-service.sit.kkday.com:8080/api/v1"
      }
    }
  }
}
```

### B. 本地開發（改 code 隨改隨測）

```bash
cd /path/to/kkday-qa-skills/mcp_servers/kkday_qa_platform
python3 -m venv .venv
.venv/bin/pip install -e .
```

Config 指到 venv python：

```json
{
  "command": "/path/to/.venv/bin/python",
  "args": ["/path/to/kkday_qa_platform/server.py"],
  "env": { "KKDAY_QA_PLATFORM_BASE": "http://autotest-service.sit.kkday.com:8080/api/v1" }
}
```

**驗證**：Claude Code 跑 `/mcp` → 看到 `kkday-qa-platform ✓ connected` + 8 tools 就 OK。

---

## 使用

用**自然語言**講就好（MCP 不是 slash command）：

| 你講 | LLM 觸發 |
|---|---|
| 「這個 MCP 有什麼」 | `help()` |
| 「copy_product 怎麼用」 | `describe_tool("copy_product")` |
| 「在 sit206 建一個普通測試商品」 | `create_product(env="sit206", prod_type="normal")` |
| 「把 sit213 的 102908 複製到 sit206」 | `copy_product_preview` → 對焦供應商 → `copy_product` |
| 「sit206 商品 9468 套餐 459106 下一單」 | `create_order(...)` |
| 「用 voucher 換 9468 的 459106」 | `redeem_voucher(...)` |
| 「幫商品 9468 的 459106 延 3 個月月曆」 | `extend_item_calendar(...)` |

**環境限制**：只接受 `stage` / `sit` 系列（sit / sit0x / sit20x）。沒指定環境或亂編（prod/uat…）
會被擋，LLM 會反問你要哪個環境。

---

## 開發

### 加新 tool

只在有對應的 **web feature** 時才加（本 server 刻意不做通用 passthrough，避免暴露個人工具）。
在 `server.py` 加一個 `@mcp.tool()` 函式，body 用 `_run_sync` / `_run_async` 打平台：

```python
@mcp.tool()
def new_thing(env: str, some_id: str) -> dict:
    """一句話說明。

    Args:
        env: sit / stage
        some_id: ...
    """
    return _run_sync("平台的 tool_name", env, {"someKey": some_id})
```

- 短 tool 用 `_run_sync`；跑數分鐘的用 `_run_async`（進程內輪詢，LLM 只看到一次呼叫）
- `tool_kwargs` 的 key **必須跟平台 tool 讀的一致**（多為 camelCase，如 `productOid`）

### 專案結構

```
mcp_servers/kkday_qa_platform/
├── server.py         # 所有 tool 定義 + _run_sync / _run_async + copy_product token 閘
├── pyproject.toml    # deps + entry point
├── README.md         # 本檔
└── .venv/            # 本地 venv（gitignored）
```

---

## 相關

- 後端 tool 來源：`kkday-QA-automation` → `QATest/src/test_tools/*.py`、`lib/tool_run.py`（ToolMap）
- 端點：`QATest/src/lib/web/routers/test_tool.py`（`/testtool/run`、`/run_async`、`/progress`）
- 前端對照（web feature 白名單來源）：`qatest-web/src/config/toolMapping.ts`
- MCP 官方 spec：<https://modelcontextprotocol.io/>

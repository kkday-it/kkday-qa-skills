# kkday-qa-tools MCP Server

把 [ai-studio 小工具（Tools 面板）](http://autotest-service.sit.kkday.com:8081/ai_studio/dashboard?dialog=tools) 的後端 API 包成 **MCP tools**，讓任何支援 MCP 的 LLM client（Claude Code / Claude Desktop / Cursor / Zed 等）用自然語言直接呼叫，取代開網頁點 UI 一個個做的流程。

**範例對話**：
> User：「幫 eden.lai@kkday.com 加 500 點 stage」
> LLM：（自動呼叫 `add_kkday_points`）✓ 已加成功，points_id = xxx

> User：「這個 MCP 有什麼功能？」
> LLM：（呼叫 `help`）→ 列出全部分類

> User：「把 test@kkday.com 拉到 gold tier 到 2027 年底」
> LLM：（chain `lookup_member` → `add_experience` → `update_member_tier`）

> User：「幫我建一個測試商品」
> LLM：（先 `product_types()` 拿 20 種商品型別給你挑 → 你選定 → `create_product(env=..., prod_type=...)` → 回傳 prod_oid / pkg_oid / item_oid + 商品頁 URL + BE2 編輯頁 URL，約 3 分鐘）

---

## Tools 一覽（29 個 + 2 個說明工具）

| 分類 | Tools |
|---|---|
| **說明** | `help` — 全 category 一覽<br>`describe_tool(name)` — 拿指定 tool 的欄位說明 + 範例 |
| **會員** | `lookup_member`, `member_lookup_history`, `register_member`, `register_member_history` |
| **點數** | `add_kkday_points`, `points_history` |
| **優惠券** | `coupon_templates`, `create_coupon`, `coupon_history` |
| **經驗值** | `add_experience`, `experience_history`, `mark_experience_downgraded`, `query_exp_value` |
| **等級** | `tier_rules`, `update_member_tier`, `tier_change_records`, `tier_upgrade_history`, `tier_downgrade_history`, `trigger_dkron_tier` |
| **訂單** | `get_member_orders`, `member_orders_history`, `complete_order` |
| **商品/兌換** | `product_categories`, `product_types`, `fetch_packages`, `create_product`, `product_create_history`, `redeem_voucher`, `redeem_history` |

**刻意不提供**：GMBE / PG 帳密相關 6 個 endpoint（`gmbe-credentials`、`pg-credentials`）— 涉敏感帳密，一律走 ai-studio UI，backend tool 需要的帳密會由 backend 自己讀。

---

## 安裝

依你想要的部署方式選一種：

### A. uvx from git（**推薦，零手動 setup**）

同事只需在 `~/.claude.json` 貼下面這段（**需先裝 `uv`**：`curl -LsSf https://astral.sh/uv/install.sh | sh`）：

```json
{
  "mcpServers": {
    "kkday-qa-tools": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/kkday-it/kkday-qa-skills.git#subdirectory=mcp_servers/kkday_qa_tools",
        "kkday-qa-tools-mcp"
      ],
      "env": {
        "KKDAY_TOOLS_BASE": "http://autotest-service.sit.kkday.com:8081/ai_studio",
        "KKDAY_TOOLS_USER_ID": "<你的 admin user_id>",
        "KKDAY_TOOLS_USER_NAME": "kkday_qa_mcp"
      }
    }
  }
}
```

Claude Code 啟動時 `uv` 自動 clone repo、建 venv、跑 server，同事零手動安裝。後端 endpoint 更新 → merge master → 重啟 Claude Code 就抓最新版。

### B. pipx（單機隔離）

```bash
pipx install /path/to/kkday-qa-skills/mcp_servers/kkday_qa_tools
```

Config 用：
```json
{
  "command": "kkday-qa-tools-mcp",
  "args": [],
  "env": { ... }
}
```

### C. 本地開發（改 code 隨改隨測）

```bash
cd /path/to/kkday-qa-skills/mcp_servers/kkday_qa_tools
python3 -m venv .venv
.venv/bin/pip install -e .
```

Config 指到 venv python：
```json
{
  "command": "/path/to/.venv/bin/python",
  "args": ["/path/to/kkday_qa_tools/server.py"],
  "env": { ... }
}
```

---

## 環境變數

| var | 預設 | 說明 |
|---|---|---|
| `KKDAY_TOOLS_BASE` | `http://autotest-service.sit.kkday.com:8081/ai_studio` | ai-studio backend URL（換 stage / prod 改這個） |
| `KKDAY_TOOLS_USER_ID` | `ml09h4qj-l7bsikcns5m` | X-User-Id header（要 admin 才能戳 `/api/tools/*`；每人改成自己的 admin id） |
| `KKDAY_TOOLS_USER_NAME` | `kkday_qa_mcp` | X-User-Name header — backend 寫入 history 的 operator 欄，用固定字串區分「MCP 呼叫」vs「UI 手動」 |

---

## 使用

**MCP 不是 slash command**，用**自然語言**跟 LLM 講就好：

| 你講 | LLM 觸發 |
|---|---|
| 「這個 MCP 有什麼」 | `help()` |
| 「create_coupon 怎麼用」 | `describe_tool("create_coupon")` |
| 「幫 xxx@ 加 500 點」 | `add_kkday_points(...)` |
| 「查 xxx@ 現在什麼等級」 | `lookup_member(...)` 或 `query_exp_value(...)` |
| 「幫 xxx@ 拉到 gold」 | chain `lookup_member` → `add_experience` → `update_member_tier` |
| 「建一張 5% 折扣券給 xxx@」 | 先 `coupon_templates()` → 選模板 → `create_coupon(...)` |
| 「幫我建測試商品」 | 先 `product_types()` → 使用者選 prod_type → `create_product(env=..., prod_type=...)`（一併建 package + item，回 prod_oid / pkg_oid / item_oid / 商品頁 URL / BE2 編輯頁 URL；~3 分鐘） |
| 「用 voucher 換商品」 | 先 `product_categories` → `fetch_packages` → `redeem_voucher(...)` |

**驗證有沒有裝好**：在 Claude Code 跑 `/mcp`（內建指令）→ 看得到 `kkday-qa-tools ✓ connected` + 31 tools 就 OK。

---

## 小提醒（LLM 會自動處理，你只要用自然語言講）

- **建優惠券**：LLM 會先把可用的模板列給你挑，再幫你建（你不用背 template 名稱）
- **建測試商品**：如果你沒指定商品類型（普通 / 郵輪 / 飯店 / 票券組合 …），LLM 會把 20 種商品類型列給你選；建立過程約 3 分鐘，會回商品頁 URL + BE2 編輯頁 URL
- **改會員等級**：直接說「拉到黃金」/「改成白金」/「降到白銀」都行，LLM 會自動對應
- **用 voucher 換商品**：LLM 會先幫你查商品分類、抓 package，再兌換

---

## Troubleshooting

**`/mcp` 看不到 kkday-qa-tools**
- Claude Code config 檔（`~/.claude.json`）改了但沒重啟 → 完全結束再開
- `uvx` 未裝 → 走本地 venv 方案（C），或先 `curl -LsSf https://astral.sh/uv/install.sh | sh`

**`kkday-qa-tools: failed to start`**
- 檢查 `~/.claude.json` 的 `command` 路徑存在
- venv 方案下手動跑一次：`.venv/bin/python server.py` 應該 hang（等待 stdio）— 秒退代表 python 環境有問題
- 看 Claude Code MCP log（`/mcp` 內每個 server 有 `View logs` 按鈕）

**呼叫 tool 回 `HTTPError 401 / 403`**
- `KKDAY_TOOLS_USER_ID` 不是 admin → 改成自己的 admin id
- `curl -H "X-User-Id: xxx" http://autotest-service.sit.kkday.com:8081/ai_studio/api/tools/coupon-templates` 手動驗證 header 對不對

**history 表 operator 欄看不到 `kkday_qa_mcp`**
- `KKDAY_TOOLS_USER_NAME` env 沒帶到 → 檢查 config 內的 env 區塊
- 或 backend 該 endpoint 沒把 X-User-Name 寫入 history（看 `tools_route.py` 內是否 `_get_operator(request)` 有被引用）

---

## 開發

### 加新 tool

server.py 內加一個 `@mcp.tool()` 函式，**type hint + docstring 就是 schema + description**：

```python
@mcp.tool()
def new_thing(param_a: str, param_b: int = 10) -> dict:
    """做什麼事的一句話說明。

    Args:
        param_a: 什麼東西
        param_b: 什麼東西，預設 10
    """
    return _call("POST", "/api/tools/new-endpoint", json={
        "param_a": param_a, "param_b": param_b,
    })
```

複雜 tool（多欄位、有前置動作）記得在 `describe_tool()` 的 `docs` dict 裡也加一份細節說明。

### 加複合 workflow tool

多步驟工作流可以直接在 server 端 compose，例如：

```python
@mcp.tool()
def upgrade_member_to_gold(uuid_or_email: str, env: str = "stage") -> dict:
    """一鍵把會員拉到 gold tier。"""
    add_experience(uuid_or_email, env, exp_value=99999)
    return update_member_tier(uuid_or_email, env, tier="gold", expiry_date="2027-12-31")
```

不寫這種也可以 — LLM 會自己 chain 現有 tool。寫的好處是「常用組合」變一鍵。

### 專案結構

```
mcp_servers/kkday_qa_tools/
├── server.py              # 所有 tool 定義
├── pyproject.toml         # deps + entry point
├── README.md              # 本檔
└── .venv/                 # 本地 venv（gitignored）
```

---

## 相關

- Backend API 來源：`ai_studio/ai_studio_core/backend/routes/tools_route.py`
- 前端對照：`ai_studio/ai-studio-project/src/components/dialogs/ToolsDialog.tsx`
- MCP 官方 spec：<https://modelcontextprotocol.io/>
- FastMCP 文件：<https://github.com/jlowin/fastmcp>

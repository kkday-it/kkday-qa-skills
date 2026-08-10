# kkday-qa-tools MCP Server

把 [ai-studio 小工具（Tools 面板）](http://autotest-service.sit.kkday.com:8081/ai_studio/dashboard?dialog=tools) 的後端 API 包成 **MCP tools**，讓任何支援 MCP 的 LLM client（Claude Code / Claude Desktop / Cursor / Zed 等）用自然語言直接呼叫，取代開網頁點 UI 一個個做的流程。

**範例對話**：
> User：「幫 eden.lai@kkday.com 加 500 點 stage」
> LLM：（自動呼叫 `add_kkday_points`）✓ 已加成功，points_id = xxx

> User：「這個 MCP 有什麼功能？」
> LLM：（呼叫 `help`）→ 列出全部分類

> User：「把 test@kkday.com 拉到 gold tier 到 2027 年底」
> LLM：（chain `lookup_member` → `add_experience` → `update_member_tier`）

---

## Tools 一覽（共 37 個，含 `help` / `describe_tool` / `health` 3 個說明工具）

> 分兩組後端：**ai-studio 工具**（會員/點數/券/等級/…）下游打 `:8081`；**QA 平台工具**（建/複製商品、下單、兌換、月曆）下游打 QA Test Platform `:8080`。兩組併存於同一個 MCP，埋點都送 ai-studio dashboard（QA 平台工具 operator 標記 `kkday_qa_platform_mcp`）。

| 分類 | Tools |
|---|---|
| **說明** | `help` — 全 category 一覽<br>`describe_tool(name)` — 拿指定 tool 的欄位說明 + 範例<br>`health` — 探兩組後端（:8081 ai-studio + :8080 QA 平台）可達性/auth，唯讀 |
| **會員** | `lookup_member`, `member_lookup_history`, `register_member`, `register_member_history` |
| **點數** | `add_kkday_points`, `points_history` |
| **優惠券** | `coupon_templates`, `create_coupon`, `coupon_history` |
| **經驗值** | `add_experience`, `experience_history`, `mark_experience_downgraded`, `query_exp_value` |
| **等級** | `tier_rules`, `update_member_tier`, `tier_change_records`, `tier_upgrade_history`, `tier_downgrade_history`, `trigger_dkron_tier` |
| **訂單** | `get_member_orders`, `member_orders_history`, `complete_order` |
| **商品** | `product_categories`, `product_types`, `fetch_packages`, `product_create_history` |
| **兌換** | `redeem_history` |
| **QA 平台 · 商品**（→:8080） | `create_product`（建測試商品，20 型別）、`copy_product_preview` + `copy_product`（跨環境複製，兩段式：preview 發 confirm_token → execute 帶 token）、`copy_product_verify`（唯讀：逐票種比對已複製商品與來源的成本售價/票種狀態） |
| **QA 平台 · 訂單/兌換**（→:8080） | `create_order_preview`（列可下單套餐×item、發 confirm_token）→ `create_order_options`（選用：查 bundle 組合與場次）→ `create_order`（帶 token 下單；(pkg,item) 配對經 token 驗證，杜絕缺 itemOid 第一單必敗）、`redeem_voucher`（用 voucher 兌換） |
| **QA 平台 · 月曆**（→:8080） | `extend_item_calendar`（延長單一套餐）、`batch_extend_item_calendar`（批次延長整個商品，不支援 OCBT 子母單） |

**刻意不提供**：GMBE / PG 帳密相關 6 個 endpoint（`gmbe-credentials`、`pg-credentials`）— 涉敏感帳密，一律走 ai-studio UI，backend tool 需要的帳密會由 backend 自己讀。

---

## 前置需求（裝之前先確認，否則會「connected 但一呼叫就失敗」）

1. **內網可達**：兩組後端都在 kkday 內網（`autotest-service.sit.kkday.com` 的 `:8081` / `:8080`）。**必須連到公司內網 / VPN** 才呼叫得動。沒內網時 MCP 仍會顯示 `connected`（server 本身有起），但**每個 tool 都會失敗** → 裝好後務必先呼叫 `health` 確認 `reachable: true`。
2. **`uv`（方案 A 用）**：`curl -LsSf https://astral.sh/uv/install.sh | sh`（裝完可能要重開 terminal 讓 `uvx` 進 PATH）。
3. 認得 MCP 的 client 一個：Claude Code / Claude Desktop / Cursor / Zed 等。

## 安裝

依你想要的部署方式選一種（**方案 A 最推薦**）。下面 JSON 都用同一組 SIT env，直接複製即可。

### A. uvx from git（**推薦，零手動 setup**）

同事只需在 `~/.claude.json` 貼下面這段（**需先裝 `uv`**：`curl -LsSf https://astral.sh/uv/install.sh | sh`）：

```json
{
  "mcpServers": {
    "kkday-qa-tools": {
      "command": "uvx",
      "args": [
        "--refresh",
        "--from",
        "git+https://github.com/kkday-it/kkday-qa-skills.git#subdirectory=mcp_servers/kkday_qa_tools",
        "kkday-qa-tools-mcp"
      ],
      "env": {
        "KKDAY_TOOLS_BASE": "http://autotest-service.sit.kkday.com:8081/ai_studio",
        "KKDAY_TOOLS_USER_ID": "mr8l9126-d483lhd1gto",
        "KKDAY_TOOLS_USER_NAME": "kkday_qa_mcp",
        "KKDAY_QA_PLATFORM_BASE": "http://autotest-service.sit.kkday.com:8080/api/v1"
      }
    }
  }
}
```

Claude Code 啟動時 `uv` 自動 clone repo、建 venv、跑 server，同事零手動安裝。

#### 用哪個 client：**團隊統一 Claude Code**

本 MCP 要打 kkday 內網後端，**請用 Claude Code**（server 以一般子行程跑、有完整網路，也是團隊統一的工具）。上面那段 JSON 貼進 Claude Code 的 MCP 設定：`~/.claude.json`（全域）或專案根的 `.mcp.json`（團隊共用）——但**建議用下方 CLI 指令加，別手改**。

> **不要用 Claude Desktop**：它把 MCP server 跑在沙盒裡、**連不到內網**，即使設定對也會 `connected` 但每個 tool 都失敗、且改不了。其他 client（Cursor / Zed 等）非團隊標準、不在此支援範圍，一律以 Claude Code 為準。

#### Claude Code 一鍵加（推薦，免手改 JSON、避免改壞設定檔）

不要手改 `~/.claude.json`（易改壞），用內建 CLI：

```bash
claude mcp add-json kkday-qa-tools '{
  "command": "uvx",
  "args": ["--refresh","--from","git+https://github.com/kkday-it/kkday-qa-skills.git#subdirectory=mcp_servers/kkday_qa_tools","kkday-qa-tools-mcp"],
  "env": {
    "KKDAY_TOOLS_BASE": "http://autotest-service.sit.kkday.com:8081/ai_studio",
    "KKDAY_TOOLS_USER_ID": "mr8l9126-d483lhd1gto",
    "KKDAY_TOOLS_USER_NAME": "kkday_qa_mcp",
    "KKDAY_QA_PLATFORM_BASE": "http://autotest-service.sit.kkday.com:8080/api/v1"
  }
}'
```

加 `-s user` 裝成跨專案的全域設定；預設是當前專案。加完重啟 Claude Code。

**`--refresh` 是關鍵**：`uvx` 預設會 cache 已建好的環境，requirement 字串沒變時**不會**重抓 git，光重啟拿到的還是舊版。加上 `--refresh` 後，每次啟動 Claude Code 都會強制重抓 master 最新 commit 再重建環境 → 永遠拿到最新版（代價：啟動多幾秒 git fetch）。所以只要有人把更新 merge 進 master，同事重啟 Claude Code 就自動更新，零手動。

> 前提：`--refresh` 是「無腦追 master」，master 必須保持可用；沒寫完的東西別 merge 進 master，否則同事下次重啟就會抓到。要更可控可改用 git tag 發版（`git+…@v0.2.0#subdirectory=…`）。

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

**驗證有沒有裝好**（兩步都要過）：
1. **連上**：在 Claude Code 跑 `/mcp`（內建指令）→ 看得到 `kkday-qa-tools ✓ connected`（約 37 tools）。
2. **打得動**：呼叫 `health`（唯讀）→ 兩組後端 `reachable: true` / `auth_ok: true`。**只到步驟 1 不算裝好** —— 沒內網時它一樣顯示 connected，但 tool 全打不動（見前置需求）。

---

## 小提醒（LLM 會自動處理，你只要用自然語言講）

- **建優惠券**：LLM 會先把可用的模板列給你挑，再幫你建（你不用背 template 名稱）
- **改會員等級**：直接說「拉到黃金」/「改成白金」/「降到白銀」都行，LLM 會自動對應

---

## Troubleshooting

**tool 呼叫一直失敗、不確定是後端還是自己的問題**
- 先呼叫 `health`（唯讀）→ 一次看兩組後端 `:8081`（ai-studio）與 `:8080`（QA 平台）的 `reachable` / `auth_ok` / `latency_ms`。
- `healthy: false` 且 `ai_studio_api.auth_ok: false` → 是 auth 問題（見下方 401/403）。
- 某一組 `reachable: false` → 該組後端沒起或網路不通，只有走那組的 tool 會壞（8081/8080 各撐一批 tool）。
- **兩組都 `reachable: false`（最常見）** → 你**不在內網 / 沒連 VPN**。後端都在 `autotest-service.sit.kkday.com` 內網，連上公司網路 / VPN 再呼叫（MCP 顯示 connected 不代表打得到後端）。

**`/mcp` 看不到 kkday-qa-tools**
- Claude Code config 檔（`~/.claude.json`）改了但沒重啟 → 完全結束再開
- `uvx` 未裝 → 走本地 venv 方案（C），或先 `curl -LsSf https://astral.sh/uv/install.sh | sh`

**`kkday-qa-tools: failed to start`**
- 檢查 `~/.claude.json` 的 `command` 路徑存在
- venv 方案下手動跑一次：`.venv/bin/python server.py` 應該 hang（等待 stdio）— 秒退代表 python 環境有問題
- 看 Claude Code MCP log（`/mcp` 內每個 server 有 `View logs` 按鈕）

**呼叫 tool 回 `HTTPError 401 / 403`**
- 通常是後端 `mr8l9126-d483lhd1gto` 這組 admin id 被移除或降權 → 找後端 admin 表確認
- `curl -H "X-User-Id: mr8l9126-d483lhd1gto" http://autotest-service.sit.kkday.com:8081/ai_studio/api/tools/coupon-templates` 手動驗證

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

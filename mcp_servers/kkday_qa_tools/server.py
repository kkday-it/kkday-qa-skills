"""KKday Tools MCP Server.

把 ai-studio backend `tools_route.py` 的 30+ 個 endpoint 包成 MCP tools，
讓 LLM（Claude Code / Claude Desktop / Cursor 等）可以自然語言呼叫，例如：
「幫 user@x.com 加 500 點 stage 環境」→ 自動呼叫 add_kkday_points tool。

執行方式（stdio 模式）：
    python server.py

Claude Code hook 進去（`.claude/settings.json` 或使用者 mcp config）：
    {
      "mcpServers": {
        "kkday-qa-tools": {
          "command": "python",
          "args": ["/absolute/path/to/mcp_servers/kkday_qa_tools/server.py"],
          "env": {
            "KKDAY_TOOLS_BASE": "http://autotest-service.sit.kkday.com:8081/ai_studio",
            "KKDAY_TOOLS_USER_ID": "ml09h4qj-l7bsikcns5m"
          }
        }
      }
    }

Env vars:
    KKDAY_TOOLS_BASE:      ai-studio backend URL（預設 SIT）
    KKDAY_TOOLS_USER_ID:   X-User-Id header 值（auth，沒設用 admin id）
    KKDAY_TOOLS_USER_NAME: X-User-Name header 值（audit log 的「操作者」欄，
                           預設 `kkday_qa_mcp`，讓後端 history 分辨 UI 操作 vs MCP 操作）
"""

import os
from typing import Optional

import requests
from fastmcp import FastMCP

BASE = os.getenv(
    "KKDAY_TOOLS_BASE",
    "http://autotest-service.sit.kkday.com:8081/ai_studio",
).rstrip("/")
USER_ID = os.getenv("KKDAY_TOOLS_USER_ID", "ml09h4qj-l7bsikcns5m")
# 後端 _get_operator 從 X-User-Name 抓，寫進各 history table 的 operator 欄。
# 用固定字串 `kkday_qa_mcp` 讓報表能區分「UI 手動 vs MCP 呼叫」的來源。
USER_NAME = os.getenv("KKDAY_TOOLS_USER_NAME", "kkday_qa_mcp")

mcp = FastMCP("kkday-qa-tools")


def _headers() -> dict:
    return {
        "X-User-Id": USER_ID,
        "X-User-Name": USER_NAME,
        "Content-Type": "application/json",
    }


def _call(
    method: str,
    path: str,
    *,
    json: Optional[dict] = None,
    params: Optional[dict] = None,
) -> dict:
    """統一呼叫 backend，把 Response 轉 JSON。錯誤直接拋 exception 讓 LLM 看到。"""
    url = f"{BASE}{path}"
    resp = requests.request(
        method, url, headers=_headers(), json=json, params=params, timeout=30
    )
    if not resp.ok:
        raise RuntimeError(f"{method} {path} → {resp.status_code}: {resp.text[:500]}")
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text}


# ── 說明 ────────────────────────────────────────────────────────────────


@mcp.tool()
def help() -> dict:
    """列出這個 MCP server 提供的所有 tool 分類 + 用途摘要。
    當 user 問「這個 MCP 有什麼功能」或不確定該用哪個 tool 時，先呼叫這個。
    """
    return {
        "server": "kkday-qa-tools",
        "base_url": BASE,
        "operator": USER_NAME,
        "categories": {
            "會員查詢/註冊": [
                "lookup_member: 查會員 UUID/tier/資訊",
                "member_lookup_history: 最近查詢紀錄",
                "register_member: 註冊測試會員",
                "register_member_history: 最近註冊紀錄",
            ],
            "點數": [
                "add_kkday_points: 加/扣點",
                "points_history: 加扣點紀錄",
            ],
            "優惠券": [
                "coupon_templates: 列可用模板",
                "create_coupon: 建券 + 歸戶",
                "coupon_history: 建券紀錄",
            ],
            "經驗值": [
                "add_experience: 加經驗值",
                "query_exp_value: 查當前經驗 + 對應 tier",
                "experience_history: 加經驗紀錄",
                "mark_experience_downgraded: 標記已降級",
            ],
            "等級": [
                "tier_rules: 各級門檻",
                "update_member_tier: 直接改 tier / expiry",
                "tier_change_records: 會員的等級變更史",
                "tier_upgrade_history: 系統 upgrade 紀錄",
                "tier_downgrade_history: 系統 downgrade 紀錄",
                "trigger_dkron_tier: 觸發 Dkron tier-expire job",
            ],
            "訂單": [
                "get_member_orders: 查會員訂單（需 PG 帳密）",
                "member_orders_history: 最近查詢紀錄",
                "complete_order: 標記訂單完成",
            ],
            "商品/兌換": [
                "product_categories: 商品分類",
                "fetch_packages: 依 OID 取 package 選項",
                "create_product: 建測試商品",
                "product_create_history: 建商品紀錄",
                "redeem_voucher: 用 voucher 兌換",
                "redeem_history: 兌換紀錄",
            ],
        },
        "workflow_examples": [
            "把 xxx@kkday.com 拉到 gold: lookup_member → add_experience → update_member_tier",
            "建帶點數的測試會員: register_member → add_kkday_points → lookup_member 確認",
            "測 voucher 兌換: create_coupon (歸戶到會員) → fetch_packages → redeem_voucher",
        ],
        "discovery_tips": [
            "create_coupon 前先 coupon_templates 拿模板列表（template 值不能亂猜）",
            "redeem_voucher 前先 product_categories → fetch_packages 拿 product_oid / package_oid",
            "update_member_tier 前先 tier_rules 確認該 env 的可用 tier 名稱",
            "任何 tool 想看細節參數 + 範例呼叫 describe_tool(name)",
        ],
        "notes": [
            f"所有操作 audit log 的 operator 欄 = '{USER_NAME}'（跟 UI 手動操作分辨）",
            "GMBE/PG 帳密相關 endpoint 刻意不暴露（敏感）",
            "預設環境 stage；prod 慎用",
        ],
    }


@mcp.tool()
def describe_tool(name: str) -> dict:
    """給指定 tool 更詳細的說明 + 範例呼叫，避免 user 不知道欄位怎麼填。

    Args:
        name: tool 名稱，例如 'create_coupon' / 'add_experience'
    """
    docs = {
        "create_coupon": {
            "purpose": "建優惠券，可歸戶到會員",
            "required_first": "先跑 coupon_templates 看有哪些 template 名稱可用",
            "params": {
                "env": "sit / stage",
                "template": "從 coupon_templates() 拿的模板名稱，或 'custom'",
                "coupon_json": "template='custom' 時傳完整 coupon JSON 字串",
                "code_qty": "多張模板才需要，張數（預設 1）",
                "member_uuid": "歸戶對象（UUID 或 email）— 建完直接掛到該會員名下",
                "user_uuid / user2_uuid": "特殊 template（例如需要「送給誰」的模板）才需要",
            },
            "example_simple": 'create_coupon(env="stage", template="basic_percentage_single", member_uuid="user@kkday.com")',
            "example_custom": 'create_coupon(env="stage", template="custom", coupon_json=\'{"type":"percentage","discount":50,...}\')',
        },
        "add_kkday_points": {
            "purpose": "加/扣 KKday 點數",
            "params": {
                "uuid_or_email": "會員 UUID 或 email",
                "env": "sit / stage / prod",
                "points": "每筆點數（預設 500）",
                "count": "加幾筆（預設 1）",
                "mode": "'add' 加點 / 'deduct' 扣點",
            },
            "example": 'add_kkday_points(uuid_or_email="user@kkday.com", env="stage", points=500)',
        },
        "add_experience": {
            "purpose": "加經驗值給會員（升等用）",
            "note": "會員經驗值累積過門檻自動升等；要直接改 tier 用 update_member_tier",
            "params": {
                "uuid_or_email": "會員 UUID 或 email",
                "env": "sit / stage / prod",
                "exp_value": "要加的經驗值（預設 100）",
            },
            "example": 'add_experience(uuid_or_email="user@kkday.com", env="stage", exp_value=5000)',
        },
        "update_member_tier": {
            "purpose": "直接改會員 tier / expiry_date（跳過經驗值累積）",
            "required_first": "先跑 tier_rules(env) 看該環境有哪些 tier 名稱",
            "params": {
                "uuid_or_email": "會員 UUID 或 email",
                "env": "sit / stage / prod",
                "tier": "目標 tier 名稱（bronze / silver / gold / diamond 等，依 tier_rules）",
                "expiry_date": "tier 到期日 YYYY-MM-DD",
            },
            "example": 'update_member_tier(uuid_or_email="user@kkday.com", env="stage", tier="gold", expiry_date="2027-12-31")',
        },
        "register_member": {
            "purpose": "註冊測試會員",
            "params": {
                "login_id": "登入 ID（通常是 email）",
                "password": "預設 'Aa12345678'",
                "env": "sit / stage / prod",
            },
            "example": 'register_member(login_id="test_20260703@kkday.com", env="stage")',
        },
        "redeem_voucher": {
            "purpose": "用 voucher 兌換商品訂單",
            "required_first": "先跑 product_categories → fetch_packages 拿 product_oid / package_oid",
            "params": {
                "env": "sit / stage",
                "product_oid": "商品 OID",
                "package_oid": "package OID（從 fetch_packages 拿）",
                "qyt": "數量（字串型別）",
            },
            "example": 'redeem_voucher(env="stage", product_oid="123456", package_oid="789", qyt="1")',
        },
    }
    if name in docs:
        return docs[name]
    return {
        "error": f"沒有 '{name}' 的詳細說明；試試看 tool 的 docstring，或跑 help() 看全部 tool 分類",
        "available": list(docs.keys()),
    }


# ── 會員查詢 ─────────────────────────────────────────────────────────────


@mcp.tool()
def lookup_member(uuid_or_email: str, env: str = "stage") -> dict:
    """查詢會員資訊（UUID / email / login_id 都可）。

    Args:
        uuid_or_email: 會員 UUID、email 或 login_id
        env: 環境 sit / stage / prod（預設 stage）
    """
    return _call(
        "POST",
        "/api/tools/lookup-member",
        json={"uuid_or_email": uuid_or_email, "env": env},
    )


@mcp.tool()
def member_lookup_history(limit: int = 20) -> dict:
    """列出最近的會員查詢紀錄。"""
    return _call("GET", "/api/tools/member-lookup-history", params={"limit": limit})


# ── 點數 ────────────────────────────────────────────────────────────────


@mcp.tool()
def add_kkday_points(
    uuid_or_email: str,
    env: str = "stage",
    points: int = 500,
    count: int = 1,
    mode: str = "add",
) -> dict:
    """加（或扣）KKday 點數給指定會員。

    Args:
        uuid_or_email: 會員 UUID 或 email
        env: sit / stage / prod
        points: 每筆點數
        count: 加幾筆
        mode: 'add' 加點 / 'deduct' 扣點
    """
    return _call(
        "POST",
        "/api/tools/add-kkday-points",
        json={
            "uuid_or_email": uuid_or_email,
            "env": env,
            "points": points,
            "count": count,
            "mode": mode,
        },
    )


@mcp.tool()
def points_history(limit: int = 20) -> dict:
    """列出最近的加/扣點紀錄。"""
    return _call("GET", "/api/tools/points-history", params={"limit": limit})


# ── 優惠券 ──────────────────────────────────────────────────────────────


@mcp.tool()
def coupon_templates() -> dict:
    """列出所有可用的 coupon 模板（basic_percentage_single 等）。"""
    return _call("GET", "/api/tools/coupon-templates")


@mcp.tool()
def create_coupon(
    env: str = "sit",
    template: str = "basic_percentage_single",
    coupon_json: Optional[str] = None,
    code_qty: int = 1,
    member_uuid: Optional[str] = None,
    user_uuid: Optional[str] = None,
    user2_uuid: Optional[str] = None,
) -> dict:
    """建立優惠券並可歸戶到會員。

    ⚠️ 呼叫前**強烈建議**先跑 `coupon_templates()` 拿可用模板列表；如果不確定要用
    哪個 template，先問 user 或跑 `describe_tool("create_coupon")` 看範例。

    Args:
        env: sit / stage
        template: 模板名（跑 `coupon_templates` 拿列表）或 "custom"
        coupon_json: template="custom" 時的完整 coupon JSON 字串
        code_qty: 多張模板的張數
        member_uuid: 歸戶的會員 UUID / email
        user_uuid / user2_uuid: 特殊 template 需要的額外歸戶對象
    """
    body = {"env": env, "template": template, "code_qty": code_qty}
    if coupon_json:
        body["coupon_json"] = coupon_json
    if member_uuid:
        body["member_uuid"] = member_uuid
    if user_uuid:
        body["user_uuid"] = user_uuid
    if user2_uuid:
        body["user2_uuid"] = user2_uuid
    return _call("POST", "/api/tools/create-coupon", json=body)


@mcp.tool()
def coupon_history(limit: int = 20) -> dict:
    """列出最近建立的 coupon 紀錄。"""
    return _call("GET", "/api/tools/coupon-history", params={"limit": limit})


# ── 經驗值 / 等級 ──────────────────────────────────────────────────────


@mcp.tool()
def add_experience(
    uuid_or_email: str, env: str = "stage", exp_value: int = 100
) -> dict:
    """加經驗值給會員（升等用）。

    Args:
        uuid_or_email: 會員 UUID 或 email
        env: sit / stage / prod
        exp_value: 要加的經驗值
    """
    return _call(
        "POST",
        "/api/tools/add-experience",
        json={
            "uuid_or_email": uuid_or_email,
            "env": env,
            "exp_value": exp_value,
        },
    )


@mcp.tool()
def experience_history(limit: int = 20) -> dict:
    """列出最近的加經驗值紀錄。"""
    return _call("GET", "/api/tools/experience-history", params={"limit": limit})


@mcp.tool()
def mark_experience_downgraded(uuid_or_email: str, downgraded: bool = True) -> dict:
    """標記某筆經驗值紀錄為「已降級」狀態（PATCH）。"""
    return _call(
        "PATCH",
        "/api/tools/experience-history",
        json={
            "uuid_or_email": uuid_or_email,
            "downgraded": downgraded,
        },
    )


@mcp.tool()
def query_exp_value(uuid_or_email: str, env: str = "stage") -> dict:
    """查詢會員當前累積經驗值 + 對應 tier。"""
    return _call(
        "POST",
        "/api/tools/query-exp-value",
        json={
            "uuid_or_email": uuid_or_email,
            "env": env,
        },
    )


@mcp.tool()
def tier_rules(env: str = "stage") -> dict:
    """取指定環境的 tier 升等規則（各等級的門檻 exp）。"""
    return _call("POST", "/api/tools/tier-rules", json={"env": env})


@mcp.tool()
def update_member_tier(
    uuid_or_email: str,
    env: str = "stage",
    tier: Optional[str] = None,
    expiry_date: Optional[str] = None,
) -> dict:
    """直接改會員 tier / expiry_date（跳過經驗值累積）。

    Args:
        uuid_or_email: 會員 UUID / email
        env: sit / stage / prod
        tier: 目標 tier（bronze / silver / gold / diamond 等）
        expiry_date: tier 到期日 YYYY-MM-DD
    """
    body = {"uuid_or_email": uuid_or_email, "env": env}
    if tier:
        body["tier"] = tier
    if expiry_date:
        body["expiry_date"] = expiry_date
    return _call("POST", "/api/tools/update-member-tier", json=body)


@mcp.tool()
def tier_change_records(uuid_or_email: str, env: str = "stage") -> dict:
    """查詢會員的等級變更紀錄。"""
    return _call(
        "POST",
        "/api/tools/tier-change-records",
        json={
            "uuid_or_email": uuid_or_email,
            "env": env,
        },
    )


@mcp.tool()
def tier_upgrade_history(limit: int = 20) -> dict:
    """列出最近的 tier upgrade 紀錄。"""
    return _call("GET", "/api/tools/tier-upgrade-history", params={"limit": limit})


@mcp.tool()
def tier_downgrade_history(limit: int = 20) -> dict:
    """列出最近的 tier downgrade 紀錄。"""
    return _call("GET", "/api/tools/tier-downgrade-history", params={"limit": limit})


@mcp.tool()
def trigger_dkron_tier(env: str = "stage") -> dict:
    """觸發 Dkron 的 tier-expire job（不直接改 DB，透過排程觸發）。"""
    return _call("POST", "/api/tools/trigger-dkron-tier", json={"env": env})


# ── GMBE / PG 帳密（刻意不提供 MCP tool）─────────────────────────────
# 這兩組 endpoint（/api/tools/gmbe-credentials, /api/tools/pg-credentials）
# 涉及敏感帳密存取，不讓 LLM 直接操作。要管帳密請走 ai-studio UI。
# 相關 tool（tier / member-orders 等）需要對應帳密才會 work，會在 backend
# 自行讀取，這邊不需要暴露。


# ── 會員註冊 ──────────────────────────────────────────────────────────


@mcp.tool()
def register_member(
    login_id: str, password: str = "Aa12345678", env: str = "stage"
) -> dict:
    """註冊測試會員。

    Args:
        login_id: 登入 ID / email
        password: 預設 "Aa12345678"
        env: sit / stage / prod
    """
    return _call(
        "POST",
        "/api/tools/register-member",
        json={
            "login_id": login_id,
            "password": password,
            "env": env,
        },
    )


@mcp.tool()
def register_member_history(limit: int = 20) -> dict:
    """列出最近註冊的測試會員。"""
    return _call("GET", "/api/tools/register-member/history", params={"limit": limit})


# ── 訂單 ────────────────────────────────────────────────────────────────


@mcp.tool()
def get_member_orders(uuid_or_email: str, env: str = "stage") -> dict:
    """查詢會員的訂單清單（走 PG 直連，需先設 pg-credentials）。"""
    return _call(
        "POST",
        "/api/tools/member-orders",
        json={
            "uuid_or_email": uuid_or_email,
            "env": env,
        },
    )


@mcp.tool()
def member_orders_history(limit: int = 20) -> dict:
    """列出最近查過 member orders 的紀錄。"""
    return _call("GET", "/api/tools/member-orders/history", params={"limit": limit})


@mcp.tool()
def complete_order(order_id: str, env: str = "stage") -> dict:
    """把訂單標記為完成狀態（測試用）。"""
    return _call(
        "POST", "/api/tools/complete-order", json={"order_id": order_id, "env": env}
    )


# ── 商品 / 兌換 ────────────────────────────────────────────────────────


@mcp.tool()
def product_categories(env: str = "stage") -> dict:
    """取商品分類列表。"""
    return _call("GET", "/api/tools/product-categories", params={"env": env})


@mcp.tool()
def fetch_packages(env: str, oid: str) -> dict:
    """依 product OID 取 package 選項（給 redeem_voucher 挑 package_oid 用）。"""
    return _call("GET", "/api/tools/fetch-packages", params={"env": env, "oid": oid})


@mcp.tool()
def create_product(env: str = "stage", prod_type: str = "normal") -> dict:
    """建立測試商品。

    Args:
        env: sit / stage / prod
        prod_type: normal / bundle / ticket 等（詳看 backend 支援清單）
    """
    return _call(
        "POST", "/api/tools/create-product", json={"env": env, "prod_type": prod_type}
    )


@mcp.tool()
def product_create_history(limit: int = 20) -> dict:
    """列出最近建立的測試商品。"""
    return _call("GET", "/api/tools/product-create-history", params={"limit": limit})


@mcp.tool()
def redeem_voucher(
    env: str, product_oid: str, package_oid: str, qyt: str = "1"
) -> dict:
    """用 voucher（優惠券）兌換商品訂單。

    Args:
        env: sit / stage
        product_oid: 商品 OID
        package_oid: package OID（跑 `fetch_packages` 拿）
        qyt: 兌換數量（字串型別）
    """
    return _call(
        "POST",
        "/api/tools/redeem-voucher",
        json={
            "env": env,
            "product_oid": product_oid,
            "package_oid": package_oid,
            "qyt": qyt,
        },
    )


@mcp.tool()
def redeem_history(limit: int = 20) -> dict:
    """列出最近的 voucher 兌換紀錄。"""
    return _call("GET", "/api/tools/redeem-history", params={"limit": limit})


def main() -> None:
    """FastMCP stdio server 入口。"""
    mcp.run()


if __name__ == "__main__":
    main()

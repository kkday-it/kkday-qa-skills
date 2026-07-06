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
                "product_types: 列建商品可用的 20 種 prod_type",
                "create_product: 建測試商品（20 種商品型別選 1）",
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
            "create_product 前先 product_types 確認 prod_type（20 選 1，不能亂猜）",
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
            "params": {
                "uuid_or_email": "會員 UUID 或 email",
                "env": "sit / stage / prod",
                "new_tier": "等級代碼 01=白銀 04=黃金 02=白金 03=黑鑽（傳 silver/gold/… 會自動轉）",
                "new_expiry_date": 'tier 到期日 "YYYY-MM-DD HH:MM:SS"（例 2027-12-31 00:00:00）',
                "trigger_dkron": "是否觸發 Dkron 降級 job（預設 False）",
            },
            "example": 'update_member_tier(uuid_or_email="user@kkday.com", env="stage", new_tier="04", new_expiry_date="2027-12-31 00:00:00")',
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
        "create_product": {
            "purpose": "建測試商品（一併建 package + item），約 3 分鐘",
            "required_first": "先跑 product_types() 看 20 種 prod_type 的 key + 中文說明",
            "params": {
                "env": "sit / stage / prod（例：sit / sit218 / stage）",
                "prod_type": "商品種類，只能用 product_types() 列的 20 種 key 之一",
            },
            "returns": "prod_oid / pkg_oid / item_oid / publish_status + 商品頁 URL + BE2 編輯頁 URL",
            "example": 'create_product(env="stage", prod_type="normal")',
            "example_bundle": 'create_product(env="sit", prod_type="bundle_product")',
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
def lookup_member(email: str, env: str = "stage") -> dict:
    """用 email 查會員 UUID / tier / 資訊（這支 endpoint 是 email → UUID）。

    Args:
        email: 會員 email（此 endpoint 只吃 email，不能傳 UUID）
        env: 環境 sit / stage / prod（預設 stage）
    """
    return _call(
        "POST",
        "/api/tools/lookup-member",
        json={"email": email, "env": env},
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
def mark_experience_downgraded(
    uuid_or_email: str, downgrade_status: str = "success"
) -> dict:
    """更新該會員最新一筆經驗值紀錄的降級狀態（PATCH）。

    Args:
        uuid_or_email: 會員 UUID 或 email
        downgrade_status: 只能是 success / dkron_failed / downgrade_failed
    """
    valid = {"success", "dkron_failed", "downgrade_failed"}
    if downgrade_status not in valid:
        raise ValueError(
            f"downgrade_status '{downgrade_status}' 無效；只能是 {', '.join(valid)}"
        )
    return _call(
        "PATCH",
        "/api/tools/experience-history",
        json={
            "uuid_or_email": uuid_or_email,
            "downgrade_status": downgrade_status,
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


# 會員等級代碼 — 後端 new_tier 只吃這些代碼，不是英文名（對齊 ToolsDialog TIER_LABELS）
TIER_CODES = {
    "01": "白銀會員 (silver)",
    "04": "黃金會員 (gold)",
    "02": "白金會員 (platinum)",
    "03": "黑鑽會員 (diamond)",
}
# 常見英文/中文名 → 代碼，方便自然語言呼叫時自動對應
_TIER_NAME_TO_CODE = {
    "silver": "01", "白銀": "01",
    "gold": "04", "黃金": "04",
    "platinum": "02", "白金": "02",
    "diamond": "03", "black": "03", "黑鑽": "03",
}


@mcp.tool()
def update_member_tier(
    uuid_or_email: str,
    env: str = "stage",
    new_tier: Optional[str] = None,
    new_expiry_date: Optional[str] = None,
    trigger_dkron: bool = False,
) -> dict:
    """直接改會員 tier / expiry_date（跳過經驗值累積）。

    ⚠️ new_tier 只能是代碼 01/02/03/04（不是英文名）：
        01=白銀(silver) / 04=黃金(gold) / 02=白金(platinum) / 03=黑鑽(diamond)。
    傳英文名（silver/gold/…）會自動換成代碼；其他值會擋下。

    Args:
        uuid_or_email: 會員 UUID / email
        env: sit / stage / prod
        new_tier: 目標等級代碼 01/02/03/04（或 silver/gold/platinum/diamond 自動轉）
        new_expiry_date: tier 到期日，格式 "YYYY-MM-DD HH:MM:SS"（例 2027-12-31 00:00:00）
        trigger_dkron: 是否觸發 Dkron 降級 job（預設 False）
    """
    body: dict = {
        "uuid_or_email": uuid_or_email,
        "env": env,
        "trigger_dkron": trigger_dkron,
    }
    if new_tier:
        code = new_tier if new_tier in TIER_CODES else _TIER_NAME_TO_CODE.get(
            new_tier.strip().lower()
        )
        if not code:
            raise ValueError(
                f"new_tier '{new_tier}' 無效；用代碼 {', '.join(TIER_CODES)} "
                f"（01=白銀 04=黃金 02=白金 03=黑鑽）"
            )
        body["new_tier"] = code
    if new_expiry_date:
        body["new_expiry_date"] = new_expiry_date
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
def complete_order(order_mid: str, env: str = "stage") -> dict:
    """把訂單推進到完成狀態（BE2 認養 + 推狀態，測試用）。

    Args:
        order_mid: 訂單編號（order master id）
        env: sit / stage / prod
    """
    return _call(
        "POST", "/api/tools/complete-order", json={"order_mid": order_mid, "env": env}
    )


# ── 商品 / 兌換 ────────────────────────────────────────────────────────


@mcp.tool()
def product_categories(env: str = "stage") -> dict:
    """取商品分類列表。"""
    return _call("GET", "/api/tools/product-categories", params={"env": env})


@mcp.tool()
def fetch_packages(
    env: str,
    product_oid: str,
    begin_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """依 product OID 取可選 package 列表（給 redeem_voucher 挑 package_oid 用）。

    Args:
        env: 環境 sit / stage
        product_oid: 商品 OID
        begin_date: 起始日 YYYY-MM-DD（預設今日）
        end_date: 結束日 YYYY-MM-DD（預設一年後）
    """
    params = {"env": env, "product_oid": product_oid}
    if begin_date:
        params["begin_date"] = begin_date
    if end_date:
        params["end_date"] = end_date
    return _call("GET", "/api/tools/fetch-packages", params=params)


# 商品種類 — 對齊「建立商品」Tab (ToolsDialog.tsx PROD_TYPE_OPTIONS)，
# 值來自 qatest-web src/config/toolMapping.ts product[0].options，不可亂猜。
PROD_TYPE_OPTIONS = {
    "normal": "普通商品",
    "manually_process_ticket": "手動出票商品",
    "khsr": "高鐵假期商品（商品最後狀態不定版）",
    "cruise": "動態郵輪商品",
    "hotel": "飯店商品（商品最後狀態不定版）",
    "open_date": "開放日期商品（商品最後狀態不定版）",
    "same_price_by_date_has_event": "每日均一價＆有場次的商品",
    "same_price_by_date_no_event": "每日均一價＆無場次的商品",
    "depends_on_price_by_date_has_event": "依日期定價＆有場次的商品",
    "depends_on_price_by_date_no_event": "依日期定價＆無場次的商品",
    "large_event": "大量場次的商品（34560 筆場次）",
    "stamp_duty_hk": "香港印花稅商品（最後狀態定版但不發布）",
    "ocbt_main_product": "OCBT_母商品",
    "ocbt_sub_product": "OCBT_子商品",
    "three_level_sku": "三層 SKU 的商品",
    "normal_mo_location": "商品所在地為澳門的商品",
    "b2d_normal": "B2D 快速成立商品（開放 B2D 渠道）",
    "b2d_channel_disable": "未開啟 B2D 渠道_普通商品（定版但不發布）",
    "bundle_product": "票券組合商品",
    "compound_calendar": "複合式月曆_旅規下放（opendate + godate）",
}


@mcp.tool()
def product_types() -> dict:
    """列出「建立商品」支援的所有 prod_type 值 + 中文說明（共 20 種），給使用者挑選用。

    使用者只說「建立商品」而沒指定種類時，**先呼叫這個 tool**，把 `options` 這份
    編號清單原樣列給使用者選，等使用者選定某個 value 後再呼叫 create_product。

    create_product 的 prod_type 只能用這裡的 key，不能亂猜（例如沒有 'ticket' /
    'bundle' 這種值，票券組合是 'bundle_product'、手動出票是 'manually_process_ticket'）。
    """
    options = [
        {"no": i, "value": v, "label": label}
        for i, (v, label) in enumerate(PROD_TYPE_OPTIONS.items(), start=1)
    ]
    return {
        "instruction": "把 options 編號清單列給使用者挑，使用者選定後再呼叫 create_product(prod_type=<value>)",
        "options": options,
    }


@mcp.tool()
def create_product(env: str, prod_type: str) -> dict:
    """建立測試商品（proxy 到 autotest-service，會一併建 package + item，較慢約 3 分鐘）。

    prod_type 判斷規則：
    - 使用者**已明確講出某種商品**（例：「建立普通商品」→ normal、「建郵輪商品」→ cruise、
      「票券組合商品」→ bundle_product）：直接對應到 value 建立，不用再問。
    - 使用者**只說「建立商品」沒指明種類**：🚫 禁止擅自帶 normal 或任何值直接建；
      必須先呼叫 `product_types()` 把 20 個選項列給使用者挑，選定後再帶入。
    - 講的種類**對不到任何 value / 模稜兩可**：一樣先跑 product_types() 讓使用者確認。
    env 未指定時同樣要問使用者。

    prod_type 只能用 product_types() 列出的 20 個 value 之一（沒有 'ticket'/'bundle' 這種值）。

    回傳含 prod_oid / pkg_oid / item_oid / publish_status，以及商品頁 URL 與 BE2 編輯頁 URL。

    Args:
        env: 環境 sit / stage / prod（例：sit / sit218 / stage）— 必填，未指定要問使用者
        prod_type: 商品種類，20 選 1（見 product_types()）— 必填，未指定要列選項給使用者挑
    """
    prod_type = (prod_type or "").strip()
    if prod_type not in PROD_TYPE_OPTIONS:
        raise ValueError(
            f"prod_type '{prod_type}' 不是有效值；請先呼叫 product_types() 把 20 個選項"
            f"列給使用者挑，選定後再帶入。可用值：{', '.join(PROD_TYPE_OPTIONS)}"
        )
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

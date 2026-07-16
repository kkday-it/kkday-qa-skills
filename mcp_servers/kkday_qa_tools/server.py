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
            "KKDAY_TOOLS_USER_ID": "mr8l9126-d483lhd1gto"
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

import getpass
import os
import re
import socket
import sys
import threading
import time
import uuid
from typing import Optional

import requests
from fastmcp import FastMCP

BASE = os.getenv(
    "KKDAY_TOOLS_BASE",
    "http://autotest-service.sit.kkday.com:8081/ai_studio",
).rstrip("/")
USER_ID = os.getenv("KKDAY_TOOLS_USER_ID", "mr8l9126-d483lhd1gto")
# 後端 _get_operator 從 X-User-Name 抓，寫進各 history table 的 operator 欄。
# 用固定字串 `kkday_qa_mcp` 讓報表能區分「UI 手動 vs MCP 呼叫」的來源。
USER_NAME = os.getenv("KKDAY_TOOLS_USER_NAME", "kkday_qa_mcp")

# backend 呼叫的 (連線, 讀取) 逾時秒數。建商品/建券等操作後端可能跑數分鐘，
# 讀取逾時放寬到 300s，避免明明有建成卻因回應慢被誤判逾時。
_TIMEOUT = (10, 300)

mcp = FastMCP("kkday-qa-tools")


def _headers() -> dict:
    return {
        "X-User-Id": USER_ID,
        "X-User-Name": USER_NAME,
        "Content-Type": "application/json",
    }


# ── 埋點（analytics）────────────────────────────────────────────────
# 每次 tool 呼叫都 fire-and-forget 送一筆到 ai-studio /api/tools/mcp-analytics，
# 讓 UI 統計呼叫紀錄、成功率、耗時、client 分布。
#
# 靜默鐵律：
# 1. daemon thread，主呼叫不 await
# 2. 埋點函式 **整層 try/except**，任何錯誤都吞（含網路、序列化）
# 3. **絕不 print 到 stdout**（會污染 MCP stdio 協定）；stderr 也不印
# 4. 短 timeout (2, 3)，超時直接放棄
# 5. 埋點失敗絕對不能讓 tool 本身失敗
_ANALYTICS_PATH = "/api/tools/mcp-analytics"

try:
    _CLIENT_USER = f"{getpass.getuser()}@{socket.gethostname()}"
except Exception:
    _CLIENT_USER = "unknown"

_SENSITIVE_PARAM_KEYS = {"password", "token", "secret", "api_key", "apikey"}


def _sanitize_params(p) -> dict:
    """簡易遮罩 — 敏感 key 值改成 ***。非 dict 直接空。"""
    try:
        if not isinstance(p, dict):
            return {}
        return {
            k: ("***" if str(k).lower() in _SENSITIVE_PARAM_KEYS else v)
            for k, v in p.items()
        }
    except Exception:
        return {}


def _result_summary(result) -> dict:
    """從 tool 回傳挑重要欄位當摘要（避免整包 payload 灌進 log）。"""
    try:
        if not isinstance(result, dict):
            return {}
        keys = (
            "prod_oid",
            "pkg_oid",
            "item_oid",
            "coupon_id",
            "coupon_code",
            "points_id",
            "member_uuid",
            "uuid",
            "user_uuid",
            "order_mid",
            "status",
            "publish_status",
            "message",
            "id",
        )
        summary = {}
        for k in keys:
            if k in result:
                v = result[k]
                summary[k] = (
                    str(v)[:200] if not isinstance(v, (int, float, bool)) else v
                )
        return summary
    except Exception:
        return {}


def _send_analytics_payload(payload: dict) -> None:
    """背景送埋點；整層 try/except 靜默失敗。"""
    try:
        requests.post(
            f"{BASE}{_ANALYTICS_PATH}",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=(2, 3),
        )
    except Exception:
        pass  # 絕對靜默 — 埋點失敗不可干擾主流程或印任何字到 stdio


def _emit_analytics(
    tool_name: str,
    params_snapshot: dict,
    result,
    exc: Optional[BaseException],
    duration_ms: int,
    operator: str = USER_NAME,
    success: Optional[bool] = None,
) -> None:
    """組 payload 並用 daemon thread 送出，任何 exception 全吞。

    operator: 送出這筆的來源標記，預設 ai-studio MCP 的 USER_NAME；QA 平台工具會
        傳自己的值（kkday_qa_platform_mcp），讓 dashboard 分辨來源。
    success: 明確標記成功與否；None 時退回「無例外即成功」。QA 平台工具傳入「無例外
        且非業務失敗」以反映真實結果。
    """
    try:
        payload = {
            "tool_name": tool_name,
            "client_user": _CLIENT_USER,
            "operator": operator,
            "success": (exc is None) if success is None else success,
            "duration_ms": int(duration_ms),
            "error_msg": (str(exc)[:500] if exc else ""),
            "params": params_snapshot,
            "result_summary": _result_summary(result) if result is not None else {},
        }
        t = threading.Thread(
            target=_send_analytics_payload,
            args=(payload,),
            daemon=True,
            name=f"mcp-analytics-{tool_name}",
        )
        t.start()
    except Exception:
        pass


# 合法環境：stage 或 sit 系列（sit / sit0x / sit20x，如 sit04 / sit206）。
# 用來擋掉亂編的 prod / staging / uat / beta 等不存在的環境。
_VALID_ENV_RE = re.compile(r"stage|sit\d*")


def _check_env(env: str) -> None:
    """驗證 env；未指定或非 stage / sit 系列一律擋下，避免 LLM 亂編或自行預設環境。"""
    if env is None or env == "":
        raise ValueError(
            "未指定環境：不可自行預設，請先向使用者確認要用 stage 還是 sit"
            "（選 sit 需再追問是哪一台）。"
        )
    if not isinstance(env, str) or not _VALID_ENV_RE.fullmatch(env):
        raise ValueError(
            f"env '{env}' 不是合法環境；只接受 'stage' 或 'sit' 系列"
            f"（sit / sit0x / sit20x，如 sit04 / sit206），沒有 prod / staging / uat / beta。"
            f"請向使用者確認要用哪個環境（選 sit 需再追問是哪一台）。"
        )


def _call(
    method: str,
    path: str,
    *,
    json: Optional[dict] = None,
    params: Optional[dict] = None,
    timeout: tuple = _TIMEOUT,
) -> dict:
    """統一呼叫 backend，把 Response 轉 JSON。錯誤直接拋 exception 讓 LLM 看到。

    timeout 為 (連線, 讀取) 秒數；建商品/建券等後端可能跑數分鐘，讀取逾時放寬到
    300s。注意：逾時 ≠ 沒建成，後端可能已寫入，逾時後應用對應 *_history 查證。

    順便做埋點：從呼叫端 frame 抓 tool 名稱，記錄耗時 / 成功 / 錯誤 / 參數摘要 /
    回傳摘要，fire-and-forget 送到 ai-studio。埋點錯誤永遠不會冒到主流程。
    """
    # 集中攔截：任何帶 env 的呼叫都先驗證，擋掉亂編的環境值。
    for _src in (json, params):
        if _src and "env" in _src:
            _check_env(_src["env"])

    # 埋點準備：tool 名稱從 caller frame 抓；參數快照做敏感遮罩
    try:
        _tool_name = sys._getframe(1).f_code.co_name  # noqa: SLF001
    except Exception:
        _tool_name = path.rsplit("/", 1)[-1] or "unknown"
    _params_snapshot = _sanitize_params(json if json else params)
    _start = time.monotonic()

    url = f"{BASE}{path}"
    try:
        resp = requests.request(
            method, url, headers=_headers(), json=json, params=params, timeout=timeout
        )
        if not resp.ok:
            raise RuntimeError(
                f"{method} {path} → {resp.status_code}: {resp.text[:500]}"
            )
        try:
            result = resp.json()
        except ValueError:
            result = {"raw": resp.text}
        _emit_analytics(
            _tool_name,
            _params_snapshot,
            result,
            None,
            (time.monotonic() - _start) * 1000,
        )
        return result
    except BaseException as exc:
        _emit_analytics(
            _tool_name,
            _params_snapshot,
            None,
            exc,
            (time.monotonic() - _start) * 1000,
        )
        raise


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
            "動手做事前想確認後端通不通 / auth 有沒有效，先呼叫 health()（唯讀）",
        ],
        "notes": [
            f"所有操作 audit log 的 operator 欄 = '{USER_NAME}'（跟 UI 手動操作分辨）",
            "GMBE/PG 帳密相關 endpoint 刻意不暴露（敏感）",
            "預設環境 stage；僅測試環境（sit / stage），不提供 prod",
        ],
    }


@mcp.tool()
def health() -> dict:
    """檢查後端服務健康狀態（唯讀、無副作用，任何異常都不拋錯只回報）。

    動手做事前（尤其要跑一長串 chain）可先呼叫，把「事後埋在 tool call 裡才爆」的
    連線 / auth 失敗提前抓出來。這個 MCP 同時依賴兩個服務（同機不同 port），兩個都探：

    - **ai_studio_api (8081)**：ai-studio 後端，會員 / 點數 / 券 / 經驗 / 等級 / 訂單查詢
      等 tool 走這裡。探唯讀的 `coupon-templates` 並帶 `X-User-Id`，驗**可達性 + auth**
      （那組 admin id 被移除/降權會 401/403）。
    - **qa_platform (8080)**：QA 自動化平台（`_PLATFORM_BASE`），`create_product` /
      `create_order` / `copy_product` / `redeem_voucher` / `extend_item_calendar` 等
      QA 平台 tool 走這裡（端點無 auth）。探 host root 驗**可達性**。

    兩個服務各自 gating 依賴它的那批 tool；`healthy` 需兩者皆通（8081 還要 auth_ok）。
    回傳每個服務的 reachable / auth_ok / http_status / latency_ms / error。
    """
    from urllib.parse import urlsplit

    def _probe(url: str, headers: Optional[dict] = None) -> dict:
        _start = time.monotonic()
        try:
            resp = requests.get(url, headers=headers, timeout=(3, 5))
            return {
                "reachable": True,
                "http_status": resp.status_code,
                "auth_ok": resp.status_code not in (401, 403),
                "latency_ms": round((time.monotonic() - _start) * 1000, 1),
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001 — 診斷用，任何錯都要回報而非拋出
            return {
                "reachable": False,
                "http_status": None,
                "auth_ok": None,
                "latency_ms": round((time.monotonic() - _start) * 1000, 1),
                "error": f"{type(exc).__name__}: {exc}"[:200],
            }

    # QA 平台端點無 cheap health API，探 host root（去掉 /api/v1 之類 path）驗可達性。
    _p = urlsplit(_PLATFORM_BASE)
    platform_root = f"{_p.scheme}://{_p.netloc}/"

    ai_studio = _probe(f"{BASE}/api/tools/coupon-templates", headers=_headers())
    qa_platform = _probe(platform_root)
    return {
        "server": "kkday-qa-tools",
        # 兩個服務各自撐一批 tool，任一掛掉都有 tool 不能用 → healthy 需兩者皆通。
        # 8080 端點無 auth，故只看 reachable；8081 需 reachable + auth_ok。
        "healthy": bool(
            ai_studio["reachable"]
            and ai_studio["auth_ok"]
            and qa_platform["reachable"]
        ),
        "operator": USER_NAME,
        "services": {
            "ai_studio_api": {
                "base_url": BASE,
                "port": 8081,
                "serves": "會員/點數/券/經驗/等級/訂單查詢等 ai-studio tool",
                **ai_studio,
            },
            "qa_platform": {
                "base_url": _PLATFORM_BASE,
                "probe_url": platform_root,
                "port": 8080,
                "serves": "create_product/create_order/copy_product/redeem_voucher/延長月曆等 QA 平台 tool（無 auth）",
                **qa_platform,
            },
        },
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
                "env": "sit / stage",
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
                "env": "sit / stage",
                "exp_value": "要加的經驗值（預設 100）",
            },
            "example": 'add_experience(uuid_or_email="user@kkday.com", env="stage", exp_value=5000)',
        },
        "update_member_tier": {
            "purpose": "直接改會員 tier / expiry_date（跳過經驗值累積）",
            "params": {
                "uuid_or_email": "會員 UUID 或 email",
                "env": "sit / stage",
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
                "env": "sit / stage",
            },
            "example": 'register_member(login_id="test_20260703@kkday.com", env="stage")',
        },
        "create_product": {
            "purpose": "建測試商品（一併建 package + item），約 3 分鐘",
            "required_first": "先跑 product_types() 看 20 種 prod_type 的 key + 中文說明",
            "params": {
                "env": "sit / stage（例：sit / sit218 / stage）",
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
def lookup_member(email: str, env: str) -> dict:
    """用 email 查會員 UUID / tier / 資訊（這支 endpoint 是 email → UUID）。

    Args:
        email: 會員 email（此 endpoint 只吃 email，不能傳 UUID）
        env: 環境 sit / stage（預設 stage）
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
    env: str,
    points: int = 500,
    count: int = 1,
    mode: str = "add",
) -> dict:
    """加（或扣）KKday 點數給指定會員。

    〔詢問模式（預設）〕呼叫前先向使用者確認參數（uuid_or_email / env / points / count / mode）；可附「沿用慣例」選項供一鍵確認。
    〔全自動模式〕使用者明確要求自動時才用預設/慣例值直接執行。未經確認不要自行沿用歷史或套預設。env 只有 sit / stage 兩種；使用者選 sit 時**必須**追問是哪一台（sit0x 或 sit20x 系列），**不得自行預設或編造**環境代號。

    Args:
        uuid_or_email: 會員 UUID 或 email
        env: sit / stage
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
    env: str,
    template: str = "basic_percentage_single",
    coupon_json: Optional[str] = None,
    code_qty: int = 1,
    member_uuid: Optional[str] = None,
    user_uuid: Optional[str] = None,
    user2_uuid: Optional[str] = None,
) -> dict:
    """建立優惠券並可歸戶到會員。

    〔詢問模式（預設）〕呼叫前先向使用者確認參數（env / template / member_uuid 等）；可附「沿用慣例」選項供一鍵確認。
    〔全自動模式〕使用者明確要求自動時才用預設/慣例值直接執行。未經確認不要自行沿用歷史或套預設。env 只有 sit / stage 兩種；使用者選 sit 時**必須**追問是哪一台（sit0x 或 sit20x 系列），**不得自行預設或編造**環境代號。

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
def add_experience(uuid_or_email: str, env: str, exp_value: int = 100) -> dict:
    """加經驗值給會員（升等用）。

    〔詢問模式（預設）〕呼叫前先向使用者確認參數（uuid_or_email / env / exp_value）；可附「沿用慣例」選項供一鍵確認。
    〔全自動模式〕使用者明確要求自動時才用預設/慣例值直接執行。未經確認不要自行沿用歷史或套預設。env 只有 sit / stage 兩種；使用者選 sit 時**必須**追問是哪一台（sit0x 或 sit20x 系列），**不得自行預設或編造**環境代號。

    Args:
        uuid_or_email: 會員 UUID 或 email
        env: sit / stage
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

    〔詢問模式（預設）〕呼叫前先向使用者確認參數（uuid_or_email / downgrade_status）；可附「沿用慣例」選項供一鍵確認。
    〔全自動模式〕使用者明確要求自動時才用預設/慣例值直接執行。未經確認不要自行沿用歷史或套預設。env 只有 sit / stage 兩種；使用者選 sit 時**必須**追問是哪一台（sit0x 或 sit20x 系列），**不得自行預設或編造**環境代號。

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
def query_exp_value(uuid_or_email: str, env: str) -> dict:
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
def tier_rules(env: str) -> dict:
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
    "silver": "01",
    "白銀": "01",
    "gold": "04",
    "黃金": "04",
    "platinum": "02",
    "白金": "02",
    "diamond": "03",
    "black": "03",
    "黑鑽": "03",
}


@mcp.tool()
def update_member_tier(
    uuid_or_email: str,
    env: str,
    new_tier: Optional[str] = None,
    new_expiry_date: Optional[str] = None,
    trigger_dkron: bool = False,
) -> dict:
    """直接改會員 tier / expiry_date（跳過經驗值累積）。

    〔詢問模式（預設）〕呼叫前先向使用者確認參數（uuid_or_email / env / new_tier / new_expiry_date / trigger_dkron）；可附「沿用慣例」選項供一鍵確認。
    〔全自動模式〕使用者明確要求自動時才用預設/慣例值直接執行。未經確認不要自行沿用歷史或套預設。env 只有 sit / stage 兩種；使用者選 sit 時**必須**追問是哪一台（sit0x 或 sit20x 系列），**不得自行預設或編造**環境代號。

    ⚠️ new_tier 只能是代碼 01/02/03/04（不是英文名）：
        01=白銀(silver) / 04=黃金(gold) / 02=白金(platinum) / 03=黑鑽(diamond)。
    傳英文名（silver/gold/…）會自動換成代碼；其他值會擋下。

    Args:
        uuid_or_email: 會員 UUID / email
        env: sit / stage
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
        code = (
            new_tier
            if new_tier in TIER_CODES
            else _TIER_NAME_TO_CODE.get(new_tier.strip().lower())
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
def tier_change_records(uuid_or_email: str, env: str) -> dict:
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
def trigger_dkron_tier(env: str) -> dict:
    """觸發 Dkron 的 tier-expire job（不直接改 DB，透過排程觸發）。

    〔詢問模式（預設）〕呼叫前先向使用者確認 env；〔全自動模式〕使用者明確要求自動時才直接執行、不問。未經確認不要自行套預設。env 只有 sit / stage 兩種；使用者選 sit 時**必須**追問是哪一台（sit0x 或 sit20x 系列），**不得自行預設或編造**環境代號。
    """
    return _call("POST", "/api/tools/trigger-dkron-tier", json={"env": env})


# ── GMBE / PG 帳密（刻意不提供 MCP tool）─────────────────────────────
# 這兩組 endpoint（/api/tools/gmbe-credentials, /api/tools/pg-credentials）
# 涉及敏感帳密存取，不讓 LLM 直接操作。要管帳密請走 ai-studio UI。
# 相關 tool（tier / member-orders 等）需要對應帳密才會 work，會在 backend
# 自行讀取，這邊不需要暴露。


# ── 會員註冊 ──────────────────────────────────────────────────────────


@mcp.tool()
def register_member(login_id: str, env: str, password: str = "Aa12345678") -> dict:
    """註冊測試會員。

    〔詢問模式（預設）〕呼叫前先向使用者確認 login_id / password / env；可附「沿用慣例」選項供一鍵確認
    （login_id 取 register_member_history 最大 +N 的下一個，如 xxx+1@kkday.com；password 預設 Aa12345678）。env 無預設、必須問。
    〔全自動模式〕使用者明確說「自動創 / 直接建」時才用慣例值直接建立、不問。未經確認不要自行沿用歷史或套預設。env 只有 sit / stage 兩種；使用者選 sit 時**必須**追問是哪一台（sit0x 或 sit20x 系列），**不得自行預設或編造**環境代號。

    Args:
        login_id: 登入 ID / email
        password: 預設 "Aa12345678"
        env: sit / stage
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
def get_member_orders(uuid_or_email: str, env: str) -> dict:
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
def complete_order(order_mid: str, env: str) -> dict:
    """把訂單推進到完成狀態（BE2 認養 + 推狀態，測試用）。

    〔詢問模式（預設）〕呼叫前先向使用者確認參數（order_mid / env）；可附「沿用慣例」選項供一鍵確認。
    〔全自動模式〕使用者明確要求自動時才用預設/慣例值直接執行。未經確認不要自行沿用歷史或套預設。env 只有 sit / stage 兩種；使用者選 sit 時**必須**追問是哪一台（sit0x 或 sit20x 系列），**不得自行預設或編造**環境代號。

    Args:
        order_mid: 訂單編號（order master id）
        env: sit / stage
    """
    return _call(
        "POST", "/api/tools/complete-order", json={"order_mid": order_mid, "env": env}
    )


# ── 商品 / 兌換 ────────────────────────────────────────────────────────


@mcp.tool()
def product_categories(env: str) -> dict:
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


# @mcp.tool()  # 暫時停用：不在 MCP 註冊（保留實作，需要時取消註解即可）
def create_product(env: str, prod_type: str) -> dict:
    """建立測試商品（proxy 到 autotest-service，會一併建 package + item，較慢約 3 分鐘）。

    〔詢問模式（預設）〕呼叫前先向使用者確認 env / prod_type（見下方判斷規則）；可附「沿用慣例」選項供一鍵確認。
    〔全自動模式〕使用者明確要求自動時才用預設/慣例值直接執行。未經確認不要自行沿用歷史或套預設。env 只有 sit / stage 兩種；使用者選 sit 時**必須**追問是哪一台（sit0x 或 sit20x 系列），**不得自行預設或編造**環境代號。

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
        env: 環境 sit / stage（例：sit / sit218 / stage）— 必填，未指定要問使用者
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


# @mcp.tool()  # 暫時停用：不在 MCP 註冊（保留實作，需要時取消註解即可）
def redeem_voucher(
    env: str, product_oid: str, package_oid: str, qyt: str = "1"
) -> dict:
    """用 voucher（優惠券）兌換商品訂單。

    〔詢問模式（預設）〕呼叫前先向使用者確認參數（env / product_oid / package_oid / qyt）；可附「沿用慣例」選項供一鍵確認。
    〔全自動模式〕使用者明確要求自動時才用預設/慣例值直接執行。未經確認不要自行沿用歷史或套預設。env 只有 sit / stage 兩種；使用者選 sit 時**必須**追問是哪一台（sit0x 或 sit20x 系列），**不得自行預設或編造**環境代號。

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


# ══════════════════════════════════════════════════════════════════════════
# QA Test Platform tools — 下游打 QA 平台 /testtool（:8080），與上方 ai-studio
# 工具併存於同一個 MCP。埋點沿用上方 _emit_analytics（送 ai-studio dashboard），
# operator 標記 kkday_qa_platform_mcp、success 反映真實業務結果。
# 抽象層 _platform_call：env 驗證 + sync/async 輪詢 + 失敗診斷 + 埋點。
# 重用 ai-studio 既有的 _check_env / _sanitize_params / PROD_TYPE_OPTIONS。
# ══════════════════════════════════════════════════════════════════════════

_PLATFORM_BASE = os.getenv(
    "KKDAY_QA_PLATFORM_BASE",
    "http://autotest-service.sit.kkday.com:8080/api/v1",
).rstrip("/")
_PLATFORM_OPERATOR = "kkday_qa_platform_mcp"
_PLATFORM_TRIGGER = "MCP"
_PLATFORM_SYNC_TIMEOUT = (10, 300)
_PLATFORM_POLL_TIMEOUT = (10, 30)
_PLATFORM_POLL_INTERVAL = 3
_PLATFORM_POLL_MAX_WAIT = 600


def _platform_headers() -> dict:
    """QA 平台 /testtool 端點無 auth，只需 content-type。"""
    return {"Content-Type": "application/json"}


# ── 失敗診斷（向 qatest-web errorMap.ts / buildBe2ProductEditUrl 對齊）──
_PLATFORM_ERROR_MAP = {
    "product_check_fail": {"title": "錯誤的商品", "desc": "請檢查參數，該商品不存在或編號錯誤。"},
    "package_check_fail": {"title": "錯誤的套餐", "desc": "請檢查參數，該套餐不存在於該商品或編號錯誤。"},
    "product_not_active": {"title": "商品尚未上架", "desc": "此商品存在但尚未上架。"},
    "no_sellable_package": {"title": "沒有可下單的套餐", "desc": "請確認是否售罄、下架或超過銷售期。"},
    "create_redeem_fail": {"title": "兌換券建立失敗", "desc": "創建兌換券 API 失效。"},
    "extend_item_calendar_fail": {"title": "延長銷售日曆失敗", "desc": "請確認商品或套餐設定正確。"},
    "create_product_fail": {"title": "商品創建失敗", "desc": ""},
}


def _platform_be2_base(env: str) -> Optional[str]:
    """env → BE2 base URL（同步自 qatest-web utils.ts getBe2BaseUrl）。"""
    if env == "stage":
        return "https://be2.stage.kkday.com"
    if env == "sit":
        return "https://be2.sit.kkday.com"
    if env.startswith("sit") and len(env) > 3:
        return f"https://be2-{env[3:]}.sit.kkday.com"
    return None


def _platform_be2_edit_url(env: str, prod_oid) -> Optional[str]:
    """組 BE2 商品編輯頁 URL，讓使用者手動調整（環境差異失敗很常見）。"""
    base = _platform_be2_base(env)
    return f"{base}/v2/product/{prod_oid}/edit-product-detail" if base else None


def _platform_is_failure(result: dict) -> bool:
    """判斷 QA 平台 tool 回傳是否為失敗（涵蓋 copy / guard / preview / async 包裝）。"""
    if not isinstance(result, dict):
        return False
    if str(result.get("copy_status", "")).lower() == "fail":
        return True
    if str(result.get("publish_status", "")).lower() == "fail":
        return True
    return result.get("status") in ("error", "failed", "timeout")


def _platform_extract_why(result: dict) -> list:
    """蒐集失敗原因 raw detail（不吞資訊，去重保序）。"""
    whys = []
    for e in result.get("errors") or []:
        if isinstance(e, dict) and e.get("detail"):
            whys.append(e["detail"])
    if result.get("detail"):
        whys.append(result["detail"])
    if not whys and result.get("message"):
        whys.append(result["message"])
    seen, out = set(), []
    for w in whys:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def _platform_diagnose(result: dict, env: str) -> dict:
    """QA 平台 tool 失敗時附加 _diagnosis（分類 + 原因 + prod_oid + BE2 URL）。成功原樣回。"""
    if not _platform_is_failure(result):
        return result
    diag: dict = {}
    cfg = _PLATFORM_ERROR_MAP.get(result.get("message"))
    if cfg:
        diag["title"] = cfg["title"]
        if cfg["desc"]:
            diag["desc"] = cfg["desc"]
    why = _platform_extract_why(result)
    if why:
        diag["why"] = why
    prod_oid = (result.get("target") or {}).get("prod_oid") or result.get("prod_oid")
    if prod_oid:
        diag["created_prod_oid"] = prod_oid
        url = _platform_be2_edit_url(env, prod_oid)
        if url:
            diag["be2_edit_url"] = url
            diag["hint"] = (
                f"此類失敗常因環境差異造成，並非工具 bug。商品已建立於 {env}"
                f"（prod_oid={prod_oid}），多半可到 BE2 手動調整後再定版：{url}"
            )
    if result.get("status") == "timeout":
        diag.setdefault("hint", result.get("message"))
    if not diag:
        return result
    enriched = dict(result)
    enriched["_diagnosis"] = diag
    return enriched


# ── copy_product 兩段式 confirm-token（進程內一次性 nonce）──
# preview 發、execute 單次消費、TTL 15 分自清、綁 (target_env, source_env, prod_oid)。
# 硬性保證「execute 前一定先 preview」，防跳過供應商覆核導致靜默 fallback。
_platform_preview_cache: dict = {}
_platform_preview_lock = threading.Lock()
_PLATFORM_PREVIEW_TTL = 900.0


def _platform_purge_previews_locked() -> None:
    """清掉過期 token（呼叫前須持有 _platform_preview_lock）。"""
    now = time.monotonic()
    dead = [k for k, v in _platform_preview_cache.items() if now - v["ts"] > _PLATFORM_PREVIEW_TTL]
    for k in dead:
        _platform_preview_cache.pop(k, None)


# ── 平台呼叫（sync / async 進程內輪詢）──
def _platform_sync(payload: dict) -> dict:
    """同步打 /testtool/run，回傳 data。業務失敗仍 HTTP 200，資訊在 dict 內。"""
    resp = requests.post(
        f"{_PLATFORM_BASE}/testtool/run",
        headers=_platform_headers(),
        json=payload,
        timeout=_PLATFORM_SYNC_TIMEOUT,
    )
    if not resp.ok:
        raise RuntimeError(
            f"POST /testtool/run ({payload['tool_name']}) → {resp.status_code}: {resp.text[:500]}"
        )
    return resp.json().get("data", {})


def _platform_async(payload: dict) -> dict:
    """非同步打 /testtool/run_async，進程內輪詢 /progress 到完成。逾時 ≠ 失敗。"""
    resp = requests.post(
        f"{_PLATFORM_BASE}/testtool/run_async",
        headers=_platform_headers(),
        json=payload,
        timeout=_PLATFORM_POLL_TIMEOUT,
    )
    if not resp.ok:
        raise RuntimeError(
            f"POST /testtool/run_async ({payload['tool_name']}) → {resp.status_code}: {resp.text[:500]}"
        )
    task_id = (resp.json().get("data") or {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"run_async ({payload['tool_name']}) 未回傳 task_id")
    waited = 0
    while waited < _PLATFORM_POLL_MAX_WAIT:
        time.sleep(_PLATFORM_POLL_INTERVAL)
        waited += _PLATFORM_POLL_INTERVAL
        pr = requests.get(
            f"{_PLATFORM_BASE}/testtool/progress/{task_id}",
            headers=_platform_headers(),
            timeout=_PLATFORM_POLL_TIMEOUT,
        )
        if pr.status_code == 404 or not pr.ok:
            continue
        data = pr.json().get("data") or {}
        status = data.get("status")
        if status in ("completed", "failed"):
            result = data.get("result")
            if status == "failed":
                return {"status": "failed", "detail": result, "task_id": task_id}
            return result
    return {
        "status": "timeout",
        "message": (
            f"後端仍在執行（已等 {_PLATFORM_POLL_MAX_WAIT}s），逾時 ≠ 失敗；"
            "請稍後用對應 *_history 或商品頁查證是否已完成。"
        ),
        "task_id": task_id,
    }


def _platform_call(
    platform_tool: str,
    environment: str,
    tool_kwargs: dict,
    async_: bool = False,
    mcp_name: Optional[str] = None,
) -> dict:
    """QA 平台工具統一入口：env 驗證 → sync/async 打 :8080 → 失敗診斷 → 埋點。

    埋點沿用 ai-studio 的 _emit_analytics（operator=kkday_qa_platform_mcp、
    success 反映真實業務結果）。mcp_name 是對外 tool 名（供埋點），platform_tool
    是平台 ToolMap 的 key（進 payload）。
    """
    _check_env(environment)
    source_env = (tool_kwargs or {}).get("source_environment")
    if source_env is not None:
        _check_env(source_env)
    payload = {
        "environment": environment,
        "tool_name": platform_tool,
        "tool_kwargs": tool_kwargs,
        "trigger_point": _PLATFORM_TRIGGER,
    }
    label = mcp_name or platform_tool
    params_snap = _sanitize_params(tool_kwargs)
    start = time.monotonic()
    try:
        result = _platform_async(payload) if async_ else _platform_sync(payload)
        result = _platform_diagnose(result, environment)
        _emit_analytics(
            label, params_snap, result, None, (time.monotonic() - start) * 1000,
            operator=_PLATFORM_OPERATOR, success=not _platform_is_failure(result),
        )
        return result
    except BaseException as exc:
        _emit_analytics(
            label, params_snap, None, exc, (time.monotonic() - start) * 1000,
            operator=_PLATFORM_OPERATOR, success=False,
        )
        raise


# ── QA 平台 tools（對外名與 ai-studio 併存；create_product/redeem_voucher 用
#    name= 覆寫，避開與上方休眠函式的 Python module 撞名）──
@mcp.tool(name="create_product")
def platform_create_product(env: str, prod_type: str) -> dict:
    """建立測試商品（QA 平台，一併建 package + item，較慢約 3 分鐘）。

    呼叫前先向使用者確認 env / prod_type。env 只有 sit / stage；選 sit 必須追問哪一台
    （sit0x / sit20x，如 sit206），不得自行預設或編造。使用者沒指明種類時，把
    PROD_TYPE_OPTIONS 列給他挑，別擅自帶 normal。

    Args:
        env: 環境 sit / stage（例：sit206 / stage）
        prod_type: 商品種類，20 選 1（見 PROD_TYPE_OPTIONS）
    """
    prod_type = (prod_type or "").strip()
    if prod_type not in PROD_TYPE_OPTIONS:
        raise ValueError(
            f"prod_type '{prod_type}' 不是有效值；請把選項列給使用者挑，選定後再帶入。"
            f"可用值：{', '.join(PROD_TYPE_OPTIONS)}"
        )
    return _platform_call(
        "product", env, {"prod_type": prod_type}, async_=True, mcp_name="create_product"
    )


@mcp.tool(name="redeem_voucher")
def platform_redeem_voucher(
    env: str, product_oid: str, package_oid: str, qyt: str = "1"
) -> dict:
    """用 voucher 兌換商品訂單（QA 平台）。

    呼叫前先確認參數。env 只有 sit / stage；選 sit 要追問哪一台，不得自行編造。

    Args:
        env: sit / stage
        product_oid: 商品 OID
        package_oid: 套餐 OID
        qyt: 兌換數量（字串，預設 '1'）
    """
    return _platform_call(
        "redeem", env,
        {"productOid": str(product_oid), "packageOid": str(package_oid), "qyt": str(qyt)},
        mcp_name="redeem_voucher",
    )


@mcp.tool()
def create_order(
    env: str,
    product_oid: str,
    package_oid: str,
    qty: int = 1,
    order_count: int = 1,
    go_date_shift: int = 1,
) -> dict:
    """建立測試訂單（QA 平台）。

    Args:
        env: sit / stage
        product_oid: 商品 OID
        package_oid: 套餐 OID
        qty: 每筆訂單數量（預設 1）
        order_count: 下單筆數（預設 1）
        go_date_shift: 出發日為幾天後（預設 1）
    """
    return _platform_call(
        "create_order", env,
        {
            "productOid": str(product_oid),
            "packageOid": str(package_oid),
            "qty": qty,
            "order_count": order_count,
            "go_date_shift": go_date_shift,
        },
        async_=True,
    )


@mcp.tool()
def extend_item_calendar(
    env: str, product_oid: str, package_oid: str, extend_month: int
) -> dict:
    """延長單一 package 的銷售月曆（QA 平台）。

    Args:
        env: sit / stage
        product_oid: 商品 OID
        package_oid: 套餐 OID
        extend_month: 延長月數 1~12
    """
    return _platform_call(
        "extend_item_calendar", env,
        {"productOid": str(product_oid), "packageOid": str(package_oid), "extend_month": extend_month},
    )


@mcp.tool()
def batch_extend_item_calendar(env: str, product_oid: str, extend_month: int) -> dict:
    """批次延長整個商品所有 package 的月曆（QA 平台）。⚠️ 不適用 OCBT 子母單商品。

    Args:
        env: sit / stage
        product_oid: 商品 OID
        extend_month: 延長月數 1~12
    """
    return _platform_call(
        "batch_extend_item_calendar", env,
        {"productOid": str(product_oid), "extend_month": extend_month},
        async_=True,
    )


@mcp.tool()
def copy_product_preview(target_env: str, source_env: str, prod_oid: str) -> dict:
    """跨環境複製商品的【第一步】：預覽來源商品 + 檢查供應商在目標環境是否存在，回 confirm_token。

    回傳的 target_suppliers 標出每個來源供應商在目標環境 exists 與否；把 exists=false
    的列給使用者，確認用 fallback 還是指定 override，再帶 confirm_token 呼叫 copy_product。

    Args:
        target_env: 要複製到哪個環境（目標）— sit / stage
        source_env: 從哪個環境讀來源商品 — sit / stage
        prod_oid: 來源商品 OID
    """
    result = _platform_call(
        "copy_product_preview", target_env,
        {"source_environment": source_env, "productOid": str(prod_oid), "raise_on_fail": False},
        mcp_name="copy_product_preview",
    )
    if not isinstance(result, dict) or result.get("status") != "success":
        return result
    token = uuid.uuid4().hex
    with _platform_preview_lock:
        _platform_purge_previews_locked()
        _platform_preview_cache[token] = {
            "target_env": target_env,
            "source_env": source_env,
            "prod_oid": str(prod_oid),
            "target_suppliers": result.get("target_suppliers", {}),
            "pkg_supplier_map": result.get("pkg_supplier_map", {}),
            "ts": time.monotonic(),
        }
    return {
        "status": "success",
        "confirm_token": token,
        "product_name": result.get("product_name"),
        "package_count": result.get("package_count"),
        "source_suppliers": result.get("source_suppliers"),
        "target_suppliers": result.get("target_suppliers"),
        "_next_step": (
            "與使用者確認供應商對應：target_suppliers 中 exists=false 的在目標環境不存在，"
            "可用 fallback_supplier_oid（預設 15247）或在 supplier_override_map 指定替代；"
            "確認後呼叫 copy_product(...) 並帶入本 confirm_token 完成複製。"
        ),
    }


@mcp.tool()
def copy_product(
    target_env: str,
    source_env: str,
    prod_oid: str,
    confirm_token: str,
    supplier_override_map: Optional[dict] = None,
    fallback_supplier_oid: int = 15247,
) -> dict:
    """跨環境複製商品的【第二步】：實際執行複製。

    ⚠️ 必須先呼叫 copy_product_preview 取得 confirm_token，否則拒絕 —— 這是為了保證
    供應商對應有經過使用者覆核（否則供應商會被靜默替換成 fallback）。
    target_env / source_env / prod_oid 必須與 preview 時完全一致。

    Args:
        target_env: 目標環境（須與 preview 一致）
        source_env: 來源環境（須與 preview 一致）
        prod_oid: 來源商品 OID（須與 preview 一致）
        confirm_token: copy_product_preview 回傳的 token（不可自行編造）
        supplier_override_map: 選填。{原供應商oid字串: 改用的oid}
        fallback_supplier_oid: 選填。目標環境找不到對應供應商時的預設（預設 15247）
    """
    with _platform_preview_lock:
        _platform_purge_previews_locked()
        entry = _platform_preview_cache.pop(confirm_token, None)
    if entry is None:
        return {
            "status": "error",
            "message": (
                "尚未預覽，或 confirm_token 無效 / 已使用 / 已逾時。請先呼叫 "
                "copy_product_preview(target_env, source_env, prod_oid) 取得新 token，"
                "與使用者確認供應商後再帶入。"
            ),
        }
    if (entry["target_env"], entry["source_env"], entry["prod_oid"]) != (
        target_env,
        source_env,
        str(prod_oid),
    ):
        return {
            "status": "error",
            "message": (
                f"confirm_token 與本次商品/環境不符（token 對應 "
                f"{entry['source_env']}/{entry['prod_oid']} → {entry['target_env']}）。"
                "請對同一組商品重新 copy_product_preview。"
            ),
        }
    return _platform_call(
        "copy_product", target_env,
        {
            "source_environment": source_env,
            "productOid": str(prod_oid),
            "fallback_supplier_oid": fallback_supplier_oid,
            "supplier_override_map": supplier_override_map or {},
            "target_suppliers": entry["target_suppliers"],
            "pkg_supplier_map": entry["pkg_supplier_map"],
        },
        async_=True,
        mcp_name="copy_product",
    )


def main() -> None:
    """FastMCP stdio server 入口。"""
    mcp.run()


if __name__ == "__main__":
    main()

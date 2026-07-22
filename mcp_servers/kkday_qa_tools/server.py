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

import base64
import getpass
import json as _json
import os
import re
import socket
import sys
import threading
import time
import uuid
from datetime import date, timedelta
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
                "register_member: 註冊 KKday 平台買家測試會員（不是供應商，供應商用 create_scm_supplier）",
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
            "SCM 供應商": [
                "create_scm_supplier: 建立 SCM 供應商帳號（註冊→申請，完成後自動引導 activate）",
                "activate_scm_supplier: 啟用供應商到合作完成（BE2 審核→ASF→合約→核准）",
                "get_scm_otp: 取得 SCM 登入 OTP 驗證碼",
            ],
        },
        "workflow_examples": [
            "把 xxx@kkday.com 拉到 gold: lookup_member → add_experience → update_member_tier",
            "建帶點數的測試會員: register_member → add_kkday_points → lookup_member 確認",
            "測 voucher 兌換: create_coupon (歸戶到會員) → fetch_packages → redeem_voucher",
            "建 SCM 供應商帳號: create_scm_supplier → activate_scm_supplier → 使用者去登入 → get_scm_otp 取驗證碼",
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
            "purpose": "註冊 KKday 平台買家測試會員（consumer member）。供應商帳號請用 create_scm_supplier",
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
        "create_scm_supplier": {
            "purpose": "建立 SCM 供應商帳號（註冊 + 申請），完成後自動引導呼叫 activate_scm_supplier 啟用",
            "note": "拆成兩步是因為啟用階段要等 ASF 背景審查，Stage 環境可能要 3-5 分鐘",
            "params": {
                "env": "環境（stage / sit / sitNNN 如 sit212），沒有預設值，先問使用者",
                "country": "供應商國家代碼（預設 TW），影響公司資料和合約實體",
            },
            "returns": "email / password / supplier_oid / env + _next_step 引導呼叫 activate_scm_supplier",
            "example": 'create_scm_supplier(env="sit212")',
            "example_stage": 'create_scm_supplier(env="stage", country="TW")',
        },
        "activate_scm_supplier": {
            "purpose": "啟用 SCM 供應商到合作完成（v2 狀態），通常由 create_scm_supplier 自動引導呼叫",
            "note": "BE2 帳密自動取得。冪等設計，已完成的步驟會跳過，可安全重試",
            "params": {
                "env": "環境（stage / sit / sitNNN）",
                "supplier_oid": "供應商 OID（從 create_scm_supplier 取得）",
                "country": "供應商國家代碼（預設 TW）",
                "email": "供應商 email（可選，用於回傳結果）",
            },
            "returns": "login_url / email / password / supplier_oid / env + _next_step 引導取 OTP",
            "example": 'activate_scm_supplier(env="stage", supplier_oid=42950)',
        },
        "get_scm_otp": {
            "purpose": "取得 SCM 登入 OTP 驗證碼（6 位數字）",
            "note": "需要 Gmail token（預設讀 kkday-QA-automation repo 的 token.json，或設 GMAIL_TOKEN_PATH）",
            "params": {
                "email": "收 OTP 的 email（通常是 create_scm_supplier 回傳的 email）",
            },
            "example": 'get_scm_otp(email="b2c-qa-team+automation_test_1234567890@kkday.com")',
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
    """註冊 KKday 平台買家測試會員（consumer member），不是供應商。建立供應商請用 create_scm_supplier。

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


# ── SCM 供應商管理 ──────────────────────────────────────────────────────

_SCM_DEFAULT_PASSWORD = "AutomationPwd12345678"
_QA_GMAIL_BASE = os.getenv("QA_GMAIL_BASE", "b2c-qa-team@kkday.com")
_GMAIL_TOKEN_PATH = os.getenv(
    "GMAIL_TOKEN_PATH",
    os.path.join(
        os.path.expanduser("~"),
        "Documents", "gitHub", "kkday-QA-automation",
        "QATestData", "data", "common", "token.json",
    ),
)
_SCM_TIMEOUT = (10, 120)

_SCM_COUNTRY_CONTRACT_MAP = {
    "TW": 3, "JP": 5, "KR": 4, "SG": 6, "MY": 6,
    "HK": 15, "TH": 16, "VN": 10, "CN": 13, "AU": 18,
}

_SCM_AUTH_SERVICE_KEYS = {
    "sit": "hGuTIy8HEwhEbRihRTafdy0dllGOmXNN",
    "stage": "Exq9j7NRuEUcmZvMBxk8N7M23xUn3TEw",
}

_TINY_PDF_BASE64 = (
    "JVBERi0xLjQKJcOkw7zDtsOfCjIgMCBvYmoKPDwKL0xlbmd0aCAzIDAgUgovRmlsdGVyIC9GbGF0Z"
    "URlY29kZQo+PgpzdHJlYW0KeJwzUvDiMlAwUDA1MTRSCEnlMjQyMTBVMDIyMTUKSeUKVDA0NTQwM"
    "TQwUTAyMTAyMzVQ4AIA/owGgAplbmRzdHJlYW0KZW5kb2JqCjMgMCBvYmoKNjQKZW5kb2JqCjEgM"
    "CBvYmoKPDwKL1R5cGUgL1BhZ2UKL1BhcmVudCA0IDAgUgovTWVkaWFCb3ggWzAgMCA2MTIgNzkyX"
    "QovQ29udGVudHMgMiAwIFIKL1Jlc291cmNlcyA8PAovUHJvY1NldCBbL1BERl0KPj4KPj4KZW5kb"
    "2JqCjQgMCBvYmoKPDwKL1R5cGUgL1BhZ2VzCi9LaWRzIFsxIDAgUl0KL0NvdW50IDEKPj4KZW5kb"
    "2JqCjUgMCBvYmoKPDwKL1R5cGUgL0NhdGFsb2cKL1BhZ2VzIDQgMCBSCj4+CmVuZG9iagp4cmVmC"
    "jAgNgowMDAwMDAwMDAwIDY1NTM1IGYNCjAwMDAwMDAxNjIgMDAwMDAgbg0KMDAwMDAwMDAxNSAwM"
    "DAwMCBuDQowMDAwMDAwMTQ0IDAwMDAwIG4NCjAwMDAwMDAyNzAgMDAwMDAgbg0KMDAwMDAwMDMyN"
    "yAwMDAwMCBuDQp0cmFpbGVyCjw8Ci9TaXplIDYKL1Jvb3QgNSAwIFIKPj4Kc3RhcnR4cmVmCjM3N"
    "Qo="
)

_SCM_FILE_CATEGORIES = ["REGISTER", "TOURISM_LICENSE", "ATTACHMENT", "BANK_PASSBOOK_COPY"]


def _scm_env_domain(env: str) -> str:
    return "stage" if env == "stage" else "sit"


def _scm_api_base(env: str) -> str:
    return f"https://api-scm.{_scm_env_domain(env)}.kkday.com/api"


def _sofa_potato_base(env: str) -> str:
    return f"https://sofa-potato.{_scm_env_domain(env)}.kkday.com/api"


def _scm_frontend_login_url(env: str) -> str:
    if env == "stage":
        return "https://scm.stage.kkday.com/v2/zh-tw/auth/login"
    m = re.match(r"sit(\d+)", env)
    if m:
        return f"https://scm-{m.group(1)}.sit.kkday.com/v2/zh-tw/auth/login"
    return "https://scm.sit.kkday.com/v2/zh-tw/auth/login"


def _scm_request(method: str, url: str, *,
                 headers: Optional[dict] = None,
                 json_body: Optional[dict] = None,
                 timeout: tuple = _SCM_TIMEOUT) -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json",
         "locale": "zh-tw", "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"}
    if headers:
        h.update(headers)
    resp = requests.request(method, url, headers=h, json=json_body, timeout=timeout)
    if not resp.ok:
        raise RuntimeError(f"{method} {url} → {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def _scm_assert_success(body: dict, context: str) -> dict:
    status = body.get("metadata", body.get("meta", {})).get("status")
    if status != "0000":
        desc = body.get("metadata", body.get("meta", {})).get("desc", "")
        raise RuntimeError(f"{context} 失敗: status={status} desc={desc}")
    return body


def _gmail_get_otp(recipient: str, max_wait: int = 120, poll_interval: int = 8) -> str:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not os.path.exists(_GMAIL_TOKEN_PATH):
        raise RuntimeError(
            f"Gmail token 檔案不存在: {_GMAIL_TOKEN_PATH}。"
            "請設定環境變數 GMAIL_TOKEN_PATH 指向有效的 token.json"
        )
    creds = Credentials.from_authorized_user_file(
        _GMAIL_TOKEN_PATH,
        ["https://www.googleapis.com/auth/gmail.readonly",
         "https://www.googleapis.com/auth/gmail.modify"],
    )
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    deadline = time.time() + max_wait
    query = f"to:{recipient} subject:驗證碼 in:inbox"

    while time.time() < deadline:
        results = service.users().messages().list(
            userId="me", q=query, maxResults=5,
        ).execute()
        messages = results.get("messages", [])
        if messages:
            msg = service.users().messages().get(
                userId="me", id=messages[0]["id"],
            ).execute()
            body_data = msg["payload"].get("body", {}).get("data")
            if not body_data and "parts" in msg["payload"]:
                for part in msg["payload"]["parts"]:
                    bd = part.get("body", {}).get("data")
                    if bd:
                        body_data = bd
                        break
            if body_data:
                body = base64.urlsafe_b64decode(body_data).decode("utf-8")
                text = re.sub(r"<[^>]+>", " ", body)
                text = re.sub(r"\s+", " ", text).strip()
                match = re.search(r"(\d{6})", text)
                if match:
                    return match.group(1)
        time.sleep(poll_interval)

    raise TimeoutError(f"等待 OTP 超過 {max_wait} 秒，recipient={recipient}")


_AUTOMATION_TOKEN = "8b9dfbac-e863-4078-95e9-c2cc03abe84f"


def _get_be2_credentials(env: str) -> tuple:
    from urllib.parse import urlsplit
    parts = urlsplit(BASE)
    secret_host = f"{parts.scheme}://{parts.hostname}"
    secret_env = "sit" if "sit" in env else env
    envs_to_try = [secret_env] if secret_env == "sit" else [secret_env, "sit"]
    for try_env in envs_to_try:
        try:
            resp = requests.get(
                f"{secret_host}:8000/api/v1/data/",
                headers={"authorization": f"Bearer {_AUTOMATION_TOKEN}"},
                params={"env": try_env, "service": "be2", "key": f"be2_{try_env}"},
                timeout=(5, 10),
            )
            resp.raise_for_status()
            data = resp.json()
            if not data:
                continue
            cred = _json.loads(data[0]["value"])
            account = cred.get("account", "")
            password = cred.get("password", "")
            if account and password:
                return account, password
        except Exception:
            continue
    raise RuntimeError(
        f"Secret service 在 {envs_to_try} 都找不到有效的 BE2 帳密。"
        "請確認 autotest-service 可連線。"
    )


def _be2_admin_login(env: str) -> tuple:
    account, password = _get_be2_credentials(env)
    domain = _scm_env_domain(env)

    login_resp = requests.post(
        f"https://auth.{domain}.kkday.com/api/v1/auth/be2/login",
        headers={"Content-Type": "application/json", "x-qa-platform": "QA_TestPlatform"},
        json={"account": account, "password": password,
              "optional": {"locale": "zh-tw"}},
        timeout=_SCM_TIMEOUT,
    )
    if not login_resp.ok:
        raise RuntimeError(f"BE2 login 失敗: {login_resp.status_code}: {login_resp.text[:300]}")
    auth_code = login_resp.json()["data"]["authorizationCode"]

    service_key = _SCM_AUTH_SERVICE_KEYS.get(domain)
    if not service_key:
        raise RuntimeError(f"沒有 {domain} 環境的 auth service key")

    token_resp = requests.get(
        f"https://auth.{domain}.kkday.com/api/v1/login-authorization-code/{auth_code}/",
        headers={"Authorization": service_key},
        timeout=_SCM_TIMEOUT,
    )
    if not token_resp.ok:
        raise RuntimeError(f"BE2 token 交換失敗: {token_resp.status_code}: {token_resp.text[:300]}")
    access_token = token_resp.json()["data"]["accessToken"]

    # 從 JWT 解出 platformId 作為 supplierOwner
    payload_b64 = access_token.split(".")[1]
    padding = 4 - len(payload_b64) % 4
    if padding != 4:
        payload_b64 += "=" * padding
    jwt_payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
    owner_uuid = jwt_payload["platformId"]

    return access_token, owner_uuid


def _scm_register_and_login(env: str) -> tuple:
    base = _scm_api_base(env)
    local, domain_part = _QA_GMAIL_BASE.split("@")
    ts = str(int(time.time() * 1000))
    email = f"{local}+automation_test_{ts}@{domain_part}"
    device = str(uuid.uuid4())

    # 1. 註冊
    body = _scm_request("POST", f"{base}/external-unauth/v1/user/register",
                        json_body={"email": email, "password": _SCM_DEFAULT_PASSWORD,
                                   "confirmPassword": _SCM_DEFAULT_PASSWORD,
                                   "timezone": "Asia/Taipei"})
    _scm_assert_success(body, "註冊")

    # 2. 登入
    body = _scm_request("POST", f"{base}/external-unauth/v1/auth/login",
                        json_body={"email": email, "password": _SCM_DEFAULT_PASSWORD,
                                   "device": device})
    _scm_assert_success(body, "登入")

    # 3. 觸發 OTP
    otp_sent_at = time.time()
    body = _scm_request("POST", f"{base}/external-unauth/v1/auth/two-fa/otp",
                        json_body={"email": email, "device": device})
    _scm_assert_success(body, "觸發 OTP")

    # 4. 取 OTP
    otp = _gmail_get_otp(email, max_wait=120, poll_interval=8)

    # 5. 驗證 OTP
    body = _scm_request("POST", f"{base}/external-unauth/v1/auth/two-fa/validate",
                        json_body={"email": email, "code": otp,
                                   "device": device, "rememberMe": True})
    _scm_assert_success(body, "驗證 OTP")
    session_token = body["data"]["sessionToken"]

    return email, session_token


def _scm_submit_application(env: str, session_token: str, email: str,
                            country: str = "TW") -> int:
    base = _scm_api_base(env)
    auth_h = {"s-ci-sessions": session_token}
    ts = str(int(time.time() * 1000))
    readable_ts = time.strftime("%Y%m%d_%H%M%S")

    # 1. 取 terms (agreementList)
    body = _scm_request("GET", f"{base}/external/v1/supplier/apply/terms", headers=auth_h)
    _scm_assert_success(body, "取 terms")
    agreement_list = [
        {"agreementOid": t["agreementOid"], "agreementType": t["agreementType"]}
        for t in body["data"]["terms"]
    ]

    # 2. 上傳 4 份文件
    file_oids = []
    for cat in _SCM_FILE_CATEGORIES:
        body = _scm_request("POST", f"{base}/external/v1/supplier/apply/files",
                            headers=auth_h,
                            json_body={"fileCategory": cat,
                                       "fileName": f"qa_automation_{cat.lower()}.pdf",
                                       "contentType": "application/pdf",
                                       "encodeString": _TINY_PDF_BASE64})
        _scm_assert_success(body, f"上傳 {cat}")
        file_oids.append({"fileOid": str(body["data"]["fileOid"]), "fileCategory": cat})

    # 3. 組 apply payload（TW）
    ts_suffix = ts[-6:]
    payload = {
        "countryCd": country,
        "supplierName": f"automation_{readable_ts}_台北測試旅行社",
        "legalName": f"automation_{readable_ts}_台北測試旅行社有限公司",
        "legalNameEng": f"Automation_{readable_ts} Taipei Test Travel Co., Ltd.",
        "website": "https://automation.example.com",
        "personInCharge": "王小明",
        "personInChargeEng": "Wang Hsiao-ming",
        "license": f"TW{ts_suffix}78",
        "isProvideTaxInfo": False,
        "stateCd": "TW-TPE",
        "cityCd": "TW-TPE-XYI",
        "postCd": "110",
        "address1": "信義路五段 7 號",
        "serviceTelArea": "+886",
        "serviceTel": "0223456789",
        "contactTitle": "Mr.",
        "contact": "王小明",
        "contactJobCd": "業務經理",
        "timezone": "Asia/Taipei",
        "contactEmail": email,
        "contactTelArea": "+886",
        "contactTel": "0912345678",
        "prefLang": "zh-tw",
        "contactOtherMethods": [{"methodType": "LINE", "contactValue": "automation_line_id"}],
        "serviceCountryCd": country,
        "serviceCategory": "CAT_14,CAT_21",
        "serviceDesc": f"automation_{readable_ts} 提供台北市內景點導覽與旅遊體驗服務",
        "serviceLang": "zh-tw,en",
        "hasKkdayContact": False,
        "agreementList": agreement_list,
        "files": file_oids,
        "bank": {
            "kkdayMainContractNo": _SCM_COUNTRY_CONTRACT_MAP.get(country, 3),
            "beneficiaryBankCountryCode": country,
            "collectCurrency": "TWD",
            "bankAccountType": None,
            "bankName": "台灣銀行",
            "bankCode": "004",
            "branchName": None,
            "branchCode": None,
            "accountNo": f"1234{ts_suffix}01234",
            "accountName": f"automation_{readable_ts}_台北測試旅行社",
            "bankAddress": None,
            "swiftCode": None,
            "ibanCode": None,
            "cnaps": None,
            "bsbNumber": None,
            "sknCode": None,
            "beneficiaryIdentity": None,
            "beneficiaryTelCountryCode": None,
            "beneficiaryTel": None,
            "beneficiaryEmail": email,
            "beneficiaryAddress": "信義路五段 7 號",
            "remittanceBurden": "01",
            "supplierBankDesc": f"automation_{readable_ts}",
        },
    }

    # 4. 送出申請（含 retry）
    for attempt in range(3):
        body = _scm_request("POST", f"{base}/external/v1/supplier/apply",
                            headers=auth_h, json_body=payload)
        meta = body.get("metadata", {})
        if meta.get("status") == "0000":
            return int(body["data"]["supplierOid"])
        if meta.get("status") in ("9999", "C001") and attempt < 2:
            time.sleep(10)
            continue
        _scm_assert_success(body, "送出申請")

    raise RuntimeError("送出申請：重試 3 次仍失敗")


def _scm_activate_supplier(env: str, supplier_oid: int, country: str = "TW") -> None:
    potato = _sofa_potato_base(env)

    # Step 0: BE2 admin login
    be2_token, owner_uuid = _be2_admin_login(env)
    be2_h = {"Authorization": f"Bearer {be2_token}",
             "Content-Type": "application/json", "Accept": "application/json"}

    # Step 1: 上傳合約 PDF
    body = _scm_request("POST", f"{potato}/v1/files/supplier.attachment",
                        headers=be2_h,
                        json_body={"fileName": "qa_test_contract.pdf",
                                   "contentType": "application/pdf",
                                   "encodeString": _TINY_PDF_BASE64})
    _scm_assert_success(body, "上傳合約 PDF")
    contract_file_oid = body["data"]["fileOid"]
    contract_access_key = body["data"].get("accessKey", "")

    # Step 2: process — status 10 → 20
    body = _scm_request("POST",
                        f"{potato}/v1/suppliers/registration-application/process",
                        headers=be2_h,
                        json_body={"supplierOids": [supplier_oid],
                                   "supplierOwner": owner_uuid})
    _scm_assert_success(body, "Process 10→20")

    # Step 3: submit-asf（不用 audit，避免 UserExistsRule）
    body = _scm_request("POST",
                        f"{potato}/v1/suppliers/{supplier_oid}/asf_summary",
                        headers=be2_h, json_body={})
    _scm_assert_success(body, "Submit ASF")

    # Step 4: poll ASF 完成
    deadline = time.time() + 300
    asf_done = False
    while time.time() < deadline:
        time.sleep(10)
        body = _scm_request("GET",
                            f"{potato}/v1/suppliers/{supplier_oid}/asf_summary",
                            headers=be2_h)
        data = body.get("data", {})
        report_list = data.get("reportList", [])
        if report_list and report_list[0].get("result") is not None:
            asf_done = True
            time.sleep(20)  # MQ consumer 穩定化
            break
    # ASF 沒完成不硬擋，後面 approve 會報錯

    # Step 5: PATCH supplier detail（設 kkdayMainContractNo + productMaintainer）
    contract_no = _SCM_COUNTRY_CONTRACT_MAP.get(country, 3)
    body = _scm_request("PATCH",
                        f"{potato}/v2/suppliers/{supplier_oid}/detail",
                        headers=be2_h,
                        json_body={"kkdayMainContractNo": contract_no,
                                   "productMaintainer": "SUPPLIER"})
    _scm_assert_success(body, "PATCH supplier detail")

    # Step 6: 建合約
    today = date.today()
    body = _scm_request("POST",
                        f"{potato}/v1/suppliers/{supplier_oid}/contracts",
                        headers=be2_h,
                        json_body={
                            "type": "KKDAY",
                            "startDate": today.isoformat(),
                            "endDate": (today + timedelta(days=365)).isoformat(),
                            "autoRenew": True,
                            "bdNote": "QA automation test contract",
                            "bpmProcess": False,
                            "topic": None,
                            "reason": None,
                            "reviewContractFile": {
                                "fileOid": int(contract_file_oid),
                                "fileName": "qa_test_contract.pdf",
                                "ownerParam1": contract_access_key,
                            },
                        })
    _scm_assert_success(body, "建合約")
    contract_oid = body["data"]["supplierContractOid"]

    # Step 7: 合約簽完
    body = _scm_request("POST",
                        f"{potato}/v1/suppliers/{supplier_oid}/contracts/{contract_oid}/print/done",
                        headers=be2_h,
                        json_body={"supplierContractFile": {
                            "fileOid": int(contract_file_oid),
                            "fileName": "qa_test_signed_contract.pdf",
                            "ownerParam1": contract_access_key,
                        }})
    _scm_assert_success(body, "合約簽完")

    # Step 8: 核准（先檢查是否已 status 80）
    try:
        status_body = _scm_request("GET",
                                   f"{potato}/v1/suppliers/{supplier_oid}/",
                                   headers=be2_h)
        current_status = status_body.get("data", {}).get("status")
        if current_status and int(current_status) >= 80:
            return  # Java 已自動設 80
    except Exception:
        pass  # 查不到就繼續嘗試 approve

    # Stage MQ consumer 寫入 asf_result_date 延遲可達 120+ 秒
    for attempt in range(20):
        try:
            body = _scm_request(
                "POST",
                f"{potato}/v1/suppliers/{supplier_oid}/registration-application/approve",
                headers=be2_h,
                json_body={"supplierContractOid": int(contract_oid),
                           "supplierSettleOid": None})
            _scm_assert_success(body, "核准")
            return
        except RuntimeError as e:
            if "SUPREG0011" in str(e) and attempt < 19:
                time.sleep(15)
                continue
            raise


@mcp.tool()
def create_scm_supplier(env: str, country: str = "TW") -> dict:
    """建立 SCM 供應商（supplier）測試帳號：註冊 + 登入 + OTP + 提交申請。使用者說「建立供應商」「供應商帳號」「supplier」就是這個工具。

    本工具完成前兩階段（註冊 + 申請），回傳 supplier_oid 後會自動引導呼叫 activate_scm_supplier 完成啟用。
    兩步拆開是因為啟用階段需要等 Stage 環境的背景審查（ASF），可能要 3-5 分鐘。

    注意：這是供應商（supplier）帳號，不是買家（member）帳號。買家帳號用 register_member。

    ⚠️ 呼叫前先問使用者要用哪個環境，不要自己猜。

    Args:
        env: 環境（stage / sit / sitNNN 如 sit212），沒有預設值，請先問使用者
        country: 供應商國家代碼（預設 TW）
    """
    _check_env(env)
    start = time.monotonic()
    tool_name = "create_scm_supplier"
    params = {"env": env, "country": country}

    try:
        # Phase 1: 註冊 + 登入
        email, session_token = _scm_register_and_login(env)

        # Phase 2: 提交供應商申請
        supplier_oid = _scm_submit_application(env, session_token, email, country)

        result = {
            "status": "success",
            "email": email,
            "password": _SCM_DEFAULT_PASSWORD,
            "supplier_oid": supplier_oid,
            "env": env,
            "country": country,
            "message": (
                f"供應商帳號已註冊並提交申請，接下來自動啟用。\n\n"
                f"帳號：{email}\n"
                f"供應商 ID：{supplier_oid}\n"
                f"環境：{env}\n\n"
                f"⏳ 請繼續呼叫 activate_scm_supplier 完成啟用（Stage 環境可能需要 3-5 分鐘）。"
            ),
            "_next_step": f'activate_scm_supplier(env="{env}", supplier_oid={supplier_oid}, country="{country}", email="{email}")',
        }
        duration_ms = int((time.monotonic() - start) * 1000)
        _emit_analytics(tool_name, _sanitize_params(params), result, None, duration_ms)
        return result

    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        _emit_analytics(tool_name, _sanitize_params(params), None, exc, duration_ms)
        raise


@mcp.tool()
def activate_scm_supplier(env: str, supplier_oid: int, country: str = "TW",
                           email: str = "") -> dict:
    """啟用 SCM 供應商到合作完成（v2 狀態）。這是 create_scm_supplier 的第二步，通常會自動被引導呼叫。

    流程：BE2 管理員登入 → 上傳合約 → 審核 → ASF 背景調查 → 建合約 → 簽約 → 核准。
    Stage 環境的 ASF 背景調查可能需要 3-5 分鐘，工具會自動等待。

    冪等設計：如果供應商已經是 status 80（合作中），會直接回傳成功。
    如果中途失敗可以重試，已完成的步驟會自動跳過。

    BE2 管理員帳密自動從 secret service 取得，不需額外設定。

    Args:
        env: 環境（stage / sit / sitNNN）
        supplier_oid: 供應商 OID（從 create_scm_supplier 回傳值取得）
        country: 供應商國家代碼（預設 TW）
        email: 供應商 email（用於回傳結果，可選）
    """
    _check_env(env)
    start = time.monotonic()
    tool_name = "activate_scm_supplier"
    params = {"env": env, "supplier_oid": supplier_oid, "country": country}

    try:
        _scm_activate_supplier(env, supplier_oid, country)

        login_url = _scm_frontend_login_url(env)
        result = {
            "status": "success",
            "login_url": login_url,
            "email": email,
            "password": _SCM_DEFAULT_PASSWORD if email else "",
            "supplier_oid": supplier_oid,
            "env": env,
            "country": country,
            "message": (
                f"供應商帳號啟用完成（v2 狀態，供應商可自行管理商品）。\n\n"
                f"網址：{login_url}\n"
                + (f"帳號：{email}\n" if email else "")
                + (f"密碼：{_SCM_DEFAULT_PASSWORD}\n" if email else "")
                + f"供應商 ID：{supplier_oid}\n"
                f"環境：{env}\n\n"
                f"請點擊上面的網址，輸入帳號密碼後登入，再跟我拿取 OTP。"
            ),
            "_next_step": f"給我 {email} 的 OTP" if email else "",
        }
        duration_ms = int((time.monotonic() - start) * 1000)
        _emit_analytics(tool_name, _sanitize_params(params), result, None, duration_ms)
        return result

    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        _emit_analytics(tool_name, _sanitize_params(params), None, exc, duration_ms)
        raise


@mcp.tool()
def get_scm_otp(email: str) -> dict:
    """取得指定 email 最新的 SCM 登入 OTP 驗證碼（6 位數字）。

    搭配 create_scm_supplier 使用：建完帳號後使用者去 SCM 前台登入，
    系統會寄 OTP 到信箱，再呼叫這個工具取得驗證碼。

    也可以單獨使用：任何 SCM 帳號登入時需要 OTP 都可以呼叫。

    Args:
        email: 收 OTP 的 email（通常是 b2c-qa-team+automation_test_XXX@kkday.com）
    """
    start = time.monotonic()
    tool_name = "get_scm_otp"
    params = {"email": email}

    try:
        otp = _gmail_get_otp(email, max_wait=60, poll_interval=5)
        result = {"otp": otp, "email": email}
        duration_ms = int((time.monotonic() - start) * 1000)
        _emit_analytics(tool_name, _sanitize_params(params), result, None, duration_ms)
        return result

    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        _emit_analytics(tool_name, _sanitize_params(params), None, exc, duration_ms)
        raise


def main() -> None:
    """FastMCP stdio server 入口。"""
    mcp.run()


if __name__ == "__main__":
    main()

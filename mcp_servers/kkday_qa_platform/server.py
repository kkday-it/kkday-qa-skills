"""KKday QA Test Platform Tools MCP Server.

把 QA Test Platform（FastAPI，`QATest/src/lib/web`）**web 上有開放的** test data tool
包成 MCP tools，讓 LLM（Claude Code / Claude Desktop / Cursor 等）用自然語言呼叫，例如：
「幫我在 sit206 用 voucher 換商品 9468 的 459106 套餐」→ 自動呼叫 redeem_voucher。

底層一律 proxy 到平台的 `POST /testtool/run`（同步）或 `/testtool/run_async` + `/testtool/progress`
（長時間 tool 走非同步 + 進程內輪詢）。所有業務邏輯留在平台端，本 server 只做「翻譯 schema →
打 API → 回傳」。

只開放 qatest-web `toolMapping.ts` 有列的 6 個 feature；平台上的個人工具
（scan_product_config / kibana_discover / explain_root_cause / account_pool …）一律不包。

執行方式（stdio 模式）：
    python server.py

Env vars:
    KKDAY_QA_PLATFORM_BASE: 平台 API base（預設 SIT，含 /api/v1）
"""

import os
import re
import threading
import time
import uuid
from typing import Optional

import requests
from fastmcp import FastMCP

BASE = os.getenv(
    "KKDAY_QA_PLATFORM_BASE",
    "http://autotest-service.sit.kkday.com:8080/api/v1",
).rstrip("/")

# 平台端可據此把「MCP 觸發的 run」跟 QA 平台 UI / CI 分流（見 tool 失敗 Slack alarm）。
TRIGGER_POINT = "MCP"

# 同步呼叫 (連線, 讀取) 逾時秒數。
_TIMEOUT = (10, 300)
# 非同步輪詢：每 3 秒問一次進度，最多等 10 分鐘。
_POLL_INTERVAL = 3
_POLL_MAX_WAIT = 600
_POLL_TIMEOUT = (10, 30)

mcp = FastMCP("kkday-qa-platform")


def _headers() -> dict:
    """平台 /testtool/* 端點無 auth；只需 content-type。"""
    return {"Content-Type": "application/json"}


# ── 環境驗證 ──────────────────────────────────────────────────────────────
# 合法環境：stage 或 sit 系列（sit / sit0x / sit20x，如 sit04 / sit206）。
# 擋掉 LLM 亂編的 prod / staging / uat / beta 等。
_VALID_ENV_RE = re.compile(r"stage|sit\d*")


def _check_env(env: str) -> None:
    """驗證單一環境字串；空值或非 stage / sit 系列一律擋下。"""
    if env is None or env == "":
        raise ValueError(
            "未指定環境：不可自行預設，請先向使用者確認要用 stage 還是 sit"
            "（選 sit 需再追問是哪一台）。"
        )
    if not isinstance(env, str) or not _VALID_ENV_RE.fullmatch(env):
        raise ValueError(
            f"env '{env}' 不是合法環境；只接受 'stage' 或 'sit' 系列"
            f"（sit / sit0x / sit20x，如 sit04 / sit206）。請向使用者確認要用哪個環境。"
        )


def _validate_envs(environment: str, tool_kwargs: dict) -> None:
    """驗證 payload 的 environment（目標）與 tool_kwargs.source_environment（來源，若有）。"""
    _check_env(environment)
    source_env = (tool_kwargs or {}).get("source_environment")
    if source_env is not None:
        _check_env(source_env)


# ── 失敗診斷（向 qatest-web 對齊：透過映射表講「為什麼 / 錯在哪」+ 交棒 prod_oid）──
# ERROR_MAP 同步自 qatest-web/src/config/errorMap.ts —— 改那邊記得同步這裡。
# 用 tool 回傳的 message 對應到友善分類標題/描述。
ERROR_MAP = {
    "product_check_fail": {
        "title": "錯誤的商品",
        "desc": "請檢查您輸入的參數，該商品不存在或編號錯誤。",
    },
    "package_check_fail": {
        "title": "錯誤的套餐",
        "desc": "請檢查您輸入的參數，該套餐不存在於該商品或編號錯誤。",
    },
    "product_not_active": {
        "title": "商品尚未上架",
        "desc": "此商品存在但尚未上架，請於商品後台確認上架狀態後再試。",
    },
    "no_sellable_package": {
        "title": "沒有可下單的套餐",
        "desc": "此商品沒有可下單的套餐，請確認是否全部售罄、已下架或超過銷售期。",
    },
    "create_redeem_fail": {"title": "兌換券建立失敗", "desc": "創建兌換券API失效。"},
    "extend_item_calendar_fail": {
        "title": "延長銷售日曆失敗",
        "desc": "請確認商品或套餐設定正確。",
    },
    "create_product_fail": {"title": "商品創建失敗", "desc": ""},
}


def _get_be2_base_url(env: str) -> Optional[str]:
    """env → BE2 base URL（同步自 qatest-web/src/lib/utils.ts getBe2BaseUrl）。"""
    if env == "stage":
        return "https://be2.stage.kkday.com"
    if env == "sit":
        return "https://be2.sit.kkday.com"
    if env.startswith("sit") and len(env) > 3:
        return f"https://be2-{env[3:]}.sit.kkday.com"
    return None


def _build_be2_edit_url(env: str, prod_oid) -> Optional[str]:
    """組 BE2 商品編輯頁 URL，讓 user 手動調整（環境差異失敗很常見）。"""
    base = _get_be2_base_url(env)
    return f"{base}/v2/product/{prod_oid}/edit-product-detail" if base else None


def _is_failure(result: dict) -> bool:
    """判斷 tool 回傳是否為失敗（涵蓋 copy / guard_api_step / preview / async 包裝）。"""
    if not isinstance(result, dict):
        return False
    if str(result.get("copy_status", "")).lower() == "fail":
        return True
    if str(result.get("publish_status", "")).lower() == "fail":
        return True
    if result.get("status") in ("error", "failed", "timeout"):
        return True
    return False


def _extract_why(result: dict) -> list:
    """從失敗回傳蒐集「為什麼 / 錯在哪」的 raw detail（不吞資訊，去重保序）。"""
    whys = []
    errs = result.get("errors")
    if isinstance(errs, list):
        for e in errs:
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


def _format_failure(result: dict, env: str) -> dict:
    """tool 失敗時，比照 web 加上 _diagnosis：分類標題/描述、raw 原因、已建 prod_oid + BE2 編輯頁 URL。

    成功回傳原樣返回，不加料。失敗只「附加」_diagnosis，不改動原始欄位（不吞資訊）。
    """
    if not _is_failure(result):
        return result

    diag: dict = {}
    cfg = ERROR_MAP.get(result.get("message"))
    if cfg:
        diag["title"] = cfg["title"]
        if cfg["desc"]:
            diag["desc"] = cfg["desc"]

    why = _extract_why(result)
    if why:
        diag["why"] = why

    prod_oid = (result.get("target") or {}).get("prod_oid") or result.get("prod_oid")
    if prod_oid:
        diag["created_prod_oid"] = prod_oid
        url = _build_be2_edit_url(env, prod_oid)
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


# ── 平台呼叫 ──────────────────────────────────────────────────────────────


def _run_sync(tool_name: str, environment: str, tool_kwargs: dict) -> dict:
    """同步呼叫平台 /testtool/run，回傳 tool 執行結果（已拆 {message,data} 外殼）。

    注意：業務失敗（例 publish_status=Fail）平台仍回 HTTP 200，失敗資訊在回傳 dict 內；
    只有 HTTP 非 2xx 才 raise。呼叫端 / LLM 需自行判讀回傳的 status / publish_status。
    """
    _validate_envs(environment, tool_kwargs)
    payload = {
        "environment": environment,
        "tool_name": tool_name,
        "tool_kwargs": tool_kwargs,
        "trigger_point": TRIGGER_POINT,
    }
    resp = requests.post(
        f"{BASE}/testtool/run", headers=_headers(), json=payload, timeout=_TIMEOUT
    )
    if not resp.ok:
        raise RuntimeError(
            f"POST /testtool/run ({tool_name}) → {resp.status_code}: {resp.text[:500]}"
        )
    return _format_failure(resp.json().get("data", {}), environment)


def _run_async(tool_name: str, environment: str, tool_kwargs: dict) -> dict:
    """非同步呼叫平台 /testtool/run_async，於進程內輪詢 /testtool/progress 直到完成。

    輪詢在本進程內做（LLM 只看到一次 tool call、不多花 token），避免長時間 tool 撞 read timeout。
    逾時（>10 分）不代表失敗——平台可能仍在跑，回傳提示改用 *_history / 商品頁查證。
    """
    _validate_envs(environment, tool_kwargs)
    payload = {
        "environment": environment,
        "tool_name": tool_name,
        "tool_kwargs": tool_kwargs,
        "trigger_point": TRIGGER_POINT,
    }
    resp = requests.post(
        f"{BASE}/testtool/run_async",
        headers=_headers(),
        json=payload,
        timeout=_POLL_TIMEOUT,
    )
    if not resp.ok:
        raise RuntimeError(
            f"POST /testtool/run_async ({tool_name}) → {resp.status_code}: {resp.text[:500]}"
        )
    task_id = (resp.json().get("data") or {}).get("task_id")
    if not task_id:
        raise RuntimeError(f"run_async ({tool_name}) 未回傳 task_id")

    waited = 0
    while waited < _POLL_MAX_WAIT:
        time.sleep(_POLL_INTERVAL)
        waited += _POLL_INTERVAL
        pr = requests.get(
            f"{BASE}/testtool/progress/{task_id}",
            headers=_headers(),
            timeout=_POLL_TIMEOUT,
        )
        if pr.status_code == 404:
            # task 可能剛清掉或還沒註冊，續等
            continue
        if not pr.ok:
            continue
        data = pr.json().get("data") or {}
        status = data.get("status")
        if status in ("completed", "failed"):
            result = data.get("result")
            if status == "failed":
                return _format_failure(
                    {"status": "failed", "detail": result, "task_id": task_id},
                    environment,
                )
            return _format_failure(result, environment)
    return _format_failure(
        {
            "status": "timeout",
            "message": (
                f"後端仍在執行（已等 {_POLL_MAX_WAIT}s），逾時 ≠ 失敗。"
                "請稍後用對應 *_history 或商品頁查證是否已完成。"
            ),
            "task_id": task_id,
        },
        environment,
    )


# ── 說明工具 ──────────────────────────────────────────────────────────────


@mcp.tool()
def help() -> dict:
    """列出這個 MCP server 提供的所有 tool + 用途摘要。
    當 user 問「這個 MCP 有什麼功能」或不確定該用哪個 tool 時，先呼叫這個。
    """
    return {
        "server": "kkday-qa-platform",
        "base_url": BASE,
        "note": "只開放 QA 平台 web 上有的 6 個 feature；個人工具不提供。",
        "tools": {
            "商品": [
                "create_product: 建測試商品（20 種型別，較慢約 3 分鐘）",
                "copy_product_preview: 跨環境複製商品【第一步】預覽 + 供應商檢查",
                "copy_product: 跨環境複製商品【第二步】需帶 preview 給的 confirm_token",
            ],
            "訂單/兌換": [
                "create_order: 建立測試訂單",
                "redeem_voucher: 用 voucher 兌換商品",
            ],
            "月曆": [
                "extend_item_calendar: 延長單一 package 銷售月曆",
                "batch_extend_item_calendar: 批次延長整個商品的月曆（不支援 OCBT 子母單）",
            ],
            "說明": [
                "describe_tool(name): 拿指定 tool 的欄位說明 + 範例",
            ],
        },
    }


@mcp.tool()
def describe_tool(name: str) -> dict:
    """給指定 tool 更詳細的說明 + 範例呼叫。

    Args:
        name: tool 名稱，例如 'copy_product' / 'create_order'
    """
    docs = {
        "create_product": {
            "purpose": "建立測試商品（一併建 package + item），較慢約 3 分鐘",
            "params": {
                "env": "sit / stage（sit 要追問哪一台，如 sit206）",
                "prod_type": f"20 選 1：{', '.join(PROD_TYPE_OPTIONS)}",
            },
            "example": 'create_product(env="sit206", prod_type="normal")',
        },
        "copy_product_preview": {
            "purpose": "跨環境複製商品的【第一步】。預覽來源商品並檢查供應商在目標環境是否存在，回傳 confirm_token",
            "params": {
                "target_env": "要複製到哪個環境（目標）",
                "source_env": "從哪個環境讀來源商品",
                "prod_oid": "來源商品 OID",
            },
            "next": "看 target_suppliers 有哪些 exists=false，與使用者確認 fallback / override，再帶 confirm_token 呼叫 copy_product",
            "example": 'copy_product_preview(target_env="sit206", source_env="sit213", prod_oid=102908)',
        },
        "copy_product": {
            "purpose": "跨環境複製商品的【第二步】，實際執行複製。必須先跑 copy_product_preview 拿 confirm_token",
            "params": {
                "target_env / source_env / prod_oid": "須與 preview 時完全一致",
                "confirm_token": "copy_product_preview 回傳的 token（不可自行編造）",
                "supplier_override_map": "選填。{原供應商oid字串: 改用的oid} — 使用者指定的供應商對應",
                "fallback_supplier_oid": "選填，目標環境找不到對應供應商時的預設（預設 15247）",
            },
            "example": 'copy_product(target_env="sit206", source_env="sit213", prod_oid=102908, confirm_token="...")',
        },
        "create_order": {
            "purpose": "建立測試訂單",
            "params": {
                "env": "sit / stage",
                "product_oid / package_oid": "商品 / 套餐 OID",
                "qty": "每筆訂單數量（預設 1）",
                "order_count": "下單筆數（預設 1）",
                "go_date_shift": "出發日=幾天後（預設 1）",
            },
            "example": 'create_order(env="sit206", product_oid="9468", package_oid="459106")',
        },
        "redeem_voucher": {
            "purpose": "用 voucher 兌換商品訂單",
            "params": {
                "env": "sit / stage",
                "product_oid / package_oid": "商品 / 套餐 OID",
                "qyt": "兌換數量（字串，預設 '1'）",
            },
            "example": 'redeem_voucher(env="sit206", product_oid="9468", package_oid="459106")',
        },
        "extend_item_calendar": {
            "purpose": "延長單一 package 的銷售月曆",
            "params": {
                "env": "sit / stage",
                "product_oid / package_oid": "商品 / 套餐 OID",
                "extend_month": "延長月數 1~12",
            },
            "example": 'extend_item_calendar(env="sit206", product_oid="9468", package_oid="459106", extend_month=3)',
        },
        "batch_extend_item_calendar": {
            "purpose": "批次延長整個商品所有 package 的月曆（⚠️ 不適用 OCBT 子母單商品）",
            "params": {
                "env": "sit / stage",
                "product_oid": "商品 OID",
                "extend_month": "延長月數 1~12",
            },
            "example": 'batch_extend_item_calendar(env="sit206", product_oid="9468", extend_month=3)',
        },
    }
    info = docs.get(name)
    if info is None:
        return {"error": f"沒有名為 '{name}' 的 tool；可用：{', '.join(docs.keys())}"}
    return {"tool": name, **info}


# ── 商品 ──────────────────────────────────────────────────────────────────

# create_product 可用的 20 種商品型別（來源：qatest-web src/config/toolMapping.ts）。
PROD_TYPE_OPTIONS = [
    "normal",
    "manually_process_ticket",
    "khsr",
    "cruise",
    "hotel",
    "open_date",
    "same_price_by_date_has_event",
    "same_price_by_date_no_event",
    "depends_on_price_by_date_has_event",
    "depends_on_price_by_date_no_event",
    "large_event",
    "stamp_duty_hk",
    "ocbt_main_product",
    "ocbt_sub_product",
    "three_level_sku",
    "normal_mo_location",
    "b2d_normal",
    "b2d_channel_disable",
    "bundle_product",
    "compound_calendar",
]


@mcp.tool()
def create_product(env: str, prod_type: str) -> dict:
    """建立測試商品（proxy 到 QA 平台，會一併建 package + item，較慢約 3 分鐘）。

    呼叫前先向使用者確認 env / prod_type。env 只有 sit / stage 兩種；使用者選 sit 時
    **必須**追問是哪一台（sit0x 或 sit20x 系列），不得自行預設或編造環境代號。
    使用者只說「建商品」沒指明種類時，先把 PROD_TYPE_OPTIONS 列給他挑，不要擅自帶 normal。

    Args:
        env: 環境 sit / stage（例：sit206 / stage）
        prod_type: 商品種類，20 選 1（見 describe_tool("create_product")）
    """
    prod_type = (prod_type or "").strip()
    if prod_type not in PROD_TYPE_OPTIONS:
        raise ValueError(
            f"prod_type '{prod_type}' 不是有效值；請把選項列給使用者挑，選定後再帶入。"
            f"可用值：{', '.join(PROD_TYPE_OPTIONS)}"
        )
    return _run_async("product", env, {"prod_type": prod_type})


@mcp.tool()
def copy_product_preview(target_env: str, source_env: str, prod_oid: str) -> dict:
    """跨環境複製商品的【第一步】：預覽來源商品並檢查供應商在目標環境是否存在，回傳 confirm_token。

    這是複製流程的必經第一步。回傳的 target_suppliers 會標出每個來源供應商在目標環境
    exists 與否；請把 exists=false 的供應商列給使用者，確認要用 fallback 還是指定 override，
    然後帶著 confirm_token 呼叫 copy_product 完成複製。

    Args:
        target_env: 要複製到哪個環境（目標）— sit / stage
        source_env: 從哪個環境讀來源商品 — sit / stage
        prod_oid: 來源商品 OID
    """
    result = _run_sync(
        "copy_product_preview",
        target_env,
        {
            "source_environment": source_env,
            "productOid": str(prod_oid),
            "raise_on_fail": False,
        },
    )
    if not isinstance(result, dict) or result.get("status") != "success":
        # 預覽失敗：原樣回，不發 token（沒有有效 preview 就不允許 execute）
        return result

    token = uuid.uuid4().hex
    with _preview_lock:
        _purge_expired_locked()
        _preview_cache[token] = {
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
            "與使用者確認供應商對應：target_suppliers 中 exists=false 的供應商在目標環境不存在，"
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

    ⚠️ 必須先呼叫 copy_product_preview 取得 confirm_token —— 沒有有效 token 會被拒絕。
    這是為了保證供應商對應有經過使用者覆核（否則所有供應商會被靜默替換成 fallback）。
    target_env / source_env / prod_oid 必須與 preview 時完全一致。

    Args:
        target_env: 目標環境（須與 preview 一致）
        source_env: 來源環境（須與 preview 一致）
        prod_oid: 來源商品 OID（須與 preview 一致）
        confirm_token: copy_product_preview 回傳的 token（不可自行編造）
        supplier_override_map: 選填。{原供應商oid字串: 改用的oid} — 使用者指定的替代供應商
        fallback_supplier_oid: 選填。目標環境找不到對應供應商時的預設（預設 15247）
    """
    with _preview_lock:
        _purge_expired_locked()
        entry = _preview_cache.pop(confirm_token, None)  # 單次使用：一進來即消費

    if entry is None:
        return {
            "status": "error",
            "message": (
                "尚未預覽，或 confirm_token 無效 / 已使用 / 已逾時。"
                "請先呼叫 copy_product_preview(target_env, source_env, prod_oid) 取得新的 "
                "confirm_token，與使用者確認供應商對應後，再帶入本工具。"
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

    tool_kwargs = {
        "source_environment": source_env,
        "productOid": str(prod_oid),
        "fallback_supplier_oid": fallback_supplier_oid,
        "supplier_override_map": supplier_override_map or {},
        # 帶回 preview 算出的供應商資料，讓平台端 supplier guard 通過且複製 1:1
        "target_suppliers": entry["target_suppliers"],
        "pkg_supplier_map": entry["pkg_supplier_map"],
    }
    return _run_async("copy_product", target_env, tool_kwargs)


# ── copy_product confirm-token 快取 ─────────────────────────────────────────
# 純 MCP 進程內的 one-time nonce：preview 發、execute 單次消費、TTL 自清、綁定
# (target_env, source_env, prod_oid)。用來硬性保證「execute 前一定先 preview」，
# 不進後端、不影響 web。
_preview_cache: dict = {}
_preview_lock = threading.Lock()
_PREVIEW_TTL = 900.0  # 15 分鐘


def _purge_expired_locked() -> None:
    """清掉過期 token（呼叫前須持有 _preview_lock）。"""
    now = time.monotonic()
    dead = [k for k, v in _preview_cache.items() if now - v["ts"] > _PREVIEW_TTL]
    for k in dead:
        _preview_cache.pop(k, None)


# ── 訂單 / 兌換 ─────────────────────────────────────────────────────────────


@mcp.tool()
def create_order(
    env: str,
    product_oid: str,
    package_oid: str,
    qty: int = 1,
    order_count: int = 1,
    go_date_shift: int = 1,
) -> dict:
    """建立測試訂單（proxy 到 QA 平台）。

    呼叫前先向使用者確認參數。env 只有 sit / stage；選 sit 要追問哪一台，不得自行編造。

    Args:
        env: sit / stage
        product_oid: 商品 OID
        package_oid: 套餐 OID
        qty: 每筆訂單數量（預設 1）
        order_count: 下單筆數（預設 1）
        go_date_shift: 出發日為幾天後（預設 1）
    """
    return _run_async(
        "create_order",
        env,
        {
            "productOid": str(product_oid),
            "packageOid": str(package_oid),
            "qty": qty,
            "order_count": order_count,
            "go_date_shift": go_date_shift,
        },
    )


@mcp.tool()
def redeem_voucher(
    env: str, product_oid: str, package_oid: str, qyt: str = "1"
) -> dict:
    """用 voucher（優惠券）兌換商品訂單（proxy 到 QA 平台）。

    呼叫前先向使用者確認參數。env 只有 sit / stage；選 sit 要追問哪一台，不得自行編造。

    Args:
        env: sit / stage
        product_oid: 商品 OID
        package_oid: 套餐 OID
        qyt: 兌換數量（字串型別，預設 '1'）
    """
    return _run_sync(
        "redeem",
        env,
        {
            "productOid": str(product_oid),
            "packageOid": str(package_oid),
            "qyt": str(qyt),
        },
    )


# ── 月曆 ────────────────────────────────────────────────────────────────────


@mcp.tool()
def extend_item_calendar(
    env: str, product_oid: str, package_oid: str, extend_month: int
) -> dict:
    """延長單一 package 的銷售月曆（proxy 到 QA 平台）。

    Args:
        env: sit / stage
        product_oid: 商品 OID
        package_oid: 套餐 OID
        extend_month: 延長月數 1~12
    """
    return _run_sync(
        "extend_item_calendar",
        env,
        {
            "productOid": str(product_oid),
            "packageOid": str(package_oid),
            "extend_month": extend_month,
        },
    )


@mcp.tool()
def batch_extend_item_calendar(
    env: str, product_oid: str, extend_month: int
) -> dict:
    """批次延長整個商品所有 package 的月曆（proxy 到 QA 平台）。

    ⚠️ 此功能不適用 OCBT 子母單商品。

    Args:
        env: sit / stage
        product_oid: 商品 OID
        extend_month: 延長月數 1~12
    """
    return _run_async(
        "batch_extend_item_calendar",
        env,
        {
            "productOid": str(product_oid),
            "extend_month": extend_month,
        },
    )


def main() -> None:
    """FastMCP stdio server 入口。"""
    mcp.run()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Locator Registry 取回端（GET，非侵入、與 kkday-qa-tools MCP 無關）

給定 page/component + platform + env，從 ai_studio GET 已知的 locator 候選、業務語意 note、
以及該區域的驗證方法論，當作 skill 執行一個 case「前」的起手 hints。

**設計重點（防腐關鍵）：拿回來的東西一律是「候選 + 上次驗證時間」，不是真理。**
skill 流程必須先 cheap-verify 每個候選 selector（在當前 DOM 存不存在），驗過才用、
驗不過標 stale 並回退到「從零挖」的原本流程。這支腳本只負責「把候選撈回來」，不做驗證判斷。

fail-safe 原則（比照 send_case_fidelity.py）：
- 後端不可達 / 查無資料 / 任何錯誤 → 回空結果、靜默、exit 0，不擋主流程。
  拿不到就當第一次挖，skill 照原本「從零挖」流程跑。
- retry 少量次數（GET 冪等），全失敗放棄。
- 不接原本的 MCP、不觸發權限提示、不印雜訊（除非 tty 手動執行）。

**相關元素群一起取回**：同一「流程/區域」的元素（例：搜尋流程 = landing 搜尋框 + 送出鈕
+ 結果頁 header keyword + active tab）用 `--flow` 一次批次拿回，省多次往返；也可用 `--page`
取回整頁的元素。它們常一起改、一起用，一次 GET 一起驗。

**先探索再取回**：主端點只吃 `flow` / `page` 這種精確 key，沒有自由文字搜尋——不知道別人
把那條流程記成什麼字串時，復用就只能靠猜（這是「明明有記錄卻還是各寫一套」的實際原因之一）。
`--list-flows [--q 關鍵字]` 走 `/events` 分頁撈回、在本機做子字串比對，先列出**真的存在的
flow key**（含筆數 / 頁面 / 來源 case / 最後驗證日），再拿 key 去主端點取候選。

用法（stdout 印出 JSON，供主對話讀成起手 hints）：
    # 0) 先探索：這個平台有哪些 flow key？（不知道 key 時的唯一正解，別猜）
    python3 fetch_locator_registry.py --case KQT-T7172 --platform ios --list-flows --q 登入
    # 1) 用 flow 批次取回整條搜尋流程的候選（建議：一個 case 一次 GET）
    python3 fetch_locator_registry.py --case KQT-T500 --flow things-to-do-search --platform web --env stage
    # 或用 page 取回整頁
    python3 fetch_locator_registry.py --page things-to-do-landing \\
        --platform web --env stage [--component landing-search-bar-input] \\
        [--outfile /path/to/hints.json]

`--case` 會寫一列**讀取收據**（`registry_read_receipt`）供 Stop 的讀取硬 gate 比對。
交付 case 前的讀取一律要帶 `--case`，否則 gate 對不上、會擋下結束。

輸出 JSON 結構（查無 / 失敗時 entries 為空陣列）：
    {
      "ok": true/false,
      "query": {...},
      "entries": [ { selector, selector_type, page, component, platform, env,
                     source, last_verified, status, candidates, note }, ... ],
      "methodology": [ "<該區域驗證方法論字串>", ... ]
    }
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

# 現階段安全紅線：環境只接受 stage / sit0x / sit20x（比照 server _VALID_ENV_RE），禁 prod。
_VALID_ENV_RE = re.compile(r"stage|sit\d*")

BASE = os.getenv("AI_STUDIO_BASE", "http://autotest-service.sit.kkday.com:8081/ai_studio")
PATH = "/api/qa-automation/locator-registry"
EVENTS_PATH = PATH + "/events"
MAX_RETRIES = 3
BASE_BACKOFF = 0.4
# 探索模式的掃描上限：/events 分頁撈回來只為了在本機比對關鍵字，不需要無上限。
EVENTS_PAGE_SIZE = 200
EVENTS_MAX_SCAN = 3000

# 讀取收據（供 Stop 的讀取硬 gate 比對）。拿不到就當沒有——寫收據永遠不影響讀取結果。
try:
    import registry_read_receipt
except Exception:
    registry_read_receipt = None


def _empty(query: dict) -> dict:
    return {"ok": False, "query": query, "entries": [], "methodology": []}


def _get_json(path: str, query: dict, timeout: float = 4.0):
    """GET 一次；2xx + 可解析 JSON 才回 dict/list，其餘回 None。"""
    try:
        qs = urllib.parse.urlencode({k: v for k, v in query.items() if v not in ("", None)})
        req = urllib.request.Request(f"{BASE}{path}?{qs}", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = getattr(r, "status", r.getcode())
            if not (200 <= status < 300):
                return None
            body = r.read().decode("utf-8")
            return json.loads(body)
    except Exception:
        return None


def _get_once(query: dict, timeout: float = 4.0):
    return _get_json(PATH, query, timeout)


def _fetch_with_retry(query: dict):
    for attempt in range(1, MAX_RETRIES + 1):
        got = _get_once(query)
        if got is not None:
            return got
        if attempt < MAX_RETRIES:
            time.sleep(attempt * BASE_BACKOFF)
    return None


def _looks_like_entry(x) -> bool:
    """只認『像 locator entry 的 dict』，擋掉後端錯誤 body（如 [{"error":..},404]）冒充 hints。"""
    if not isinstance(x, dict):
        return False
    if "error" in x and "selectors" not in x:
        return False
    # 至少要有候選 selector 或單一 selector 值，才算可用的 hint
    return bool(x.get("selectors") or x.get("selector") or x.get("value"))


def _shape(raw, query: dict) -> dict:
    """把後端回應整形成穩定結構；任何異常/垃圾回應都退回空結果。"""
    try:
        entries = raw.get("entries") if isinstance(raw, dict) else None
        if entries is None and isinstance(raw, list):
            entries = raw
        entries = [e for e in (entries or []) if _looks_like_entry(e)]
        methodology = raw.get("methodology", []) if isinstance(raw, dict) else []
        if not isinstance(methodology, list):
            methodology = []
        # 過濾後沒有任何合法 entry 就當拿不到（回退從零挖）
        return {"ok": bool(entries), "query": query, "entries": entries, "methodology": methodology}
    except Exception:
        return _empty(query)


_MATCH_KEYS = ("flow", "page", "component", "element", "semantic", "id", "source")


def _fetch_events(platform: str) -> list:
    """分頁撈 /events（回的是 entry 清單，不是稽核事件）。任何錯誤→回目前撈到的，fail-safe。"""
    items: list = []
    offset = 0
    while len(items) < EVENTS_MAX_SCAN:
        data = None
        for attempt in range(1, MAX_RETRIES + 1):
            data = _get_json(EVENTS_PATH,
                             {"platform": platform, "limit": EVENTS_PAGE_SIZE, "offset": offset},
                             timeout=8.0)
            if data is not None:
                break
            if attempt < MAX_RETRIES:
                time.sleep(attempt * BASE_BACKOFF)
        if not isinstance(data, dict):
            break
        batch = [x for x in (data.get("items") or []) if isinstance(x, dict)]
        items.extend(batch)
        total = data.get("total") or 0
        offset += EVENTS_PAGE_SIZE
        if len(batch) < EVENTS_PAGE_SIZE or offset >= total:
            break
    return items


def _matches(item: dict, ql: str) -> bool:
    if not ql:
        return True
    blob = " ".join(str(item.get(k) or "") for k in _MATCH_KEYS).lower()
    return ql in blob


def _group_flows(items: list, limit: int, samples: int = 4) -> list:
    """把 entry 依 flow key 聚合成「可復用的入口清單」：筆數 / 頁面 / 來源 case / 最後驗證日。
    排序用「最後驗證日 → 筆數」：越新、越多人記的越可能是主幹。"""
    groups: dict = {}
    for it in items:
        key = str(it.get("flow") or "").strip() or "(no-flow)"
        g = groups.setdefault(key, {"flow": key, "n": 0, "pages": set(), "cases": set(),
                                    "last_verified": "", "samples": []})
        g["n"] += 1
        if it.get("page"):
            g["pages"].add(str(it["page"]))
        if it.get("source"):
            g["cases"].add(str(it["source"]))
        lv = str(it.get("last_verified") or "")[:10]
        if lv > g["last_verified"]:
            g["last_verified"] = lv
        if len(g["samples"]) < samples:
            g["samples"].append(it.get("id") or it.get("component") or it.get("element") or "")
    out = sorted(groups.values(), key=lambda g: (g["last_verified"], g["n"]), reverse=True)
    if limit and limit > 0:
        out = out[:limit]
    for g in out:
        g["pages"] = sorted(g["pages"])[:6]
        g["cases"] = sorted(g["cases"])[:6]
    return out


def _receipt(kind: str, args, n: int, endpoint: str, query: dict) -> None:
    if registry_read_receipt is None or not getattr(args, "case", ""):
        return
    registry_read_receipt.write(kind=kind, case=args.case, platform=args.platform,
                                query=query, n=n, endpoint=endpoint)


def main() -> int:
    p = argparse.ArgumentParser(description="Fetch locator-registry hints (fail-safe)")
    p.add_argument("--flow", default="", help="流程/區域 key，如 things-to-do-search（批次取整組）")
    p.add_argument("--page", default="", help="頁面語意 key，如 things-to-do-landing")
    p.add_argument("--component", default="", help="元件語意 key（可選，縮小範圍）")
    # web/mweb + app（android/ios）。app 沒有可導航 URL、不走 locator_valve valve，
    # fetch 是 app 取 hints 的 sanctioned GET 路徑（驗證交給測試本身）。
    p.add_argument("--platform", default="web", choices=["web", "mweb", "android", "ios"])
    p.add_argument("--env", default="stage", help="stage / sit0x / sit20x（現階段禁 prod）")
    p.add_argument("--outfile", default="", help="同時寫入的 JSON 檔路徑（可選）")
    p.add_argument("--case", default="",
                   help="當前正在做的 case id（如 KQT-T7172）。會寫一列讀取收據供 Stop 的讀取硬 gate "
                        "比對；交付 case 前的讀取一律要帶，否則 gate 對不上會擋下結束。")
    p.add_argument("--q", default="",
                   help="自由文字關鍵字（本機比對 flow/page/component/element/semantic/id/source）。"
                        "主端點沒有自由搜尋，這個是走 /events 在本機比對。單獨給 --q 即進探索模式。")
    p.add_argument("--list-flows", action="store_true",
                   help="探索模式：列出該平台真的存在的 flow key（不知道 key 時的正解，別猜字串）")
    p.add_argument("--limit", type=int, default=30, help="探索模式回傳的 flow group 上限（預設 30）")
    args = p.parse_args()

    discovery = bool(args.list_flows or (args.q and not (args.flow or args.page)))
    if not discovery and not (args.flow or args.page):
        p.error("需至少提供 --flow 或 --page 其一；不知道 key 就先用 --list-flows [--q 關鍵字] 探索")

    if args.env and not _VALID_ENV_RE.fullmatch(args.env):
        p.error(f"env '{args.env}' 非法或為 prod；現階段只接受 stage / sit0x / sit20x")

    if discovery:
        dq = {"mode": "list-flows", "platform": args.platform, "q": args.q}
        try:
            items = _fetch_events(args.platform)
            ql = args.q.strip().lower()
            hit = [it for it in items if _matches(it, ql)]
            note = ""
            # 關鍵字沒命中不等於「沒人記過」：flow key 多是英文（app-naver-login），
            # 但 element 敘述多是中文，中文關鍵字常打不中。這時退回列全部——不退的話
            # 讀的人會得到「registry 沒東西」的錯結論，然後又自己造一套。
            if ql and not hit:
                hit = items
                note = f"關鍵字 '{args.q}' 沒命中任何 entry；以下列出該平台全部 flow key（key 多為英文，中文關鍵字常打不中）"
            flows = _group_flows(hit, args.limit)
            result = {"ok": bool(flows), "mode": "list-flows", "query": dq,
                      "scanned": len(items), "matched": len(hit), "flows": flows,
                      "note": note,
                      "next": "拿 flows[].flow 當 --flow 再打一次本腳本取候選 selector"}
        except Exception:
            result = {"ok": False, "mode": "list-flows", "query": dq,
                      "scanned": 0, "matched": 0, "flows": []}
        _receipt("locator", args, len(result.get("flows") or []), EVENTS_PATH, dq)
        text = json.dumps(result, ensure_ascii=False)
        print(text)
        if args.outfile:
            try:
                with open(args.outfile, "w", encoding="utf-8") as f:
                    f.write(text)
            except Exception:
                pass
        return 0

    query = {
        "flow": args.flow,
        "page": args.page,
        "component": args.component,
        "platform": args.platform,
        "env": args.env,
    }

    result = _empty(query)
    try:
        raw = _fetch_with_retry(query)
        if raw is not None:
            result = _shape(raw, query)
    except Exception:
        result = _empty(query)  # 絕對 fail-safe：拿不到就回空
    _receipt("locator", args, len(result.get("entries") or []), PATH, query)

    text = json.dumps(result, ensure_ascii=False)
    # 不論 tty 都把 JSON 印到 stdout（主對話要讀）；失敗時也是合法空 JSON
    print(text)
    if args.outfile:
        try:
            with open(args.outfile, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

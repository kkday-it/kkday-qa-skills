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

用法（stdout 印出 JSON，供主對話讀成起手 hints）：
    # 用 flow 批次取回整條搜尋流程的候選（建議：一個 case 一次 GET）
    python3 fetch_locator_registry.py --flow things-to-do-search --platform web --env stage
    # 或用 page 取回整頁
    python3 fetch_locator_registry.py --page things-to-do-landing \\
        --platform web --env stage [--component landing-search-bar-input] \\
        [--outfile /path/to/hints.json]

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
import sys
import time
import urllib.parse
import urllib.request

BASE = os.getenv("AI_STUDIO_BASE", "http://autotest-service.sit.kkday.com:8081/ai_studio")
PATH = "/api/qa-automation/locator-registry"
MAX_RETRIES = 3
BASE_BACKOFF = 0.4


def _empty(query: dict) -> dict:
    return {"ok": False, "query": query, "entries": [], "methodology": []}


def _get_once(query: dict, timeout: float = 4.0):
    """GET 一次；2xx + 可解析 JSON 才回 dict，其餘回 None。"""
    try:
        qs = urllib.parse.urlencode({k: v for k, v in query.items() if v})
        req = urllib.request.Request(f"{BASE}{PATH}?{qs}", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = getattr(r, "status", r.getcode())
            if not (200 <= status < 300):
                return None
            body = r.read().decode("utf-8")
            return json.loads(body)
    except Exception:
        return None


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


def main() -> int:
    p = argparse.ArgumentParser(description="Fetch locator-registry hints (fail-safe)")
    p.add_argument("--flow", default="", help="流程/區域 key，如 things-to-do-search（批次取整組）")
    p.add_argument("--page", default="", help="頁面語意 key，如 things-to-do-landing")
    p.add_argument("--component", default="", help="元件語意 key（可選，縮小範圍）")
    p.add_argument("--platform", default="web", choices=["web", "mweb"])
    p.add_argument("--env", default="stage", choices=["stage", "prod"])
    p.add_argument("--outfile", default="", help="同時寫入的 JSON 檔路徑（可選）")
    args = p.parse_args()

    if not (args.flow or args.page):
        p.error("需至少提供 --flow 或 --page 其一")

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

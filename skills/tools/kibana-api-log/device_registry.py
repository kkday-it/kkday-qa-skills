#!/usr/bin/env python3
"""
Device Registry client：用**裝置端拿得到的** udid / serial，換**裝置端拿不到的** ad-id。

為什麼需要：APP 沒 DevTools、又有 SSL pinning，要知道它打了哪些 API 只能撈 Kibana；而要在 Kibana
「只看我手上這台」，唯一夠精準的識別是 header 的 `ad-id`（device-model 會誤中同前綴型號、同型號也
可能好幾台；測試帳號也不唯一——實測同一個帳號同時掛在兩台上）。偏偏 **ad-id 在裝置端讀不到**：
IDFA/IDFV 類，只有 app 行程內拿得到，ideviceinfo / adb / idevicesyslog 都不吐。

所以只能從 log 反推（device-model ∩ 帳號 的交集），推出來一次就寄回 ai_studio，用 udid 當 key 存著，
下次任何人任何機器直接查得到。對應後端 `/api/qa-automation/device-registry`。

🔴 **用前先驗**（同 locator / flow registry 的語意）：app 重裝會換 IDFV，取回的 ad_id 不保證還有效。
拿它去撈，撈不到就 `save(..., status="stale")` 回寫並重推。**不要盲信快取。**

為什麼不照 `fetch_*.py` / `send_*.py` 拆兩支：那兩對是給 Stop hook 背景批次送的，才需要 jsonl 佇列
＋ purge。這裡的消費者是 `api_from_kibana.py` 同一個行程、同步讀寫一筆，拆開只會讓「讀到的」
跟「寫回的」漂成兩套欄位定義。

用法（也可當 CLI 直接查）：
    python3 device_registry.py --list                      # 目前登記過的機器
    python3 device_registry.py --udid <udid>               # 查單台
    python3 device_registry.py --udid <udid> --stale       # 手動標記過期（app 重裝後）
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

BASE = os.getenv("AI_STUDIO_BASE", "http://autotest-service.sit.kkday.com:8081/ai_studio")
PATH = "/api/qa-automation/device-registry"
TIMEOUT = 4.0

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from telemetry_identity import resolve_client_user, resolve_operator
    OPERATOR = resolve_operator()
    CLIENT_USER = resolve_client_user()
except Exception:
    OPERATOR = os.getenv("KKDAY_TOOLS_USER_NAME", "kkday_qa_mcp")
    try:
        import socket
        CLIENT_USER = f"{os.getlogin()}@{socket.gethostname()}"
    except Exception:
        CLIENT_USER = "unknown"


def fetch(udid=None, platform=None, device_model=None):
    """查登記過的裝置。回 entry list（查不到或後端掛掉都回 []）。

    fail-safe：這只是快取層，後端連不上就當作「沒登記過」回頭自己推，不能讓主流程掛掉。
    """
    params = {k: v for k, v in
              (("udid", udid), ("platform", platform), ("device_model", device_model)) if v}
    url = f"{BASE}{PATH}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
            body = json.loads(r.read().decode("utf-8"))
    except Exception:
        return []
    # 🔴 後端把 404 包成 HTTP 200 body `[{"error":...},404]`，光看 status code 分不出端點在不在。
    # 判準是「回來的東西長不長得像 {entries:[...]}」。
    if not isinstance(body, dict):
        return []
    return [e for e in (body.get("entries") or []) if isinstance(e, dict)]


def fetch_ad_id(udid, platform):
    """只要「還沒過期」那筆的 ad_id；沒有就回 None。"""
    for e in fetch(udid=udid, platform=platform):
        if e.get("status") == "verified" and e.get("ad_id"):
            return e["ad_id"]
    return None


def save(udid, platform, ad_id="", device_model="", mixpanel_id="",
         app_version="", derived_from="", status="verified"):
    """upsert 一筆裝置對應。回 True/False，失敗不拋例外。

    status="stale" 時只要帶識別欄位就好：後端對空欄位走 $setOnInsert，不會把既有的
    ad_id / device_model 清掉——那個舊值是重推時判斷「是不是真的重裝了」的比對基準。
    """
    payload = {
        "udid": udid, "platform": platform, "ad_id": ad_id,
        "device_model": device_model, "mixpanel_id": mixpanel_id,
        "app_version": app_version, "derived_from": derived_from,
        "status": status, "operator": OPERATOR, "client_user": CLIENT_USER,
    }
    try:
        req = urllib.request.Request(
            f"{BASE}{PATH}", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return 200 <= getattr(r, "status", r.getcode()) < 300
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description="查 / 標記 ai_studio 裝置註冊庫")
    ap.add_argument("--udid")
    ap.add_argument("--platform", choices=["ios", "android"])
    ap.add_argument("--list", action="store_true", help="列出全部登記過的裝置")
    ap.add_argument("--stale", action="store_true",
                    help="把該台標記為過期（app 重裝換了 IDFV 之後用）")
    args = ap.parse_args()

    if args.stale:
        if not args.udid:
            sys.exit("--stale 要指定 --udid")
        ok = save(args.udid, args.platform or "ios", status="stale")
        print("已標記 stale" if ok else "標記失敗（後端連不上）")
        return

    entries = fetch(udid=args.udid, platform=args.platform)
    if not entries:
        print("(查不到；可能是還沒登記，或後端連不上)")
        return
    for e in entries:
        print(f"  {e.get('platform'):<8} {e.get('device_model','?'):<16} "
              f"udid={e.get('udid')}\n"
              f"           ad-id={e.get('ad_id') or '(未推出)'}  "
              f"status={e.get('status')}  last_verified={e.get('last_verified')}\n"
              f"           來源={e.get('derived_from') or '-'}  by {e.get('operator')}")


if __name__ == "__main__":
    main()

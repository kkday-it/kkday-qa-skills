# -*- coding: utf-8 -*-

"""
觀察用戶手動操作 KKday web 時實際打的 API。

Usage:
    python scripts/sniff_kkday_api.py [--env stage|prod] [--output /tmp/sniff.log]

開瀏覽器後手動操作（登入 → 選商品 → 下單 → 付款...），全部 KKday-related API call
（method / url / payload / status / response body）會寫到 output 檔。

關閉用：terminal 按 Enter 鍵終止 script。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from playwright.sync_api import sync_playwright


KKDAY_API_PATTERNS = (
    "/api/_nuxt/",          # Nuxt JSON endpoints (login, change-currency, direct-purchase, point/calculate-points)
    "/zh-tw/booking/",      # CodeIgniter booking endpoints (step1, ajax_validate_cart_items, ajax_select_booking_coupon, step2)
    "/zh-tw/member/",       # member endpoints (ajax_retain_coupon, member info)
    "/zh-tw/point/",        # 點數相關
    "/zh-tw/order/",        # 訂單列表 / 詳情 (orderlist, ajax_get_order_*)
    "/api/v",               # b2c-api versioned endpoints (orderCart/new, payment/success, products, packages)
    "/api/member/",         # b2c-api member endpoints (login)
    "/api/order",           # 訂單查詢 endpoints
    "/orderCart/",          # 訂單建立 / 取消
    "/api/cancel/",         # 訂單取消
    "kkday.com/api",        # 兜底
)
SKIP_PATTERNS = (  # noisy 第三方分析
    "tiktok.com", "google", "facebook", "doubleclick", "criteo", "datadome",
    "sentry", "bugsnag", "newrelic", "segment.io", "amplitude",
)


def _matches(url: str) -> bool:
    if any(s in url for s in SKIP_PATTERNS):
        return False
    return any(p in url for p in KKDAY_API_PATTERNS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default="stage", choices=["stage", "prod", "sit"], help="KKday env")
    parser.add_argument("--output", default="/tmp/kkday_sniff.log", help="output log path")
    parser.add_argument("--start-url", default=None, help="optional initial URL to load")
    args = parser.parse_args()

    base_url = f"https://www.{args.env}.kkday.com" if args.env != "prod" else "https://www.kkday.com"
    start_url = args.start_url or base_url

    out = open(args.output, "w", buffering=1)  # line-buffered

    def log(line: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        out.write(f"[{ts}] {line}\n")
        print(f"[{ts}] {line}")

    log(f"=== sniff start, env={args.env}, output={args.output} ===")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, args=["--start-maximized"])
        context = browser.new_context(no_viewport=True)

        def on_request(req):
            try:
                if not _matches(req.url):
                    return
                body = req.post_data
                if body and len(body) > 4000:
                    body = body[:4000] + "...<truncated>"
                log(f">>> {req.method} {req.url}")
                if body:
                    log(f"    body: {body}")
            except Exception as e:
                log(f"!!! request log err: {e}")

        def on_response(resp):
            try:
                if not _matches(resp.url):
                    return
                try:
                    body = resp.text()
                except Exception:
                    body = "<binary>"
                if body and len(body) > 4000:
                    body = body[:4000] + "...<truncated>"
                log(f"<<< {resp.status} {resp.url}")
                if body and body != "<binary>":
                    # 試 pretty-print 短 JSON
                    if body.strip().startswith("{") and len(body) < 500:
                        try:
                            body = json.dumps(json.loads(body), ensure_ascii=False)
                        except Exception:
                            pass
                    log(f"    resp: {body}")
            except Exception as e:
                log(f"!!! response log err: {e}")

        context.on("request", on_request)
        context.on("response", on_response)
        page = context.new_page()
        page.goto(start_url)

        log(f"=== browser opened at {start_url}, please operate manually ===")
        log("=== press Enter in terminal to stop and dump summary ===")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass

        log("=== sniff stop ===")
        context.close()
        browser.close()

    out.close()
    print(f"\nFull log saved to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

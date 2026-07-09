#!/usr/bin/env python3
"""
Locator cheap 驗證腳本（非 LLM、非遙測）

給定 URL + 優先序候選 selector，開一個真實 browser 檢查每個候選在當前 DOM『存不存在』，
回報第一個命中的候選與 verified/stale。這是 locator registry「用前先驗」那道閥的實作雛形：
便宜（一次 DOM 查詢，不需 LLM 探索）、可掛在 skill 起手流程或 CI。

**核心語意**：registry 拿回來的 selector 只是候選 hint，不是真理。這支腳本負責把「候選」
變成「當前 DOM 驗證過的事實」——
  - 有任一候選命中 → status=verified，回報命中的那個（skill 直接用，省下重挖）。
  - 全部候選都沒命中 → status=stale，skill 必須回退到「從零挖」原本流程並重挖。

mweb 方法論（重要）：kkday 靠 User-Agent 決定回 web 還是 mweb DOM，不是看 viewport。
所以 mweb 驗證要用 `--device "iPhone 15"`（＝框架 mweb 用的同一台，見
QATest/src/lib/fixtures/playwright.py 的 devices["iPhone 15"]），**不能只縮 viewport 冒充**。

需要 playwright（Python）：`pip install playwright && playwright install chromium`。

用法 A — 單一元素，直接給候選（type:value，可重複，依優先序）：
    python3 verify_locator.py --url https://www.stage.kkday.com/zh-tw/product/ticket \\
        --candidate "css:input.things-to-do-search-bar__input" \\
        --candidate "xpath://input[contains(@class,'search-input__keyword')]" \\
        [--device "iPhone 15"]

用法 B — 驗整個 registry（可依 flow/page/platform/env 過濾），並可回寫 status/last_verified：
    python3 verify_locator.py --registry locator_registry/registry.json \\
        --flow things-to-do-search --platform web --env stage [--write]

輸出：JSON 到 stdout。單元素模式回 {status, hit, checked}；registry 模式回每筆結果陣列 + 摘要。
exit code：單元素 verified=0 / stale=2；registry 模式恆 0（除非 playwright 缺）。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_locator_str(sel_type: str, value: str) -> str:
    """把 (type, value) 轉成 Playwright locator 語法。"""
    if sel_type == "xpath":
        return f"xpath={value}"
    return f"css={value}"


def _open_page(pw, device: str):
    """開 browser context；device 非空則套 Playwright 內建 device profile（帶手機 UA）。"""
    browser = pw.chromium.launch(headless=True)
    if device:
        desc = pw.devices.get(device)
        if not desc:
            raise RuntimeError(f"未知 device profile: {device}")
        context = browser.new_context(**desc)
    else:
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
    return browser, context, context.new_page()


def _verify_candidates(page, candidates: list, timeout_ms: int = 8000) -> dict:
    """逐一驗候選，回第一個 count>0 的。candidates: [{'type','value',...}, ...] 依優先序。"""
    checked = []
    for c in candidates:
        sel_type = c.get("type", "css")
        value = c.get("value", "")
        if not value:
            continue
        exists = False
        try:
            loc = page.locator(_to_locator_str(sel_type, value))
            exists = loc.count() > 0
        except Exception as e:
            checked.append({"type": sel_type, "value": value, "exists": False, "error": str(e)[:120]})
            continue
        checked.append({"type": sel_type, "value": value, "exists": exists})
        if exists:
            return {"status": "verified", "hit": {"type": sel_type, "value": value}, "checked": checked}
    return {"status": "stale", "hit": None, "checked": checked}


def _mode_single(args) -> int:
    candidates = []
    for raw in args.candidate:
        if ":" not in raw:
            continue
        t, v = raw.split(":", 1)
        candidates.append({"type": t.strip(), "value": v})
    if not candidates:
        print(json.dumps({"error": "no --candidate given"}, ensure_ascii=False))
        return 3
    with sync_playwright() as pw:
        browser, _, page = _open_page(pw, args.device)
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=20000)
            result = _verify_candidates(page, candidates)
        finally:
            browser.close()
    result["url"] = args.url
    result["device"] = args.device or "desktop"
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "verified" else 2


def _mode_registry(args) -> int:
    with open(args.registry, "r", encoding="utf-8") as f:
        reg = json.load(f)
    entries = reg.get("entries", [])
    # 過濾
    def _match(e):
        return ((not args.flow or e.get("flow") == args.flow)
                and (not args.page or e.get("page") == args.page)
                and (not args.platform or e.get("platform") == args.platform)
                and (not args.env or e.get("env") == args.env))
    targets = [e for e in entries if _match(e)]

    results = []
    with sync_playwright() as pw:
        for e in targets:
            url = e.get("verify_url")
            device = "iPhone 15" if e.get("platform") == "mweb" else ""
            if not url:
                results.append({"id": e.get("id"), "status": "skipped", "reason": "no verify_url"})
                continue
            browser, _, page = _open_page(pw, device)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                r = _verify_candidates(page, e.get("selectors", []))
            except Exception as ex:
                r = {"status": "stale", "hit": None, "checked": [], "error": str(ex)[:120]}
            finally:
                browser.close()
            # 回寫（--write）
            if args.write:
                e["status"] = r["status"]
                e["last_verified"] = _now()
            results.append({"id": e.get("id"), "platform": e.get("platform"),
                            "status": r["status"], "hit": r.get("hit"), "error": r.get("error")})

    if args.write and targets:
        reg["updated_at"] = _now()
        with open(args.registry, "w", encoding="utf-8") as f:
            json.dump(reg, f, ensure_ascii=False, indent=2)
            f.write("\n")

    verified = sum(1 for r in results if r["status"] == "verified")
    stale = sum(1 for r in results if r["status"] == "stale")
    print(json.dumps({
        "summary": {"total": len(results), "verified": verified, "stale": stale, "written": bool(args.write)},
        "results": results,
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Cheap locator verifier (non-LLM)")
    p.add_argument("--url", help="單元素模式：要驗的頁面 URL")
    p.add_argument("--candidate", action="append", default=[], help="單元素模式：type:value（可重複，依優先序）")
    p.add_argument("--device", default="", help="套 Playwright device profile（mweb 用 'iPhone 15'）")
    p.add_argument("--registry", help="registry 模式：registry.json 路徑")
    p.add_argument("--flow", default="", help="registry 模式過濾：flow key")
    p.add_argument("--page", default="", help="registry 模式過濾：page key")
    p.add_argument("--platform", default="", choices=["", "web", "mweb"], help="registry 模式過濾")
    p.add_argument("--env", default="", choices=["", "stage", "prod"], help="registry 模式過濾")
    p.add_argument("--write", action="store_true", help="registry 模式：把驗證結果回寫 status/last_verified")
    args = p.parse_args()

    if sync_playwright is None:
        print(json.dumps({"error": "playwright 未安裝。請 pip install playwright && playwright install chromium"}, ensure_ascii=False))
        return 4

    if args.registry:
        return _mode_registry(args)
    if args.url:
        return _mode_single(args)
    p.error("需提供 --registry（批次）或 --url + --candidate（單元素）")


if __name__ == "__main__":
    sys.exit(main())

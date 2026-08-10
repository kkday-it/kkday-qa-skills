#!/usr/bin/env python3
"""create_order 兩段式閘（preview → confirm_token → 下單）+ copy_product_verify 的單元測試。

跑法（用本目錄的 venv，才有 fastmcp）：
    KKDAY_TOOLS_BASE=http://localhost:1 .venv/bin/python test_order_gate.py

全程 monkeypatch `server._platform_call`，不打任何網路；KKDAY_TOOLS_BASE 指向
無效位址是雙保險（真的漏 patch 會立刻 connection refused 而不是打到真環境）。

為什麼要有這支：2026-08-10 稽核發現第一版 PR 只跑了丟棄式 inline assertion、
repo 內零回歸保護——閘的規則（單次消費 / 不代挑 / 列候選）以後被改壞不會有人知道。
"""
import os
import sys
import time

os.environ.setdefault("KKDAY_TOOLS_BASE", "http://localhost:1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server  # noqa: E402

PASS = 0


def ok(label: str, cond: bool, ctx=None):
    """單條斷言：失敗立刻退出非零，Jenkins / 手跑都看得懂。"""
    global PASS
    if not cond:
        print(f"❌ {label}" + (f"\n   {ctx}" if ctx is not None else ""))
        sys.exit(1)
    PASS += 1
    print(f"✅ {label}")


def seed(pairs: dict, env: str = "stage", prod: str = "28750") -> str:
    """種一筆 preview cache，回 token。"""
    tok = f"tok_{PASS}"
    server._platform_preview_cache[tok] = {
        "kind": "create_order", "env": env, "prod_oid": prod, "pairs": pairs, "ts": time.monotonic(),
    }
    return tok


def main():
    calls = []
    server._platform_call = lambda tool, env, kw, **k: calls.append((tool, kw)) or {"mock": tool}

    # ── create_order 的 token 閘 ─────────────────────────────────────────
    r = server.create_order(env="stage", product_oid="28750", package_oid="P1", confirm_token="bogus")
    ok("假 token 被擋且指引重跑 preview", r.get("status") == "rejected" and "preview" in r.get("detail", ""), r)

    t = seed({"P1": ["I1"]})
    r = server.create_order(env="sit", product_oid="28750", package_oid="P1", confirm_token=t)
    ok("env 與 preview 不符被擋", r.get("status") == "rejected" and "不符" in r.get("detail", ""), r)

    t = seed({"P1": ["I1"]})
    r = server.create_order(env="stage", product_oid="28750", package_oid="P9", confirm_token=t)
    ok("陌生 pkg 被擋且列出候選", r.get("status") == "rejected" and "P1" in r.get("detail", ""), r)

    t = seed({"P1": ["I1"]})
    r = server.create_order(env="stage", product_oid="28750", package_oid="P1", confirm_token=t)
    ok("單 item 自動補 itemOid", r == {"mock": "create_order"} and calls[-1][1].get("itemOid") == "I1", calls[-1])

    r = server.create_order(env="stage", product_oid="28750", package_oid="P1", confirm_token=t)
    ok("token 單次消費，重用被擋", r.get("status") == "rejected", r)

    t = seed({"P1": ["I1", "I2"]})
    r = server.create_order(env="stage", product_oid="28750", package_oid="P1", confirm_token=t)
    ok("多 item 絕不代挑、列出候選", r.get("status") == "rejected" and "I1" in r["detail"] and "I2" in r["detail"], r)

    t = seed({"P1": ["I1", "I2"]})
    server.create_order(env="stage", product_oid="28750", package_oid="P1", item_oid="I2", confirm_token=t)
    ok("指定 item 原樣透傳", calls[-1][1].get("itemOid") == "I2", calls[-1])

    t = seed({"P1": ["I1"]})
    r = server.create_order(env="stage", product_oid="28750", package_oid="P1", item_oid="I9", confirm_token=t)
    ok("不屬於套餐的 item 被擋", r.get("status") == "rejected" and "I9" in r.get("detail", ""), r)

    t = seed({"P1": ["I1", "I2"]})
    server.create_order(env="stage", product_oid="28750", package_oid="P1", confirm_token=t,
                        bundle_package_oid="B1", sub_package_oids=["S1", "S2"])
    kw = calls[-1][1]
    ok("bundle 單不強制 item、母子參數透傳",
       kw.get("bundlePackageOid") == "B1" and kw.get("subPackageOids") == ["S1", "S2"] and "itemOid" not in kw, kw)

    t = seed({"P1": ["I1"]})
    server.create_order(env="stage", product_oid="28750", package_oid="P1", confirm_token=t,
                        lst_go_dt="2026-08-15", event_time="13:30")
    kw = calls[-1][1]
    ok("lstGoDt / eventTime 透傳", kw.get("lstGoDt") == "2026-08-15" and kw.get("eventTime") == "13:30", kw)

    # ── create_order_preview ────────────────────────────────────────────
    server._platform_call = lambda *a, **k: [
        {"pkg_oid": "P1", "pkg_name": "單item", "item_oid": "I1", "min_quantity": 1, "max_quantity": 4},
        {"pkg_oid": "P2", "pkg_name": "雙item", "item_oid": "I2a", "min_quantity": 1, "max_quantity": 2},
        {"pkg_oid": "P2", "pkg_name": "雙item", "item_oid": "I2b", "min_quantity": 1, "max_quantity": 2},
    ]
    r = server.create_order_preview(env="stage", product_oid="28750")
    cached = server._platform_preview_cache.get(r.get("confirm_token") or "", {})
    ok("preview 建立 (pkg→items) 配對表 + token",
       r.get("status") == "success" and cached.get("pairs") == {"P1": ["I1"], "P2": ["I2a", "I2b"]}, r)

    server._platform_call = lambda *a, **k: {"message": "no_sellable_package", "detail": "全部售罄"}
    r = server.create_order_preview(env="stage", product_oid="999")
    ok("preview 失敗訊息原樣透傳（不發 token）",
       r.get("message") == "no_sellable_package" and "confirm_token" not in r, r)

    # ── create_order_options ────────────────────────────────────────────
    def fake(tool, env, kw, **k):
        if tool == "get_bundles_for_package":
            return [{"bundle_pkg_oid": "B1", "label": "雙人套票", "sub_package_oids": ["S1"]}]
        if tool == "get_events":
            return [{"date": "2026-08-15", "event": "13:30", "sku_oid": "K1"}]
        raise AssertionError(tool)

    server._platform_call = fake
    r = server.create_order_options(env="stage", product_oid="28750", package_oid="P1",
                                    item_oid="I1", include_events=True)
    ok("options 回 bundle combos + 場次",
       r.get("is_bundle_anchor") is True and r["bundle_combos"][0]["bundle_pkg_oid"] == "B1"
       and r["events"][0]["event"] == "13:30", r)

    server._platform_call = lambda tool, env, kw, **k: []
    r = server.create_order_options(env="stage", product_oid="28750", package_oid="P1", include_events=True)
    ok("非 bundle + 查場次缺 item 有明確提示",
       r.get("is_bundle_anchor") is False and "events_error" in r, r)

    # ── copy_product_verify ─────────────────────────────────────────────
    seen = {}
    server._platform_call = lambda tool, env, kw, **k: seen.update({"tool": tool, "kw": kw}) or {"verify_status": "identical"}
    server.copy_product_verify(target_env="stage", source_env="stage",
                               source_prod_oid="28750", target_prod_oid="701540")
    ok("copy_product_verify 透傳且不帶空 pkg_map",
       seen["tool"] == "copy_product_verify" and seen["kw"]["source_prod_oid"] == "28750"
       and "pkg_map" not in seen["kw"], seen)

    print(f"\n{PASS}/{PASS} 全過")


if __name__ == "__main__":
    main()

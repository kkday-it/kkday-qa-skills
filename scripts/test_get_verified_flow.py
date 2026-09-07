#!/usr/bin/env python3
"""get_verified_flow 讀取端天花板純函式測試：去重 / 相關性排序 / top-N。

確保「registry 越大、餵給 AI 越多」這條被切斷。
跑法：python3 scripts/test_get_verified_flow.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from get_verified_flow import (  # noqa: E402
    _datekey,
    _dedup,
    _emit_row,
    _EMIT_KEYS,
    _platform_family,
    _rank_and_cap,
    _score,
)


def test_datekey():
    assert _datekey({"last_verified": "2026-07-16T00:00:00+00:00"}) == "2026-07-16"
    assert _datekey({"last_verified": "2026-07-09T00:00:00Z"}) == "2026-07-09"
    assert _datekey({}) == ""


def test_dedup_keeps_latest():
    cands = [
        {"name": "go_pay", "platform": "app", "kind": "test_step", "last_verified": "2026-07-01"},
        {"name": "go_pay", "platform": "app", "kind": "test_step", "last_verified": "2026-07-16"},  # 較新
        {"name": "go_pay", "platform": "web", "kind": "test_step", "last_verified": "2026-07-05"},  # 不同平台，保留
    ]
    out = _dedup(cands)
    # app 的兩筆去重成一筆（留最新），web 保留 → 共 2
    assert len(out) == 2
    app = [e for e in out if e["platform"] == "app"][0]
    assert app["last_verified"] == "2026-07-16"


def test_dedup_collapses_platform_family_but_not_across_families():
    """同家族的不同寫法要併（ios/app/"ios,android" 是同一件事被記三次）；
    跨家族**不可**併——app 的實作與 web 的實作是兩份真的不同的東西，併掉一份就靜默消失。"""
    cands = [
        {"name": "go_pay", "kind": "test_step", "platform": "ios",
         "last_verified": "2026-07-01", "platform_match": "exact", "location": "a/ios_pay.py:10"},
        {"name": "go_pay", "kind": "test_step", "platform": "app",
         "last_verified": "2026-07-16", "platform_match": "family", "location": "a/pay.py:20"},
        {"name": "go_pay", "kind": "test_step", "platform": "ios,android",
         "last_verified": "2026-07-05", "platform_match": "family", "location": "a/pay.py:20"},
        {"name": "go_pay", "kind": "test_step", "platform": "web",
         "last_verified": "2026-07-05", "platform_match": "sibling", "location": "w/pay.py:30"},
    ]
    out = _dedup(cands)
    assert len(out) == 2, [e["platform"] for e in out]
    app = [e for e in out if _platform_family(e["platform"]) == "app"][0]
    assert app["platform_variants"] == ["app", "ios", "ios,android"], app.get("platform_variants")
    # 家族內合併掉的另一個檔案不能無聲消失
    assert app["location_variants"] == ["a/ios_pay.py:10", "a/pay.py:20"], app.get("location_variants")
    web = [e for e in out if _platform_family(e["platform"]) == "web"][0]
    assert web.get("platform_variants") is None


def test_platform_family():
    for p in ("ios", "android", "app", "mobile", "ios,android", "ios/android"):
        assert _platform_family(p) == "app", p
    for p in ("web", "mweb", "desktop"):
        assert _platform_family(p) == "web", p
    assert _platform_family("api") == "api"
    for p in ("any", "all", "", None):
        assert _platform_family(p) == "any", p
    # 認不出來的不硬塞進家族（寧可多一列，也不要把不同東西併掉）
    assert _platform_family("be2") == "be2"
    assert _platform_family("web,api") == "api+web"


def test_score_relevance_order():
    exact = {"name": "activate_supplier", "purpose": ""}
    partial = {"name": "activate_supplier_to_active", "purpose": ""}
    in_purpose = {"name": "foo", "purpose": "會 activate_supplier 的 helper"}
    irrelevant = {"name": "bar", "purpose": "baz"}
    q = "activate_supplier"
    assert _score(exact, q) > _score(partial, q) > _score(in_purpose, q) > _score(irrelevant, q)


def test_score_recency_tiebreak():
    # 同 relevance（都 name 含 q）→ 新的排前
    a = {"name": "go_pay_x", "purpose": "", "last_verified": "2026-07-16"}
    b = {"name": "go_pay_y", "purpose": "", "last_verified": "2026-07-01"}
    assert _score(a, "go_pay") > _score(b, "go_pay")


def test_rank_and_cap_limits():
    cands = [{"name": f"f{i}", "platform": "api", "kind": "helper",
              "purpose": "activate", "last_verified": f"2026-07-{i:02d}"} for i in range(1, 21)]
    out = _rank_and_cap(cands, "activate", limit=5)
    assert len(out) == 5
    # 相關性同分 → 按 recency，最新（day 20）在最前
    assert out[0]["last_verified"] == "2026-07-20"


def test_rank_and_cap_no_limit():
    cands = [{"name": f"f{i}", "platform": "api", "kind": "helper", "last_verified": "2026-07-01"}
             for i in range(10)]
    assert len(_rank_and_cap(cands, "", limit=0)) == 10  # <=0 不限


def test_rank_and_cap_dedups_before_cap():
    # 5 筆但其中 3 筆是同一個（同 name/platform/kind）→ 去重後 3 筆，limit=5 全回
    cands = [
        {"name": "dup", "platform": "api", "kind": "helper", "last_verified": "2026-07-01"},
        {"name": "dup", "platform": "api", "kind": "helper", "last_verified": "2026-07-09"},
        {"name": "dup", "platform": "api", "kind": "helper", "last_verified": "2026-07-16"},
        {"name": "b", "platform": "api", "kind": "helper", "last_verified": "2026-07-02"},
        {"name": "c", "platform": "api", "kind": "helper", "last_verified": "2026-07-03"},
    ]
    out = _rank_and_cap(cands, "", limit=5)
    assert len(out) == 3
    dup = [e for e in out if e["name"] == "dup"][0]
    assert dup["last_verified"] == "2026-07-16"


def test_emit_row_stale_keeps_all_fields():
    """stale 那筆一定要帶全欄位。

    後端 upsert 是整包 $set，少帶一個欄位就會把共享庫裡既有的 purpose / location /
    signature / example 清成空字串——那筆 flow 原本是幹嘛的、在哪，線索一起沒了。
    """
    entry = {
        "id": "app-goto-pay", "name": "go_pay", "kind": "test_step",
        "purpose": "到訂購頁", "location": "common.py:320", "signature": "(x=1)",
        "example": "- step: go_pay", "platform": "app", "repo": "kkday-QA-automation",
    }
    stale = _emit_row(entry, "go_pay", "stale")
    verified = _emit_row(entry, "go_pay", "verified")
    # 兩者欄位集合一致，只有 status 不同
    assert set(stale) == set(verified) == set(_EMIT_KEYS) | {"status"}
    assert stale["status"] == "stale" and verified["status"] == "verified"
    for k in ("purpose", "location", "signature", "example"):
        assert stale[k] == entry[k], f"{k} 被丟掉了"


def test_emit_row_name_wins_over_entry():
    """entry 缺 name 時，用實際驗證過的 name（後端 name 為必填）。"""
    row = _emit_row({"platform": "web"}, "do_login", "stale")
    assert row["name"] == "do_login"


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f" FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())

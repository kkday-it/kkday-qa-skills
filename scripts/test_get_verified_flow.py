#!/usr/bin/env python3
"""get_verified_flow 讀取端天花板純函式測試：去重 / 相關性排序 / top-N。

確保「registry 越大、餵給 AI 越多」這條被切斷。
跑法：python3 scripts/test_get_verified_flow.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from get_verified_flow import _dedup, _score, _rank_and_cap, _datekey  # noqa: E402


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

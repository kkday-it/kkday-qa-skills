#!/usr/bin/env python3
"""
Flow Registry 取回 + 用前先驗（GET → verify），非侵入、與 kkday-qa-tools MCP 無關。

qa-case-planner 規劃前起手：拿「可重用 flow（setup flow / test step / helper）」的候選當 hints，
省重複 grep repo、少發明第二套。但**候選一律是 hint、不是真理**——這支對每個候選做「用前先驗」：
grep 框架 repo 確認該 function 名還在（沒被改名/搬走），驗過才回傳給 planner 用；驗不過標 stale、
回傳裡不給它，強制 planner 回退到「grep repo 從零找」。

fail-safe（比照 fetch/send_locator_registry.py）：後端不可達 / 查無 / 任何錯 → 回空、靜默、exit 0，
planner 照原本 grep 流程跑，不受影響。

用法（stdout 印 JSON）：
    python3 get_verified_flow.py --q "訂購頁" --platform app \\
        --repo-path /path/to/kkday-QA-automation [--kind setup_flow] \\
        [--registry flow_registry/registry.json] [--emit /tmp/flow_results.jsonl]

輸出：
    { ok, query, verified:[<entry+status:verified>...], stale:[<name...>], checked:N }
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

BASE = os.getenv("AI_STUDIO_BASE", "http://autotest-service.sit.kkday.com:8081/ai_studio")
PATH = "/api/qa-automation/flow-registry"
MAX_RETRIES = 3
BASE_BACKOFF = 0.4

# 讀取收據（供 Stop 的讀取硬 gate 比對）。拿不到就當沒有——寫收據永不影響讀取結果。
try:
    import registry_read_receipt
except Exception:
    registry_read_receipt = None

# ── platform 詞彙統一（讀取端在本機做，不靠後端）─────────────────────────────
# 後端的 platform filter 是「完全相等 ∪ 'any'」：寫入端各寫 ios / app / mobile / "ios,android"，
# 讀取端用 --platform ios 就只撈得到剛好寫 "ios" 的那些，其餘全撈不到（實測約四成撈不到）。
# 這是「明明有記錄卻復用不到」最大的單一原因——同一個 function 因此被不同人重新註冊好幾次。
# 對策：後端一律用 platform=any（＝不過濾）撈回，平台判斷改在本機做別名展開。
_FAMILY = {
    "ios": {"ios", "app", "mobile"},
    "android": {"android", "app", "mobile"},
    "web": {"web", "desktop"},
    "mweb": {"mweb"},
    "api": {"api", "backend"},
}
# 共用主幹：repo 慣例是 web ↔ mweb、ios ↔ android 共用 test step（見 qa-test-runner SKILL.md），
# 所以兄弟平台的 entry 也當候選回傳，但標記 platform_match=sibling、排序讓它落在後面。
_SIBLING = {"ios": {"android"}, "android": {"ios"}, "web": {"mweb"}, "mweb": {"web"}}
_GENERIC = {"any", "all", "*"}
_PLATFORM_RANK = {"exact": 3, "family": 2, "generic": 1, "sibling": 0}


def _platform_tokens(value) -> set:
    """把 entry 的 platform 欄位拆成 token 集合：'ios,android' / 'ios/android' / 'ios android' 都吃。"""
    raw = str(value or "").lower()
    return {t for t in re.split(r"[,\s/|;+]+", raw) if t}


_FAMILY_OF = {
    "ios": "app", "android": "app", "app": "app", "mobile": "app",
    "web": "web", "mweb": "web", "desktop": "web",
    "api": "api", "backend": "api",
}


def _platform_family(value) -> str:
    """platform 原字串 → 平台家族（去重用）。

    ios / android / app / mobile / "ios,android" 一律回 'app'；web / mweb 回 'web'；
    api 回 'api'；any / all / 空回 'any'；認不出來的回排序後的原 token（不硬塞進任何家族，
    寧可多一列也不要把不同東西併掉）。
    """
    toks = _platform_tokens(value)
    if not toks or toks <= _GENERIC:
        return "any"
    fams = {_FAMILY_OF[t] for t in toks if t in _FAMILY_OF}
    unknown = sorted(t for t in toks if t not in _FAMILY_OF and t not in _GENERIC)
    if fams and not unknown:
        return "+".join(sorted(fams))
    return "+".join(sorted(fams) + unknown) or "any"


def _platform_match(entry_platform, want: str):
    """回 'exact' / 'family' / 'generic' / 'sibling'，完全不相關回 None。"""
    want = (want or "").strip().lower()
    toks = _platform_tokens(entry_platform)
    if not want or want in _GENERIC:
        return "exact"          # 沒指定平台＝不過濾
    if want in toks:
        return "exact"
    if toks & (_FAMILY.get(want, set()) - {want}):
        return "family"         # app / mobile 這種明示「兩端都適用」的寫法
    if toks & _GENERIC or not toks:
        return "generic"        # any / 沒填
    if toks & _SIBLING.get(want, set()):
        return "sibling"        # 共用主幹的另一端，仍值得看
    return None


def _get_candidates(kind: str, q: str, repo: str) -> list:
    """從後端 GET 候選；失敗回 []（呼叫端會退本地 registry）。

    platform 一律送 'any'（後端的 any ＝不過濾），過濾交給 `_platform_match` 在本機做別名展開。
    """
    params = {"platform": "any", "repo": repo}
    if kind:
        params["kind"] = kind
    if q:
        params["q"] = q
    url = f"{BASE}{PATH}?" + urllib.parse.urlencode(params)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=4.0) as r:
                data = json.loads(r.read().decode("utf-8"))
                return data.get("entries", []) or []
        except Exception:
            if attempt < MAX_RETRIES:
                time.sleep(attempt * BASE_BACKOFF)
    return []


def _local_fallback(registry_path: str, platform: str, kind: str, q: str) -> list:
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            entries = json.load(f).get("entries", [])
    except Exception:
        return []
    out = []
    ql = (q or "").lower()
    for e in entries:
        if _platform_match(e.get("platform"), platform) is None:
            continue
        if kind and e.get("kind") != kind:
            continue
        if ql and ql not in (e.get("name", "") + " " + e.get("purpose", "")).lower():
            continue
        out.append(e)
    return out


def _datekey(entry: dict) -> str:
    """取 last_verified 的日期前綴（YYYY-MM-DD）當 recency tiebreaker；缺就空字串（排最後）。"""
    v = str(entry.get("last_verified") or "").strip()
    return v[:10] if len(v) >= 10 else ""


def _loc_key(loc) -> str:
    """location 正規化：去掉行號與其後的註記、去掉 QATest/src/ prefix 差異。
    用來判斷兩筆是「同一個檔的重複註冊」還是「真的兩個不同實作」。"""
    s = str(loc or "").strip()
    s = re.sub(r":\d+.*$", "", s)
    s = re.sub(r"^QATest/src/", "", s)
    return s.strip()


def _dedup(cands: list) -> list:
    """讀取端去重：key = **(name, kind, 平台家族)**。

    刻意用「家族」而不是 platform 原字串：同一個 function 被不同人分別註冊成
    ios / app / mobile / "ios,android" 是最常見的重複來源（platform 詞彙不統一的直接產物）。
    用原字串當 key 等於承認那些是不同東西，於是讀的人看到三四筆長得一樣的候選，再自己造第五個。
    同家族合併成一筆、把看過的寫法收進 `platform_variants`，讓「其實是同一個」看得出來。

    🔴 但**不可**跨家族合併：app 的實作與 web 的實作是兩個真的不同的東西（實測 298 筆裡
    `create_ticket_event` 就同時有 api 與 web 兩份、落在不同檔）。整個 platform 都不進 key 時
    這兩份會被併成一筆，另一份靜默消失 —— 那正是「讀回來的東西不完整」的來源。
    留最新一筆（by last_verified）、平台吻合度高的優先。"""
    def _key(e):
        return (str(e.get("name", "")).lower(), str(e.get("kind", "")).lower(),
                _platform_family(e.get("platform")))

    best = {}
    for e in cands:
        key = _key(e)
        cur = best.get(key)
        if cur is None or (_PLATFORM_RANK.get(e.get("platform_match"), 0),
                           _datekey(e)) > (_PLATFORM_RANK.get(cur.get("platform_match"), 0),
                                           _datekey(cur)):
            best[key] = e
    for key, e in best.items():
        same = [c for c in cands if _key(c) == key]
        variants = sorted({str(c.get("platform") or "") for c in same})
        if len(variants) > 1:
            e["platform_variants"] = variants
        # 同名同 kind 但**真的落在不同檔案**時，合併會讓另一個實作靜默消失（實測 298 筆裡有
        # 1 筆：create_ticket_event 同時存在 api/BE2/TicketEvent.py 與 be2/crm.py）。行號/prefix
        # 漂移不算（那是同一個東西被重複註冊，正是這裡要收斂的噪音），所以比較前先正規化。
        locs = sorted({_loc_key(c.get("location")) for c in same} - {""})
        if len(locs) > 1:
            e["location_variants"] = sorted({str(c.get("location") or "").strip()
                                             for c in same if c.get("location")})
    # 保持原順序穩定（Python dict 保序），去重後由 _score 再排
    return list(best.values())


def _score(entry: dict, q: str):
    """排序鍵（配合 sort reverse=True）：回 (relevance:int, platform_rank:int, datekey:str)。
    relevance：name 完全等於 q > name 含 q > purpose 含 q > 其他；q 空時全 0、純靠後兩者。
    platform_rank：exact > family > generic > sibling（別名展開的吻合度）。
    datekey：last_verified 日期前綴，當同分 tiebreaker（越新越前）。"""
    ql = (q or "").strip().lower()
    name = str(entry.get("name", "")).lower()
    purpose = str(entry.get("purpose", "")).lower()
    rel = 0
    if ql:
        if name == ql:
            rel = 100
        elif ql in name:
            rel = 50
        elif ql in purpose:
            rel = 20
    return (rel, _PLATFORM_RANK.get(entry.get("platform_match"), 0), _datekey(entry))


def _rank_and_cap(cands: list, q: str, limit: int) -> list:
    """去重 → 相關性+平台吻合度+recency 排序 → 取前 limit 筆。讓餵給 AI 的量與 registry 大小脫鉤。"""
    ranked = sorted(_dedup(cands), key=lambda e: _score(e, q), reverse=True)
    return ranked[:limit] if (limit and limit > 0) else ranked


def _function_still_exists(name: str, repo_path: str) -> bool:
    """用前先驗：grep 框架 repo 確認該 function/step 名還在（沒被改名/搬走）。"""
    if not name or not repo_path or not os.path.isdir(repo_path):
        return False
    src = os.path.join(repo_path, "QATest", "src")
    target = src if os.path.isdir(src) else repo_path
    try:
        # 找 `def <name>(`；找到即視為仍存在
        r = subprocess.run(
            ["grep", "-rInsq", rf"def {re.escape(name)}\b", target],
            timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return False


_EMIT_KEYS = (
    "id", "name", "kind", "purpose", "location", "signature",
    "example", "platform", "repo",
)


def _emit_row(entry: dict, name: str, status: str) -> dict:
    """verified / stale 都帶**全欄位**。

    後端 upsert 是整包 `$set`，少帶欄位就會把 registry 既有的
    purpose / location / signature / example 蓋成空字串——stale 那筆一旦被清空，
    人就再也看不出「原本這個 flow 是幹嘛的、在哪」，等於把重挖線索一起丟掉。
    """
    return {**{k: entry.get(k) for k in _EMIT_KEYS}, "name": name, "status": status}


def main() -> int:
    p = argparse.ArgumentParser(description="Get + verify reusable flows (non-LLM)")
    p.add_argument("--q", default="", help="關鍵字（對 name/purpose 子字串）")
    p.add_argument("--kind", default="", help="setup_flow|test_step|helper|fixture")
    p.add_argument("--platform", default="any",
                   help="ios|android|app|web|mweb|api|any。後端只做完全相等比對，所以這裡改成"
                        "本機別名展開：ios 也會撈到寫成 app / mobile / 'ios,android' 的 entry，"
                        "並把共用主幹的兄弟平台（ios↔android、web↔mweb）當候選、標 platform_match=sibling。")
    p.add_argument("--case", default="",
                   help="當前正在做的 case id（如 KQT-T7172）。會寫一列讀取收據供 Stop 的讀取硬 gate "
                        "比對；交付 case 前的讀取一律要帶，否則 gate 對不上會擋下結束。")
    p.add_argument("--repo", default="kkday-QA-automation")
    p.add_argument("--repo-path", default="", help="框架 repo 本機路徑（驗證用；不給則跳過驗證只回候選）")
    p.add_argument("--registry", default="", help="本地 fallback registry.json")
    p.add_argument("--emit", default="", help="把驗證結果寫成 jsonl（供 send_flow_registry 回寫 status）")
    p.add_argument("--limit", type=int, default=8,
                   help="讀取端 top-N 上限：去重+相關性排序後只回最相關的前 N 筆（預設 8；<=0 不限）。"
                        "確保給 AI 的量與 registry 大小脫鉤——registry 再大，也只回最相關的幾筆。")
    args = p.parse_args()

    result = {"ok": False, "query": vars(args), "verified": [], "stale": [], "checked": 0}
    try:
        cands = _get_candidates(args.kind, args.q, args.repo)
        if not cands and args.registry:
            cands = _local_fallback(args.registry, args.platform, args.kind, args.q)

        # 平台過濾改在本機做（後端已用 any 撈全部）：別名展開 + 標記吻合度供排序用
        tagged = []
        for e in cands:
            m = _platform_match(e.get("platform"), args.platform)
            if m is None:
                continue
            e = dict(e)
            e["platform_match"] = m
            tagged.append(e)
        result["platform_filtered_out"] = len(cands) - len(tagged)
        cands = tagged

        # 讀取端天花板：去重 + 相關性/recency 排序 + top-N。與 registry 大小脫鉤——
        # 不管累積多少筆，都只把「最相關的前 N 筆」拿去驗證/回給 AI，避免資料越多餵越多。
        result["total_candidates"] = len(cands)
        cands = _rank_and_cap(cands, args.q, args.limit)

        emit_rows = []
        for e in cands:
            name = e.get("name", "")
            result["checked"] += 1
            # 沒給 repo-path 就不驗（只回候選，planner 自行驗）；給了就用前先驗
            if not args.repo_path:
                result["verified"].append(e)
                continue
            if _function_still_exists(name, args.repo_path):
                e2 = dict(e); e2["status"] = "verified"
                result["verified"].append(e2)
                emit_rows.append(_emit_row(e, name, "verified"))
            else:
                result["stale"].append(name)
                emit_rows.append(_emit_row(e, name, "stale"))
        result["ok"] = bool(result["verified"])

        if args.emit and emit_rows:
            try:
                with open(args.emit, "a", encoding="utf-8") as f:
                    for row in emit_rows:
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
            except Exception:
                pass
    except Exception:
        pass  # fail-safe：任何錯都回空、不擋 planner

    # 讀取收據：撈到空的也要寫（收據記的是「有沒有去問」；後端抽風不該變成過不了的 gate）
    if registry_read_receipt is not None and args.case:
        registry_read_receipt.write(
            kind="flow", case=args.case, platform=args.platform,
            query={"q": args.q, "kind": args.kind, "repo": args.repo},
            n=len(result.get("verified") or []), endpoint=PATH)

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

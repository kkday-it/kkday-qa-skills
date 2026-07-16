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


def _get_candidates(platform: str, kind: str, q: str, repo: str) -> list:
    """從後端 GET 候選；失敗回 []（呼叫端會退本地 registry）。"""
    params = {"platform": platform, "repo": repo}
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
        if platform not in ("", "any") and e.get("platform") not in (platform, "any"):
            continue
        if kind and e.get("kind") != kind:
            continue
        if ql and ql not in (e.get("name", "") + " " + e.get("purpose", "")).lower():
            continue
        out.append(e)
    return out


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


def main() -> int:
    p = argparse.ArgumentParser(description="Get + verify reusable flows (non-LLM)")
    p.add_argument("--q", default="", help="關鍵字（對 name/purpose 子字串）")
    p.add_argument("--kind", default="", help="setup_flow|test_step|helper|fixture")
    p.add_argument("--platform", default="any", help="app|web|mweb|api|any")
    p.add_argument("--repo", default="kkday-QA-automation")
    p.add_argument("--repo-path", default="", help="框架 repo 本機路徑（驗證用；不給則跳過驗證只回候選）")
    p.add_argument("--registry", default="", help="本地 fallback registry.json")
    p.add_argument("--emit", default="", help="把驗證結果寫成 jsonl（供 send_flow_registry 回寫 status）")
    args = p.parse_args()

    result = {"ok": False, "query": vars(args), "verified": [], "stale": [], "checked": 0}
    try:
        cands = _get_candidates(args.platform, args.kind, args.q, args.repo)
        if not cands and args.registry:
            cands = _local_fallback(args.registry, args.platform, args.kind, args.q)

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
                emit_rows.append({**{k: e.get(k) for k in (
                    "id", "name", "kind", "purpose", "location", "signature",
                    "example", "platform", "repo")}, "status": "verified"})
            else:
                result["stale"].append(name)
                emit_rows.append({"id": e.get("id"), "name": name, "kind": e.get("kind"),
                                  "platform": e.get("platform"), "repo": e.get("repo"),
                                  "status": "stale"})
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

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Flow Registry 寫回端（POST，非侵入、與 kkday-qa-tools MCP 無關）

qa-case-planner grep repo 發現 / 確認一個可重用 flow 後，把它 append 到 jsonl；這支 POST 到
ai_studio 的 /api/qa-automation/flow-registry，供跨人共享、少重複 grep。設計完全比照
send_locator_registry.py（fail-safe、retry 5 次、無 PII）。

**後端只是共享層、非真理**：這裡送的 flow 只是「某人某時在 repo 看過的可重用做法」，附
last_verified / status。取回端（get_verified_flow.py）拿回後一律「用前先驗」（grep 確認 function
名還在），驗不過標 stale 重挖。

用法：
    python3 send_flow_registry.py --infile /tmp/flow_results.jsonl [--purge]

每行 JSON 欄位（* 必填）：
    name*      可重用 flow 的真實 function / step 名
    kind       setup_flow | test_step | helper | fixture（預設 setup_flow）
    purpose    語意（這 flow 幹嘛）
    location   file:line 或 module 路徑
    signature  參數簽名
    example    使用範例
    platform   app | web | mweb | api | any（預設 any）
    repo       預設 kkday-QA-automation
    id / last_verified / status（預設 verified）
"""
import argparse
import json
import os
import sys
import time
import urllib.request

BASE = os.getenv("AI_STUDIO_BASE", "http://autotest-service.sit.kkday.com:8081/ai_studio")
PATH = "/api/qa-automation/flow-registry"
MAX_RETRIES = 5
BASE_BACKOFF = 0.5

# operator / client_user 身分抽到共用 telemetry_identity；取不到就 fail-safe 回退。
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from telemetry_identity import resolve_operator, resolve_client_user
    OPERATOR = resolve_operator()
    _CLIENT_USER = resolve_client_user()
except Exception:
    OPERATOR = os.getenv("KKDAY_TOOLS_USER_NAME", "kkday_qa_mcp")
    try:
        import socket
        _CLIENT_USER = f"{os.getlogin()}@{socket.gethostname()}"
    except Exception:
        _CLIENT_USER = "unknown"


def _post_once(payload: dict, timeout: float = 4.0) -> bool:
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{BASE}{PATH}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= getattr(r, "status", r.getcode()) < 300
    except Exception:
        return False


def _send_with_retry(payload: dict) -> bool:
    for attempt in range(1, MAX_RETRIES + 1):
        if _post_once(payload):
            return True
        if attempt < MAX_RETRIES:
            time.sleep(attempt * BASE_BACKOFF)
    return False


def _normalize(row: dict) -> dict:
    keys = (
        "id", "name", "kind", "purpose", "location", "signature",
        "example", "platform", "repo", "last_verified", "status",
    )
    out = {k: row[k] for k in keys if k in row}
    out.setdefault("kind", "setup_flow")
    out.setdefault("platform", "any")
    out.setdefault("repo", "kkday-QA-automation")
    out.setdefault("status", "verified")
    out["operator"] = OPERATOR
    out["client_user"] = _CLIENT_USER
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Send flow-registry telemetry (fail-safe)")
    p.add_argument("--infile", required=True, help="flow 結果 jsonl 路徑")
    p.add_argument("--purge", action="store_true", help="全部送完後刪除結果檔")
    args = p.parse_args()

    sent = failed = 0
    try:
        if not os.path.isfile(args.infile):
            return 0
        with open(args.infile, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        for ln in lines:
            try:
                row = json.loads(ln)
                if not row.get("name"):  # 必填：name
                    continue
            except Exception:
                continue
            if _send_with_retry(_normalize(row)):
                sent += 1
            else:
                failed += 1
        if args.purge:
            try:
                os.remove(args.infile)
            except Exception:
                pass
    except Exception:
        pass  # 絕對 fail-safe

    if sys.stdout.isatty():
        print(f"[flow-registry] sent={sent} failed(gave up after {MAX_RETRIES})={failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

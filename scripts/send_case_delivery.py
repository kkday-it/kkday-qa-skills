#!/usr/bin/env python3
"""Case 交付記錄（#5 的地基）—— batch flow 每交付一個 case 就記一筆。

問題（#5）：case 被 batch flow 判「綠/達標」後就沒人管了。產品改版讓它慢慢壞（test rot）、
或 bug 綠了卻漏到線上（escaped defect）——都沒機制回頭。要能回頭，第一步是**留下交付記錄**：
哪個 case、涵蓋哪些平台、驗了哪些斷言、對應哪個 PR/commit、什麼時候判綠的。有這筆，
detect_test_rot.py（後來壞了？）和 link_escaped_defect.py（綠了卻出事？反查是哪個 case 給了
假信心）才有東西可查，才接得回 harness 的 escaped-defect 迴路（false_confidence.jsonl）。

雙寫：
  1. **本地 ledger**（預設 ~/.claude/harness/case_delivery.jsonl，與 false_confidence.jsonl 同窩）
     —— 一定寫得到，後端掛也不影響 #5 下游能用。
  2. **POST 後端**（/api/qa-automation/case-delivery）—— 共享/dashboard 用，fail-safe（掛了靜默 skip）。

用法：
    python3 send_case_delivery.py --infile /tmp/delivery.jsonl [--ledger PATH] [--no-post]

每行 JSON（* 必填）：
    caseid*     KQT-T#####
    platforms   已達標交付的平台清單（如 ["web","mweb"]）
    delivered   bool（預設 true）
    min_runs    flaky 防護跑幾次（1=未開）
    pr_url      對應 PR（有就填，供 escaped-defect 反查對到 PR）
    commit      對應 commit sha
    assertions  關鍵斷言摘要（供日後判「綠了卻沒抓到」）
    traceability step→assertion 可追溯表
    repo        預設 kkday-QA-automation
"""
import argparse
import json
import os
import sys
import time
import urllib.request

BASE = os.getenv("AI_STUDIO_BASE", "http://autotest-service.sit.kkday.com:8081/ai_studio")
PATH = "/api/qa-automation/case-delivery"
MAX_RETRIES = 5
BASE_BACKOFF = 0.5
DEFAULT_LEDGER = os.path.expanduser("~/.claude/harness/case_delivery.jsonl")

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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


def _post_once(payload, timeout=4.0):
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(f"{BASE}{PATH}", data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= getattr(r, "status", r.getcode()) < 300
    except Exception:
        return False


def _send_with_retry(payload):
    for attempt in range(1, MAX_RETRIES + 1):
        if _post_once(payload):
            return True
        if attempt < MAX_RETRIES:
            time.sleep(attempt * BASE_BACKOFF)
    return False


def _normalize(row, now):
    keys = ("caseid", "platforms", "delivered", "min_runs", "pr_url", "commit",
            "assertions", "traceability", "repo")
    out = {k: row[k] for k in keys if k in row}
    out.setdefault("delivered", True)
    out.setdefault("repo", "kkday-QA-automation")
    out.setdefault("min_runs", 1)
    out["ts"] = now
    out["operator"] = OPERATOR
    out["client_user"] = _CLIENT_USER
    return out


def _append_ledger(path, rows):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def main():
    p = argparse.ArgumentParser(description="Send/record case delivery (雙寫 ledger + 後端，fail-safe)")
    p.add_argument("--infile", required=True, help="交付結果 jsonl 路徑")
    p.add_argument("--ledger", default=DEFAULT_LEDGER, help=f"本地 ledger（預設 {DEFAULT_LEDGER}）")
    p.add_argument("--no-post", action="store_true", help="只寫本地 ledger、不 POST 後端")
    p.add_argument("--purge", action="store_true", help="送完刪 infile")
    args = p.parse_args()

    now = int(time.time())
    rows, sent, failed = [], 0, 0
    try:
        if not os.path.isfile(args.infile):
            return 0
        with open(args.infile, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        for ln in lines:
            try:
                row = json.loads(ln)
                if not row.get("caseid"):
                    continue
            except Exception:
                continue
            norm = _normalize(row, now)
            rows.append(norm)
            if not args.no_post:
                if _send_with_retry(norm):
                    sent += 1
                else:
                    failed += 1
        # 本地 ledger 是 #5 下游的真理來源，一定寫（後端只是共享層）
        ledger_ok = _append_ledger(args.ledger, rows) if rows else True
        if args.purge:
            try:
                os.remove(args.infile)
            except Exception:
                pass
    except Exception:
        pass  # 絕對 fail-safe

    if sys.stdout.isatty():
        print(f"[case-delivery] ledger={len(rows)}筆({'ok' if ledger_ok else 'FAIL'}) "
              f"posted={sent} failed={failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

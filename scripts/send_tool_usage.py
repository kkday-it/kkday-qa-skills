#!/usr/bin/env python3
"""把「工具使用量」遙測（tool_usage）POST 到 ai_studio 的 /api/qa-automation/tool-usage。

跟 send_case_fidelity.py 同一套設計（fail-safe、retry 5 次、無 PII），差別是這條是
**使用面**：由 emit_tool_usage.py 在「工具一叫用當下」就寫一筆到 jsonl，這支通常掛
Stop hook 背景送出。即使使用者中途放棄、沒交付，那筆 outcome=invoked 也送得出去。

用法：
    python3 send_tool_usage.py --infile /tmp/tool_usage.jsonl [--purge]

每行 jsonl 欄位（白名單）：
    run_id, tool, outcome, interactive, case_ids, platforms, case_count, note
"""
import argparse
import json
import os
import socket
import sys
import time
import urllib.request

BASE = os.getenv("AI_STUDIO_BASE", "http://autotest-service.sit.kkday.com:8081/ai_studio")
PATH = "/api/qa-automation/tool-usage"
OPERATOR = os.getenv("KKDAY_TOOLS_USER_NAME", "kkday_qa_mcp")
MAX_RETRIES = 5
BASE_BACKOFF = 0.5  # 秒；第 n 次失敗後 sleep n*BASE_BACKOFF

try:
    _CLIENT_USER = f"{os.getlogin()}@{socket.gethostname()}"
except Exception:
    _CLIENT_USER = "unknown"


def _post_once(payload: dict, timeout: float = 4.0) -> bool:
    """送一次；2xx 視為成功。任何例外回 False。"""
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
    """最多 MAX_RETRIES 次；成功即回 True，全失敗回 False。"""
    for attempt in range(1, MAX_RETRIES + 1):
        if _post_once(payload):
            return True
        if attempt < MAX_RETRIES:
            time.sleep(attempt * BASE_BACKOFF)
    return False


def _normalize(row: dict) -> dict:
    """挑白名單欄位，補預設，附 operator / client_user。不帶任何額外資料。"""
    keys = (
        "run_id", "tool", "outcome", "interactive",
        "case_ids", "platforms", "case_count", "note",
    )
    out = {k: row[k] for k in keys if k in row}
    out["operator"] = OPERATOR
    out["client_user"] = _CLIENT_USER
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Send tool-usage telemetry (fail-safe)")
    p.add_argument("--infile", required=True, help="usage jsonl 路徑")
    p.add_argument("--purge", action="store_true", help="全部送完後刪除結果檔")
    args = p.parse_args()

    sent = failed = 0
    try:
        if not os.path.isfile(args.infile):
            return 0  # 沒有檔就靜默結束
        with open(args.infile, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        for ln in lines:
            try:
                row = json.loads(ln)
                if not row.get("tool"):
                    continue
            except Exception:
                continue
            if _send_with_retry(_normalize(row)):
                sent += 1
            else:
                failed += 1  # 5 次都失敗，放棄這筆
        if args.purge:
            try:
                os.remove(args.infile)
            except Exception:
                pass
    except Exception:
        pass  # 絕對 fail-safe：不干擾主流程

    if sys.stdout.isatty():
        print(f"[tool-usage] sent={sent} failed(gave up after {MAX_RETRIES})={failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Case Fidelity 品質遙測發送端（非侵入、與 kkday-qa-tools MCP 無關）

讀主對話產出的 fidelity 結果檔（每行一筆 JSON = 一個 case×平台的 fidelity 紀錄），
POST 到 ai_studio 的 /api/qa-automation/case-fidelity。設計原則：

- **不接原本的 MCP**：這是獨立腳本，通常由 Claude Code 的 Stop hook 在背景執行，
  不會出現在 agent 對話裡、不觸發權限提示。
- **不影響使用者**：整體 fail-safe，任何錯誤都吞掉、exit 0，絕不干擾主流程或印雜訊。
- **retry 5 次、全失敗就放棄**：每筆最多送 5 次（遞增退避），都失敗就跳過該筆、繼續下一筆。
- **只送品質指標 + operator**（無 PII）。為揭露式遙測，見 repo NOTICE。

用法：
    python3 send_case_fidelity.py --infile /path/to/fidelity_results.jsonl [--purge]

每行 JSON 欄位（缺的用預設）：
    run_id, case_id(必), platform, mode, interactive,
    step_total, step_covered, assertion_total, assertion_covered,
    fidelity, confidence, fix_rounds, recommend, blocked_reason
"""
import argparse
import json
import os
import socket
import sys
import time
import urllib.request

BASE = os.getenv("AI_STUDIO_BASE", "http://autotest-service.sit.kkday.com:8081/ai_studio")
PATH = "/api/qa-automation/case-fidelity"
MAX_RETRIES = 5
BASE_BACKOFF = 0.5  # 秒；第 n 次失敗後 sleep n*BASE_BACKOFF

# operator / client_user 身分抽到共用 telemetry_identity（三支 sender 共用，避免漂移）；
# 取不到就 fail-safe 回退舊行為，不讓遙測因「取身分」而失敗。
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from telemetry_identity import resolve_operator, resolve_client_user
    OPERATOR = resolve_operator()
    _CLIENT_USER = resolve_client_user()
except Exception:
    OPERATOR = os.getenv("KKDAY_TOOLS_USER_NAME", "kkday_qa_mcp")
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
        "run_id", "case_id", "platform", "mode", "interactive",
        "step_total", "step_covered", "assertion_total", "assertion_covered",
        "fidelity", "confidence", "fix_rounds", "recommend", "blocked_reason",
    )
    out = {k: row[k] for k in keys if k in row}
    out["operator"] = OPERATOR
    out["client_user"] = _CLIENT_USER
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Send case-fidelity telemetry (fail-safe)")
    p.add_argument("--infile", required=True, help="fidelity 結果 jsonl 路徑")
    p.add_argument("--purge", action="store_true", help="全部送完後刪除結果檔")
    args = p.parse_args()

    sent = failed = 0
    try:
        if not os.path.isfile(args.infile):
            return 0  # 沒有結果檔就靜默結束
        with open(args.infile, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        for ln in lines:
            try:
                row = json.loads(ln)
                if not row.get("case_id"):
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

    # 只在手動執行（tty）時印摘要，hook 背景執行不印
    if sys.stdout.isatty():
        print(f"[case-fidelity] sent={sent} failed(gave up after {MAX_RETRIES})={failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

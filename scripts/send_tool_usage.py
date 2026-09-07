#!/usr/bin/env python3
"""把「工具使用量」遙測（tool_usage）POST 到 ai_studio 的 /api/qa-automation/tool-usage。

跟 send_case_fidelity.py 同一套設計（fail-safe、retry 5 次、無 PII），差別是這條是
**使用面**：由 emit_tool_usage.py 在「工具一叫用當下」就寫一筆到 jsonl，這支通常掛
Stop hook 背景送出。即使使用者中途放棄、沒交付，那筆 outcome=invoked 也送得出去。

用法：
    python3 send_tool_usage.py --infile /tmp/tool_usage.jsonl [--purge] [--hooks-rev N]

每行 jsonl 欄位（白名單）：
    run_id, tool, outcome, interactive, case_ids, platforms, case_count, note,
    request_text（使用者原始輸入）, stage（停在哪階段）, blocked_reason
    ⚠️ request_text 可能含 PII → 僅 admin-only dashboard 呈現，見 docs/telemetry.md 揭露

版本兩欄（後台「誰還在跑舊版」用，見 docs/telemetry.md）：
    skills_version  本 clone 的 git short HEAD ——「**磁碟上**是哪一版」
    hooks_rev       由 `--hooks-rev` 帶入 ——「**這個 session 正在生效**的 hook 是哪一世代」

🔴 兩欄缺一不可，因為它們可以不一致，而那個不一致正是要找的東西：Claude Code 在啟動時把
hook 清單讀成快照，之後 `sync_hooks.py` 再改寫 `settings.json` 也不會被重讀。所以「已經
pull 到最新（skills_version 新）、但還在用開 session 當時那批 hook（hooks_rev 舊）」的人，
會漏掉新加的把關而**完全沒有症狀**。`--hooks-rev` 的值是 sync_hooks 寫進指令字串的，
所以這支收到的數字必然來自快照，不會被磁碟上的新版本蓋掉——這是唯一測得到快照世代的方法。
沒帶 `--hooks-rev` → 0 = 那個快照比「版本號上線」還早。
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
MAX_RETRIES = 5
BASE_BACKOFF = 0.5  # 秒；第 n 次失敗後 sleep n*BASE_BACKOFF

# operator / client_user 身分抽到共用 telemetry_identity（三支 sender 共用，避免漂移）；
# 取不到就 fail-safe 回退舊行為，不讓遙測因「取身分」而失敗。
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from telemetry_identity import (
        resolve_client_user,
        resolve_operator,
        resolve_skills_version,
    )
    OPERATOR = resolve_operator()
    _CLIENT_USER = resolve_client_user()
    _SKILLS_VERSION = resolve_skills_version()
except Exception:
    OPERATOR = os.getenv("KKDAY_TOOLS_USER_NAME", "kkday_qa_mcp")
    try:
        _CLIENT_USER = f"{os.getlogin()}@{socket.gethostname()}"
    except Exception:
        _CLIENT_USER = "unknown"
    _SKILLS_VERSION = ""

# 由 --hooks-rev 覆寫（見 main()）。模組層預設 0＝沒帶＝快照比版本號上線更早。
_HOOKS_REV = 0


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
        # 診斷欄位（admin-only 呈現，見 docs/telemetry.md）：原始輸入 / 停在哪階段 / blocked 原因
        "request_text", "stage", "blocked_reason",
    )
    out = {k: row[k] for k in keys if k in row}
    out["operator"] = OPERATOR
    out["client_user"] = _CLIENT_USER
    out["skills_version"] = _SKILLS_VERSION   # 磁碟版本
    out["hooks_rev"] = _HOOKS_REV             # 快照世代（見檔頭）
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Send tool-usage telemetry (fail-safe)")
    p.add_argument("--infile", required=True, help="usage jsonl 路徑")
    p.add_argument("--purge", action="store_true", help="全部送完後刪除結果檔")
    p.add_argument("--hooks-rev", type=int, default=0,
                   help="hook 定義世代；由 sync_hooks.py 寫進 hook 指令字串，"
                        "所以收到的值來自「這個 session 的快照」而非磁碟。0＝快照比版本號上線更早")
    args = p.parse_args()
    global _HOOKS_REV
    _HOOKS_REV = args.hooks_rev

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

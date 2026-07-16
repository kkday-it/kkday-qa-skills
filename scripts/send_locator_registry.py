#!/usr/bin/env python3
"""
Locator Registry 遙測發送端（POST，非侵入、與 kkday-qa-tools MCP 無關）

讀主對話產出的 locator 結果檔（每行一筆 JSON = 一個 locator×平台×環境的驗證紀錄），
POST 到 ai_studio 的 /api/qa-automation/locator-registry。設計原則完全比照
send_case_fidelity.py：

- **不接原本的 MCP**：獨立腳本，通常由 Claude Code 的 Stop hook 在背景執行，
  不會出現在 agent 對話裡、不觸發權限提示。
- **不影響使用者**：整體 fail-safe，任何錯誤都吞掉、exit 0，絕不干擾主流程或印雜訊。
- **retry 5 次、全失敗就放棄**：每筆最多送 5 次（遞增退避），都失敗就跳過該筆、繼續下一筆。
- **只送白名單欄位 + operator**（無 PII）。為揭露式遙測，見 docs/telemetry.md。

重要語意：**後端只是儲存與共享層，不是真理來源。** 這裡送上去的 locator 只是「某人某時在某環境
驗證過的候選」，附 last_verified 時間戳與 status。取回端（fetch_locator_registry.py）拿回來後
一律要「用前先驗」，驗不過標 stale 重挖 —— 後端幫的是跨人共享 + 趨勢，不是讓大家盲信快取。

用法：
    # 掃目錄逐檔送（Stop hook 用；per-process 檔並行安全）
    python3 send_locator_registry.py --indir /tmp/locator_results.d --purge
    # 或送單一檔
    python3 send_locator_registry.py --infile /path/to/locator_results.jsonl [--purge]

每行 JSON 欄位（缺的用預設；* 為必填）：
    id               —— 穩定 slug（如 ttd-landing-search-input-web-stage），供 remine 辨識
    element*         —— 元素語意名稱（如 things-to-do landing 搜尋框 input）
    page*            —— 所屬頁面語意 key（如 things-to-do-landing）
    component        —— 元件語意 key（如 landing-search-bar-input）
    flow             —— 流程/區域 key（如 things-to-do-search，供批次 GET）
    selectors*       —— 優先序候選陣列，每項 {"type":"css|xpath","value":"...","note":"..."}
    platform         —— web | mweb（預設 web）
    env              —— stage | prod（預設 stage）
    source           —— 來源 case id / 出處（如 KQT-T37931）
    last_verified    —— ISO8601 時間戳（缺則用當下 UTC）
    status           —— verified | stale（預設 verified）
    verify_url       —— 驗證當下用的 URL（可選，方便下次重驗）
"""
import argparse
import glob
import json
import os
import re
import socket
import sys
import time
import urllib.request
from datetime import datetime, timezone

# 現階段安全紅線：環境只接受 stage / sit0x / sit20x（比照 server _VALID_ENV_RE），禁 prod。
_VALID_ENV_RE = re.compile(r"stage|sit\d*")

BASE = os.getenv("AI_STUDIO_BASE", "http://autotest-service.sit.kkday.com:8081/ai_studio")
PATH = "/api/qa-automation/locator-registry"
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
    """挑白名單欄位，補預設，附 operator / client_user。不帶任何額外資料（無 PII）。"""
    keys = (
        "id", "element", "page", "component", "flow", "selectors",
        "semantic", "platform", "env", "source", "last_verified", "status",
        "verify_url",
    )
    out = {k: row[k] for k in keys if k in row}
    out.setdefault("platform", "web")
    out.setdefault("env", "stage")
    out.setdefault("status", "verified")
    out.setdefault("last_verified", datetime.now(timezone.utc).isoformat())
    out["operator"] = OPERATOR
    out["client_user"] = _CLIENT_USER
    return out


def _process_file(path: str, purge: bool) -> tuple:
    """送一個結果檔的每一筆；purge 時整檔讀完才刪自己這份（不碰別人正在寫的檔）。
    回 (sent, failed)。任何錯誤 fail-safe 吞掉。"""
    sent = failed = 0
    try:
        if not os.path.isfile(path):
            return 0, 0
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        for ln in lines:
            try:
                row = json.loads(ln)
                # 必填三件：element / page / selectors，缺一跳過
                if not (row.get("element") and row.get("page") and row.get("selectors")):
                    continue
            except Exception:
                continue
            # 現階段禁 prod：env 非 stage/sit 系列（含 prod）一律不送
            if not _VALID_ENV_RE.fullmatch(row.get("env") or "stage"):
                continue
            if _send_with_retry(_normalize(row)):
                sent += 1
            else:
                failed += 1  # 5 次都失敗，放棄這筆
        if purge:
            try:
                os.remove(path)
            except Exception:
                pass
    except Exception:
        pass  # 絕對 fail-safe：不干擾主流程
    return sent, failed


def main() -> int:
    p = argparse.ArgumentParser(description="Send locator-registry telemetry (fail-safe)")
    p.add_argument("--infile", default="", help="單一 locator 結果 jsonl 路徑")
    p.add_argument("--indir", default="",
                   help="結果目錄；掃其中所有 *.jsonl 逐檔送（per-process 檔，並行安全）。"
                        "與 --infile 擇一或並用。")
    p.add_argument("--purge", action="store_true", help="每個檔送完後刪除該檔")
    args = p.parse_args()

    # 收集待處理檔：--indir 的所有 *.jsonl + --infile（去重）
    targets = []
    if args.indir:
        try:
            targets.extend(sorted(glob.glob(os.path.join(args.indir, "*.jsonl"))))
        except Exception:
            pass
    if args.infile and args.infile not in targets:
        targets.append(args.infile)

    sent = failed = 0
    for path in targets:
        s, fl = _process_file(path, args.purge)
        sent += s
        failed += fl

    # 只在手動執行（tty）時印摘要，hook 背景執行不印
    if sys.stdout.isatty():
        print(f"[locator-registry] files={len(targets)} sent={sent} "
              f"failed(gave up after {MAX_RETRIES})={failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

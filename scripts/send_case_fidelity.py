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

相容：reviewer 若寫的是 step_coverage / assertion_coverage（"N/M" 字串），
normalize 會解析成 step_covered/step_total、assertion_covered/assertion_total 整數，
避免欄位名漂移導致 dashboard 覆蓋率恆為 0%。
"""
import argparse
import glob
import hashlib
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


def _parse_ratio(val):
    """把 "3/5" 這種字串拆成 (covered, total) 整數；拆不出回 (None, None)。"""
    try:
        if isinstance(val, str) and "/" in val:
            a, b = val.split("/", 1)
            return int(a.strip()), int(b.strip())
    except Exception:
        pass
    return None, None


def _normalize(row: dict) -> dict:
    """挑白名單欄位，補預設，附 operator / client_user。不帶任何額外資料。

    相容層：reviewer 寫的是 step_coverage / assertion_coverage（"N/M" 字串），
    若沒有分開的整數欄位，就從 N/M 解析出 *_covered / *_total —— 否則這些覆蓋率
    數字會因不在白名單而被整包丟掉，dashboard 覆蓋率恆為 0%（本次要修的 bug）。
    """
    keys = (
        "run_id", "case_id", "platform", "mode", "interactive",
        "step_total", "step_covered", "assertion_total", "assertion_covered",
        "fidelity", "confidence", "fix_rounds", "recommend", "blocked_reason",
    )
    out = {k: row[k] for k in keys if k in row}

    # 相容：step_coverage / assertion_coverage("N/M") → 補出整數欄位（已有整數則不覆蓋）
    for prefix, cov_key in (("step", "step_coverage"), ("assertion", "assertion_coverage")):
        covered_k, total_k = f"{prefix}_covered", f"{prefix}_total"
        if covered_k not in out or total_k not in out:
            covered, total = _parse_ratio(row.get(cov_key))
            if covered is not None:
                out.setdefault(covered_k, covered)
                out.setdefault(total_k, total)

    out["operator"] = OPERATOR
    out["client_user"] = _CLIENT_USER
    return out


def _fingerprint(row: dict) -> str:
    """內容指紋：對 row 的穩定 JSON（sort_keys）取 sha256。內容不變 → 指紋不變 →
    已送過就不重送；reviewer 重跑產出不同結果（例如後來修成 pass）指紋才會變、才再送。"""
    try:
        blob = json.dumps(row, sort_keys=True, ensure_ascii=False)
    except Exception:
        blob = repr(row)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _ledger_path(target_path: str) -> str:
    """已送指紋帳本：放 target 所在目錄的隱藏檔。刻意不用 *.jsonl 副檔名，
    才不會被 _collect_targets 當成待送結果檔掃進來。"""
    return os.path.join(os.path.dirname(os.path.abspath(target_path)), ".sent_fingerprints")


def _load_ledger(path: str) -> set:
    """讀已送指紋集合。讀不到就回空集合（fail-safe：寧可重送，不可炸）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {ln.strip() for ln in f if ln.strip()}
    except FileNotFoundError:
        return set()
    except Exception:
        return set()


def _append_ledger(path: str, fp: str) -> None:
    """把一筆指紋寫進帳本。fail-safe：寫不進去就算了（頂多下次重送，不影響主流程）。"""
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(fp + "\n")
    except Exception:
        pass


def _process_file(path: str, purge: bool, dedup: bool = True) -> tuple:
    """送一個 fidelity 結果檔的每一筆；purge 才刪。回 (sent, failed, skipped)。fail-safe。

    dedup=True（預設）：送成功的 row 記內容指紋到帳本；下次讀到**內容相同**的同一筆就
    跳過不重送。修的是「非 pass 的 case 檔不會被 gate 清（永遠不 pass），於是每次
    Stop hook 都重讀同一個未 purge 的檔、把同一筆一直往 dashboard 灌」的重複列問題。
    送失敗（5 次都掛）的不記帳本 → 下次會再試。"""
    sent = failed = skipped = 0
    try:
        if not os.path.isfile(path):
            return 0, 0, 0
        ledger_path = _ledger_path(path)
        seen = _load_ledger(ledger_path) if dedup else set()
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        for ln in lines:
            try:
                row = json.loads(ln)
                if not row.get("case_id"):
                    continue
            except Exception:
                continue
            fp = _fingerprint(row)
            if dedup and fp in seen:
                skipped += 1  # 內容沒變、已送過 → 不重送
                continue
            if _send_with_retry(_normalize(row)):
                sent += 1
                if dedup:
                    _append_ledger(ledger_path, fp)
                    seen.add(fp)
            else:
                failed += 1  # 5 次都失敗，放棄這筆（不記帳本，下次再試）
        if purge:
            try:
                os.remove(path)
            except Exception:
                pass
    except Exception:
        pass  # 絕對 fail-safe：不干擾主流程
    return sent, failed, skipped


def _collect_targets(indir: str, infile: str) -> list:
    """待處理檔清單：--indir 的所有 *.jsonl（排序）+ --infile（去重）。"""
    targets = []
    if indir:
        try:
            targets.extend(sorted(glob.glob(os.path.join(indir, "*.jsonl"))))
        except Exception:
            pass
    if infile and infile not in targets:
        targets.append(infile)
    return targets


def main() -> int:
    p = argparse.ArgumentParser(description="Send case-fidelity telemetry (fail-safe)")
    p.add_argument("--infile", default="", help="單一 fidelity 結果 jsonl 路徑")
    p.add_argument("--indir", default="",
                   help="結果目錄；掃其中所有 *.jsonl 逐檔送（per case×平台 一檔）。")
    p.add_argument("--purge", action="store_true",
                   help="每個檔送完後刪除。⚠️ 若該結果同時是忠實度 gate 的輸入（Stop hook 情境），"
                        "**不要 purge**——生命週期交給 gate：pass 才刪，否則會在 gate 擋下時把它的"
                        "輸入刪掉、下輪變成『找不到結果檔』。")
    p.add_argument("--no-dedup", action="store_true",
                   help="關閉內容去重（預設開啟）。預設會記已送指紋，未 purge 的檔內容不變就不重送，"
                        "避免非 pass 的 case 被 Stop hook 每次重讀重送、灌爆 dashboard。"
                        "後端遺失資料需強制重送時才用這個。")
    args = p.parse_args()

    dedup = not args.no_dedup
    targets = _collect_targets(args.indir, args.infile)
    sent = failed = skipped = 0
    for path in targets:
        s, fl, sk = _process_file(path, args.purge, dedup)
        sent += s
        failed += fl
        skipped += sk

    # 只在手動執行（tty）時印摘要，hook 背景執行不印
    if sys.stdout.isatty():
        print(f"[case-fidelity] files={len(targets)} sent={sent} "
              f"skipped(dup)={skipped} failed(gave up after {MAX_RETRIES})={failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

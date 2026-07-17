#!/usr/bin/env python3
"""Registry stale sweep —— 定期清 flow / locator registry 的過期 entry。

問題（#7）：get_verified_flow / locator_valve 是「讀的時候」驗、跳過 stale——保護了「讀」，
但 stale entry 不會自清，噪音只增不減。這支負責「主動清」。

stale 判準（保守，寧可留不可誤刪）：
  - flow registry：`name`（function 名）已不在框架 repo（grep `def <name>` 查無）→ stale；
                   或 last_verified 超過 --max-age-days → stale。
  - locator registry：selector 有效性要跑瀏覽器、不在本腳本範圍，故只用 age——
                      last_verified 超過 --max-age-days → stale。
  - status 已是 stale/deprecated 的 entry：一律清。

安全（比照 CLAUDE.md：destructive 操作需人審）：
  - 預設 **dry-run**：只印「會刪什麼、為什麼」，不動檔案。
  - --apply 才真的刪；刪前寫 <registry>.bak 備份（另 git 也可還原）。
  - 無法 grep（repo 路徑沒給/不存在）時，flow 的 function 檢查**跳過**，只用 age——
    不因「查不到」就誤刪。

用法：
  python3 sweep_registry.py --registry both --repo-path /path/to/kkday-QA-automation   # dry-run
  python3 sweep_registry.py --registry flow --repo-path ... --apply                    # 真的清
  python3 sweep_registry.py --registry locator --max-age-days 120 --json
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
DEFAULT_FLOW = os.path.join(_ROOT, "flow_registry", "registry.json")
DEFAULT_LOCATOR = os.path.join(_ROOT, "locator_registry", "registry.json")
DEFAULT_MAX_AGE_DAYS = 90


def _parse_ts(v):
    if not v:
        return None
    s = str(v).strip().replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _age_days(entry, now):
    dt = _parse_ts(entry.get("last_verified"))
    if dt is None:
        return None
    return (now - dt).total_seconds() / 86400.0


def is_stale(entry, kind, now, max_age_days, func_exists=None):
    """純判定（好測）：回 (stale: bool, reason: str)。

    func_exists：flow registry 用的「function 是否還在 repo」查詢結果——
      True/False=已查；None=沒查（repo 沒給），此時只用 age、不因查不到而刪。
    """
    status = str(entry.get("status", "")).lower()
    if status in ("stale", "deprecated"):
        return True, f"status={status}"
    if kind == "flow" and func_exists is False:
        return True, f"function `{entry.get('name')}` 已不在 repo（grep `def` 查無）"
    age = _age_days(entry, now)
    if age is not None and age > max_age_days:
        return True, f"last_verified {round(age)} 天前 > {max_age_days} 天上限"
    return False, ""


def _function_still_exists(name, repo_path):
    if not name or not repo_path or not os.path.isdir(repo_path):
        return None  # 沒法查 → 不參與判定
    src = os.path.join(repo_path, "QATest", "src")
    target = src if os.path.isdir(src) else repo_path
    try:
        r = subprocess.run(["grep", "-rInsq", rf"def {re.escape(name)}\b", target], timeout=20)
        return r.returncode == 0
    except Exception:
        return None


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data, data.get("entries", []) or []
    if isinstance(data, list):
        return {"entries": data}, data
    return {"entries": []}, []


def sweep_one(path, kind, now, max_age_days, repo_path, apply):
    if not os.path.isfile(path):
        return {"registry": kind, "path": path, "error": "檔案不存在", "removed": [], "kept": 0}
    doc, entries = _load(path)
    kept, removed = [], []
    for e in entries:
        func_exists = None
        if kind == "flow":
            func_exists = _function_still_exists(e.get("name"), repo_path)
        stale, reason = is_stale(e, kind, now, max_age_days, func_exists)
        if stale:
            removed.append({"id": e.get("id") or e.get("name"), "reason": reason})
        else:
            kept.append(e)
    if apply and removed:
        try:
            with open(path + ".bak", "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        doc["entries"] = kept
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
    return {"registry": kind, "path": path, "total": len(entries),
            "removed": removed, "kept": len(kept), "applied": bool(apply and removed)}


def main():
    p = argparse.ArgumentParser(description="Registry stale sweep（預設 dry-run）")
    p.add_argument("--registry", choices=("flow", "locator", "both"), default="both")
    p.add_argument("--flow-path", default=DEFAULT_FLOW)
    p.add_argument("--locator-path", default=DEFAULT_LOCATOR)
    p.add_argument("--repo-path", default="", help="框架 repo（flow function 存在性檢查用；不給則只用 age）")
    p.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    p.add_argument("--apply", action="store_true", help="真的刪 stale（預設只 dry-run）；刪前寫 .bak")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    now = datetime.now(timezone.utc)
    reports = []
    if args.registry in ("flow", "both"):
        reports.append(sweep_one(args.flow_path, "flow", now, args.max_age_days, args.repo_path, args.apply))
    if args.registry in ("locator", "both"):
        reports.append(sweep_one(args.locator_path, "locator", now, args.max_age_days, args.repo_path, args.apply))

    if args.json:
        print(json.dumps({"dry_run": not args.apply, "reports": reports}, ensure_ascii=False, indent=2))
        return 0

    mode = "APPLY（已寫回）" if args.apply else "DRY-RUN（未動檔案）"
    print(f"=== Registry sweep [{mode}] max_age={args.max_age_days}天 ===")
    for r in reports:
        if r.get("error"):
            print(f"\n[{r['registry']}] {r['path']}: {r['error']}")
            continue
        print(f"\n[{r['registry']}] {r['path']}")
        print(f"  總 {r['total']} → 保留 {r['kept']}，清除 {len(r['removed'])}")
        for x in r["removed"]:
            print(f"    - {x['id']}：{x['reason']}")
    if not args.apply and any(r.get("removed") for r in reports):
        print("\n（dry-run）確認無誤後加 --apply 真的清；會先寫 <registry>.bak 備份。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

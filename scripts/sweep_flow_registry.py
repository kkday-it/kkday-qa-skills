#!/usr/bin/env python3
"""
Flow Registry 清理端（sweep）：把「用前先驗」驗不過的 stale entry 從 registry.json 清掉。

為什麼要這支：get_verified_flow.py（讀端）雖然「用前先驗」——grep 框架 repo 確認 function 名還在、
驗不過標 stale 並回退——但它只**標記**、不移除。registry.json 因此只長不清，stale entry 越積越多，
每次讀端 grep 的噪音與耗時都跟著漲。這支離線 sweep 定期把驗不過的 entry 真的清掉（或標記），
讓 registry 保持健康。

**離線維運用，不在 planner 熱路徑跑**（驗證要 grep 整個 repo，慢）。建議定期（如每週 / release 前）手動或
排程跑一次。驗證邏輯與 get_verified_flow.py 的 `_function_still_exists` 完全一致，行為對齊。

用法：
    # dry-run（預設，只印報告不寫檔）
    python3 sweep_flow_registry.py --repo-path <kkday-QA-automation 路徑>

    # 真的移除 stale 並寫回
    python3 sweep_flow_registry.py --repo-path <...> --apply

    # 保守模式：不移除，只把 stale 的 status 改成 "stale" 後寫回（保留紀錄）
    python3 sweep_flow_registry.py --repo-path <...> --apply --mark-only
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

THIS = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REGISTRY = os.path.normpath(
    os.path.join(THIS, "..", "flow_registry", "registry.json")
)


def _function_still_exists(name: str, repo_path: str) -> bool:
    """用前先驗：grep 框架 repo 確認該 function/step 名還在。

    與 get_verified_flow.py 同一套邏輯，兩端行為必須一致——讀端會標 stale 的，
    sweep 端就該清掉；讀端認得的，sweep 端就該留。
    """
    if not name or not repo_path or not os.path.isdir(repo_path):
        return False
    src = os.path.join(repo_path, "QATest", "src")
    target = src if os.path.isdir(src) else repo_path
    try:
        r = subprocess.run(
            ["grep", "-rInsq", rf"def {re.escape(name)}\b", target],
            timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return False


def sweep(entries, repo_path):
    """把 entries 分成 (kept, stale)。純函數、無副作用，好測。"""
    kept, stale = [], []
    for e in entries:
        if _function_still_exists(e.get("name", ""), repo_path):
            kept.append(e)
        else:
            stale.append(e)
    return kept, stale


def main() -> int:
    p = argparse.ArgumentParser(description="Sweep stale entries from flow-registry")
    p.add_argument(
        "--repo-path", required=True, help="框架 repo 本機路徑（驗證用；沒有就無法判 stale）"
    )
    p.add_argument("--registry", default=DEFAULT_REGISTRY, help="registry.json 路徑")
    p.add_argument(
        "--apply", action="store_true", help="真的寫回（預設 dry-run 只印報告不寫檔）"
    )
    p.add_argument(
        "--mark-only",
        action="store_true",
        help="保守模式：不移除，只把 stale 的 status 改成 'stale'（需搭 --apply）",
    )
    args = p.parse_args()

    if not os.path.isdir(args.repo_path):
        print(f"[sweep] repo-path 不存在，無法驗證：{args.repo_path}", file=sys.stderr)
        return 2
    if not os.path.isfile(args.registry):
        print(f"[sweep] registry 不存在：{args.registry}", file=sys.stderr)
        return 2

    with open(args.registry, "r", encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("entries", [])
    if not isinstance(entries, list):
        print("[sweep] registry.entries 不是陣列，格式異常", file=sys.stderr)
        return 2

    kept, stale = sweep(entries, args.repo_path)
    print(f"[sweep] total={len(entries)} kept={len(kept)} stale={len(stale)}")
    for e in stale:
        print(f"  STALE: {e.get('name')} @ {e.get('location')}")

    if not args.apply:
        print("[sweep] dry-run（未寫檔）；確認無誤後加 --apply 生效")
        return 0
    if not stale:
        print("[sweep] 無 stale，registry 已乾淨，不寫檔")
        return 0

    if args.mark_only:
        stale_names = {id(e) for e in stale}
        for e in entries:
            if id(e) in stale_names:
                e["status"] = "stale"
        data["entries"] = entries
    else:
        data["entries"] = kept
    data["updated_at"] = datetime.now(timezone.utc).isoformat()

    with open(args.registry, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    verb = "標記" if args.mark_only else "移除"
    print(f"[sweep] 已寫回：{verb} {len(stale)} 筆 stale")
    return 0


if __name__ == "__main__":
    sys.exit(main())

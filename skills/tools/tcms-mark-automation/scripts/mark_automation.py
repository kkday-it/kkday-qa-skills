#!/usr/bin/env python3
"""批次修改 TCMS case 的 automation_status。

支援三種來源：
  --suite-id N     Suite 底下所有 case
  --run-id N       TestRun 內的 case（unique case_id）
  --cases KQT-T…   逗號分隔 KQT-T ID
  --case-ids 1,2,3 逗號分隔數字 case_id

Dry-run 預設；加 --apply 才真的 PUT。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from urllib import error, request

DEFAULT_BASE = "http://autotest-service.sit.kkday.com:8081/tcms/api/v1"
DEFAULT_USER_ID = "ml09h4qj-l7bsikcns5m"  # Eden Lai admin
VALID_STATUSES = {"Automated", "Manual", "Not Set"}


def _token() -> str:
    tk = os.environ.get("TCMS_TOKEN")
    if tk:
        return tk.strip()
    p = os.path.expanduser("~/.cache/tcms_token")
    if os.path.exists(p):
        return open(p).read().strip()
    print("TCMS token not found (~/.cache/tcms_token or $TCMS_TOKEN)", file=sys.stderr)
    sys.exit(2)


def _get(url: str, hdrs: dict) -> object:
    req = request.Request(url, headers=hdrs)
    with request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "replace"), strict=False)


def _put(url: str, hdrs: dict, body: dict) -> int:
    req = request.Request(url, data=json.dumps(body).encode(), headers=hdrs, method="PUT")
    with request.urlopen(req, timeout=20) as r:
        return r.status


def _resolve_case_ids(args, hdrs_get: dict, base: str) -> list[int]:
    if args.case_ids:
        return [int(x) for x in args.case_ids.split(",") if x.strip()]
    if args.suite_id:
        cases = _get(f"{base}/cases/suite/{args.suite_id}", hdrs_get)
        return [c["id"] for c in cases]
    if args.run_id:
        results = _get(f"{base}/results/run/{args.run_id}", hdrs_get)
        return sorted({r["case_id"] for r in results if r.get("case_id")})
    if args.cases:
        # KQT-T → id via GET /cases/{external_id}
        ids = []
        for ext in args.cases.split(","):
            ext = ext.strip()
            if not ext:
                continue
            c = _get(f"{base}/cases/{ext}", hdrs_get)
            ids.append(c["id"])
        return ids
    print("must supply one of --suite-id / --run-id / --cases / --case-ids", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite-id", type=int)
    ap.add_argument("--run-id", type=int)
    ap.add_argument("--cases", help="逗號分隔 KQT-T ID")
    ap.add_argument("--case-ids", help="逗號分隔數字 case_id")
    ap.add_argument("--status", required=True, choices=sorted(VALID_STATUSES))
    ap.add_argument("--apply", action="store_true", help="加了才真的寫入")
    ap.add_argument("--tcms-base", default=DEFAULT_BASE)
    ap.add_argument("--tcms-user-id", default=DEFAULT_USER_ID)
    args = ap.parse_args()

    token = _token()
    hdrs_get = {"Authorization": f"Bearer {token}"}
    hdrs_put = {**hdrs_get, "X-User-Id": args.tcms_user_id, "Content-Type": "application/json"}

    ids = _resolve_case_ids(args, hdrs_get, args.tcms_base)
    print(f"resolved case_ids: {len(ids)}")

    todo = []
    breakdown_before = Counter()
    for cid in ids:
        c = _get(f"{args.tcms_base}/cases/{cid}", hdrs_get)
        cur = c.get("automation_status")
        breakdown_before[cur] += 1
        if cur != args.status:
            todo.append((cid, c.get("external_id"), cur, (c.get("title") or "")[:60]))
    print(f"current breakdown: {dict(breakdown_before)}")
    print(f"need update: {len(todo)}")
    for row in todo[:15]:
        print(" ", row)
    if len(todo) > 15:
        print(f"  ... +{len(todo)-15} more")

    if not args.apply:
        print("\n(dry-run) 加 --apply 才會真的寫入。")
        return

    ok = fail = 0
    for cid, ext, _, _ in todo:
        try:
            status = _put(f"{args.tcms_base}/cases/{cid}", hdrs_put, {"automation_status": args.status})
            if status == 200:
                ok += 1
            else:
                fail += 1
                print(f"  {cid} ({ext}) HTTP {status}")
        except error.HTTPError as e:
            fail += 1
            print(f"  {cid} ({ext}) {e.code}: {e.read()[:200]}")
        except Exception as e:
            fail += 1
            print(f"  {cid} ({ext}) {e}")
    print(f"\napplied ok={ok} fail={fail}")

    # verify
    after = Counter()
    for cid in ids:
        c = _get(f"{args.tcms_base}/cases/{cid}", hdrs_get)
        after[c.get("automation_status")] += 1
    print(f"verified: {dict(after)}")


if __name__ == "__main__":
    main()
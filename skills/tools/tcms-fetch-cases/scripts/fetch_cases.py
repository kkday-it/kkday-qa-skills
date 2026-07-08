#!/usr/bin/env python3
"""
從 TCMS run 抓指定 assignee 的 cases，或直接接受 KQT-T ID 清單，
輸出 JSON 供 qa-automation-writer 使用。
Steps 直接從 TCMS /cases/{id} 取，無需 Zephyr。
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# 並行抓 case 詳情的併發數（避免 run 內 case 多時 N+1 sequential 卡慢）
MAX_WORKERS = 8

TCMS_BASE = "http://autotest-service.sit.kkday.com:8081/tcms/api/v1"


def tcms_get(path: str) -> dict | list:
    import urllib.request
    url = f"{TCMS_BASE}{path}"
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read())


def find_user(name_or_email: str) -> dict:
    users = tcms_get("/users/")
    name_lower = name_or_email.lower()
    for u in users:
        if (
            name_lower in u.get("full_name", "").lower()
            or name_lower in u.get("email", "").lower()
            or name_lower in u.get("username", "").lower()
        ):
            return u
    raise SystemExit(f"找不到使用者：{name_or_email}\n可用：{[u['full_name'] for u in users]}")


def fetch_case_detail(case_id: int) -> dict:
    """從 TCMS /cases/{id} 取完整 case 資料（含 steps）。"""
    try:
        return tcms_get(f"/cases/{case_id}")
    except Exception as e:
        print(f"  警告：無法取得 case {case_id} 詳情：{e}", file=sys.stderr)
        return {}


def find_case_by_external_id(external_id: str) -> dict | None:
    """透過 /cases?external_id= 或搜尋取得 case。"""
    try:
        result = tcms_get(f"/cases/?external_id={external_id}")
        if isinstance(result, list) and result:
            return result[0]
        if isinstance(result, dict) and result.get("id"):
            return result
    except Exception:
        pass
    # fallback: search all cases
    try:
        cases = tcms_get(f"/cases/?search={external_id}")
        if isinstance(cases, list):
            for c in cases:
                if c.get("external_id") == external_id:
                    return c
    except Exception:
        pass
    return None


def build_entry(case_id: int, result_row: dict | None = None) -> dict:
    detail = fetch_case_detail(case_id)
    tc = result_row.get("test_case", {}) if result_row else {}
    steps = detail.get("steps", [])
    return {
        "result_id": result_row.get("id") if result_row else None,
        "case_id": case_id,
        "external_id": detail.get("external_id") or tc.get("external_id"),
        "title": detail.get("title") or tc.get("title"),
        "priority": detail.get("priority") or tc.get("priority"),
        "status": result_row.get("status") if result_row else None,
        "suite_id": detail.get("suite_id"),
        "preconditions": detail.get("preconditions", ""),
        "steps": [
            {
                "order": s.get("order"),
                "action": s.get("action", ""),
                "data": s.get("data", ""),
                "expected_result": s.get("expected_result", ""),
            }
            for s in steps
        ],
    }


def main():
    p = argparse.ArgumentParser(description="Fetch TCMS cases for implementation")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--run-id", type=int, help="TCMS Run ID")
    g.add_argument("--cases", help="手動貼 KQT-T ID，逗號分隔（如 KQT-T1234,KQT-T5678）")
    p.add_argument("--assignee", help="Full name / email（搭配 --run-id 用）")
    p.add_argument("--out", default="/tmp/tcms_cases.json")
    args = p.parse_args()

    output = []

    # 模式 A：手動貼 KQT-T ID
    if args.cases:
        keys = [k.strip() for k in args.cases.split(",") if k.strip()]
        print(f"📋 手動模式：{len(keys)} 個 case")

        def fetch_by_key(key: str) -> tuple:
            case = find_case_by_external_id(key)
            return key, (build_entry(case["id"]) if case else None)

        # 並行抓取；ex.map 保留輸入順序，輸出結果順序不受併發影響
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            for key, entry in ex.map(fetch_by_key, keys):
                if entry is None:
                    print(f"  ⚠️  找不到 {key}")
                    continue
                print(f"  {key} {entry['title']}  ({len(entry['steps'])} steps)")
                output.append(entry)

    # 模式 B：從 TCMS Run 抓 cases（--assignee 為可選的「篩人」條件）
    else:
        results = tcms_get(f"/results/run/{args.run_id}")
        if args.assignee:
            user = find_user(args.assignee)
            print(f"✅ 找到使用者：{user['full_name']} (id={user['id']})")
            selected = [r for r in results if r.get("assignee_id") == user["id"]]
            print(f"📋 Run {args.run_id} 共 {len(results)} 個 case，{user['full_name']} 負責 {len(selected)} 個")
        else:
            selected = results
            print(f"📋 Run {args.run_id} 共 {len(results)} 個 case（未篩人，全撈）")

        rows = [r for r in selected if r.get("case_id")]
        # 並行抓取每個 case 詳情，解決 N+1 sequential 呼叫
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            for entry in ex.map(lambda r: build_entry(r["case_id"], r), rows):
                print(f"  {entry['external_id']} {entry['title']}  ({len(entry['steps'])} steps)")
                output.append(entry)

    Path(args.out).write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\n💾 已儲存至 {args.out}")


if __name__ == "__main__":
    main()

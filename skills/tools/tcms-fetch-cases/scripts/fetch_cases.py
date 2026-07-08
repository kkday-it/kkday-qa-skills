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


def load_project_index(project_id: int) -> dict:
    """一次撈整個 project 的 cases，建 external_id -> case（含 steps）的索引。

    TCMS 沒有「用 external_id 查單筆」的 GET 端點（/cases/?external_id= 會 405），
    唯一能拿到 external_id -> case_id 對應的方式就是撈整包 project 再 client 端 filter。
    好在 /cases/project/{id} 回傳已含 steps，所以貼 ID 模式一次請求就夠、不需 N+1。
    """
    cases = tcms_get(f"/cases/project/{project_id}")
    if not isinstance(cases, list):
        raise SystemExit(f"/cases/project/{project_id} 回傳非預期格式：{type(cases).__name__}")
    return {c["external_id"]: c for c in cases if c.get("external_id")}


def build_entry(case_id: int, result_row: dict | None = None, detail: dict | None = None) -> dict:
    # detail 已備妥（如貼 ID 模式從 project 索引拿）就直接用，省一次 /cases/{id}
    if detail is None:
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
    p.add_argument("--project-id", type=int, default=1, help="貼 ID 模式用的 TCMS project（預設 1＝KKday QA）")
    p.add_argument("--out", default="/tmp/tcms_cases.json")
    args = p.parse_args()

    output = []

    # 模式 A：手動貼 KQT-T ID —— 撈整個 project 建索引後 client 端 filter
    if args.cases:
        keys = [k.strip() for k in args.cases.split(",") if k.strip()]
        print(f"📋 手動模式：{len(keys)} 個 case")
        index = load_project_index(args.project_id)  # 一次 API call，含 steps
        for key in keys:
            case = index.get(key)
            if not case:
                print(f"  ⚠️  找不到 {key}")
                continue
            entry = build_entry(case["id"], detail=case)  # steps 已在索引，免再打
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

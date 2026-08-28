#!/usr/bin/env python3
"""TCMS 寫入端 CLI — clone 既有 case / 從 JSON spec 建新 case。

**這是例外工具。** 日常建立與修改 case 一律由人直接在 TCMS 網頁操作；本 script 只處理
「一次要建很多支」「以某支為範本大量複製」這類網頁一支一支點不合理的情境。
讀取請用 `tcms-fetch-cases`。

打的是團隊自己的 TCMS：http://autotest-service.sit.kkday.com:8081/tcms/api/v1
外部 Zephyr Scale 在寫入這條路上已經沒有角色（TCMS 只留 POST /cases/import/zephyr 匯入）。

Auth:
  Authorization: Bearer <token>
  GET 端點權限寬鬆（連 placeholder token 都通），POST/PUT/DELETE 必須真 token。

  Token 解析順序：
    1. $TCMS_TOKEN
    2. ~/.cache/tcms_token（快取）
    3. Secret Data Management（UI 在 :8080，API 在 :8000）→ 寫回快取
       GET :8000/api/v1/data/?env=stage&service=tcms_skill_token&key=tcms_skill_token_stage
       Bearer $AUTOMATION_TOKEN；回 [{... "value": "{\"token\": \"…\"}"}]

  401 時**自動回 secret service 重取一次**再重試（快取過期的常見情況）；
  再 401 才停下來要人處理 —— 不無限重試。

Subcommands:
  clone <CASE_REF> --name N     複製一支 case（可換 suite / 改欄位），來源可用 KQT-T63751
  create <spec.json>            從 JSON spec 建 1~多支
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://autotest-service.sit.kkday.com:8081/tcms/api/v1"
TOKEN_PATH = os.path.expanduser("~/.cache/tcms_token")
# Secret Data Management：UI 掛在 :8080，取值 API 在 :8000（與 mcp_servers/.../scm_supplier.py
# 同一套）。token 的正本在這裡，~/.cache/tcms_token 只是快取，過期就回來重取。
SECRET_HOST = os.getenv("SECRET_SERVICE_HOST", "http://autotest-service.sit.kkday.com")
SECRET_PARAMS = {
    "env": os.getenv("TCMS_SECRET_ENV", "stage"),
    "service": os.getenv("TCMS_SECRET_SERVICE", "tcms_skill_token"),
    "key": os.getenv("TCMS_SECRET_KEY", "tcms_skill_token_stage"),
}
AUTOMATION_TOKEN = os.getenv("AUTOMATION_TOKEN", "8b9dfbac-e863-4078-95e9-c2cc03abe84f")

# 人工補救路徑（secret service 也拿不到時才走）。兩份既有文件寫的頁面路徑不同
# （gherkin-to-tcms 寫 /tcms/account#api-tokens，reference-tcms-api-token memory 寫
# /tcms/settings#api-tokens），兩個都印，讓人自己點得到。
TOKEN_PAGE = ("http://autotest-service.sit.kkday.com:8081/tcms/account#api-tokens"
              "（若 404 改試 /tcms/settings#api-tokens）")

# TestCaseCreate 允許帶的欄位（OpenAPI 3.1，KK TCMS 1.5.0）。
# title / suite_id 必填，其餘後端各有預設值。external_id 刻意不在此列——
# 留空後端才會自動生 KQT-Txxx；自己指定會撞 409。
OPTIONAL_FIELDS = (
    "status",              # 預設 Active
    "lifecycle_status",    # 預設 Draft
    "description",
    "severity",            # 預設 Normal
    "priority",            # 預設 Not Set
    "type",
    "layer",
    "behavior",            # 預設 Not Set
    "automation_status",   # 預設 Manual
    "is_flaky",            # 預設 False
    "muted",               # 預設 False
    "preconditions",
    "postconditions",
    "tags",                # 字串包 JSON array，見 _as_json_string
    "labels",              # 同上
    "jira_keys",
    "default_owner_id",
)

STEP_FIELDS = ("action", "data", "expected_result", "order")


def _as_json_string(val):
    """tags / labels 在 TCMS 是**字串包 JSON array**，不是 list。

    實際 case 長這樣：labels = '["Automated_PC", "UI"]'
    寫 spec 的人自然會寫成 list，這裡幫忙轉，已經是字串就原樣放行。
    """
    if val is None or isinstance(val, str):
        return val
    return json.dumps(val, ensure_ascii=False)


def fetch_token_from_secret_service() -> str:
    """回 secret service 取 token 正本，順便寫回本機快取。"""
    qs = urllib.parse.urlencode(SECRET_PARAMS)
    req = urllib.request.Request(
        f"{SECRET_HOST}:8000/api/v1/data/?{qs}",
        headers={"authorization": f"Bearer {AUTOMATION_TOKEN}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        sys.exit(f"❌ 向 secret service 取 TCMS token 失敗：{e}\n"
                 f"   （{SECRET_PARAMS}）\n"
                 f"   人工補救：到 {TOKEN_PAGE} 產 token 寫入 {TOKEN_PATH}")
    if not rows:
        sys.exit(f"❌ secret service 查無資料：{SECRET_PARAMS}")
    # value 是「字串包 JSON」，內層 {"token": "..."}；萬一哪天改成裸字串也接得住
    raw = rows[0].get("value")
    try:
        tok = json.loads(raw)["token"]
    except (TypeError, ValueError, KeyError):
        tok = (raw or "").strip()
    if not tok:
        sys.exit(f"❌ secret service 回的 value 解不出 token：{str(raw)[:120]}")
    try:
        os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
        with open(TOKEN_PATH, "w") as f:
            f.write(tok)
        os.chmod(TOKEN_PATH, 0o600)
    except OSError:
        pass  # 快取寫不進去不影響這次執行
    return tok


def load_token(force_refresh: bool = False) -> str:
    if not force_refresh:
        tok = os.getenv("TCMS_TOKEN")
        if tok:
            return tok.strip()
        if os.path.exists(TOKEN_PATH):
            cached = open(TOKEN_PATH).read().strip()
            if cached:
                return cached
    return fetch_token_from_secret_service()


def _headers(token: str) -> dict:
    # 不送 X-User-Id：後端不看它，認證只認 Bearer token，
    # history 的 user_id 也一律記成 token 擁有者，指定不了。
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }


class _Auth:
    """持有目前這輪用的 token，401 時可就地換掉，讓後續 case 接著推不用重跑。"""

    def __init__(self):
        self.token = load_token()
        self.refreshed = False

    def refresh(self) -> bool:
        """回 secret service 換新的。一輪只換一次，避免壞 token 打成無限迴圈。"""
        if self.refreshed:
            return False
        self.refreshed = True
        self.token = load_token(force_refresh=True)
        return True


def _request(method: str, path: str, auth: "_Auth", body: dict | None = None):
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    while True:
        req = urllib.request.Request(
            f"{BASE}{path}", data=payload, headers=_headers(auth.token), method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", errors="replace")[:400]
            if e.code == 401 and auth.refresh():
                print("⚠️  401 —— 快取 token 疑似過期，已向 secret service 重取，重試中…",
                      file=sys.stderr)
                continue
            if e.code == 401:
                sys.exit("❌ 換過新 token 仍 401。可能 secret service 上的 token 本身也過期了。"
                         f"人工補救：到 {TOKEN_PAGE} 產 token 寫入 {TOKEN_PATH}")
            sys.exit(f"❌ {method} {path} → {e.code}: {text}")
        except Exception as e:  # noqa: BLE001 — 網路層什麼都可能噴，一律終止並印原因
            sys.exit(f"❌ {method} {path} → {e}")


def build_case_payload(spec: dict, suite_id: int) -> dict:
    """把 spec dict 轉成 TestCaseCreate body。"""
    if not spec.get("title"):
        sys.exit(f"❌ case 缺 title：{json.dumps(spec, ensure_ascii=False)[:200]}")
    payload = {"title": spec["title"], "suite_id": suite_id}
    for f in OPTIONAL_FIELDS:
        if spec.get(f) is not None:
            payload[f] = _as_json_string(spec[f]) if f in ("tags", "labels") else spec[f]
    steps = []
    for i, st in enumerate(spec.get("steps") or [], 1):
        if not st.get("action"):
            sys.exit(f"❌ step #{i} 缺 action（TestStepCreate 必填）")
        item = {k: st[k] for k in STEP_FIELDS if st.get(k) is not None}
        item.setdefault("order", i)
        steps.append(item)
    if steps:
        payload["steps"] = steps
    return payload


def cmd_clone(args):
    auth = _Auth()
    src = _request("GET", f"/cases/{args.case_ref}", auth)

    spec = {f: src.get(f) for f in OPTIONAL_FIELDS}
    spec["title"] = args.name
    # steps 要去掉 id / test_case_id，那是來源 case 的，帶過去會被當更新目標
    spec["steps"] = [
        {k: st.get(k) for k in STEP_FIELDS if st.get(k) is not None}
        for st in (src.get("steps") or [])
    ]
    for k, v in (
        ("suite_id", args.suite_id),
        ("priority", args.priority),
        ("automation_status", args.automation_status),
    ):
        if v is not None:
            spec[k] = v
    if args.draft:
        spec["lifecycle_status"] = "Draft"

    suite_id = spec.pop("suite_id", None) or src.get("suite_id")
    if not suite_id:
        sys.exit("❌ 無法決定 suite_id，請用 --suite-id 指定")

    created = _request("POST", "/cases/", auth, build_case_payload(spec, int(suite_id)))
    print(json.dumps({
        "id": created.get("id"),
        "external_id": created.get("external_id"),
        "suite_id": created.get("suite_id"),
        "title": created.get("title"),
        "steps_copied": len(created.get("steps") or []),
        "source": args.case_ref,
    }, ensure_ascii=False, indent=2))


def cmd_create(args):
    auth = _Auth()
    spec = json.load(open(args.spec_json))

    # 兩種格式都收：單支 {title:...} 或整批 {suite_id, labels, cases:[...]}
    cases = spec.get("cases") if isinstance(spec.get("cases"), list) else [spec]
    suite_id = args.suite_id or spec.get("suite_id")
    if not suite_id:
        sys.exit("❌ 缺 suite_id（--suite-id 或 spec 的 suite_id）。"
                 "從 TCMS 網址列 /tcms/repository?suite=XXXX 抓")
    suite_id = int(suite_id)

    shared = {k: spec.get(k) for k in ("labels", "tags") if spec.get(k) is not None}

    print(f"準備推 {len(cases)} 個 case 到 suite_id={suite_id}")
    ok, results = 0, []
    for i, c in enumerate(cases, 1):
        merged = {**shared, **{k: v for k, v in c.items() if v is not None}}
        created = _request("POST", "/cases/", auth, build_case_payload(merged, suite_id))
        ok += 1
        results.append({"id": created.get("id"), "external_id": created.get("external_id"),
                        "title": created.get("title")})
        print(f"  [{i:02d}/{len(cases)}] ✓ id={created.get('id')} "
              f"ext={created.get('external_id')} | {(c.get('title') or '')[:50]}")

    print(f"\n總計 {ok}/{len(cases)} 成功")
    print(json.dumps(results, ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser(
        prog="tcms_create_case",
        description="TCMS 寫入端（例外工具；日常建立/修改請直接在 TCMS 網頁操作）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("clone", help="複製既有 case（來源可用 KQT-Txxx 或內部 id）")
    c.add_argument("case_ref", help="來源 case，如 KQT-T63751")
    c.add_argument("--name", required=True, help="新 case 的 title")
    c.add_argument("--suite-id", type=int, help="換 suite（預設沿用來源的）")
    c.add_argument("--priority", help="覆寫 priority")
    c.add_argument("--automation-status", help="覆寫 automation_status")
    c.add_argument("--draft", action="store_true",
                   help="強制 lifecycle_status=Draft（複製出來待人確認時用）")
    c.set_defaults(func=cmd_clone)

    n = sub.add_parser("create", help="從 JSON spec 建 1~多支")
    n.add_argument("spec_json", help="spec JSON 路徑")
    n.add_argument("--suite-id", type=int, help="覆寫 spec 裡的 suite_id")
    n.set_defaults(func=cmd_create)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
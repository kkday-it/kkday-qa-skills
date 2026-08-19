"""把 ai_studio test-suite report URL 解析成「可直接派工給 qa-case-automator 的工單」。

輸入是使用者從瀏覽器複製的 report URL（或裸 uuid）：

  # 單一 case
  http://autotest-service.sit.kkday.com:8081/ai_studio/test-suites/report?uuid=<run_uuid>&caseid=KQT-T7562
  # 整批 fail（不帶 caseid）
  http://autotest-service.sit.kkday.com:8081/ai_studio/test-suites/report?uuid=<run_uuid>&reportFail=1

解析鏈路（platform 是 report 本身沒有、必須繞一層才拿得到的關鍵欄位）：

  URL --uuid--> /api/test-suite/cached-report/<run_uuid>   → cases + terminal_output + fail_function
           └--> data.test_suite_uuid
                └--> /api/test-suite/cached-suite-detail/<suite_uuid> → platform / device / environment

Examples
--------
# 人看的工單（預設）
python3 resolve_report.py "<url>"

# 給程式吃的 JSON
python3 resolve_report.py "<url>" --json

# 附上完整 terminal_output（預設截斷到 2000 字）
python3 resolve_report.py "<url>" --full
"""
import argparse
import json
import re
import sys
import urllib.parse
import urllib.request

API_BASE_DEFAULT = "http://autotest-service.sit.kkday.com:8081/ai_studio/api"

# suite detail 的 platform 是給人看的字串；qatest --platform 吃小寫
PLATFORM_MAP = {
    "android": "android",
    "ios": "ios",
    "web": "web",
    "mweb": "mweb",
    "m-web": "mweb",
}

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def parse_url(raw):
    """吃完整 URL 或裸 uuid，回 (run_uuid, caseid|None)。"""
    raw = raw.strip()
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query)
    run_uuid = (qs.get("uuid") or [None])[0]
    caseid = (qs.get("caseid") or [None])[0]
    if not run_uuid:
        m = UUID_RE.search(raw)
        if not m:
            sys.exit(f"URL 裡找不到 run uuid：{raw}")
        run_uuid = m.group(0)
    return run_uuid, caseid


def fetch(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def fetch_report(api_base, run_uuid):
    d = fetch(f"{api_base}/test-suite/cached-report/{run_uuid}")
    if d.get("status") != "ok" or not d.get("data"):
        sys.exit(f"report API 沒有回 ok：{json.dumps(d, ensure_ascii=False)[:300]}")
    return d


def fetch_suite(api_base, suite_uuid):
    """suite detail 掛掉不該讓整支失敗——platform 缺就標 unknown 讓人補。"""
    if not suite_uuid:
        return {}
    try:
        d = fetch(f"{api_base}/test-suite/cached-suite-detail/{suite_uuid}")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 取 suite detail 失敗（platform 待補）：{exc}", file=sys.stderr)
        return {}
    return d.get("data") or {}


def normalize_platform(suite):
    raw = (suite.get("platform") or "").strip()
    return PLATFORM_MAP.get(raw.lower(), raw.lower() or "unknown")


def parse_steps(case):
    """report 的 steps 是一串 JSON 字串，攤成 dict 方便閱讀。"""
    out = []
    for s in case.get("steps") or []:
        try:
            out.append(json.loads(s))
        except (TypeError, ValueError):
            out.append({"name": str(s)})
    return out


def parse_fail_function(case):
    raw = case.get("fail_function")
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {"name": str(raw)}


def collect_cases(report, caseid, include_pass):
    cases = report["data"].get("cases") or {}
    out = []
    for group, items in cases.items():
        for it in items or []:
            it = dict(it, group=group)
            if caseid:
                if it.get("name") == caseid:
                    out.append(it)
            elif include_pass or it.get("status") == "Fail":
                out.append(it)
    return out


def build(raw_url, api_base, include_pass=False):
    run_uuid, caseid = parse_url(raw_url)
    report = fetch_report(api_base, run_uuid)
    data = report["data"]
    suite = fetch_suite(api_base, data.get("test_suite_uuid"))
    platform = normalize_platform(suite)

    picked = collect_cases(report, caseid, include_pass)
    if caseid and not picked:
        sys.exit(f"report {run_uuid} 裡沒有 {caseid}（可能還沒跑到，或 case 不在這個 suite）")

    all_cases = [c for v in (data.get("cases") or {}).values() for c in v or []]
    counts = {}
    for c in all_cases:
        counts[c.get("status", "?")] = counts.get(c.get("status", "?"), 0) + 1

    return {
        "run": {
            "uuid": run_uuid,
            "run_id": data.get("id"),
            "suite_uuid": data.get("test_suite_uuid"),
            "suite_title": suite.get("title"),
            "platform": platform,
            "device": suite.get("device"),
            "environment": suite.get("environment"),
            "team": data.get("team"),
            "status": data.get("status"),
            "still_running": bool(report.get("refreshing")) or data.get("end_time") is None,
            "start_time": data.get("start_time"),
            "end_time": data.get("end_time"),
            "counts": counts,
            "total_seen": len(all_cases),
        },
        "mode": "single" if caseid else "batch-fail",
        "requested_case": caseid,
        "tasks": [
            {
                "case_id": c.get("name"),
                "platform": platform,
                "status": c.get("status"),
                "group": c.get("group"),
                "description": c.get("description"),
                "exc_time": c.get("exc_time"),
                "fail_function": parse_fail_function(c),
                "steps": parse_steps(c),
                "terminal_output": c.get("terminal_output") or "",
                "log_file_path": c.get("log_file_path") or "",
                "build_version": c.get("build_version"),
            }
            for c in picked
        ],
    }


def render(result, full=False, trim=2000):
    r = result["run"]
    lines = []
    running = "  ⚠️ 這份 report 還在跑，fail 清單之後可能再增加" if r["still_running"] else ""
    lines.append(f"# Report {r['uuid']}")
    lines.append(f"suite: {r['suite_title']}  (run id {r['run_id']})")
    lines.append(
        f"platform: {r['platform']} | device: {r['device']} | env: {r['environment']} | team: {r['team']}"
    )
    lines.append(f"status: {r['status']} | 已見 {r['total_seen']} cases {r['counts']}{running}")
    lines.append("")
    lines.append(f"## 派工 {len(result['tasks'])} 件（mode={result['mode']}）")
    for t in result["tasks"]:
        lines.append("")
        lines.append(f"### {t['case_id']} {t['platform']}  [{t['status']}]")
        lines.append(f"- {t['description']}")
        ff = t["fail_function"]
        if ff:
            lines.append(f"- 失敗函式: {ff.get('name')}  (case line {ff.get('lineNum')})")
        if t["steps"]:
            chain = " → ".join(s.get("name", "?") for s in t["steps"])
            lines.append(f"- 步驟鏈: {chain}")
        if t["log_file_path"]:
            lines.append(f"- runner log（在跑測機器上）: {t['log_file_path']}")
        if t["terminal_output"]:
            body = t["terminal_output"]
            if not full and len(body) > trim:
                body = body[: trim // 4] + "\n...(中略)...\n" + body[-(trim * 3 // 4) :]
            lines.append("- terminal_output:")
            lines.append("```")
            lines.append(body)
            lines.append("```")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="report URL 或裸 run uuid")
    ap.add_argument("--api-base", default=API_BASE_DEFAULT)
    ap.add_argument("--json", action="store_true", help="輸出 JSON 而非人看的工單")
    ap.add_argument("--full", action="store_true", help="terminal_output 不截斷")
    ap.add_argument("--include-pass", action="store_true", help="批次模式也列 Pass 的 case")
    args = ap.parse_args()

    result = build(args.url, args.api_base.rstrip("/"), include_pass=args.include_pass)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render(result, full=args.full))


if __name__ == "__main__":
    main()

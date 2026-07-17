#!/usr/bin/env python3
"""在「工具一叫用當下」append 一筆使用紀錄到 jsonl，供 Stop hook 之後送出。

重點：**與成敗脫鉤**。主對話/skill/workflow 一開始跑就呼叫這支寫一筆 outcome=invoked，
之後即使卡住 / 使用者放棄 / 沒跑完 review，那筆「有人用過」也已經落地。跑完若要更新
狀態，可再 emit 一筆 outcome=delivered/blocked（同 run_id 串起來）。

絕對 fail-safe：任何錯都吞掉、絕不影響主流程（純本地 append，不連網；送出交給
send_tool_usage.py + Stop hook）。

用法：
    python3 emit_tool_usage.py --tool automate-tcms-cases \
        --run-id <id> --outcome invoked \
        --cases KQT-T37931,KQT-T37934 --platforms ios,web [--interactive] [--note ""]
    # 或用數量而非明細：--case-count 8
    # 診斷欄位（admin-only）：--request-text "<使用者原始輸入>" --stage automate \
    #   --blocked-reason "stage 登入 500(blocked-environment)"
預設寫到 /tmp/tool_usage.jsonl，可用環境變數 TOOL_USAGE_FILE 覆寫。
"""
import argparse
import json
import os
import sys

OUTFILE = os.getenv("TOOL_USAGE_FILE", "/tmp/tool_usage.jsonl")


def main() -> int:
    try:
        p = argparse.ArgumentParser(description="emit a tool-usage row (local append, fail-safe)")
        p.add_argument("--tool", required=True)
        p.add_argument("--run-id", default="")
        p.add_argument("--outcome", default="invoked",
                       choices=["invoked", "delivered", "blocked", "abandoned"])
        p.add_argument("--cases", default="", help="逗號分隔的 case id")
        p.add_argument("--platforms", default="", help="逗號分隔的平台")
        p.add_argument("--case-count", type=int, default=0)
        p.add_argument("--interactive", action="store_true")
        p.add_argument("--note", default="")
        # 診斷用（admin-only dashboard 呈現，見 docs/telemetry.md）：
        p.add_argument("--request-text", default="",
                       help="觸發本次的使用者原始輸入（逐字），供 admin 診斷『使用者到底打了什麼、卡在哪』")
        p.add_argument("--stage", default="",
                       help="停在哪階段：fetch|plan|confirm|automate|gate|report")
        p.add_argument("--blocked-reason", default="",
                       help="blocked/abandoned 時的簡短原因，如『stage 登入 500(blocked-environment)』『缺商品 oid』")
        a = p.parse_args()

        cases = [c.strip() for c in a.cases.split(",") if c.strip()]
        platforms = [x.strip() for x in a.platforms.split(",") if x.strip()]
        row = {
            "tool": a.tool,
            "run_id": a.run_id,
            "outcome": a.outcome,
            "interactive": a.interactive,
            "case_ids": cases,
            "platforms": platforms,
            "case_count": a.case_count or len(cases),
            "note": a.note,
            "request_text": a.request_text,
            "stage": a.stage,
            "blocked_reason": a.blocked_reason,
        }
        with open(OUTFILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 絕不影響主流程
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""per-platform 交付 gate（確定性、非 LLM）

驗一個 TCMS case 的**每個 tag 平台**是否真的交付 —— 不信 automator 自評。

框架：一份 case（platform=web 用 web_playwright driver）天生能跑 web+mweb；mobile driver 能跑
android+ios。`limit_test_platform` 才把 case「限死只跑單一平台、其餘 Skip」。故判「能跑某平台」：
  - web / mweb : platform=web 的 entry，且沒被 limit_test_platform 限成別的
  - android / ios : mobile(AppRegression) 的 entry，且沒被 limit 限死
「交付」＝ 能跑該平台 **且** 該平台真的 `--platform X` 跑過 pass（--results，來自 qatest 那行 `0 failed`）。
注意：gate 只判「能跑 + 跑過 pass」；「test_step 是否真對該平台有效（非硬套 web）」是 fidelity review 的事。

缺任一 tag 平台 → 列出未交付平台、exit 1（擋下，不准算完成）。全到齊才 exit 0。

用法：
  python3 check_platform_delivery.py --caseid KQT-T37931 \
      --tags web,mweb,android,ios --repo /path/to/kkday-QA-automation \
      [--results /tmp/per_platform_results.jsonl]

--results jsonl 每行：{"caseid":"KQT-T37931","platform":"web","status":"pass"}
"""
import argparse
import glob
import json
import os
import sys

try:
    import yaml
except ImportError:
    yaml = None

_ALL = ("web", "mweb", "android", "ios")


def _covered_platforms(repo: str, caseid: str) -> set:
    """掃 automation repo 的 case yaml，回該 caseid 實際涵蓋（有正確註冊）的平台集合。"""
    covered = set()
    yaml_root = os.path.join(repo, "QATestData", "cases", "yaml")
    for path in glob.glob(os.path.join(yaml_root, "**", "*.yaml"), recursive=True):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception:
            continue
        if not isinstance(data, dict) or caseid not in data:
            continue
        entry = data[caseid]
        if not isinstance(entry, dict):
            continue
        plat = str(entry.get("platform", "")).lower()

        # limit_test_platform：把此 case「限死」只跑某平台（framework common.py 的
        # limit_test_platform：當前 platform 不符就 raise Skip）。沒有它 = 該 driver 的平台都能跑。
        limit = None
        pre = entry.get("pre-condition", [])
        if isinstance(pre, list):
            for step in pre:
                if isinstance(step, dict) and "limit_test_platform" in step:
                    lp = step.get("limit_test_platform")
                    if isinstance(lp, dict):
                        limit = str(lp.get("test_platform", "")).lower()

        # driver 天生能跑的平台群（一份 test_step 用 if platform==X 分支處理各平台差異）
        if plat == "web":
            group = {"web", "mweb"}        # web_playwright driver
        elif plat in ("android", "ios", "app", "mobile"):
            group = {"android", "ios"}     # mobile(Appium) driver
        else:
            group = {plat} if plat else set()

        if limit:
            covered.add(limit)             # 被 limit 限死 → 只該單一平台能跑
        else:
            covered |= group               # 無 limit → 該 driver 的平台都能跑
    return covered


def _passed_platforms(results_path: str, caseid: str) -> set:
    """讀 per-platform 測試結果 jsonl，回該 caseid 有 pass 的平台集合。"""
    passed = set()
    if not results_path or not os.path.isfile(results_path):
        return passed
    with open(results_path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                row = json.loads(ln)
            except Exception:
                continue
            if row.get("caseid") == caseid and str(row.get("status", "")).lower() == "pass":
                p = str(row.get("platform", "")).lower()
                if p:
                    passed.add(p)
    return passed


def platform_verdict(results, min_runs: int = 1) -> str:
    """純判定（好測）：依 case×平台「依時間排序的每次跑結果」判交付狀態。

    results：["pass"/"fail",...]（parse_qatest_log 回的 `results`）。
    min_runs<=1：只看最後一次（維持舊行為，不做 flaky 偵測）。
    min_runs>1（flaky 防護）：看最近 min_runs 次——
      - 這窗口內有任何 fail → 'flaky'（一次過≠穩定過，擋下）
      - 全 pass 但跑不足 min_runs 次 → 'insufficient'（沒跑滿，不算過）
      - 跑滿 min_runs 次且全 pass → 'pass'
    回：'pass' | 'fail' | 'flaky' | 'insufficient' | 'not-run'
    """
    if not results:
        return "not-run"
    if min_runs <= 1:
        return "pass" if results[-1] == "pass" else "fail"
    window = results[-min_runs:]
    if any(r != "pass" for r in window):
        return "flaky"
    if len(window) < min_runs:
        return "insufficient"
    return "pass"


def _verdicts_from_log(log_path: str, caseid: str, platforms, min_runs: int):
    """用 parse_qatest_log 對每平台客觀判（不靠 automator 自報）。
    回 (passed:set, flaky:list, insufficient:list)。"""
    passed, flaky, insufficient = set(), [], []
    if not log_path or not os.path.isfile(log_path):
        return passed, flaky, insufficient
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from parse_qatest_log import parse as _parse
    except Exception:
        return passed, flaky, insufficient
    for plat in platforms:
        seq = _parse(log_path, caseid, plat).get("results", [])
        v = platform_verdict(seq, min_runs)
        if v == "pass":
            passed.add(plat)
        elif v == "flaky":
            flaky.append(plat)
        elif v == "insufficient":
            insufficient.append(plat)
    return passed, flaky, insufficient


def main() -> int:
    p = argparse.ArgumentParser(description="per-platform 交付 gate（確定性）")
    p.add_argument("--caseid", required=True, help="TCMS case id，如 KQT-T37931")
    p.add_argument("--tags", required=True,
                   help="該 case tag 要求的平台，逗號分隔（web,mweb,android,ios）")
    p.add_argument("--repo", required=True, help="kkday-QA-automation repo 路徑")
    p.add_argument("--results", default="", help="（次選）per-platform 結果 jsonl（automator 自報）")
    p.add_argument("--qatest-log", default="",
                   help="（建議）qatest.log 路徑：客觀 parse 該 case×平台 pass/fail，優先於 --results")
    p.add_argument("--min-runs", type=int, default=1,
                   help="flaky 防護：每平台需「最近 N 次跑全 pass」才算交付（N>1 需 --qatest-log；預設 1＝維持舊行為）")
    args = p.parse_args()

    if yaml is None:
        print("ERROR: 需要 pyyaml（pip install pyyaml）才能解析 case yaml", file=sys.stderr)
        return 4

    required = [t.strip().lower() for t in args.tags.split(",") if t.strip()]
    required = [t for t in required if t in _ALL]
    if not required:
        print("ERROR: --tags 沒有有效平台（web/mweb/android/ios）", file=sys.stderr)
        return 4

    covered = _covered_platforms(args.repo, args.caseid)
    missing_reg = [t for t in required if t not in covered]

    min_runs = max(1, args.min_runs)

    # pass 憑據：優先 qatest.log 客觀 parse（不靠自報），否則退 --results（automator 自報）
    missing_pass = []
    flaky = []
    insufficient = []
    pass_source = "（未驗 pass）"
    if args.qatest_log:
        passed, flaky, insufficient = _verdicts_from_log(
            args.qatest_log, args.caseid, required, min_runs)
        missing_pass = [t for t in required if t not in passed]
        pass_source = (f"qatest.log 客觀 parse（min_runs={min_runs}）"
                       if min_runs > 1 else "qatest.log 客觀 parse")
    elif args.results:
        if min_runs > 1:
            print("WARN: --min-runs>1 需 --qatest-log 才能數多次跑；--results 自報只有單次結果，"
                  "本次退回單次判定。", file=sys.stderr)
        passed = _passed_platforms(args.results, args.caseid)
        missing_pass = [t for t in required if t not in passed]
        pass_source = "--results 自報"

    # 交付判準（使用者定案）：每個 required 平台在 qatest.log 都 parse 出 pass（＝真跑出 0 failed）。
    # 「--platform X 真跑出 pass」本就隱含「能跑該平台」，故 missing_registration（查 yaml）僅供參考、
    # 不擋交付——尤其 case 還在 automator worktree、主 repo 尚無 entry 時，查 yaml 會空。
    # flaky/insufficient 的平台不會進 passed，故已含在 missing_pass；ok 判定不變。
    ok = pass_source != "（未驗 pass）" and not missing_pass
    report = {
        "caseid": args.caseid,
        "required_platforms": required,
        "registered_platforms": sorted(covered),
        "missing_registration": missing_reg,
        "missing_pass": missing_pass if (args.qatest_log or args.results) else "（未給憑據，略過 pass 檢查）",
        "flaky_platforms": flaky,              # 最近 N 次有 fail：一次過≠穩定過
        "insufficient_runs": insufficient,     # 全 pass 但跑不足 min_runs 次
        "min_runs": min_runs,
        "pass_source": pass_source,
        "delivered": ok,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if not ok:
        parts = []
        if missing_reg:
            parts.append(f"未註冊平台: {missing_reg}")
        if flaky:
            parts.append(f"⚠️ flaky（最近{min_runs}次有 fail，非穩定過）: {flaky}")
        if insufficient:
            parts.append(f"跑不足{min_runs}次: {insufficient}")
        other = [p for p in missing_pass if p not in flaky and p not in insufficient]
        if other:
            parts.append(f"未 pass 平台: {other}")
        print(f"\n❌ {args.caseid} 未完全交付 —— {'；'.join(parts)}。缺的平台要補實作/補跑/確認穩定，不准算完成。",
              file=sys.stderr)
        return 1

    print(f"\n✅ {args.caseid} 全平台交付齊備: {required}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

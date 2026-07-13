#!/usr/bin/env python3
"""per-platform 交付 gate（確定性、非 LLM）

驗一個 TCMS case 的**每個 tag 平台**是否真的交付 —— 不信 automator 自評、矇混不過。
「--platform mweb 跑得綠」不算數（會被 web case 硬套矇混）；要 yaml 真有該平台的正確
註冊，才算交付。

依 kkday-QA-automation 框架慣例判定各平台是否有 case 註冊：
  - web  : cases yaml 有該 caseid 的 entry，platform=web 且未被 limit_test_platform 限成 mweb
  - mweb : 有該 caseid 帶 `limit_test_platform.test_platform=mweb` 的 entry
           （框架慣例：mweb 不是獨立 platform，而是 web driver + limit 成 mweb）
  - android / ios : AppRegression/ 下有該 caseid、platform 對應的 case
（可選 --results：再要求該平台在測試結果 jsonl 裡有 pass）

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

        # 找 pre-condition 裡的 limit_test_platform.test_platform
        test_plat = None
        pre = entry.get("pre-condition", [])
        if isinstance(pre, list):
            for step in pre:
                if isinstance(step, dict) and "limit_test_platform" in step:
                    lp = step.get("limit_test_platform")
                    if isinstance(lp, dict):
                        test_plat = str(lp.get("test_platform", "")).lower()

        if plat in ("android", "ios"):
            covered.add(plat)
        elif plat == "web":
            # web driver：靠 limit_test_platform 區分實際跑 web 還 mweb
            if test_plat == "mweb":
                covered.add("mweb")
            elif test_plat in (None, "", "web"):
                covered.add("web")
            else:
                covered.add(test_plat)
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


def main() -> int:
    p = argparse.ArgumentParser(description="per-platform 交付 gate（確定性）")
    p.add_argument("--caseid", required=True, help="TCMS case id，如 KQT-T37931")
    p.add_argument("--tags", required=True,
                   help="該 case tag 要求的平台，逗號分隔（web,mweb,android,ios）")
    p.add_argument("--repo", required=True, help="kkday-QA-automation repo 路徑")
    p.add_argument("--results", default="", help="（可選）per-platform 測試結果 jsonl")
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

    # 若給了 results，還要求每個 required 平台有 pass
    missing_pass = []
    if args.results:
        passed = _passed_platforms(args.results, args.caseid)
        missing_pass = [t for t in required if t not in passed]

    ok = not missing_reg and not missing_pass
    report = {
        "caseid": args.caseid,
        "required_platforms": required,
        "registered_platforms": sorted(covered),
        "missing_registration": missing_reg,
        "missing_pass": missing_pass if args.results else "（未給 --results，略過 pass 檢查）",
        "delivered": ok,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if not ok:
        parts = []
        if missing_reg:
            parts.append(f"未註冊平台: {missing_reg}")
        if missing_pass:
            parts.append(f"未 pass 平台: {missing_pass}")
        print(f"\n❌ {args.caseid} 未完全交付 —— {'；'.join(parts)}。缺的平台要補實作/補跑，不准算完成。",
              file=sys.stderr)
        return 1

    print(f"\n✅ {args.caseid} 全平台交付齊備: {required}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

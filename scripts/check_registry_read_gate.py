#!/usr/bin/env python3
"""
Registry 讀取硬 Gate —— 擋掉「交付了 case 卻沒先讀過共享記憶」。

背景（為什麼要這支）：
寫入側有硬 gate（`check_locator_gate.py`），讀取側只有軟指令，於是實測結果是
**只寫不讀**——locator registry 累積 1600+ 筆，flow registry 的 stale 率兩個月恆為 0.0。
沒讀的直接後果就是使用者在意的那件事：同一件事被不同人寫成好幾套差不多的 test step。
`docs/lessons-learned.md` 的判準（失敗靜默、又會累積的規則必須用硬 gate）本來就涵蓋讀取側，
只是當初沒套上去。

判準（刻意訂得低）：claimed 的每個 case×平台，`--receipt-dir` 內至少要有**一列**讀取收據。
- 收據記的是「有沒有去問」，不是「有沒有問到」——registry 還沒資料的新流程一樣過得了，
  否則第一個開路的人被永久擋死。
- 這道 gate 不保證 agent 有好好用讀回來的東西（沒有 hook 做得到），它只負責把預設值從
  「永遠沒讀」翻成「一定讀過」。復用的判斷仍在 automator / fidelity reviewer 手上。

claim 來源沿用 locator 寫入 gate 的 `$LOCATOR_CLAIMED`（automator 交付 UI case 時 arm 的
同一個檔），不另立契約——多一套 arm 機制就多一個會忘記 arm 的地方。

生命週期：**pass 時什麼都不刪**。claimed 檔的所有權在 locator 寫入 gate（它 pass 才刪），
本 gate 只讀不刪；收據若在本 gate pass 時就清掉，接著寫入 gate 擋下 → 下一輪 claimed 還在、
收據卻沒了 → 假性卡死（要重讀一次才過）。收據很小，改用「超過保留天數就 prune」控制成長。

退出碼：
  0  claimed 的每個 case×平台都有讀取收據（或這輪沒有任何 claim）
  1  有任何 claim 缺收據 / claimed 檔壞 / 參數錯 → 擋下（fail-CLOSED）

用法：
  python3 check_registry_read_gate.py --claimed /tmp/locator_claimed.<sid>.jsonl \\
      --receipt-dir /tmp/registry_reads.d
  python3 check_registry_read_gate.py --caseids KQT-T1:ios,KQT-T2:web
"""
import argparse
import glob
import json
import os
import sys
import time

PRUNE_DAYS = 7


def _norm(v) -> str:
    return "" if v is None else str(v).strip().lower()


def _load_claimed(claimed_path, caseids):
    """回傳 (claims, hard_error)。claims = list of (case_id, platform_or_None)。"""
    claims = []
    if caseids:
        for item in caseids.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" in item:
                cid, plat = item.split(":", 1)
                claims.append((cid.strip(), plat.strip() or None))
            else:
                claims.append((item, None))
        return claims, False

    if not claimed_path:
        return claims, False
    try:
        with open(claimed_path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
    except FileNotFoundError:
        return claims, False  # 沒有 claimed 檔＝這輪沒交付 case
    except Exception as e:
        print(f"[read-gate] 讀取 claimed 檔失敗：{claimed_path}（{e}）", file=sys.stderr)
        return claims, True

    for ln in lines:
        try:
            obj = json.loads(ln)
        except Exception:
            print(f"[read-gate] --claimed 有無法解析的行 → 擋下：{ln[:80]}", file=sys.stderr)
            return claims, True
        if not isinstance(obj, dict) or not obj.get("case_id"):
            print(f"[read-gate] --claimed 有缺 case_id 的行 → 擋下：{ln[:80]}", file=sys.stderr)
            return claims, True
        plat = obj.get("platform")
        claims.append((str(obj["case_id"]).strip(), plat if plat not in (None, "") else None))
    return claims, False


def _load_receipts(receipt_dir: str):
    """回 (pairs, cases)：pairs=set((case, platform))、cases=set(case)。"""
    pairs, cases = set(), set()
    if not os.path.isdir(receipt_dir):
        return pairs, cases
    for fp in glob.glob(os.path.join(receipt_dir, "*.jsonl")):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        row = json.loads(ln)
                    except Exception:
                        continue
                    case = _norm(row.get("case"))
                    if not case:
                        continue
                    cases.add(case)
                    pairs.add((case, _norm(row.get("platform"))))
        except Exception:
            continue
    return pairs, cases


def _has_receipt(cid, plat, pairs, cases) -> bool:
    cid_n = _norm(cid)
    if plat is None:
        return cid_n in cases
    # 平台空字串的收據（沒帶 --platform 的讀取）也算：讀到的是跨平台候選，仍是讀過
    return (cid_n, _norm(plat)) in pairs or (cid_n, "") in pairs


def _prune(receipt_dir: str, days: int = PRUNE_DAYS) -> None:
    """只按保留天數 prune，不跟任何 gate 的 pass/block 綁——綁了就會製造假性卡死。fail-safe。"""
    if not os.path.isdir(receipt_dir):
        return
    cutoff = time.time() - days * 86400
    for fp in glob.glob(os.path.join(receipt_dir, "*.jsonl")):
        try:
            if os.path.getmtime(fp) < cutoff:
                os.remove(fp)
        except Exception:
            continue


def main() -> int:
    p = argparse.ArgumentParser(
        description="Registry 讀取硬 Gate：交付 case 卻沒讀過共享 registry 就擋下")
    p.add_argument("--claimed", help="locator_claimed jsonl（每行含 case_id，platform 選填）")
    p.add_argument("--caseids", help='逗號分隔，每項 "CASE" 或 "CASE:PLATFORM"')
    p.add_argument("--receipt-dir", default=os.getenv("REGISTRY_READ_DIR", "/tmp/registry_reads.d"),
                   help="讀取收據目錄（registry_read_receipt.write 寫入處）")
    args = p.parse_args()

    if not args.claimed and not args.caseids:
        print("[read-gate] 錯誤：至少要 --claimed 或 --caseids → 擋下", file=sys.stderr)
        return 1

    claims, hard_error = _load_claimed(args.claimed, args.caseids)
    if hard_error:
        print("[read-gate] 結果：擋下（BLOCKED）— claimed 清單有壞行，無法信任", file=sys.stderr)
        return 1
    _prune(args.receipt_dir)
    if not claims:
        print("[read-gate] 沒有任何 case claim，無需把關 → 通過")
        return 0

    pairs, cases = _load_receipts(args.receipt_dir)
    missing = [(cid, plat) for cid, plat in claims if not _has_receipt(cid, plat, pairs, cases)]

    if missing:
        print("[read-gate] 以下 case×平台沒有 registry 讀取收據（BLOCKED）：", file=sys.stderr)
        for cid, plat in missing:
            print(f"[read-gate]   - {cid} / {plat if plat is not None else '(未指定平台)'}",
                  file=sys.stderr)
        d = os.path.dirname(os.path.abspath(__file__))
        print("[read-gate] 處理方式：對這些 case 真的打一次共享 registry 的讀取端點（帶 --case）：",
              file=sys.stderr)
        print(f"[read-gate]   app（ios/android）：python3 {d}/fetch_locator_registry.py --case <CASE> "
              "--platform <ios|android> --list-flows [--q 關鍵字]，找到 flow key 後再 --flow <key>",
              file=sys.stderr)
        print(f"[read-gate]   web/mweb：python3 {d}/locator_valve.py --case <CASE> "
              "--platform <web|mweb> --flow <key>", file=sys.stderr)
        print(f"[read-gate]   可重用 step/flow：python3 {d}/get_verified_flow.py --case <CASE> "
              "--q <關鍵字> --platform <ios|android|web|mweb> --repo-path <framework repo 路徑>",
              file=sys.stderr)
        print("[read-gate] 讀回來是空的也算過（收據記的是『有沒有去問』）——但不准跳過不問。",
              file=sys.stderr)
        print(f"[read-gate] 已經跑過上面的指令卻還是被擋？先看收據目錄寫不寫得進去："
              f"{args.receipt_dir}（不可寫時讀取指令仍會印 ok:true，但收據是空的；"
              f"該情況讀取端會在 stderr 印 [read-receipt] 警告，可用 REGISTRY_READ_DIR 換路徑）",
              file=sys.stderr)
        return 1

    print(f"[read-gate] {len(claims)} 筆 case×平台都有 registry 讀取收據 → 通過（PASS）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

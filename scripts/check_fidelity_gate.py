#!/usr/bin/env python3
"""
忠實度硬 Gate（非 LLM、確定性守門腳本）

用途：擋在「彙整批次報告 / 送遙測」之前，防止主對話「漏跑忠實度 review 就把
case 當過」。這個漏在真實 session 發生過——review（spawn qa-case-fidelity-reviewer）
是主對話「要記得做」的步驟，靠記憶會漏。這支死程式用確定性檢查把關：

    每個「聲稱跑過的 case×平台」都必須有一筆對應的 fidelity 結果，
    且該筆的最終判定是 pass；否則一律擋下（exit 1）。

與 send_case_fidelity.py 的差別（方向相反）：
    - sender 是「送遙測」，fail-safe 傾向**放行**：資料缺就靜默略過、exit 0，不干擾主流程。
    - 本 gate 是「守門」，fail-safe 傾向**擋下**：資料缺、格式壞、沒對應 review 一律當不合格，
      **寧可誤擋，不可放行**。任何不確定都 exit 1。

「pass」的判定（確定性、無 LLM）：
    - 若該筆有 recommend 欄位：唯有 recommend == "pass" 才算過；
      needs-fix / blocked / flag-for-human 或任何其他值 → 不過。
    - 若沒有 recommend 欄位：退而用 fidelity == "PASS" 才算過。
    - recommend 與 fidelity 都缺 → 視為資料不完整 → 不過（擋下）。
    （recommend 為主要訊號；欄位對齊 scripts/send_case_fidelity.py。）

輸入：
    聲稱跑過的清單（擇一或並用）：
        --claimed <jsonl>   每行一筆 JSON，至少含 case_id，platform 選填
        --caseids <清單>    逗號分隔，每項 "CASE" 或 "CASE:PLATFORM"
    fidelity 結果：
        --fidelity <jsonl>  qa-case-fidelity-reviewer 產出的結果（每行一筆）

平台比對：大小寫不敏感、去空白。claim 未指定 platform 時，比對同 case_id 的
**所有** fidelity 筆數，且要求「至少一筆且全部都過」才算該 case 合格。

Exit code：
    0  全部聲稱跑過的 case×平台都有對應 review 且判定為 pass
    1  有任何不合格（缺 review / 判定非 pass / 資料缺漏或格式壞 / 參數錯誤）

用法範例：
    python3 check_fidelity_gate.py --caseids KQT-T34933:web,KQT-T34933:mweb \\
        --fidelity fidelity_results.jsonl
    python3 check_fidelity_gate.py --claimed claimed.jsonl --fidelity fidelity_results.jsonl
"""
import argparse
import glob
import json
import os
import sys
import time

# 明確的「不過」recommend 值（僅供訊息分類；判定邏輯只認 == "pass"）
NON_PASS_RECOMMEND = ("needs-fix", "blocked", "flag-for-human")


def _norm(v) -> str:
    """平台/字串正規化：轉字串、去頭尾空白、小寫。None → 空字串。"""
    if v is None:
        return ""
    return str(v).strip().lower()


def _load_fidelity(path: str):
    """
    讀 fidelity 結果。`path` 可為單一 jsonl（相容舊用法）或**目錄**（新：per case×平台
    一檔，reviewer 各自覆寫）。目錄模式讀其中所有 *.jsonl 合併。回傳 (rows, hard_error)。
    hard_error=True 代表缺失/讀不到/完全無有效筆數等「守門該擋」的情況。
    壞掉的單行會被跳過（不能拿來當通過依據），但不因此直接 hard_error。
    """
    rows = []
    raw_lines = []
    try:
        if os.path.isdir(path):
            files = sorted(glob.glob(os.path.join(path, "*.jsonl")))
            if not files:
                print(f"[gate] fidelity 結果目錄是空的（無 *.jsonl）：{path}", file=sys.stderr)
                return rows, True
            for fp in files:
                with open(fp, "r", encoding="utf-8") as f:
                    raw_lines.extend(ln.strip() for ln in f if ln.strip())
        else:
            with open(path, "r", encoding="utf-8") as f:
                raw_lines = [ln.strip() for ln in f if ln.strip()]
    except FileNotFoundError:
        print(f"[gate] 找不到 fidelity 結果檔/目錄：{path}", file=sys.stderr)
        return rows, True
    except Exception as e:
        print(f"[gate] 讀取 fidelity 結果失敗：{path}（{e}）", file=sys.stderr)
        return rows, True

    for ln in raw_lines:
        try:
            obj = json.loads(ln)
        except Exception:
            # 壞行不能當通過依據，跳過即可（守門天然偏向擋下）
            print(f"[gate] 略過無法解析的 fidelity 行：{ln[:80]}", file=sys.stderr)
            continue
        if not isinstance(obj, dict) or not obj.get("case_id"):
            print(f"[gate] 略過缺 case_id 的 fidelity 行：{ln[:80]}", file=sys.stderr)
            continue
        rows.append(obj)
    return rows, False


def _row_passes(row: dict) -> bool:
    """
    確定性判定單筆 fidelity 是否算「過」。
    recommend 為主：有 recommend 就唯認 "pass"；沒有才退用 fidelity == "PASS"。
    兩者皆缺 → 不過。
    """
    if "recommend" in row and row.get("recommend") is not None:
        return _norm(row.get("recommend")) == "pass"
    if "fidelity" in row and row.get("fidelity") is not None:
        return _norm(row.get("fidelity")) == "pass"  # "PASS" 正規化後為 "pass"
    return False


def _load_claimed(claimed_path, caseids_arg):
    """
    彙整聲稱跑過的清單。回傳 (claims, hard_error)。
    claims：list of (case_id, platform_or_None)。platform 為 None 代表未指定。
    """
    claims = []
    hard_error = False

    if claimed_path:
        try:
            with open(claimed_path, "r", encoding="utf-8") as f:
                raw_lines = [ln.strip() for ln in f if ln.strip()]
        except Exception as e:
            print(f"[gate] 讀取 --claimed 檔失敗：{claimed_path}（{e}）", file=sys.stderr)
            return claims, True
        for ln in raw_lines:
            try:
                obj = json.loads(ln)
            except Exception:
                # 守門：讀不懂聲稱清單就不能信任 → 硬擋
                print(f"[gate] --claimed 有無法解析的行 → 擋下：{ln[:80]}", file=sys.stderr)
                hard_error = True
                continue
            if not isinstance(obj, dict) or not obj.get("case_id"):
                print(f"[gate] --claimed 有缺 case_id 的行 → 擋下：{ln[:80]}", file=sys.stderr)
                hard_error = True
                continue
            platform = obj.get("platform")
            claims.append((str(obj["case_id"]).strip(), platform if platform not in (None, "") else None))

    if caseids_arg:
        for item in caseids_arg.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" in item:
                cid, plat = item.split(":", 1)
                cid, plat = cid.strip(), plat.strip()
                claims.append((cid, plat if plat else None))
            else:
                claims.append((item, None))

    return claims, hard_error


def _evaluate(claims, fidelity_rows):
    """
    對每個 claim 判定合格與否。回傳 list of (case_id, platform_or_None, ok, reason)。
    """
    results = []
    for cid, plat in claims:
        cid_n = _norm(cid)
        # 找對應 fidelity 筆數
        if plat is None:
            matched = [r for r in fidelity_rows if _norm(r.get("case_id")) == cid_n]
        else:
            plat_n = _norm(plat)
            matched = [
                r for r in fidelity_rows
                if _norm(r.get("case_id")) == cid_n and _norm(r.get("platform")) == plat_n
            ]

        if not matched:
            results.append((cid, plat, False, "缺 fidelity review（找不到對應結果）"))
            continue

        # 全部 matched 都要過；任一不過 → 擋
        failing = [r for r in matched if not _row_passes(r)]
        if failing:
            reasons = []
            for r in failing:
                rec = r.get("recommend")
                fid = r.get("fidelity")
                if rec is not None:
                    reasons.append(f"recommend={rec}")
                elif fid is not None:
                    reasons.append(f"fidelity={fid}")
                else:
                    reasons.append("無 recommend/fidelity 欄位")
            results.append((cid, plat, False, "判定非 pass（" + "；".join(reasons) + "）"))
        else:
            results.append((cid, plat, True, f"pass（{len(matched)} 筆）"))
    return results


def main() -> int:
    p = argparse.ArgumentParser(
        description="忠實度硬 Gate：沒過忠實度 review 的 case 一律擋下（守門，寧可誤擋不可放行）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--claimed", help="聲稱跑過的 case×平台 jsonl（每行含 case_id，platform 選填）")
    p.add_argument("--caseids", help='聲稱跑過的清單，逗號分隔，每項 "CASE" 或 "CASE:PLATFORM"')
    p.add_argument("--fidelity", required=True,
                   help="fidelity 結果來源：單一 jsonl 檔，或目錄（讀其中所有 *.jsonl，"
                        "reviewer per case×平台 各寫一檔）")
    p.add_argument("--cleanup-on-pass", action="store_true",
                   help="通過時，只刪掉**本次 claimed 的 case×平台**對應的結果檔（目錄模式），"
                        "不動別的 session 的檔——避免 rm -rf 整個目錄誤刪同機他人正在驗的結果。")
    p.add_argument("--delivery-ledger", default="",
                   help="#5 根治：通過時把交付記錄寫進此 ledger（預設不寫）。"
                        "由 Stop hook 帶入 → 交付 ledger 變成『過 gate』的副產品，"
                        "不再靠主對話記得跑 send_case_delivery（沒過 gate 就沒 ledger、過了就一定有）。")
    args = p.parse_args()

    if not args.claimed and not args.caseids:
        print("[gate] 錯誤：至少要提供 --claimed 或 --caseids 其中一個 → 擋下", file=sys.stderr)
        return 1

    claims, claim_hard_error = _load_claimed(args.claimed, args.caseids)

    if not claims and not claim_hard_error:
        # 沒有任何 claim：沒東西要送/彙整，放行（沒有「漏跑」風險）
        print("[gate] 沒有任何聲稱跑過的 case×平台，無需把關 → 通過")
        return 0

    fidelity_rows, fidelity_hard_error = _load_fidelity(args.fidelity)

    # 守門：只要聲稱清單壞了、或連 fidelity 結果檔都拿不到，直接擋
    if claim_hard_error:
        print("[gate] 結果：擋下（BLOCKED）— 聲稱清單有壞行，無法信任", file=sys.stderr)
        return 1
    if fidelity_hard_error:
        print(
            f"[gate] 結果：擋下（BLOCKED）— 拿不到 fidelity 結果檔，"
            f"視同全部 {len(claims)} 筆缺 review", file=sys.stderr
        )
        return 1

    results = _evaluate(claims, fidelity_rows)
    failed = [r for r in results if not r[2]]
    passed = [r for r in results if r[2]]

    print(f"[gate] 聲稱跑過 {len(results)} 筆 case×平台 ｜ 合格 {len(passed)} ｜ 不合格 {len(failed)}")

    if failed:
        print("[gate] 以下 case×平台不合格，擋下彙整/送遙測（BLOCKED）：", file=sys.stderr)
        for cid, plat, _ok, reason in failed:
            plat_disp = plat if plat is not None else "(未指定平台)"
            print(f"[gate]   - {cid} / {plat_disp}：{reason}", file=sys.stderr)
        print(
            "[gate] 處理方式：先對上述 case 補跑 qa-case-fidelity-reviewer "
            "（needs-fix 要丟回 automator 重修再 review），全部 pass 後再彙整/送出。",
            file=sys.stderr,
        )
        return 1

    print("[gate] 全部聲稱跑過的 case×平台都有對應 review 且判定 pass → 通過（PASS）")
    # #5 根治：交付 ledger 是「過 gate」的副產品，寫在 cleanup 之前（此刻才確定性地算交付）
    if args.delivery_ledger:
        n = _write_delivery_ledger(args.delivery_ledger, passed, fidelity_rows)
        if n:
            print(f"[gate] 已寫 {n} 筆交付記錄進 ledger：{args.delivery_ledger}")
    if args.cleanup_on_pass:
        _cleanup_passed(args.fidelity, claims)
    return 0


def _write_delivery_ledger(ledger_path: str, passed, fidelity_rows) -> int:
    """#5：通過的 case 各寫一筆交付記錄（每 case 聚合其通過平台）。fail-safe：任何錯不影響 gate。
    passed：_evaluate 回的合格項 [(cid, plat_or_None, ok, reason), ...]。
    平台來源：claim 有指定就用它；未指定則從該 case 的 fidelity 筆數收集。"""
    try:
        # 依 case 聚合通過的 claim：明確指定平台的精準記；platform=None（wildcard）代表
        # 「該 case 全平台」，此時才從 fidelity 列補平台（_evaluate 已確保那些全 pass）。
        agg = {}  # cid -> {"explicit": set, "wildcard": bool}
        for cid, plat, _ok, _reason in passed:
            slot = agg.setdefault(cid, {"explicit": set(), "wildcard": False})
            if plat:
                slot["explicit"].add(_norm(plat))
            else:
                slot["wildcard"] = True
        if not agg:
            return 0
        by_case = {}
        for cid, slot in agg.items():
            cid_n = _norm(cid)
            plats = set(slot["explicit"])
            if slot["wildcard"]:
                for r in fidelity_rows:
                    if _norm(r.get("case_id")) == cid_n and r.get("platform"):
                        plats.add(_norm(r.get("platform")))
            # confidence 只收「有記進交付平台」的那些 fidelity 列（不重複、不含未交付平台）
            conf = [
                r.get("confidence") for r in fidelity_rows
                if _norm(r.get("case_id")) == cid_n and r.get("confidence") is not None
                and (not r.get("platform") or _norm(r.get("platform")) in plats)
            ]
            by_case[cid] = {"platforms": plats, "conf": conf}
        now = int(time.time())
        os.makedirs(os.path.dirname(ledger_path) or ".", exist_ok=True)
        written = 0
        with open(ledger_path, "a", encoding="utf-8") as f:
            for cid, slot in by_case.items():
                rec = {
                    "caseid": cid,
                    "platforms": sorted(slot["platforms"]),
                    "delivered": True,
                    "ts": now,
                    "source": "fidelity_gate",  # 註明來源＝過 gate 自動寫，非人工
                    "repo": "kkday-QA-automation",
                }
                if slot["conf"]:
                    rec["fidelity_confidence"] = slot["conf"]
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1
        return written
    except Exception as e:
        print(f"[gate] 寫交付 ledger 失敗（不影響 gate 判定）：{e}", file=sys.stderr)
        return 0


def _cleanup_passed(fidelity_path: str, claims) -> None:
    """通過後只刪本次 claimed 的結果檔（目錄模式）。檔名對齊 reviewer 寫的
    `<case_id>__<platform>.jsonl`；claim 未指定平台時刪該 case 的所有平台檔。
    不 rm 整個目錄，避免誤刪同機其他 session 的結果。fail-safe。"""
    if not os.path.isdir(fidelity_path):
        return
    for cid, plat in claims:
        try:
            if plat:
                targets = [os.path.join(fidelity_path, f"{cid}__{plat}.jsonl")]
            else:
                targets = glob.glob(os.path.join(fidelity_path, f"{cid}__*.jsonl"))
            for fp in targets:
                try:
                    os.remove(fp)
                except Exception:
                    pass
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())

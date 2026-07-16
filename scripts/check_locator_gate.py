#!/usr/bin/env python3
"""
Locator 硬 Gate —— 擋掉「交付了 UI case 卻沒把 locator 驗證/收成回寫」。

背景：locator 共享記憶原本只有軟指令（「起手跑 valve」），agent 很容易跳過（讀 registry.json
敘述冒充跑 valve），而且失敗是**靜默**的——後端讀空看起來跟「還沒資料」一樣，沒人會發現。
這支死程式把「回寫」變成硬約束：**你聲稱交付的每個 UI case×平台，都必須有對應的 locator
emit 證據**（valve 驗過的，或 app/from-scratch 收成的），否則擋下結束。

證據來源（統一）：`--emit-dir`（預設 /tmp/locator_results.d）內任一 *.jsonl 有一列
`source == <case_id>`（且平台相符或 claim 未指定平台）。這同時涵蓋：
  - web/mweb：`get_verified_locator.py` valve 驗證後 emit（source=case）
  - app / from-scratch：automator 在測試通過後把用到的 locator 收成 emit（source=case, status=verified）

與 fidelity gate 一致的守門哲學：fail-CLOSED（拿不到證據一律當不合格、擋下）。
生命週期：`send_locator_registry.py` 在 Stop hook **不帶 --purge**（後端 upsert 冪等、重送無害），
本 gate 在 pass 時才用 --cleanup-on-pass 清掉本次 claimed 的 emit 檔——避免 sender 在擋下時
先刪掉證據造成假性卡死（與 fidelity 的 purge race 同一課）。

退出碼：
  0  claimed 的 UI case×平台都有 locator emit 證據（或沒有任何 claim）
  1  有任何 claim 缺證據 / 資料壞 / 參數錯 → 擋下

用法：
  python3 check_locator_gate.py --claimed /tmp/locator_claimed.jsonl --emit-dir /tmp/locator_results.d
  python3 check_locator_gate.py --caseids KQT-T1:web,KQT-T2:android --emit-dir /tmp/locator_results.d
"""
import argparse
import glob
import json
import os
import sys


def _norm(v) -> str:
    if v is None:
        return ""
    return str(v).strip().lower()


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
        return claims, False  # 沒有 claimed 檔＝這輪沒交付 UI case，交給呼叫端判斷
    except Exception as e:
        print(f"[locator-gate] 讀取 claimed 檔失敗：{claimed_path}（{e}）", file=sys.stderr)
        return claims, True

    for ln in lines:
        try:
            obj = json.loads(ln)
        except Exception:
            print(f"[locator-gate] --claimed 有無法解析的行 → 擋下：{ln[:80]}", file=sys.stderr)
            return claims, True
        if not isinstance(obj, dict) or not obj.get("case_id"):
            print(f"[locator-gate] --claimed 有缺 case_id 的行 → 擋下：{ln[:80]}", file=sys.stderr)
            return claims, True
        plat = obj.get("platform")
        claims.append((str(obj["case_id"]).strip(), plat if plat not in (None, "") else None))
    return claims, False


def _load_evidence(emit_dir: str):
    """讀 emit-dir 內所有 *.jsonl，回傳 set of (source_case, platform) 與 dict 供比對。
    回 (pairs, sources) —— pairs=set((case,platform)), sources=set(case)。"""
    pairs = set()
    sources = set()
    if not os.path.isdir(emit_dir):
        return pairs, sources
    for fp in glob.glob(os.path.join(emit_dir, "*.jsonl")):
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
                    src = _norm(row.get("source"))
                    if not src:
                        continue
                    sources.add(src)
                    pairs.add((src, _norm(row.get("platform"))))
        except Exception:
            continue
    return pairs, sources


def _has_evidence(cid, plat, pairs, sources) -> bool:
    cid_n = _norm(cid)
    if plat is None:
        return cid_n in sources  # 未指定平台：該 case 有任何平台的 emit 即可
    return (cid_n, _norm(plat)) in pairs


def _cleanup_passed(emit_dir: str, passed_sources) -> None:
    """pass 後只刪「所有列的 source 都屬本次通過 case」的 emit 檔，不碰含他人 case 的檔。
    後端 upsert 冪等，就算沒刪、下輪重送也無害；清理只是避免 /tmp 無限長。fail-safe。"""
    if not os.path.isdir(emit_dir):
        return
    passed = {_norm(s) for s in passed_sources}
    for fp in glob.glob(os.path.join(emit_dir, "*.jsonl")):
        try:
            file_sources = set()
            with open(fp, "r", encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        row = json.loads(ln)
                    except Exception:
                        continue
                    file_sources.add(_norm(row.get("source")))
            file_sources.discard("")
            # 整個檔的 case 都在本次通過集合內才刪；混到別的 case 就保留
            if file_sources and file_sources <= passed:
                try:
                    os.remove(fp)
                except Exception:
                    pass
        except Exception:
            continue


def main() -> int:
    p = argparse.ArgumentParser(
        description="Locator 硬 Gate：交付 UI case 卻沒 locator emit 證據就擋下（守門，寧可誤擋）")
    p.add_argument("--claimed", help="locator_claimed jsonl（每行含 case_id，platform 選填；只 arm UI case）")
    p.add_argument("--caseids", help='逗號分隔，每項 "CASE" 或 "CASE:PLATFORM"')
    p.add_argument("--emit-dir", default="/tmp/locator_results.d",
                   help="locator emit 目錄（valve / 收成寫入處）")
    p.add_argument("--cleanup-on-pass", action="store_true",
                   help="通過時只刪本次 claimed 對應、且不含他人 case 的 emit 檔")
    args = p.parse_args()

    if not args.claimed and not args.caseids:
        print("[locator-gate] 錯誤：至少要 --claimed 或 --caseids → 擋下", file=sys.stderr)
        return 1

    claims, hard_error = _load_claimed(args.claimed, args.caseids)
    if hard_error:
        print("[locator-gate] 結果：擋下（BLOCKED）— claimed 清單有壞行，無法信任", file=sys.stderr)
        return 1
    if not claims:
        print("[locator-gate] 沒有任何 UI case claim，無需把關 → 通過")
        return 0

    pairs, sources = _load_evidence(args.emit_dir)

    missing = []
    for cid, plat in claims:
        if not _has_evidence(cid, plat, pairs, sources):
            missing.append((cid, plat))

    if missing:
        print(f"[locator-gate] 以下 UI case×平台缺 locator emit 證據（BLOCKED）：", file=sys.stderr)
        for cid, plat in missing:
            plat_disp = plat if plat is not None else "(未指定平台)"
            print(f"[locator-gate]   - {cid} / {plat_disp}：{args.emit_dir} 內找不到 source=={cid} 的 emit 列",
                  file=sys.stderr)
        print("[locator-gate] 處理方式：對這些 case 真的跑 get_verified_locator.py valve（web/mweb），"
              "或在測試通過後把用到的 locator 收成 emit（app/from-scratch，source=<case>, status=verified）；"
              "不是讀 registry.json 敘述冒充。", file=sys.stderr)
        return 1

    print(f"[locator-gate] {len(claims)} 筆 UI case×平台都有 locator emit 證據 → 通過（PASS）")
    if args.cleanup_on_pass:
        _cleanup_passed(args.emit_dir, {c for c, _ in claims})
    return 0


if __name__ == "__main__":
    sys.exit(main())

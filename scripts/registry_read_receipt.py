#!/usr/bin/env python3
"""
Registry 讀取收據（read receipt）—— 讀取側硬 gate 的證據來源。

背景：寫入側早就有硬 gate（`check_locator_gate.py` 驗 emit 證據），讀取側卻只有軟指令
（「起手先 fetch registry 拿 hints」）。實測後果：locator registry 累積 1600+ 筆，但
flow registry 的 stale 率兩個月**恆為 0.0**——沒有人在讀，於是共享記憶只進不出，
大家繼續各寫一份差不多的 step。`docs/lessons-learned.md` 的判準（失敗靜默且會累積的
規則要用硬 gate，不能只靠軟指令）當初只套在寫入側。

這支只做一件事：把「某個 case 真的打過 registry 讀取端點」寫成一列 jsonl 收據，
供 `check_registry_read_gate.py` 在 Stop 時比對。

刻意的設計：
- **記錄「有沒有去問」，不是「有沒有問到」**（問到什麼記在 `hit` / `n`）。registry 還沒
  有資料的新流程一樣要能過 gate，否則第一個開路的人被永久擋死。
- 後端不可達也照寫收據：讀取端全都 fail-safe 回空，若「拿不到就沒收據」，後端一抽風
  就變成過不了的 gate。
- fail-safe：寫收據的任何錯誤**不影響讀取本身的回傳值**，但一定要**吼一聲到 stderr**。
  完全靜默吞掉會製造一個無解的 fail-closed：收據目錄不可寫時，讀取指令看起來完全成功
  （印出 `ok: true`），gate 卻永遠說「沒讀」——人明明照做了卻被擋死，而且沒有任何線索
  指向真因。實測踩過（見 `docs/lessons-learned.md` 的「fail-closed 卻無解」那類）。
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

DEFAULT_DIR = os.getenv("REGISTRY_READ_DIR", "/tmp/registry_reads.d")


def write(kind: str, case: str, platform: str = "", query=None,
          n: int = 0, endpoint: str = "", receipt_dir: str = "") -> str:
    """寫一列讀取收據，回傳寫入路徑（失敗回空字串）。

    kind：locator | flow —— 讀的是哪個 registry。
    case：當前正在做的 case id（沒有就不寫收據：gate 是按 case 比對的，無 case 的探索
          性讀取不需要也不該產生證據）。
    """
    if not case:
        return ""
    try:
        d = receipt_dir or DEFAULT_DIR
        os.makedirs(d, exist_ok=True)
        row = {
            "kind": kind,
            "case": str(case).strip(),
            "platform": (platform or "").strip(),
            "query": query if isinstance(query, dict) else {"q": query},
            "n": int(n or 0),
            "hit": bool(n),
            "endpoint": endpoint,
            "read_at": datetime.now(timezone.utc).isoformat(),
        }
        path = os.path.join(d, f"{os.getpid()}-{int(time.time())}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path
    except Exception as exc:
        # 不 raise（讀取本身不能因為記帳失敗而壞掉），但必須可見：這是「照做了卻被 gate 擋死」
        # 唯一的線索。stderr 不會污染 stdout 的 JSON，程式化取用不受影響。
        print(
            f"[read-receipt] 收據寫入失敗，Stop 的讀取 gate 會判定「沒讀過」而擋下："
            f"{type(exc).__name__}: {exc}\n"
            f"[read-receipt] 目錄={receipt_dir or DEFAULT_DIR}"
            f"（可用 REGISTRY_READ_DIR 指到可寫路徑後重跑本指令）",
            file=sys.stderr,
        )
        return ""

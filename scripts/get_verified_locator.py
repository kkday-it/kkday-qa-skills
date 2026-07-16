#!/usr/bin/env python3
"""
get_verified_locator —— agent/skill 端取用 locator 的【唯一正規入口】

硬約束（設計繞著它走）：**沒有「只拿不驗」這條路。** 驗證不是 agent 要「記得」做的習慣，
而是寫死在這支程式裡的閥。agent 端不准直接呼叫 fetch（GET）或直接吃 registry 的 selector 來用；
唯一被允許的用法是呼叫這支，它內部替你做完「GET 候選 → 當前 DOM 逐一 cheap-verify → 回第一個活的」。

流程（每次呼叫都完整跑，agent 想繞都繞不過）：
  1. GET 候選：先打 ai_studio（fetch_locator_registry，跨人共享 + 趨勢），拿不到再退到本地
     registry.json。兩者都是「候選 hint」來源，不是真理。
  2. **逐一 cheap-verify**：在當前 DOM 依優先序驗每個候選 selector 存不存在（verify_locator）。
     mweb 自動套 device profile（iPhone 15），不用 viewport 冒充。
  3. 判定：
       - 有候選命中 → status=verified，回『那個活的 selector』給 agent 直接用（省下重挖）。
       - 全部候選死 → status=stale，**不回任何可用 selector**，改回 action="remine"：
         agent 必須回退到「從零挖」原本流程重挖。腐爛 selector 在這一步被擋下，不會傳染全隊。
  4. 回寫（不在此 inline POST，遵守 fail-safe / 不真送）：把每筆 verified/stale + last_verified
     寫成 jsonl，交給 Stop hook 的 send_locator_registry.py 之後背景 POST 回後端。
     **emit 預設就開**（寫到 DEFAULT_EMIT_DIR 下的 per-process 檔，與 Stop hook 的 --indir 對齊）；
     不必記得帶 --emit，回寫才不會因「忘了帶旗標」而靜默斷掉。要停用回寫才明確傳 `--emit ''`。
     per-process 檔名讓批次並行/多 session 各寫各的，不互相覆寫、不被別人的 purge 掃掉。

因為第 3 步 stale 時「回傳裡就沒有可用 selector」，agent 結構上拿不到未驗證的 selector 來用。
「先驗」因此變成 API 的唯一形狀。

需要 playwright（Python）：`pip install playwright && playwright install chromium`。
fetch_localator/verify_locator 為本檔內部依賴（同目錄），agent 不應單獨呼叫它們當「拿了直接用」。

用法（一個 case 起手，建議用 flow 一次批次驗整組相關元素）：
    # 回寫預設就開（寫到 /tmp/locator_results.d/<pid>-<ts>.jsonl），不必帶 --emit
    python3 get_verified_locator.py --flow things-to-do-search --platform web --env stage \\
        --registry locator_registry/registry.json
    # 或指定單一 element / component
    python3 get_verified_locator.py --element search-result-active-tab-web-stage \\
        --platform web --env stage --registry locator_registry/registry.json

輸出 JSON（stdout）：
    {
      "query": {...},
      "source": "backend" | "local" | "none",
      "results": [
        {"id","element","component","platform","verify_url",
         "status":"verified","selector":{"type","value"}},           # 可直接用
        {"id","element","component","platform","verify_url",
         "status":"stale","action":"remine","tried":[...]}            # 無可用 selector，須重挖
      ],
      "must_remine": ["<id>", ...]      # 全死、需從零挖的元素
    }
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

# 現階段安全紅線：環境只接受 stage / sit0x / sit20x（比照 server _VALID_ENV_RE），禁 prod。
# 獨立定義,不依賴 verify_locator/playwright import 是否成功 —— prod 紅線不能因缺 playwright 而失效。
_VALID_ENV_RE = re.compile(r"stage|sit\d*")

# 回寫待送檔的預設目錄。**刻意給預設、不留空**：Stop hook 的 send_locator_registry.py 掃這個
# 目錄再 POST 回後端；若 --emit 省略就不寫檔，整條回寫會被靜默略過（呼叫端「忘了帶旗標」＝
# 資料永遠上不了後端）。改成預設就寫，把「有沒有回寫」從「呼叫端記不記得」變成預設行為。
#
# 為什麼是「目錄 + per-process 檔」而非單一固定檔：批次並行時多個 automator（甚至多個 session）
# 會同時 emit，若都寫同一個檔，sender 的 read→POST→purge 之間有人插入寫入就會被 purge 掉未送出
# （靜默丟失）。改成每個 process 各寫 `<pid>-<utc_ts>.jsonl`，彼此天然隔離；sender 逐檔讀完才刪
# 自己那份，不碰別人正在寫的。與 ~/.claude/settings.json Stop hook 的 --indir 對齊。
DEFAULT_EMIT_DIR = "/tmp/locator_results.d"


def _default_emit_path() -> str:
    """每個 process 一個唯一檔名，避免並行互相覆寫/被 purge。"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
    return os.path.join(DEFAULT_EMIT_DIR, f"{os.getpid()}-{ts}.jsonl")


def _resolve_emit(emit_arg) -> str:
    """把 --emit 參數解成實際路徑：
    - None（未帶旗標）→ 預設 per-process 檔（回寫預設就開）
    - ""（--emit ''）  → 停用回寫
    - 其他             → 使用者指定的明確路徑
    """
    if emit_arg is None:
        return _default_emit_path()
    return emit_arg


def _is_prod_url(url: str) -> bool:
    """判斷 URL 是否指向 prod 正式站。現階段禁打 prod：非 stage/sit 標記的 kkday 站一律當 prod 擋下。"""
    if not url:
        return False
    u = url.lower()
    if "stage" in u or ".sit" in u or "sit0" in u or "sit2" in u:
        return False
    return "kkday.com" in u


# 內部依賴（同目錄）——不對 agent 單獨暴露
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from fetch_locator_registry import _fetch_with_retry, _shape  # noqa: E402
except Exception:
    _fetch_with_retry = None
    _shape = None
try:
    from verify_locator import _open_page, _verify_candidates  # noqa: E402
    from playwright.sync_api import sync_playwright  # noqa: E402
except Exception:
    _open_page = None
    _verify_candidates = None
    sync_playwright = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_case(source) -> str:
    """source 可能是 dict（registry.json 的 {case,origin,ref}）或 str（後端回存）或 None。
    一律取出 case id 字串，避免對 str 呼叫 .get 而炸掉。"""
    if isinstance(source, dict):
        return source.get("case", "")
    return source or ""


def _emit_source(current_case: str, entry_source) -> str:
    """emit 列的 source：優先用「當前正在做的 case」（--case），這樣 locator gate 對得上
    這次交付的 case；沒帶才退回 entry 的 origin case（provenance）。重用既有 locator 時
    origin ≠ 當前 case，若用 origin 會讓 gate 找不到證據而假擋。"""
    return current_case or _source_case(entry_source)


def _gather_candidate_entries(args) -> tuple:
    """步驟 1：GET 候選。先 backend，拿不到退本地 registry。回 (entries, source)。"""
    query = {"flow": args.flow, "page": args.page, "component": args.component,
             "element": args.element, "platform": args.platform, "env": args.env}

    # 1a. backend（fail-safe：不可達就回空）
    if _fetch_with_retry is not None:
        try:
            raw = _fetch_with_retry({k: v for k, v in query.items() if v})
            if raw is not None:
                shaped = _shape(raw, query)
                if shaped.get("entries"):
                    return shaped["entries"], "backend"
        except Exception:
            pass

    # 1b. 本地 registry fallback
    if args.registry and os.path.isfile(args.registry):
        try:
            with open(args.registry, "r", encoding="utf-8") as f:
                reg = json.load(f)
            entries = reg.get("entries", [])
            out = [e for e in entries if (
                (not args.flow or e.get("flow") == args.flow)
                and (not args.page or e.get("page") == args.page)
                and (not args.component or e.get("component") == args.component)
                and (not args.element or e.get("id") == args.element or e.get("component") == args.element)
                and (not args.platform or e.get("platform") == args.platform)
                and (not args.env or e.get("env") == args.env)
            )]
            if out:
                return out, "local"
        except Exception:
            pass

    return [], "none"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="get_verified_locator: 取用 locator 的唯一入口（先驗才回）")
    p.add_argument("--flow", default="", help="流程/區域 key（建議：一次批次驗整組）")
    p.add_argument("--page", default="", help="頁面語意 key")
    p.add_argument("--component", default="", help="元件語意 key")
    p.add_argument("--element", default="", help="單一元素 id 或 component")
    p.add_argument("--case", default="",
                   help="當前正在自動化的 case id（如 KQT-T500）。emit 列的 source 會蓋成這個，"
                        "讓 locator gate 對得上『這次交付的 case』——重用既有 locator 時，registry "
                        "entry 的 origin case 不等於當前 case，不帶會造成 gate 假擋。")
    p.add_argument("--platform", default="web", choices=["web", "mweb"])
    p.add_argument("--env", default="stage", help="環境：stage / sit0x / sit20x（現階段禁 prod）")
    p.add_argument("--registry", default="", help="本地 registry.json（backend 拿不到時的 fallback 來源）")
    p.add_argument("--url", default="", help="覆寫驗證 URL（預設用 entry 的 verify_url）")
    p.add_argument("--emit", default=None,
                   help="把驗證結果寫成 jsonl，供 Stop hook sender 之後 POST 回寫。"
                        f"預設寫到 {DEFAULT_EMIT_DIR}/<pid>-<ts>.jsonl（per-process，並行安全）；"
                        "傳空字串（--emit ''）可停用回寫。")
    return p


def main() -> int:
    p = _build_parser()
    args = p.parse_args()

    if not (args.flow or args.page or args.component or args.element):
        p.error("需至少提供 --flow / --page / --component / --element 其一")

    if args.env and not _VALID_ENV_RE.fullmatch(args.env):
        print(json.dumps({"error": f"env '{args.env}' 非法或為 prod；現階段只接受 stage / sit0x / sit20x",
                          "results": [], "must_remine": []}, ensure_ascii=False))
        return 3

    # 沒有 playwright 就無法履行「先驗」的硬約束 —— 明確報錯，不得回未驗 selector
    if sync_playwright is None or _verify_candidates is None:
        print(json.dumps({"error": "playwright 未安裝，無法履行『先驗才回』的硬約束。"
                          " 請 pip install playwright && playwright install chromium",
                          "results": [], "must_remine": []}, ensure_ascii=False))
        return 4

    entries, source = _gather_candidate_entries(args)

    results = []
    emit_rows = []
    if entries:
        with sync_playwright() as pw:
            for e in entries:
                url = args.url or e.get("verify_url")
                device = "iPhone 15" if e.get("platform") == "mweb" else ""
                base = {"id": e.get("id"), "element": e.get("element"),
                        "component": e.get("component"), "platform": e.get("platform"),
                        "verify_url": url}
                if not url:
                    results.append({**base, "status": "stale", "action": "remine",
                                    "reason": "no verify_url", "tried": []})
                    continue
                if _is_prod_url(url):
                    # 現階段禁打 prod：不開站,直接判 stale/remine
                    results.append({**base, "status": "stale", "action": "remine",
                                    "reason": "prod blocked（現階段禁打 prod）", "tried": []})
                    continue
                browser, _, page = _open_page(pw, device)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    v = _verify_candidates(page, e.get("selectors", []))
                except Exception as ex:
                    v = {"status": "stale", "hit": None, "checked": [], "error": str(ex)[:120]}
                finally:
                    browser.close()

                if v["status"] == "verified":
                    results.append({**base, "status": "verified", "selector": v["hit"]})
                    emit_rows.append({"id": e.get("id"), "element": e.get("element"),
                                      "page": e.get("page"),
                                      "component": e.get("component"), "flow": e.get("flow"),
                                      "selectors": [v["hit"]], "platform": e.get("platform"),
                                      "env": e.get("env"), "source": _emit_source(args.case, e.get("source")),
                                      "verify_url": url, "status": "verified",
                                      "last_verified": _now()})
                else:
                    # stale：不回任何可用 selector，只給 remine 指令
                    results.append({**base, "status": "stale", "action": "remine",
                                    "tried": v.get("checked", []), "error": v.get("error")})
                    emit_rows.append({"id": e.get("id"), "element": e.get("element"),
                                      "page": e.get("page"),
                                      "component": e.get("component"), "flow": e.get("flow"),
                                      "selectors": e.get("selectors", []), "platform": e.get("platform"),
                                      "env": e.get("env"), "source": _emit_source(args.case, e.get("source")),
                                      "verify_url": url, "status": "stale",
                                      "last_verified": _now()})

    # remine 識別碼：id 優先；後端未回存 id 時退回 component / element，避免整串 None 讓呼叫端無從辨識
    must_remine = [(r.get("id") or r.get("component") or r.get("element"))
                   for r in results if r["status"] == "stale"]

    emit_path = _resolve_emit(args.emit)
    if emit_path and emit_rows:
        try:
            os.makedirs(os.path.dirname(emit_path) or ".", exist_ok=True)
            with open(emit_path, "a", encoding="utf-8") as f:
                for row in emit_rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass  # emit 失敗不影響回傳

    print(json.dumps({"query": {"flow": args.flow, "page": args.page,
                                 "component": args.component, "element": args.element,
                                 "platform": args.platform, "env": args.env},
                       "source": source, "results": results,
                       "must_remine": must_remine}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""從「框架接下來要跑的那段 code」生出 probe steps，不要人手編。

存在理由：
  修完一顆 locator 就重跑，等於用 15~20 分鐘去問「下一顆壞不壞」。改成在同一輪 session 往下點
  （probe_live_session.py）可以省掉那些重跑 —— 但前提是**點的順序要跟框架真的會跑的順序一樣**。
  人手編 steps 是在驗自己想像的流程；從失敗那一行往下讀 AST 生出來，才是在驗框架後面的流程。

做法：
  1. 從 `--at <file>:<line>` 找到所在的 function，取那一行之後的 statement
  2. 把每個 `pages.<page>.<element>.<action>()` 轉成一行 probe 動作，順序照 code
  3. 呼叫到同 repo 其他 test step function 時往下追（預設深度 2）
  4. 每個 element 去 `pages/mobile/<platform>/` 把 locator 字面值解出來；解不出來（f-string、
     `t()` i18n、要傳參數的 method）就標 `# ⚠️ 動態` 並附原始碼位置，讓人補

限制（**輸出是草稿，不是真理**，送去 probe 前要看過）：
  - `if` / `match` 兩邊都會列出來並標註條件，因為靜態看不出當下會走哪一邊
  - `no_exception=True` 的等待是刻意的選擇性分支 → 只生 `find` 並標 `# optional`，不生 `click`
  - function 結束就停。**後面的流程在 caller 或下一個 yaml step 裡**，再用一次 `--at` 指過去
    （可給多個 `--at`，會依序接起來）

用法：
    plan_probe_steps.py --platform ios \
      --at test_steps/kkday/app/bookings/payment.py:527 --branch paypay > /tmp/probe.txt

平台：
  - **ios / android**：讀 `pages/mobile/<platform>/`，產出直接餵給 `probe_live_session.py`。
    這才是這組工具的主場 —— 一輪 15~20 分鐘、實體機同時只能一個 session，所以「同一輪內把下游
    走完」省下來的是真金白銀。
  - **web / mweb**：讀 `pages/web_playwright/kkday/<platform>/`（css locator 會標成 `css=…`）。
    產出是**要在同一個瀏覽器 session 內逐一確認的清單**，不是給 probe 吃的 —— web 沒有
    appium/裝置獨佔問題，一輪 1~3 分鐘、Playwright 隨時可以另開一個對 stage，
    所以不需要 sniff/probe 那套；但「一次修完整段、不要下一輪才發現」這條規則一樣適用。
"""

import argparse
import ast
import glob
import os
import re
import sys

ACTION_MAP = {
    "click": "click",
    "send_keys": "type",
    "clear": "click",
    "tap": "click",
}


def log(msg):
    print("[plan] %s" % msg, file=sys.stderr, flush=True)


def find_src_root(start):
    d = os.path.abspath(start)
    while d != "/":
        if os.path.isdir(os.path.join(d, "pages")) and os.path.isdir(os.path.join(d, "test_steps")):
            return d
        d = os.path.dirname(d)
    return None


METHODS = {
    # mobile（pages/element.py）
    "wait",
    "click",
    "scroll_to",
    "send_keys",
    "clear",
    "tap",
    "swipe",
    "is_present",
    "get_attribute",
    "text",
    # web/mweb（pages/playwright_element.py）—— 方法名不同，漏了就會把 `wait_for_visible`
    # 當成 element 名去 page object 找 def，然後全部標成「找不到」
    "wait_for_visible",
    "wait_for_hidden",
    "wait_for_exists",
    "wait_for_enabled",
    "double_click",
    "right_click",
    "triple_click",
    "hover",
    "inner_text",
    "is_visible",
    "is_not_visible",
    "is_checked",
    "is_enabled",
    "is_disabled",
    "get_value",
    "value",
    "locator",
    "center",
    "rect",
}


def chain_of(node):
    """把 pages.a.b.wait().click() 攤成 element 鏈 ['pages','a','b']；不是從 pages 起頭回 None。

    中間會夾 Call（`.wait()`），所以不能只走 Attribute —— 之前就是漏了這一段，導致最關鍵的
    那一行（`pages.x.y.wait().click()`）完全沒被生出來。
    """
    parts = []
    cur = node
    while True:
        if isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        elif isinstance(cur, ast.Call):
            cur = cur.func
        else:
            break
    if not isinstance(cur, ast.Name) or cur.id != "pages":
        return None
    parts.append("pages")
    parts.reverse()
    chain = []
    for p in parts:
        if p in METHODS:
            break
        chain.append(p)
    return chain


def literal_locator(func_node):
    """從 page object 的 property 裡把 Element(("xpath", "...")) 的字面值挖出來。"""
    for n in ast.walk(func_node):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "Element"):
            continue
        if not n.args or not isinstance(n.args[0], ast.Tuple) or len(n.args[0].elts) < 2:
            continue
        by, val = n.args[0].elts[0], n.args[0].elts[1]
        if isinstance(by, ast.Constant) and isinstance(val, ast.Constant):
            return str(by.value), str(val.value)
        return None, ast.dump(val)[:80]
    return None


def page_dir(src_root, platform):
    if platform in ("ios", "android"):
        return os.path.join(src_root, "pages", "mobile", platform)
    return os.path.join(src_root, "pages", "web_playwright", "kkday", platform)


def resolve_element(src_root, platform, chain):
    """chain=['pages','third_party_app_page','paypay_pay_button'] → (locator, 來源說明)。"""
    page, attr = chain[1], chain[-1]
    root = page_dir(src_root, platform)
    pat = os.path.join(root, "**", "%s.py" % page)
    files = glob.glob(pat, recursive=True) or glob.glob(os.path.join(root, "**", "*.py"), recursive=True)
    hits = []
    for f in files:
        try:
            tree = ast.parse(open(f).read())
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name == attr:
                hits.append((f, n))
    if not hits:
        return None, "在 pages/mobile/%s/ 找不到 `def %s`" % (platform, attr)
    for f, n in hits:
        got = literal_locator(n)
        if got and got[0]:
            note = "%s:%d" % (os.path.relpath(f, src_root), n.lineno)
            if len(hits) > 1:
                note += "（有 %d 個同名 def，挑了第一個解得出字面值的）" % len(hits)
            return (got[0], got[1]), note
    f, n = hits[0]
    return None, "%s:%d 的 locator 不是字面值（f-string / t() i18n / 要傳參數）" % (
        os.path.relpath(f, src_root),
        n.lineno,
    )


def action_of(call):
    """從 pages.x.y.wait().click() 這串 Call 找出最終動作與是否 no_exception。"""
    act, optional, arg_text = None, False, None
    cur = call
    while isinstance(cur, ast.Call):
        if isinstance(cur.func, ast.Attribute):
            name = cur.func.attr
            if name in ACTION_MAP and act is None:
                act = ACTION_MAP[name]
                if cur.args and isinstance(cur.args[0], ast.Constant):
                    arg_text = str(cur.args[0].value)
            if name in ("wait", "scroll_to"):
                for kw in cur.keywords:
                    if kw.arg == "no_exception" and getattr(kw.value, "value", False) is True:
                        optional = True
            cur = cur.func.value
        else:
            break
    return act, optional, arg_text


def steps_from(src_root, platform, path, from_line, branch, depth, seen):
    """回傳 probe steps 行的 list。"""
    rel = os.path.relpath(path, src_root)
    try:
        tree = ast.parse(open(path).read())
    except (OSError, SyntaxError) as e:
        return ["# ⚠️ 讀不到 %s: %r" % (rel, e)]

    target = None
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.lineno <= from_line <= (n.end_lineno or n.lineno):
            if target is None or n.lineno > target.lineno:
                target = n
    if target is None:
        return ["# ⚠️ %s:%d 不在任何 function 內" % (rel, from_line)]

    out = ["# ── 從 %s:%d（function `%s`）往下 ──" % (rel, from_line, target.name)]
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    emit(out, src_root, platform, target, from_line, branch, depth, seen, rel, funcs, path)
    out.append("# ── `%s` 到這裡結束；後面的流程在 caller 或下一個 yaml step，再給一個 --at ──" % target.name)
    return out


def outermost_calls(stmt):
    """只回傳「不是別人 func 鏈一部分」的 Call，避免 `.wait().click()` 被當兩次處理。"""
    calls = [n for n in ast.walk(stmt) if isinstance(n, ast.Call)]
    inner = set()
    for c in calls:
        cur = c.func
        while True:
            if isinstance(cur, ast.Attribute):
                cur = cur.value
            elif isinstance(cur, ast.Call):
                inner.add(id(cur))
                cur = cur.func
            else:
                break
    return [c for c in calls if id(c) not in inner]


def emit(out, src_root, platform, node, from_line, branch, depth, seen, rel, funcs, path):
    for stmt in walk_body(node, from_line, branch, out):
        for sub in outermost_calls(stmt):
            handle_call(out, src_root, platform, sub, depth, seen, rel, funcs, path, branch)


def walk_body(node, from_line, branch, out):
    """依原始順序吐出 from_line 之後的 statement；match/case 依 --branch 篩，if 兩邊都吐並標註。"""
    for stmt in ast.iter_child_nodes(node):
        if not isinstance(stmt, ast.stmt):
            continue
        if (stmt.end_lineno or stmt.lineno) < from_line:
            continue
        if isinstance(stmt, ast.Match):
            for case in stmt.cases:
                label = ast.unparse(case.pattern) if hasattr(ast, "unparse") else "?"
                if branch and branch not in label:
                    continue
                out.append("# case %s:" % label)
                for s in walk_body(case, from_line, branch, out):
                    yield s
            continue
        if isinstance(stmt, (ast.If, ast.For, ast.While, ast.Try, ast.With)):
            cond = ""
            if isinstance(stmt, ast.If) and hasattr(ast, "unparse"):
                cond = ast.unparse(stmt.test)[:100]
            out.append("# ⚠️ 條件分支（靜態看不出當下走哪邊）: %s" % cond)
            if isinstance(stmt, ast.If):
                # 條件本身常是 `xxx.wait(no_exception=True).is_present` —— 那也是要驗的一步，
                # 包成一個假 statement 送下去，否則只會留一行註解、真正該 find 的元素被漏掉。
                yield ast.Expr(value=stmt.test, lineno=stmt.test.lineno, col_offset=0)
            for s in walk_body(stmt, from_line, branch, out):
                yield s
            continue
        yield stmt


def handle_call(out, src_root, platform, call, depth, seen, rel, funcs, path, branch):
    if isinstance(call.func, ast.Attribute):
        chain = chain_of(call.func)
        if chain and len(chain) >= 3:
            act, optional, arg_text = action_of(call)
            if act is None:
                act = "find"
            got, note = resolve_element(src_root, platform, chain)
            where = "%s:%d" % (rel, call.lineno)
            if got is None:
                out.append("# ⚠️ 動態 locator，需自行填 —— %s（%s）@%s" % (".".join(chain[1:]), note, where))
                return
            by, val = got
            if by != "xpath":
                if platform in ("ios", "android"):
                    out.append("# ⚠️ 非 xpath（%s=%s），probe_live_session 只吃 xpath —— %s @%s" % (by, val, note, where))
                    return
                val = "%s=%s" % (by, val)
            if optional or act == "find":
                why = "optional（no_exception）" if optional else "只查在不在"
                out.append("find  %s   # %s %s @%s" % (val, why, note, where))
            elif act == "type":
                out.append("type  %s %s   # %s @%s" % (val, arg_text or "<文字>", note, where))
            else:
                out.append("click %s   # %s @%s" % (val, note, where))
            return

        if call.func.attr == "sleep":
            secs = call.args[0].value if call.args and isinstance(call.args[0], ast.Constant) else 1
            out.append("sleep %s" % secs)
            return

    if isinstance(call.func, ast.Name) and call.func.id in funcs and depth > 0:
        key = (path, call.func.id)
        if key in seen:
            return
        seen.add(key)
        inner = funcs[call.func.id]
        out.append("# ↓ 進入 `%s`（%s:%d）" % (call.func.id, rel, inner.lineno))
        emit(out, src_root, platform, inner, inner.lineno, branch, depth - 1, seen, rel, funcs, path)
        out.append("# ↑ 離開 `%s`" % call.func.id)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", required=True, choices=["ios", "android", "web", "mweb"])
    ap.add_argument("--at", action="append", required=True, help="<file>:<line>，可給多個依序接起來")
    ap.add_argument("--branch", help="match/case 只取這個分支（例：paypay）")
    ap.add_argument("--depth", type=int, default=2, help="往下追同檔 function 的深度")
    ap.add_argument("--repo", help="kkday-QA-automation 路徑（不給則從 --at 往上找）")
    a = ap.parse_args()

    first = a.at[0].rsplit(":", 1)[0]
    base = a.repo and os.path.join(a.repo, "QATest", "src") or None
    src_root = base if base and os.path.isdir(base) else find_src_root(os.path.dirname(first) or ".")
    if not src_root:
        log("找不到 QATest/src，請用 --repo 指定")
        return 1

    print("# probe steps —— 由 plan_probe_steps.py 從框架 code 生成，送去 probe 前請看過")
    print("# platform=%s branch=%s src=%s" % (a.platform, a.branch, src_root))
    seen = set()
    for spec in a.at:
        path, _, line = spec.rpartition(":")
        path = path if os.path.isabs(path) else os.path.join(src_root, path)
        for ln in steps_from(src_root, a.platform, path, int(line), a.branch, a.depth, seen):
            print(ln)
    if a.platform in ("ios", "android"):
        print("dump  end_of_probe")
    else:
        print("# web/mweb：這份是「同一個瀏覽器 session 內要逐一確認」的清單，不是餵 probe 的")
    return 0


if __name__ == "__main__":
    sys.exit(main())

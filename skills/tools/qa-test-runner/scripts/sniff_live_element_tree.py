#!/usr/bin/env python3
"""趁 run 還在跑，從「正在跑的那個 appium session」撈失敗畫面的元素樹＋截圖。

存在理由：
  A 類（找不到元素）要比對「畫面現在長什麼樣」，但 run 結束後 appium 已關、App 也離開那一頁，
  只剩框架自己 _handle_fail_case 截的那張 —— 而那張常常因為 appium 先死而失敗（get screen shot error）。
  另起一台 appium 去 dump 又搶不到裝置（實體機同時只能一個 session），還會在 4723/3080 留殘留。
  正解是「不另起 server、掛上現有 session 唯讀撈」：`wait()` 預設輪詢 60 秒，那就是窗口。

只發 GET（/sessions、/source、/screenshot），不點不滑不改 session 狀態，不影響跑測結果。

用法（在 run 已經起來之後、跑到失敗點之前掛上）：
    sniff_live_element_tree.py "XCUIElementTypeStaticText[@name='Pay']"
    sniff_live_element_tree.py "input_payment_amount_button" --port 10045
    sniff_live_element_tree.py "homeTxtSearch" --out /tmp/sniff --timeout 2400

trigger 給「framework 找不到的那個 locator 的一小段」即可（appium log 會原樣印出 findElement 的
value）。撈到的東西存成 <out>/<stem>_source.xml、<stem>_screen.png、<stem>_names.txt。
"""

import argparse
import base64
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

OUTPUT_ROOT = os.path.expanduser("~/Documents/QATest_Output")
IOS_PORTS = range(10100, 10200)
ANDROID_PORTS = range(10000, 10100)


def log(msg):
    print("[sniff] %s" % msg, flush=True)


def detect_port(log_dir=None):
    """從跑著的 appium 挑 platform port 段那一台；4723/3080 之類探索用的不算。

    機器上常有前幾天沒收乾淨的孤兒 appium（也在 platform port 段），所以先用 appium 自己
    `--log <path>` 指向的 run 目錄比對，只留屬於這一輪的那台；比不出來才回報要人明示。
    """
    out = subprocess.run(["pgrep", "-fl", "appium"], capture_output=True, text=True).stdout
    found = {}
    for line in out.splitlines():
        m = re.search(r"appium\s+-p\s+(\d+)", line)
        if not m:
            continue
        p = int(m.group(1))
        if p not in IOS_PORTS and p not in ANDROID_PORTS:
            continue
        lm = re.search(r"--log\s+(\S+)", line)
        found[p] = lm.group(1) if lm else ""
    if not found:
        log("找不到 platform port 段（10000-10199）的 appium —— run 還沒起來或已經結束")
        return None
    if len(found) > 1 and log_dir:
        mine = [p for p, lp in found.items() if log_dir.rstrip("/") in lp]
        if len(mine) == 1:
            log("有多台 appium %s，按 --log 路徑挑出屬於本輪的 %d" % (sorted(found), mine[0]))
            return mine[0]
    if len(found) > 1:
        log("有多台 appium %s 且無法從 --log 路徑分辨，請用 --port 明示" % sorted(found))
        return None
    return next(iter(found))


def detect_log_dir():
    dirs = [d for d in glob.glob(os.path.join(OUTPUT_ROOT, "2*_*")) if os.path.isdir(d)]
    if not dirs:
        return None
    return max(dirs, key=os.path.getmtime)


def get(port, path, timeout=30):
    url = "http://localhost:%d%s" % (port, path)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def session_id(port):
    for s in get(port, "/sessions").get("value") or []:
        sid = s.get("id") or s.get("sessionId")
        if sid:
            return sid
    return None


def log_has(log_dir, needle):
    for f in glob.glob(os.path.join(log_dir, "**", "*.log"), recursive=True):
        try:
            with open(f, errors="ignore") as fh:
                if needle in fh.read():
                    return f
        except OSError:
            pass
    return None


def summarize_names(src):
    """把 name/label/value 抽成一份好讀的清單，挑新 locator 時不用翻幾萬行 XML。"""
    lines = []
    for m in re.finditer(r"<(\w+)([^>]*)/?>", src):
        tag, attrs = m.group(1), m.group(2)
        got = {k: v for k, v in re.findall(r'(\w+)="([^"]*)"', attrs)}
        if got.get("visible") == "false" or got.get("displayed") == "false":
            continue
        ident = got.get("name") or got.get("label") or got.get("resource-id") or got.get("text")
        if not ident:
            continue
        lines.append(
            "%-34s %s%s"
            % (
                tag,
                ident,
                "  [label=%s]" % got["label"] if got.get("label") and got.get("label") != ident else "",
            )
        )
    seen, uniq = set(), []
    for line in lines:
        if line not in seen:
            seen.add(line)
            uniq.append(line)
    return "\n".join(uniq)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trigger", help="appium log 裡會出現的 locator 片段")
    ap.add_argument("--port", type=int, help="appium port（不給則自動偵測 platform port 段那台）")
    ap.add_argument("--log-dir", help="QATest_Output 下的 run 目錄（不給則取最新）")
    ap.add_argument("--out", default=None, help="輸出目錄（預設 = log-dir）")
    ap.add_argument("--timeout", type=int, default=2400, help="等 trigger 出現的秒數上限")
    a = ap.parse_args()

    log_dir = a.log_dir or detect_log_dir()
    if not log_dir:
        log("QATest_Output 下找不到 run 目錄")
        return 1
    port = a.port or detect_port(log_dir)
    if not port:
        return 1
    out_dir = a.out or log_dir
    os.makedirs(out_dir, exist_ok=True)
    log("port=%d log_dir=%s out=%s" % (port, log_dir, out_dir))

    if log_has(log_dir, a.trigger):
        log("⚠️ trigger 在 log 裡已經出現過 —— run 可能早就跑過那一點，撈到的會是別的畫面")

    log("等 trigger: %s" % a.trigger)
    hit = None
    for _ in range(a.timeout):
        hit = log_has(log_dir, a.trigger)
        if hit:
            break
        time.sleep(1)
    if not hit:
        log("等到逾時，trigger 沒出現。run 可能死在更前面，去看 run 的 output")
        return 1
    log("命中於 %s" % hit)

    sid = session_id(port)
    if not sid:
        log("appium 上沒有 active session —— 它可能剛死掉，去比對 appium log 的 SIGTERM/exit")
        return 1
    log("session=%s" % sid)

    stem = os.path.join(out_dir, "sniff_%s" % time.strftime("%H%M%S"))
    try:
        src = get(port, "/session/%s/source" % sid)["value"]
        with open(stem + "_source.xml", "w") as f:
            f.write(src)
        with open(stem + "_names.txt", "w") as f:
            f.write(summarize_names(src))
        log("元素樹 %s_source.xml（%d bytes）＋ 可見節點清單 %s_names.txt" % (stem, len(src), stem))
    except Exception as e:
        log("撈 source 失敗: %r" % (e,))
    try:
        b64 = get(port, "/session/%s/screenshot" % sid)["value"]
        with open(stem + "_screen.png", "wb") as f:
            f.write(base64.b64decode(b64))
        log("截圖 %s_screen.png" % stem)
    except Exception as e:
        log("截圖失敗: %r" % (e,))
    return 0


if __name__ == "__main__":
    sys.exit(main())

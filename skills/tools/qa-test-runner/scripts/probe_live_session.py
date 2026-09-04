#!/usr/bin/env python3
"""在 run 死在某一頁的當下，接上同一個 appium session 把「後面幾步」真的點過去。

存在理由（跟 sniff_live_element_tree.py 是一組，那支唯讀、這支會動）：
  修 locator 的驗收標準若只到「元素找得到」，那只證明了那一顆。第三方 App／自家 App 改版
  一次改一整段，於是「改一顆 → 重跑 15 分鐘 → 死在下一顆 → 再改一顆」，一個 case 花掉一整天，
  而且每一輪都在重跑前面十幾個沒問題的步驟。
  run 死在那一頁時，session 還活著、App 還停在正確位置 —— 那是唯一能「往前走幾步看還有沒有壞」
  的時機。這支就是把那個時機用掉：照 script 依序 find / click / dump，一次把整段下游的破口攤出來。

🔴 這支**會動到裝置**（送 click / tap），只准用在**已經注定失敗的那一輪**：case 已經死在
   locator 上，session 剩下的時間本來就是浪費掉的。**不要**掛在還可能通過的 run 上 —— 那會
   把跑測結果弄成假紅/假綠。所以 `--confirm-mutates` 是必填，逼你確認一次。

窗口怎麼撐開：`wait()` 沒明示 timeout 時吃 `PAGEOBJECT_DEFAULT_WAIT_TIMEOUT`（pages/element.py:31，
預設 60）。重現那一輪開跑前 export 大一點，就能把探測時間從 1 分鐘變成 10 分鐘：

    PAGEOBJECT_DEFAULT_WAIT_TIMEOUT=600 run_case.sh KQT-T7507 ios <udid>

（只影響沒帶 timeout 的等待；`timeout=2` 那種捲動嘗試不受影響，所以不會改變流程行為。）

用法：
    # 等失敗 trigger 出現，然後照 steps 檔往下點
    probe_live_session.py --after "XCUIElementTypeStaticText[@name='Pay']" \
        --steps ~/probe_paypay.txt --confirm-mutates

    # session 已經在那一頁了（例如 sniff 剛撈完），直接開始
    probe_live_session.py --steps - --confirm-mutates <<'EOF'
    find  //*[@name='Pay']
    click //*[@name='Pay']
    sleep 3
    dump  after_pay
    EOF

steps 檔**不要用手編** —— 用 `plan_probe_steps.py` 從框架接下來要跑的那段 code 生出來，
才是在驗「框架後面的流程」而不是驗你想像的流程：

    plan_probe_steps.py --platform ios --at test_steps/.../payment.py:527 --branch paypay

steps 檔語法（一行一步，`#` 開頭是註解）：
    find   <xpath>        只解析不互動，回報在不在＋座標（用來驗候選 locator）
    click  <xpath>        解析後點下去
    type   <xpath> <文字>  解析後輸入（對應框架的 send_keys）
    tap    <x> <y>        照座標點（locator 全掛時的最後手段）
    swipe  up|down [px]   捲動（對應框架的 scroll_to，逼近畫面外的元素）
    dump   [label]        存 source.xml + names.txt + screen.png
    sleep  <秒>
    back                  送返回（iOS 用 navigate back、Android 用 keycode 4）

每一步都會印結果；任何一步找不到元素**不中斷**，繼續往下做並在結尾列出「壞掉的步驟」清單，
因為目的就是「一次攤出所有破口」，不是跑通。
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sniff_live_element_tree import (  # noqa: E402
    detect_log_dir,
    detect_port,
    log_has,
    session_id,
    summarize_names,
)


def log(msg):
    print("[probe] %s" % msg, flush=True)


def req(port, method, path, body=None, timeout=60):
    url = "http://localhost:%d%s" % (port, path)
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def find_element(port, sid, xpath):
    try:
        v = req(port, "POST", "/session/%s/element" % sid, {"using": "xpath", "value": xpath})["value"]
    except urllib.error.HTTPError:
        return None
    return v.get("ELEMENT") or v.get("element-6066-11e4-a52e-4f735466cecf")


def rect(port, sid, eid):
    try:
        return req(port, "GET", "/session/%s/element/%s/rect" % (sid, eid))["value"]
    except Exception:
        return None


def dump(port, sid, out_dir, label):
    stem = os.path.join(out_dir, "probe_%s_%s" % (time.strftime("%H%M%S"), label or "dump"))
    try:
        src = req(port, "GET", "/session/%s/source" % sid)["value"]
        with open(stem + "_source.xml", "w") as f:
            f.write(src)
        with open(stem + "_names.txt", "w") as f:
            f.write(summarize_names(src))
        log("dump 元素樹 %s_source.xml（%d bytes）＋清單 _names.txt" % (stem, len(src)))
    except Exception as e:
        log("dump source 失敗: %r" % (e,))
    try:
        b64 = req(port, "GET", "/session/%s/screenshot" % sid)["value"]
        with open(stem + "_screen.png", "wb") as f:
            f.write(base64.b64decode(b64))
        log("dump 截圖 %s_screen.png" % stem)
    except Exception as e:
        log("dump 截圖失敗: %r" % (e,))


def platform_of(port, sid):
    try:
        caps = req(port, "GET", "/session/%s" % sid)["value"]
        return str(caps.get("platformName", "")).lower()
    except Exception:
        return ""


def run_step(port, sid, out_dir, line):
    """回傳 (ok, 說明)。ok=False 只記帳不中斷 —— 目的是一次攤出所有破口。"""
    parts = line.split()
    op = parts[0].lower()
    arg = line[len(parts[0]) :].strip()

    if op == "sleep":
        time.sleep(float(parts[1]))
        return True, "睡 %s 秒" % parts[1]

    if op == "dump":
        dump(port, sid, out_dir, arg or None)
        return True, "已 dump"

    if op == "back":
        if platform_of(port, sid).startswith("android"):
            req(port, "POST", "/session/%s/appium/device/press_keycode" % sid, {"keycode": 4})
        else:
            req(port, "POST", "/session/%s/back" % sid)
        return True, "已返回"

    if op == "tap":
        x, y = int(parts[1]), int(parts[2])
        req(
            port,
            "POST",
            "/session/%s/actions" % sid,
            {
                "actions": [
                    {
                        "type": "pointer",
                        "id": "finger1",
                        "parameters": {"pointerType": "touch"},
                        "actions": [
                            {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                            {"type": "pointerDown", "button": 0},
                            {"type": "pause", "duration": 100},
                            {"type": "pointerUp", "button": 0},
                        ],
                    }
                ]
            },
        )
        return True, "已點 (%d,%d)" % (x, y)

    if op == "swipe":
        direction = parts[1].lower()
        px = int(parts[2]) if len(parts) > 2 else 400
        size = req(port, "GET", "/session/%s/window/rect" % sid)["value"]
        cx, cy = size["width"] // 2, size["height"] // 2
        dy = -px if direction == "up" else px
        req(
            port,
            "POST",
            "/session/%s/actions" % sid,
            {
                "actions": [
                    {
                        "type": "pointer",
                        "id": "finger1",
                        "parameters": {"pointerType": "touch"},
                        "actions": [
                            {"type": "pointerMove", "duration": 0, "x": cx, "y": cy - dy // 2},
                            {"type": "pointerDown", "button": 0},
                            {"type": "pause", "duration": 100},
                            {"type": "pointerMove", "duration": 600, "x": cx, "y": cy + dy // 2},
                            {"type": "pointerUp", "button": 0},
                        ],
                    }
                ]
            },
        )
        return True, "已 swipe %s %dpx" % (direction, px)

    if op in ("find", "click", "type"):
        if op == "type":
            xpath, _, text = arg.rpartition(" ")
            if not xpath:
                return False, "type 要給 <xpath> <文字>"
        else:
            xpath, text = arg, None
        eid = find_element(port, sid, xpath)
        if not eid:
            return False, "找不到 %s" % xpath
        r = rect(port, sid, eid)
        where = " @%s" % r if r else ""
        if op == "find":
            return True, "找到%s" % where
        try:
            if op == "type":
                req(port, "POST", "/session/%s/element/%s/value" % (sid, eid), {"text": text})
                return True, "找到%s 並已輸入 %r" % (where, text)
            req(port, "POST", "/session/%s/element/%s/click" % (sid, eid))
        except urllib.error.HTTPError as e:
            return False, "找到%s 但互動失敗: %s" % (where, e)
        return True, "找到%s 並已點擊" % where

    return False, "不認得的動作 %r" % op


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", required=True, help="steps 檔路徑，`-` 讀 stdin")
    ap.add_argument("--confirm-mutates", action="store_true", help="必填：確認知道這會動到裝置")
    ap.add_argument("--after", help="先等這段字出現在 appium/qatest log 才開始（通常是失敗的 locator）")
    ap.add_argument("--port", type=int, help="appium port（不給則自動偵測 platform port 段那台）")
    ap.add_argument("--log-dir", help="QATest_Output 下的 run 目錄（不給則取最新）")
    ap.add_argument("--out", help="輸出目錄（預設 = log-dir）")
    ap.add_argument("--timeout", type=int, default=2400, help="等 --after 的秒數上限")
    a = ap.parse_args()

    if not a.confirm_mutates:
        log("這支會送 click/tap 到實機，只准用在已注定失敗的那一輪；要跑請加 --confirm-mutates")
        return 2

    raw = sys.stdin.read() if a.steps == "-" else open(a.steps).read()
    steps = [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not steps:
        log("steps 是空的")
        return 1

    log_dir = a.log_dir or detect_log_dir()
    if not log_dir:
        log("QATest_Output 下找不到 run 目錄")
        return 1
    port = a.port or detect_port(log_dir)
    if not port:
        return 1
    out_dir = a.out or log_dir
    os.makedirs(out_dir, exist_ok=True)
    log("port=%d log_dir=%s out=%s steps=%d" % (port, log_dir, out_dir, len(steps)))

    if a.after:
        log("等 trigger: %s" % a.after)
        hit = None
        for _ in range(a.timeout):
            hit = log_has(log_dir, a.after)
            if hit:
                break
            time.sleep(1)
        if not hit:
            log("等到逾時，trigger 沒出現；run 可能死在更前面")
            return 1
        log("命中於 %s" % hit)

    sid = session_id(port)
    if not sid:
        log("appium 上沒有 active session —— 窗口已經關了（run 結束或 appium 死了）")
        return 1
    log("session=%s" % sid)

    broken = []
    for i, line in enumerate(steps, 1):
        try:
            ok, msg = run_step(port, sid, out_dir, line)
        except Exception as e:
            ok, msg = False, "爆炸: %r" % (e,)
        log("%s [%d/%d] %s → %s" % ("✅" if ok else "❌", i, len(steps), line, msg))
        if not ok:
            broken.append((i, line, msg))

    if broken:
        log("這段下游有 %d 個破口，一次修完再重跑：" % len(broken))
        for i, line, msg in broken:
            log("  [%d] %s → %s" % (i, line, msg))
    else:
        log("整段下游都走得通 —— 這次的修法可以直接進重跑驗證")
    return 0


if __name__ == "__main__":
    sys.exit(main())

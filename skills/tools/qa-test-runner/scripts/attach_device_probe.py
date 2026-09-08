#!/usr/bin/env python3
"""
接上「run 已結束、App 還停在失敗畫面」的實機，直接拿 page object 的 locator 實打。

補的是 sniff_live_element_tree.py / probe_live_session.py 都補不到的那個洞：那兩支要有一個
活著的 appium session 可以 attach，而 run 一結束 session 就沒了。於是「跑完才發現要看那一頁」
時，手上只剩「再燒 15~20 分鐘重跑一輪」或「讀 code 猜」—— 實測會選後者，然後連猜三個互相
矛盾的故事（2026-09-08 KQT-T7516：Safari 回跳 → scroll_to 慣性滑動 → locator union，全是推論）。

iOS App 在 run 結束後仍留在前景那一頁，所以那個畫面其實還在，只是沒有 session。這支用
noReset 接上現況（不重啟 App、不清資料），然後：

  1. 印出可見節點清單（name/label/type/rect），挑新 locator 用
  2. 對每個 --xpath 印出「解析到幾個節點、各自的 rect 與 name」
     —— union（`|`）與 `following::` 這類會靜默選到別的節點的寫法，這裡一眼看得出來
  3. 加 --tap 才會真的點下去（並在點前後各存一張截圖，證明落點）

跑法（唯讀，不點）：
  python3 attach_device_probe.py --udid <udid> --bundle-id com.kkday.member \
      --xpath "//XCUIElementTypeStaticText[@name='付款方式']"

要點下去（會動到裝置）：
  ... --xpath "<locator>" --tap --confirm-mutates
"""
import argparse
import json
import os
import subprocess
import sys
import time

OUT_DIR_DEFAULT = os.path.expanduser("~/Documents/QATest_Output/_attach_probe")
IOS_PORT = 10199
WDA_PORT_DEFAULT = 8158


def _start_appium(port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        ["appium", "-p", str(port), "--relaxed-security"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        time.sleep(1)
        try:
            import urllib.request

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=2) as r:
                if r.status == 200:
                    return proc
        except Exception:
            continue
    proc.kill()
    sys.exit(f"appium 起不來 (port {port})")


def _visible_nodes(driver, limit: int) -> list:
    nodes = []
    for el in driver.find_elements("xpath", "//*[@visible='true']"):
        try:
            name = el.get_attribute("name") or ""
            label = el.get_attribute("label") or ""
            if not name and not label:
                continue
            r = el.rect
            nodes.append(
                {
                    "type": el.tag_name,
                    "name": name,
                    "label": label if label != name else "",
                    "rect": [r["x"], r["y"], r["width"], r["height"]],
                }
            )
        except Exception:
            continue
        if len(nodes) >= limit:
            break
    return nodes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--udid", required=True)
    ap.add_argument("--bundle-id", required=True)
    ap.add_argument("--xpath", action="append", default=[], help="要實打的 locator，可重複")
    ap.add_argument("--tap", action="store_true", help="真的點下去（需同時給 --confirm-mutates）")
    ap.add_argument("--confirm-mutates", action="store_true")
    ap.add_argument("--tap-xy", metavar="X,Y", help="照座標硬點（locator 全掛時用來確認流程本身還對不對）")
    ap.add_argument("--type", dest="type_text", help="app 層 typeText（mobile: keys），不經元素")
    ap.add_argument("--send-keys", help="對第一個 --xpath 解析到的元素 send_keys，等同框架 Element.input")
    ap.add_argument("--tap-keys", help="逐一點 XCUIElementTypeKey 鍵盤按鍵輸入這串字（locator 全無時的唯一路徑）")
    ap.add_argument("--port", type=int, default=IOS_PORT)
    ap.add_argument("--wda-port", type=int, default=WDA_PORT_DEFAULT)
    ap.add_argument("--out", default=OUT_DIR_DEFAULT)
    ap.add_argument("--max-nodes", type=int, default=200)
    args = ap.parse_args()

    mutating = (args.tap, args.tap_xy, args.type_text, args.send_keys, args.tap_keys)
    if any(mutating) and not args.confirm_mutates:
        sys.exit("--tap / --tap-xy / --type / --send-keys / --tap-keys 會動到裝置，必須同時給 --confirm-mutates")

    try:
        from appium import webdriver
        from appium.options.ios import XCUITestOptions
    except ImportError:
        sys.exit("找不到 appium client；請用 framework repo 的 venv 跑這支")

    os.makedirs(args.out, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    appium_proc = _start_appium(args.port)

    opts = XCUITestOptions()
    opts.set_capability("udid", args.udid)
    opts.set_capability("bundleId", args.bundle_id)
    opts.set_capability("noReset", True)
    opts.set_capability("fullReset", False)
    opts.set_capability("wdaLocalPort", args.wda_port)
    opts.set_capability("newCommandTimeout", 600)
    opts.set_capability("usePrebuiltWDA", True)

    driver = None
    try:
        driver = webdriver.Remote(f"http://127.0.0.1:{args.port}", options=opts)

        src_path = os.path.join(args.out, f"{stamp}_source.xml")
        with open(src_path, "w") as f:
            f.write(driver.page_source)
        shot_path = os.path.join(args.out, f"{stamp}_screen.png")
        driver.get_screenshot_as_file(shot_path)

        nodes = _visible_nodes(driver, args.max_nodes)
        nodes_path = os.path.join(args.out, f"{stamp}_nodes.json")
        with open(nodes_path, "w") as f:
            json.dump(nodes, f, ensure_ascii=False, indent=1)

        print(f"source : {src_path}")
        print(f"screen : {shot_path}")
        print(f"nodes  : {nodes_path}  ({len(nodes)} 個可見節點)")
        print("\n=== 可見節點（依畫面 y 排序）===")
        for n in sorted(nodes, key=lambda x: x["rect"][1]):
            extra = f" label={n['label']!r}" if n["label"] else ""
            print(f"  y={n['rect'][1]:>5} h={n['rect'][3]:>4} {n['type']:<28} name={n['name']!r}{extra}")

        first_els = []
        for xp in args.xpath:
            print(f"\n=== xpath: {xp}")
            try:
                els = driver.find_elements("xpath", xp)
            except Exception as e:
                print(f"  !! 解析失敗: {type(e).__name__}: {e}")
                continue
            if xp == args.xpath[0]:
                first_els = els
            print(f"  解析到 {len(els)} 個節點" + ("  ← union/following:: 選到多個，框架會取第一個" if len(els) > 1 else ""))
            for i, el in enumerate(els):
                try:
                    r = el.rect
                    print(
                        f"  [{i}] {el.tag_name:<28} name={el.get_attribute('name')!r} "
                        f"visible={el.get_attribute('visible')} rect=({r['x']},{r['y']},{r['width']},{r['height']})"
                    )
                except Exception as e:
                    print(f"  [{i}] 取屬性失敗: {e}")
            if args.tap and els:
                before = os.path.join(args.out, f"{stamp}_tap_before.png")
                after = os.path.join(args.out, f"{stamp}_tap_after.png")
                driver.get_screenshot_as_file(before)
                els[0].click()
                time.sleep(2)
                driver.get_screenshot_as_file(after)
                print(f"  已點第 [0] 個；截圖 before={before} after={after}")

        if args.tap_xy:
            x, y = (int(v) for v in args.tap_xy.split(","))
            before = os.path.join(args.out, f"{stamp}_tapxy_before.png")
            after = os.path.join(args.out, f"{stamp}_tapxy_after.png")
            driver.get_screenshot_as_file(before)
            driver.execute_script("mobile: tap", {"x": x, "y": y})
            time.sleep(2)
            driver.get_screenshot_as_file(after)
            src2 = os.path.join(args.out, f"{stamp}_tapxy_source.xml")
            with open(src2, "w") as f:
                f.write(driver.page_source)
            print(f"\n=== tap ({x},{y})")
            print(f"  截圖 before={before} after={after}")
            print(f"  點完的元素樹 {src2}")

        if args.send_keys:
            print(f"\n=== send_keys {args.send_keys!r}（等同框架 Element.input）")
            if not first_els:
                print("  !! 第一個 --xpath 沒解析到元素，無法 send_keys")
            else:
                before = os.path.join(args.out, f"{stamp}_sendkeys_before.png")
                after = os.path.join(args.out, f"{stamp}_sendkeys_after.png")
                driver.get_screenshot_as_file(before)
                try:
                    first_els[0].send_keys(args.send_keys)
                    print("  send_keys 沒拋錯（不代表真的進到欄位，看 after 截圖）")
                except Exception as e:
                    print(f"  !! send_keys 拋錯: {type(e).__name__}: {e}")
                time.sleep(2)
                driver.get_screenshot_as_file(after)
                print(f"  截圖 before={before} after={after}")

        if args.tap_keys:
            print(f"\n=== tap_keys {args.tap_keys!r}（逐鍵點 XCUIElementTypeKey，{len(args.tap_keys)} 鍵）")
            before = os.path.join(args.out, f"{stamp}_tapkeys_before.png")
            after = os.path.join(args.out, f"{stamp}_tapkeys_after.png")
            driver.get_screenshot_as_file(before)
            failed = []
            for ch in args.tap_keys:
                try:
                    driver.find_element("xpath", f"//XCUIElementTypeKey[@name='{ch}']").click()
                except Exception as e:
                    failed.append(f"{ch}({type(e).__name__})")
            time.sleep(2)
            driver.get_screenshot_as_file(after)
            print(f"  點不到的鍵: {failed or '無'}")
            print(f"  截圖 before={before} after={after}")

        if args.type_text:
            before = os.path.join(args.out, f"{stamp}_type_before.png")
            after = os.path.join(args.out, f"{stamp}_type_after.png")
            driver.get_screenshot_as_file(before)
            driver.execute_script("mobile: keys", {"keys": list(args.type_text)})
            time.sleep(2)
            driver.get_screenshot_as_file(after)
            print(f"\n=== type {args.type_text!r}（{len(args.type_text)} 字）")
            print(f"  截圖 before={before} after={after}")
        return 0
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        appium_proc.kill()


if __name__ == "__main__":
    sys.exit(main())

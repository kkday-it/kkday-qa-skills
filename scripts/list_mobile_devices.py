#!/usr/bin/env python3
"""列出目前在線的 mobile 實體機/模擬器 —— 給主對話在 spawn mobile automator「前」挑裝置用。

為什麼要這支：接多隻裝置時，automator（subagent）不能問人，若「隨便抓一隻」可能跑到
別人正在用的、或錯的 OS 版本。故主對話先跑這支列出來 → 互動模式問使用者選哪隻 → 把選定
的 udid 傳進 automator prompt；自主模式套安全預設（見 --pick）。

來源：
  - iOS   : `idb list-targets`（實體機 kind=device；模擬器 kind=simulator）
  - Android: `adb devices -l`（狀態 device 才算在線；unauthorized/offline 標出但不選）

用法：
  python3 list_mobile_devices.py                 # 人看：分平台列表
  python3 list_mobile_devices.py --json          # 機讀：結構化，給主對話挑
  python3 list_mobile_devices.py --platform ios  # 只列某平台
  python3 list_mobile_devices.py --json --pick    # 自主/harness 模式：auto_pick=第一隻在線實體機
"""
import argparse
import json
import re
import shutil
import subprocess
import sys


def _run(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return out.stdout or ""
    except Exception:
        return ""


def list_ios():
    """回 [{platform, udid, name, os, kind, state}]。kind=device 為實體機。"""
    if not shutil.which("idb"):
        return []
    devices = []
    for ln in _run(["idb", "list-targets"]).splitlines():
        ln = ln.strip()
        if not ln or "|" not in ln:
            continue
        # 例：`C iPhone 15 | 00008120-xxxx | Booted | device | iOS 17.7.2 | arm64e | /tmp/...sock`
        parts = [p.strip() for p in ln.split("|")]
        if len(parts) < 5:
            continue
        name = re.sub(r"^[A-Za-z]\s+", "", parts[0]).strip()  # 去開頭單字母欄
        udid, state, kind = parts[1], parts[2], parts[3]
        osver = parts[4]
        devices.append({
            "platform": "ios",
            "udid": udid,
            "name": name,
            "os": osver,
            "kind": kind,          # device=實體機 / simulator=模擬器
            "state": state,        # Booted / Shutdown ...
        })
    return devices


def list_android():
    """回 [{platform, udid, name, os, kind, state}]。"""
    if not shutil.which("adb"):
        return []
    devices = []
    lines = _run(["adb", "devices", "-l"]).splitlines()
    for ln in lines[1:]:  # 首行是 "List of devices attached"
        ln = ln.strip()
        if not ln:
            continue
        m = re.match(r"^(\S+)\s+(\S+)(.*)$", ln)
        if not m:
            continue
        serial, state, rest = m.group(1), m.group(2), m.group(3)
        model = ""
        mm = re.search(r"model:(\S+)", rest)
        if mm:
            model = mm.group(1)
        kind = "simulator" if serial.startswith("emulator-") else "device"
        devices.append({
            "platform": "android",
            "udid": serial,
            "name": model or serial,
            "os": "",
            "kind": kind,
            "state": state,        # device=在線可用 / unauthorized / offline
        })
    return devices


def _online(d):
    if d["platform"] == "ios":
        return d["state"].lower() == "booted"
    return d["state"].lower() == "device"


def main():
    p = argparse.ArgumentParser(description="列出在線 mobile 裝置給主對話挑")
    p.add_argument("--platform", choices=["ios", "android"], help="只列某平台")
    p.add_argument("--json", action="store_true", help="輸出 JSON（給主對話挑）")
    p.add_argument("--pick", action="store_true",
                   help="自主/harness 模式：附 auto_pick＝各平台第一隻在線實體機（多隻也直接取第一隻、不問）")
    a = p.parse_args()

    devices = []
    if a.platform in (None, "ios"):
        devices += list_ios()
    if a.platform in (None, "android"):
        devices += list_android()

    if a.json:
        out = {"devices": devices}
        if a.pick:
            pick = {}
            for plat in ("ios", "android"):
                online_real = [d for d in devices
                               if d["platform"] == plat and d["kind"] == "device" and _online(d)]
                # harness/自主模式：直接取第一隻在線實體機（多隻也不問、不 block）。
                # 互動模式才把多隻列給使用者選——那是主對話的事，不在這支。
                pick[plat] = online_real[0]["udid"] if online_real else None
            out["auto_pick"] = pick
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if not devices:
        print("（沒偵測到任何 mobile 裝置；確認 idb / adb 已裝、裝置已連）")
        return 0
    for plat in ("ios", "android"):
        ds = [d for d in devices if d["platform"] == plat]
        if not ds:
            continue
        print(f"── {plat.upper()} ──")
        for i, d in enumerate(ds, 1):
            flag = "✓在線" if _online(d) else f"✗{d['state']}"
            tag = "實體機" if d["kind"] == "device" else "模擬器"
            print(f"  [{i}] {d['name']}  {d['os']}  ({tag}) {flag}")
            print(f"       udid={d['udid']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

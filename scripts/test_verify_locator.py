#!/usr/bin/env python3
"""
verify_locator / locator_valve 的 platform 閘門單元測試（不需 playwright / 不打網路）。

守的是一個會靜默造成誤判的洞：web 與 mweb 是兩套 DOM，靠 UA 切換。這兩支腳本原本
「不明示 platform 就用桌面 viewport」，於是做 mweb 卻忘了帶旗標時**不會報錯**，只會讓每個
候選都 stale → 判成「locator 過期」→ 去改一條其實正確的 locator。2026-09-08 KQT-T11835
就是這樣被誤診的（真因是 tag 從 <div> 改成 <a>）。

所以這裡驗的是「忘了帶會被擋下」本身 —— 這條規則寫在文件裡會被漏讀，只有硬擋才有效，
而硬擋很容易在日後被「順手加回預設值」而失效，故用測試釘住。

跑法：python3 scripts/test_verify_locator.py
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import locator_valve as gvl  # noqa: E402
import verify_locator as vl  # noqa: E402


def _args(**kw):
    base = {"device": "", "platform": ""}
    base.update(kw)
    return argparse.Namespace(**base)


def test_platform_mweb_maps_to_framework_device():
    assert vl._resolve_device(_args(platform="mweb")) == vl.MWEB_DEVICE


def test_platform_web_maps_to_desktop():
    assert vl._resolve_device(_args(platform="web")) == ""


def test_missing_platform_is_rejected():
    try:
        vl._resolve_device(_args())
    except ValueError as e:
        assert "--platform" in str(e), f"錯誤訊息要指出缺哪個旗標：{e}"
        return
    raise AssertionError("不明示 platform 必須擋下，不可回退成桌面 viewport")


def test_explicit_device_still_honoured():
    assert vl._resolve_device(_args(device="Pixel 7")) == "Pixel 7"


def test_valve_platform_is_required():
    parser = gvl._build_parser()
    stderr, sys.stderr = sys.stderr, open(os.devnull, "w")  # argparse 會印 usage，測試輸出留乾淨
    try:
        parser.parse_args(["--flow", "x"])
    except SystemExit:
        return
    finally:
        sys.stderr.close()
        sys.stderr = stderr
    raise AssertionError("locator_valve 的 --platform 必須是必填，不可有預設值")


def test_valve_shares_one_device_constant():
    assert gvl.MWEB_DEVICE == vl.MWEB_DEVICE, "兩支若各寫一份 device 名稱就會 drift"


def test_tag_relaxation_targets_only_tag_steps():
    relaxed = vl._TAG_STEP_RE.sub("*", "//div[contains(@class,'x')][.//span[text()='a']]")
    assert relaxed == "//*[contains(@class,'x')][.//*[text()='a']]"


def test_tag_relaxation_leaves_tagless_xpath_alone():
    xp = "//*[@id='a']"
    assert vl._TAG_STEP_RE.sub("*", xp) == xp, "已是 * 的不該被當成有 tag 假設而重複診斷"


def main():
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            failures += 1
            print(f"FAIL {name}: {e}")
    total = sum(1 for n in globals() if n.startswith("test_"))
    print(f"\n{total - failures} passed" + (f", {failures} failed" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

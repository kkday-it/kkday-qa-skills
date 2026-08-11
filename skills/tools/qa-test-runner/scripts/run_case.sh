#!/usr/bin/env bash
# run_case.sh — 跑 qatest case 的「唯一入口」，把 agent 每次都會漏的東西綁死：
#   1) HEADLESS=1（web/mweb 不彈實體瀏覽器）
#   2) 正確 venv（repo 根的 venv，不是空殼 QATest/venv，也不是缺 pymouse 的系統 python3）
#   3) web/mweb 自動加 --use_driver playwright
#   4) **不猜 clone**：多 clone 環境抓錯 clone 會產出 `total 0 cases` 假綠（見下方 QA_REPO）
#   5) **跑前先確認 case 真的在這個 clone 裡**（yaml grep），沒有就直接失敗，不讓假綠發生
#   6) android 跑前先移除殘留的 appium server apk（版本不符會讓 UiAutomator2 instrumentation 崩）
#
# 用法：run_case.sh <caseid> <platform> [device]
#   e.g. run_case.sh KQT-T37931 web
#        run_case.sh KQT-T37193 android <adb serial 或 ip:5555>
#        run_case.sh KQT-T37193 ios <iphone udid>
# platform: web | mweb | ios | android
# device  ：選填。app 才有意義 —— 傳下去當 `--device`，android 同時當 adb serial。
#           **同一支手機同時有 USB + wifi 兩個 transport 時必填**，否則本 script 拒絕猜。
#
# 選項（env）：
#   QA_REPO=<clone 絕對路徑>
#             指定要用哪個 clone。很多人本機同時有數個 clone（web / app / 測試各一份），
#             各在不同 branch。抓錯 clone 的下場是 `0 failed, 0 passed (total 0 cases)`
#             —— 長得像通過，其實根本沒跑。未設時本 script 只接受「cwd 所在的 clone」，
#             cwd 不在任何 clone 內就直接失敗，不再依序猜。
#   HEADED=1  只對 web/mweb 有效 —— 彈實體瀏覽器（headed）供人「觀看自動化流程」。
#             預設 headless。app 走實體機本來就看得到，此旗標對 app 無意義。
#             ⚠️ 框架讀 bool(getenv("HEADLESS"))，設 HEADLESS=0 仍會 headless（非空字串為真），
#             故 headed 唯一正解是「完全不 export HEADLESS」——本 wrapper 已處理，別自己補設 0。
#   SKIP_APPIUM_CLEAN=1  跳過 android 的 appium apk 清除（除非你確知不需要，別設）。
#
# 存在理由：headless / venv / 選對 clone 這些規範「寫在 skill 裡靠 agent 記得讀」會漏（實測會）。
# 綁進單一入口，讓「漏」在結構上不可能發生，而不是靠自律。
set -euo pipefail

CASEID="${1:?用法: run_case.sh <caseid> <platform> [device]，例 run_case.sh KQT-T37931 web}"
PLATFORM="${2:?需指定 platform: web|mweb|ios|android}"
DEVICE="${3:-}"

is_clone() { [ -d "$1/QATest/src" ] && [ -f "$1/venv/bin/activate" ]; }

# ── 選 clone：明示優先，其次 cwd 所在的 clone，絕不依序猜 ────────────────
REPO=""
if [ -n "${QA_REPO:-}" ]; then
  if ! is_clone "$QA_REPO"; then
    echo "ERROR: QA_REPO='$QA_REPO' 不是有效 clone（需含 QATest/src 且 venv/bin/activate）" >&2
    echo "       注意 QATest/venv 常是空殼；正確 venv 在 repo 根目錄。" >&2
    exit 1
  fi
  REPO="$QA_REPO"
else
  d="$PWD"
  while [ "$d" != "/" ]; do
    if is_clone "$d"; then REPO="$d"; break; fi
    d="$(dirname "$d")"
  done
fi
if [ -z "$REPO" ]; then
  echo "ERROR: 無法決定要用哪個 clone。cwd 不在任何 kkday-QA-automation clone 內。" >&2
  echo "       請用 QA_REPO 明示（本機常有多個 clone，各在不同 branch，抓錯會產生假綠）：" >&2
  for c in "$HOME"/kkday-QA-automation "$HOME"/*/kkday-QA-automation \
           "$HOME"/*/*/kkday-QA-automation "$HOME"/*/*/*/kkday-QA-automation; do
    is_clone "$c" && echo "         QA_REPO=$c $(cd "$c" && git rev-parse --abbrev-ref HEAD 2>/dev/null)" >&2
  done
  exit 1
fi

# ── 假綠防線：case 不在這個 clone 的 yaml 裡就別跑 ──────────────────────
# `total 0 cases` 不是通過，是「這個 clone 沒有這條 case」。多 clone / 忘記 checkout 分支時常見。
if [ -d "$REPO/QATestData/cases/yaml" ] \
   && ! grep -rqs "^\s*${CASEID}:" "$REPO/QATestData/cases/yaml"; then
  echo "ERROR: 在 $REPO 的 QATestData/cases/yaml 找不到 '$CASEID:'。" >&2
  echo "       跑下去只會得到 '0 failed, 0 passed (total 0 cases)' 假綠。" >&2
  echo "       確認：(a) clone 選對了嗎（QA_REPO=...）(b) 分支 checkout 了嗎（實作可能在別的 branch）" >&2
  exit 1
fi

# ── platform 決定 driver / headless ───────────────────────────────────
EXTRA=()
case "$PLATFORM" in
  web|mweb)
    if [ "${HEADED:-0}" = "1" ]; then
      echo "[run_case] HEADED=1 → 彈實體瀏覽器（headed），供人觀看自動化流程"
    else
      export HEADLESS=1
    fi
    EXTRA+=(--use_driver playwright)
    ;;
  ios)
    [ -n "$DEVICE" ] && EXTRA+=(--device "$DEVICE")
    ;;
  android)
    [ -n "$DEVICE" ] && EXTRA+=(--device "$DEVICE")
    # 殘留的 appium server apk（版本與 driver 不符）會讓 UiAutomator2 instrumentation 直接崩，
    # 症狀是跑到一半噴 "cannot be proxied to UiAutomator2 server because the instrumentation
    # process is not running (probably crashed)" + socket hang up —— 看起來像 case 壞了，其實是環境。
    # 每輪跑前先移除，讓 appium 自己重裝對應版本。
    if [ "${SKIP_APPIUM_CLEAN:-0}" != "1" ]; then
      SERIAL="$DEVICE"
      if [ -z "$SERIAL" ]; then
        DEVS=$(adb devices | awk '/\tdevice$/{print $1}')
        N=$(printf '%s\n' "$DEVS" | grep -c . || true)
        if [ "$N" = "1" ]; then
          SERIAL="$DEVS"
        else
          echo "ERROR: adb 接到 $N 個 device，請用第三個參數明示要哪一個：" >&2
          printf '         %s\n' $DEVS >&2
          echo "       （同一支手機的 USB serial 與 wifi <ip>:5555 會各算一個）" >&2
          exit 1
        fi
      fi
      echo "[run_case] 清除 $SERIAL 上殘留的 appium server apk"
      for pkg in io.appium.uiautomator2.server.test io.appium.uiautomator2.server io.appium.settings; do
        out=$(adb -s "$SERIAL" uninstall "$pkg" 2>&1) || true
        echo "[run_case]   uninstall $pkg → $out"
      done
    fi
    ;;
  *)
    echo "ERROR: platform 需為 web|mweb|ios|android，收到 '$PLATFORM'" >&2
    exit 1
    ;;
esac

# shellcheck disable=SC1091
source "$REPO/venv/bin/activate"
cd "$REPO/QATest/src"
echo "[run_case] repo=$REPO ($(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null)) caseid=$CASEID platform=$PLATFORM HEADLESS=${HEADLESS:-<unset>} extra=${EXTRA[*]:-<none>}"
# macOS 內建 bash 3.2 在 set -u 下展開空陣列會噴 unbound variable（ios/android 走這條），
# 故用 ${arr[@]+...} 形式：陣列為空就整段不展開。
exec python -m qatest run --caseid "$CASEID" --platform "$PLATFORM" ${EXTRA[@]+"${EXTRA[@]}"}

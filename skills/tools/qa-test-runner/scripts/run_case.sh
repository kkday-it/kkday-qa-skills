#!/usr/bin/env bash
# run_case.sh — 跑 qatest case 的「唯一入口」，把 agent 每次都會漏的東西綁死：
#   1) HEADLESS=1（web/mweb 不彈實體瀏覽器）
#   2) 正確 venv（repo 根的 venv，不是空殼 QATest/venv，也不是缺 pymouse 的系統 python3）
#   3) web/mweb 自動加 --use_driver playwright
#   4) **不猜 clone**：多 clone 環境抓錯 clone 會產出 `total 0 cases` 假綠（見下方 QA_REPO）
#   5) **跑前先確認 case 真的在這個 clone 裡**（yaml grep），沒有就直接失敗，不讓假綠發生
#   6) android 跑前先移除殘留的 appium server apk（版本不符會讓 UiAutomator2 instrumentation 崩）
#   7) app 跑前先砍掉「同一個 platform」殘留的 appium server（跨平台的不動，見下方 SKIP_APPIUM_KILL）
#
# 用法：run_case.sh <caseid|caseid,caseid,...> <platform> [device]
#   e.g. run_case.sh KQT-T37931 web
#        run_case.sh KQT-T7490,KQT-T7494,KQT-T7495 android <serial>   # 批量：一次 run 跑完
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
#   SKIP_APPIUM_KILL=1   跳過「砍同 platform 殘留 appium server」。**唯一該設的情境**：你正在
#             同一個 platform、不同實體機上刻意平行跑兩條 run —— 此時預設行為會把另一條的
#             appium 一起砍掉。其餘情況別設。
#
# 存在理由：headless / venv / 選對 clone 這些規範「寫在 skill 裡靠 agent 記得讀」會漏（實測會）。
# 綁進單一入口，讓「漏」在結構上不可能發生，而不是靠自律。
set -euo pipefail

CASEID_ARG="${1:?用法: run_case.sh <caseid|caseid,caseid,...> <platform> [device]，例 run_case.sh KQT-T37931 web}"
PLATFORM="${2:?需指定 platform: web|mweb|ios|android}"
DEVICE="${3:-}"

# 批量：第一個參數可以是逗號或空白分隔的多個 case，一次 run 跑完。
# 為什麼要支援：批量以前只能手拼 `python -m qatest run --caseid a b c`，繞過本 script ⇒
# 同時繞過 appium 清理、venv、選 clone、假綠防線這四道，正是踩過坑的地方。
IFS=', ' read -r -a CASEIDS <<< "$CASEID_ARG"
[ "${#CASEIDS[@]}" -eq 0 ] && { echo "ERROR: 沒解析出任何 caseid" >&2; exit 1; }
CASEID="${CASEIDS[*]}"

is_clone() { [ -d "$1/QATest/src" ] && [ -f "$1/venv/bin/activate" ]; }

# ── 砍掉「同一個 platform」殘留的 appium server ───────────────────────────
# 為什麼要砍：run 被 SIGTERM / 中途死掉時，`screen -dmS appium_server_<port> appium ...`
# 起的 session 會 detached 留著並持續佔 port。累積下來的下場有兩種，且都不會明說是殘留：
#   (a) 新 session 一連就 RemoteDisconnected → 收 `total 0 cases` 假綠
#   (b) 選 port 撞到殘留的 → EADDRINUSE（且 lsof 有時查不到 owner，極難查）
# 為什麼要分 platform：framework 的 port 是照平台切段的（QATest/src/lib/util.py:start_appium
# + lib/constants/port.py:CHROME_DRIVER_PORT=10000），Android 用 10000–10099、iOS 用 10100–10199。
# screen 名稱就是 appium_server_<chrome_driver_port>，所以照 port 落在哪一段就能精準只砍同平台的，
# 不會誤殺正在跑的另一平台（iOS + Android 本來就支援同時跑）。
kill_stale_appium() {
  local plat="$1" lo hi killed=0 name port
  case "$plat" in
    android) lo=10000; hi=10099 ;;
    ios)     lo=10100; hi=10199 ;;
    *)       return 0 ;;
  esac
  # 先掃掉已死但還掛在 socket 目錄裡的屍體（`screen -ls` 會顯示成 (Dead ???)），
  # 否則下面的列舉會撈到根本不存在的 session，quit 失敗又繼續留著干擾下一輪偵測。
  # `-wipe` 只移除死掉/連不上的，**不會動到任何活著的 session**，所以全域跑不影響另一平台。
  screen -wipe >/dev/null 2>&1 || true
  while read -r name; do
    port="${name##*appium_server_}"
    case "$port" in ''|*[!0-9]*) continue ;; esac
    [ "$port" -ge "$lo" ] && [ "$port" -le "$hi" ] || continue
    screen -S "$name" -X quit >/dev/null 2>&1 || true
    echo "[run_case]   quit screen $name"
    killed=$((killed + 1))
  done < <(screen -ls 2>/dev/null | grep -o '[0-9]*\.appium_server_[0-9]*' || true)

  # screen 沒了但 appium 進程仍在（screen 被清過、或當初不是用 screen 起的）也要收，
  # 否則 port 還是被佔著。只砍 -p 落在本平台 port 段的，比對的是 appium 自己的參數。
  for pid in $(pgrep -f 'appium -p [0-9]' 2>/dev/null || true); do
    port=$(ps -o command= -p "$pid" 2>/dev/null | sed -n 's/.*appium -p \([0-9]*\).*/\1/p')
    case "$port" in ''|*[!0-9]*) continue ;; esac
    [ "$port" -ge "$lo" ] && [ "$port" -le "$hi" ] || continue
    kill "$pid" 2>/dev/null || true
    echo "[run_case]   kill orphan appium pid=$pid port=$port"
    killed=$((killed + 1))
  done

  # 🔴 這行的變數一律用 ${} 界定：macOS 內建 bash 3.2 不認 UTF-8，`$hi）` 會把全角括號的位元組
  # 吃進變數名，於是 set -u 判定 hi 未定義並中止整個 script —— 症狀是「清掉殘留 appium 之後
  # 就 line 85: hi: unbound variable，case 根本沒跑」。只在真的有清到殘留時才觸發，極易誤判。
  if [ "$killed" -gt 0 ]; then
    echo "[run_case] 已清除 ${killed} 個 ${plat} 殘留 appium（port ${lo}-${hi}）；另一平台不受影響"
  fi

  # ── 另一個洞：agent 手動開的「探索用」appium 不在平台 port 段內 ──────────
  # skill 教人用 3080 截 iOS 畫面、subagent 也常隨手挑 4999 之類的 port 開一台來 dump 元素樹。
  # 這些跟平台 port 段完全無關，上面那圈按 port 段砍**砍不到**，於是留在背景跟正式 run 搶同一支
  # 手機 —— 兩台 appium 各自裝自己的 UiAutomator2 instrumentation、互相把對方的踢掉，症狀是
  # 跑到一半隨機噴 InvalidSessionIdException / NoSuchDriverError，看起來像 flaky，其實是殘留。
  # app run 對單一裝置本來就是獨占的，開跑當下還活著的探索 server 一律是殘留，直接收掉。
  if [ "${KEEP_ADHOC_APPIUM:-0}" != "1" ]; then
    for pid in $(pgrep -f 'appium' 2>/dev/null || true); do
      port=$(ps -o command= -p "$pid" 2>/dev/null | sed -n 's/.*appium -p \([0-9]*\).*/\1/p')
      [ -z "$port" ] && port=4723
      [ "$port" -ge 10000 ] 2>/dev/null && [ "$port" -le 10199 ] && continue
      echo "[run_case]   kill 探索用 appium pid=$pid port=$port（非平台 port 段，開跑當下必為殘留）"
      kill -9 "$pid" 2>/dev/null || true
    done
    sleep 2
  fi
}

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
if [ -d "$REPO/QATestData/cases/yaml" ]; then
  MISSING=()
  for cid in "${CASEIDS[@]}"; do
    grep -rqs "^\s*${cid}:" "$REPO/QATestData/cases/yaml" || MISSING+=("$cid")
  done
  if [ "${#MISSING[@]}" -gt 0 ]; then
    echo "ERROR: 在 $REPO 的 QATestData/cases/yaml 找不到：${MISSING[*]}" >&2
    echo "       跑下去只會得到 '0 failed, 0 passed (total 0 cases)' 假綠。" >&2
    echo "       確認：(a) clone 選對了嗎（QA_REPO=...）(b) 分支 checkout 了嗎（實作可能在別的 branch）" >&2
    exit 1
  fi
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
    [ "${SKIP_APPIUM_KILL:-0}" != "1" ] && kill_stale_appium ios
    ;;
  android)
    [ -n "$DEVICE" ] && EXTRA+=(--device "$DEVICE")
    [ "${SKIP_APPIUM_KILL:-0}" != "1" ] && kill_stale_appium android
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
echo "[run_case] repo=$REPO ($(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null)) caseid=${#CASEIDS[@]} 個: $CASEID platform=$PLATFORM HEADLESS=${HEADLESS:-<unset>} extra=${EXTRA[*]:-<none>}"
# macOS 內建 bash 3.2 在 set -u 下展開空陣列會噴 unbound variable（ios/android 走這條），
# 故用 ${arr[@]+...} 形式：陣列為空就整段不展開。
exec python -m qatest run --caseid "${CASEIDS[@]}" --platform "$PLATFORM" ${EXTRA[@]+"${EXTRA[@]}"}

#!/usr/bin/env bash
# run_case.sh — 跑 qatest case 的「唯一入口」，把 agent 每次都會漏的東西綁死：
#   1) HEADLESS=1（web/mweb 不彈實體瀏覽器）
#   2) 正確 venv（repo 根的 venv，不是空殼 QATest/venv，也不是缺 pymouse 的系統 python3）
#   3) web/mweb 自動加 --use_driver playwright
#   4) 前景跑（qatest 的 background scheduler 不可靠）
#
# 用法：run_case.sh <caseid> <platform>   e.g. run_case.sh KQT-T37931 web
# platform: web | mweb | ios | android
#
# 選項（env）：
#   HEADED=1  只對 web/mweb 有效 —— 彈實體瀏覽器（headed）供人「觀看自動化流程」。
#             預設 headless。app 走實體機本來就看得到，此旗標對 app 無意義。
#             ⚠️ 框架讀 bool(getenv("HEADLESS"))，設 HEADLESS=0 仍會 headless（非空字串為真），
#             故 headed 唯一正解是「完全不 export HEADLESS」——本 wrapper 已處理，別自己補設 0。
#
# 存在理由：headless / venv 這些規範「寫在 skill 裡靠 agent 記得讀」會漏（實測會）。
# 綁進單一入口，讓「漏」在結構上不可能發生，而不是靠自律。
set -euo pipefail

CASEID="${1:?用法: run_case.sh <caseid> <platform>，例 run_case.sh KQT-T37931 web}"
PLATFORM="${2:?需指定 platform: web|mweb|ios|android}"

# ── 找 repo root：含 QATest/src 且 venv 有效（有 bin/activate）─────────
REPO=""
for d in "$HOME/Downloads/qa_test/test/kkday-QA-automation" \
         "$HOME/Downloads/qa_test/app/kkday-QA-automation" \
         "$HOME/kkday-QA-automation" \
         "$PWD"; do
  if [ -d "$d/QATest/src" ] && [ -f "$d/venv/bin/activate" ]; then REPO="$d"; break; fi
done
# 找不到就從 cwd 往上爬
if [ -z "$REPO" ]; then
  d="$PWD"
  while [ "$d" != "/" ]; do
    if [ -d "$d/QATest/src" ] && [ -f "$d/venv/bin/activate" ]; then REPO="$d"; break; fi
    d="$(dirname "$d")"
  done
fi
if [ -z "$REPO" ]; then
  echo "ERROR: 找不到有效的 kkday-QA-automation clone（需含 QATest/src 且 venv/bin/activate 存在）" >&2
  echo "       注意 QATest/venv 常是空殼；正確 venv 在 repo 根目錄。" >&2
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
  ios|android)
    : # app 走實體機，不設 HEADLESS、不加 playwright driver
    ;;
  *)
    echo "ERROR: platform 需為 web|mweb|ios|android，收到 '$PLATFORM'" >&2
    exit 1
    ;;
esac

# shellcheck disable=SC1091
source "$REPO/venv/bin/activate"
cd "$REPO/QATest/src"
echo "[run_case] repo=$REPO caseid=$CASEID platform=$PLATFORM HEADLESS=${HEADLESS:-<unset>} driver=${EXTRA[*]:-<none>}"
exec python -m qatest run --caseid "$CASEID" --platform "$PLATFORM" "${EXTRA[@]}"

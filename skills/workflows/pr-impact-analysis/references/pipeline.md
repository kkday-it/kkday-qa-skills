# Pipeline — 5 支 API 詳細

## Step 1：get-diff（先探勘大小）

**本地用 `gh` CLI 撈完整 compare** → 連同 `prefetched_compare` 一起 POST 給後端：

```bash
gh api "repos/kkday-it/$repo/compare/$base...$target"
# 若 total_commits > 250 → 用 commits API 分頁補齊（per_page=100，遇 base_sha 停）
# 然後對每個 commit 跑 gh api "repos/kkday-it/$repo/commits/$sha" 抓 files+patch（max 並行 20）

# 組 payload（簡化示意）
PAYLOAD=$(jq -n --argjson p "$prefetched_json" \
  --arg repo "$repo" --arg base "$base" --arg target "$target" \
  '{github_repo:$repo, base_ref:$base, target_ref:$target, prefetched_compare:$p}')

curl -s -X POST "$BACKEND/api/release-impact/get-diff" \
  -H "Content-Type: application/json" \
  -H "X-User-Id: mp0qewxc-idis9qqi1d" \
  -d "$PAYLOAD"
```

`prefetched_compare` 結構：

```json
{
  "diff": {
    "baseRef": "v3.5.6",
    "targetRef": "master",
    "commits": [{"sha":"...", "message":"...", "author":"...", "files":[{"path":"...", "changeType":"added|modified|deleted", "patch":"..."}]}]
  },
  "total_files": 597,
  "ahead_by": 193,
  "behind_by": 0,
  "total_commits": 193
}
```

後端看到 `prefetched_compare != None` 會跳過所有 GitHub call（不耗共用 token），直接用 client 提供的資料跑 filter / hash / `save_task_step_result`，回 `{diff, ahead_by, behind_by, total_files, code_changed, release_only, kept_files, ignored_files, diff_hash_or_files_hash, task_id}` — 完整 step1 紀錄會寫進 backend DB，web UI 看得到。

> **Wait 模式提示**：commits 多時並行撈 patch 比較費事；若使用者顯然想偷懶或臨時想看，建議走 Background 模式（`run_pipeline.py` 已內建 gh CLI 邏輯）。

**短路**：`code_changed=false` 或 `release_only=true && kept_files=[]` → 直接回「本次變更僅為版本號 / lock file，無需 regression」，**不打**後續 step。

## Step 2 開始：判斷模式

| 規模 | 判斷 | 走法 |
| --- | --- | --- |
| 短 | `ahead_by ≤ 30` 且 `kept_files ≤ 50` | **Wait 模式**：在 chat 內把後端 SSE stream 到完，預估 1~3 分鐘 |
| 長 | 超過上面條件 | **Background 模式**：本地 script 背景跑 ai-studio 後端 5 支 API，預估 5~20 分鐘 |
| 太大 | `ahead_by > 300` | 強烈警告：建議改 base / 用前一個 release tag，**不**繼續 |

`ahead_by` 是主指標（影響 step5 ai-analyze-impact 的 batch 數）。`kept_files`（後端過濾後留下的 code 檔）次要（影響 step3 analyze-components）。閾值用 `commits=N` / `files=N` 覆寫。

**兩種模式都走完整 5 支 API**（analyze-components → get-version-impact-summary → get-test-cases → ai-analyze-impact），差別只在「在 chat 內等」vs「丟背景跑」。結果完全一致、都被 ai-studio 後端 cache 收錄。

## 進度面板（兩種模式都建議印）

啟動 pipeline（不管 wait 或 background）後，先在 chat 印 Unicode 方框面板，把 base/target/規模/cycle/各 step 狀態列出來。Monitor / SSE 進度更新時把對應 step 改 ✅。

範例：

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 📊 PR Impact Analysis — kkday-b2c-web                                    │
├─────────────────────────────────────────────────────────────────────────┤
│ base    : task/KB2CW-3938-forced-reflow-scroll-throttle-hydration-       │
│ target  : master                                                          │
│ size    : ahead=6 commits, files=7  →  Wait 模式                          │
│ cycles  : 🔁 KQT-R929  📦 KQT-R1056  🚗 KQT-R1189 (all)                   │
├─────────────────────────────────────────────────────────────────────────┤
│ Step 1  get-diff                  ✅ done                                  │
│ Step 2  analyze-components        ⏳ 跑中                                  │
│ Step 3  version-impact-summary    ⏸ 等                                    │
│ Step 4  get-test-cases (×N cycle) ⏸ 等                                    │
│ Step 5  ai-analyze-impact (×N)    ⏸ 等                                    │
│ Step 6  fetch automated_case_ids  ⏸ 等                                    │
└─────────────────────────────────────────────────────────────────────────┘
```

狀態符號約定：`⏸ 等` / `⏳ 跑中` / `✅ done` / `❌ 失敗`。

## Background 模式 awk per-step 去重

Background 模式搭配 Monitor `tail -f` bash task output file，**用 awk state tracking 做 per-step 去重**——每個 step 只在切換時 fire 一次，避免 step2 的 37 batch 並行噪音灌爆 context：

```bash
tail -f /private/tmp/claude-*/.../tasks/<bash_task_id>.output 2>/dev/null \
  | awk '
    /\[step[0-9]+_[a-z_]+(\[[a-z]+\])?\]/ {
      match($0, /\[step[0-9]+_[a-z_]+(\[[a-z]+\])?\]/)
      step = substr($0, RSTART, RLENGTH)
      if (step != last_step) {
        print
        fflush()
        last_step = step
      }
      next
    }
    /\[done\]|FAIL|Traceback|\[error\]|pipeline completed/ { print; fflush() }
    '
```

**為什麼用 awk 而不是 grep**：step2 (analyze-components) / step5 (ai-analyze-impact) 並行度 20，每個 batch 都會輸出 `[step2_analyze_components] 批次 N 正在調用 AI API` / `批次 N 分析完成` 兩條 — 37 batch 合計近百條同 step name，grep 全部 match 就會把 chat context 灌爆。awk 用 `last_step` 變數記住上一個 step name，只在 step 切換才 fire — 每個 step 最多 1 條通知，pipeline 從 step0 到 done 整輪通常 ≤ 10 條 notification。

**注意**：cycle group 標籤（`[regression]` / `[project]` / `[trans]`）算 step name 一部分，所以每個 cycle 進場時都會 fire 一次。

bash task output 路徑：Bash `run_in_background` 啟動後回傳的 `Output is being written to: <path>` — 直接抓那個路徑給 Monitor。Monitor 收到每條 notification 時，**更新面板對應 step 狀態**（重印整個 box，不要只 echo raw log）。

Wait 模式雖然沒有 Monitor，但每跑完一個 SSE step 也要重印 panel，讓使用者看得到節奏。

## Wait 模式（短任務）

在 chat 內把 step2→3→4→5 全跑完。每個 step SSE stream：
- 遇 `data: {type:"progress"}` 顯示 1 行進度
- 遇 `data: {type:"result"}` 拿結果
- 遇 `data: {type:"end"}` 結束

**注意**：後端 SSE 兩種格式混用：
- `data: {...}\n\n`（type 在 payload 裡）
- `event: progress\ndata: {...}\n\n`（同時有 event header 和 data type）

只看 `data: ` 那行 + JSON parse 就好，`event:` line 忽略。

順序：
1. `POST /analyze-components`（SSE）→ 拿 `impacted = {components, globalComponents, apis}`
2. `POST /get-version-impact-summary`（SSE，input 帶 step1 的 diff + step2 的 impacted）→ 拿 `{modules_impacted, apis_impacted, risk_level, change_types, summary}`
3. 若 `cycle != none`：`POST /get-test-cases`（SSE）→ test_cases；接著 `POST /ai-analyze-impact`（SSE）→ `results: [{test_case_id, label, reason, impact_score, tags}]`

跑完直接進「結果解讀」段。

## Background 模式（長任務）

呼叫 `<skill-root>/scripts/run_pipeline.py`，用 Bash `run_in_background=true` 啟動。Script 把 5 支 API 串起來，進度與結果隨時寫進 `/tmp/release_impact_<task_id>.json`，同時把每條 progress **即時 print 到 stdout**（給 Monitor 工具 stream）。

**啟動**（stdout 不重導，stderr 收進 log）：

```bash
TASK_ID=$(python3 -c "import uuid; print(uuid.uuid4().hex[:12])")
OUT=/tmp/release_impact_${TASK_ID}.json
LOG=/tmp/release_impact_${TASK_ID}.log

python3 -u <skill-root>/scripts/run_pipeline.py \
  --task-id "$TASK_ID" \
  --repo "$repo" \
  --base "$base" \
  --target "$target" \
  --cycle "$cycle" \
  --backend "$BACKEND" \
  --user "mp0qewxc-idis9qqi1d" \
  --out "$OUT" \
  2> "$LOG"
```

關鍵：`python3 -u` 強制 unbuffered output；**不**把 stdout 重導 — 留給 Monitor 工具讀。

**啟動後**：

1. 用 Monitor 工具監看 background bash task ID，每條 stdout line（`[ts] [step] message`）會自動推進 chat — 接近進度條效果
2. 同時立刻回給用戶 task ID + 預估時間 + 「跑完自動解讀」

```
分析範圍偏大（ahead_by=193, kept_files=597），已丟本地背景跑，全程不上 web UI。

Task ID: <task_id>
Output JSON: /tmp/release_impact_<task_id>.json
Log (stderr): /tmp/release_impact_<task_id>.log

預估 8~15 分鐘。每完成一個 step 系統會即時推進度，跑完自動解讀。
```

**完成通知處理**：Bash run_in_background 完成時 Claude 收到通知 → 直接讀 `OUT` JSON → 進「結果解讀」段。Monitor 工具的最後一行通常是 `[done] pipeline completed`。

**Monitor 用法**（啟動 background bash 拿到 task ID 後立刻呼叫）：

ToolSearch 載入 Monitor schema → 監看 bash task ID。每條 stdout line = 1 個 notification。不需要 sleep / poll。

**用戶問「task <id> 跑到哪」**：若 Monitor 還在串，回最近一條；否則讀 JSON 的 `current_step` + `progress_log` 最後幾條，回 1~2 行進度，**不要等**。

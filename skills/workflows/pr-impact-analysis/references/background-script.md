# Background script

位於 `<skill-root>/scripts/run_pipeline.py`。

## 用法

```
python3 run_pipeline.py \
  --task-id <id> \
  --repo <name> \
  --base <ref> \
  --target <ref> \
  [--cycle <id>|none] \
  [--backend <url>] \
  [--user <email>] \
  --out <path>
```

只依賴 `requests`，python 標準環境就能跑。

## Output JSON 結構

```json
{
  "task_id": "...",
  "status": "running" | "completed" | "failed",
  "current_step": "step3_version_impact_summary",
  "params": {...},
  "started_at": "...",
  "updated_at": "...",
  "completed_at": "...",
  "diff_meta": {"ahead_by": 193, "kept_files_count": 597, ...},
  "impacted": {...},
  "impact_summary": {...},
  "test_cases": [...],                      // flat 合併（向後相容）
  "ai_results": [...],                      // flat 合併（向後相容）
  "cycles_resolved": [                      // 本次實際跑的 cycle 列表（按執行順序）
    {"group": "regression", "cycle_id": "KQT-R929"},
    {"group": "project", "cycle_id": "KQT-R1056"},
    {"group": "trans", "cycle_id": "KQT-R1189"}
  ],
  "test_cases_by_cycle": {                  // 按 cycle 分組的 test_cases
    "regression": {"cycle_id": "KQT-R929", "test_cases": [...]},
    "project": {"cycle_id": "KQT-R1056", "test_cases": [...]},
    "trans": {"cycle_id": "KQT-R1189", "test_cases": [...]}
  },
  "ai_results_by_cycle": {                  // 按 cycle 分組的 ai_results
    "regression": {"cycle_id": "KQT-R929", "results": [...]},
    "project": {"cycle_id": "KQT-R1056", "results": [...]},
    "trans": {"cycle_id": "KQT-R1189", "results": [...]}
  },
  "automated_case_ids": ["KQT-T7180", "KQT-T7203", ...],
  "progress_log": [{"ts": "...", "step": "...", "message": "..."}],
  "errors": []
}
```

## 重要欄位說明

**`cycles_resolved`**：Script 內建 `CYCLE_MAP`（repo → {regression / project / trans}），把 `--cycle` 參數解析後的最終 cycle 列表。輸出階段按這個列表的順序分組顯示。

**`test_cases_by_cycle` / `ai_results_by_cycle`**：跑多 cycle 時各組獨立保存，輸出階段用這個分組（🔁/📦/🚗）。單 cycle 時也會用同一格式（只有一個 key）。`group` 不在 regression/project/trans 中的（例如使用者直接給 `cycle=KQT-Rxxxx` 而該 ID 不在 CYCLE_MAP 中）會標 `custom`。

**`automated_case_ids`**：Step 6 從 `http://autotest-service.sit.kkday.com:8080/api/v1/testcase` 拿到的全平台自動化 case_id list（不分 platform / team），用來在輸出時跟 MUST/SHOULD 比對標 ⚙️/🖐。拿不到時為 `[]`，輸出時跳過標記並提示。

## Atomic write

每跑完一個 step 或收到 progress 都會 atomic write JSON（`.tmp` → rename），Claude 隨時讀都拿得到一致狀態。

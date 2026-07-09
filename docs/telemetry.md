# 品質遙測揭露（Case 忠實度）

本 repo 的 QA 自動化流程會**選配性地**回傳「case 忠實度」品質遙測到內部 ai_studio 後台，用來對團隊/stakeholder 用數據呈現「AI 產出跟原始 TCMS case 有多一致」。**此為公開揭露、非隱藏蒐集。**

## 收什麼

每個 case×平台一筆（`scripts/send_case_fidelity.py` 送到 `POST {AI_STUDIO_BASE}/api/qa-automation/case-fidelity`）：

- `run_id`、`case_id`、`platform`、`mode`(create/fix)、`interactive`
- `step_total` / `step_covered`、`assertion_total` / `assertion_covered`
- `fidelity`(PASS/FAIL)、`confidence`、`fix_rounds`、`recommend`、`blocked_reason`
- `operator`（`KKDAY_TOOLS_USER_NAME`，稽核用）、`client_user`（`login@hostname`）

**不收**：測試碼內容、案件業務資料、任何個資（PII）。只有上述品質指標與操作者識別。

## 怎麼運作

- 由 **Claude Code Stop hook** 在 agent 停止後**背景執行** sender，讀主對話產出的 fidelity 結果 jsonl 送出。
- **不接原本的 `kkday-qa-tools` MCP**（獨立腳本），**不會出現在對話裡、不觸發權限提示、不干擾使用者操作**。
- **fail-safe**：每筆最多 retry 5 次，全失敗就放棄該筆；任何錯誤靜默、`exit 0`，絕不影響主流程。

## 啟用（Stop hook 範例）

在 `.claude/settings.json` 加（`<results-jsonl>` 換成主對話寫 fidelity 結果的路徑）：

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 <repo>/scripts/send_case_fidelity.py --infile <results-jsonl> --purge"
          }
        ]
      }
    ]
  }
}
```

環境變數：`AI_STUDIO_BASE`（預設 SIT ai_studio）、`KKDAY_TOOLS_USER_NAME`（operator）。

## 關閉

移除上面的 Stop hook 即完全停止回傳；沒有 hook 時流程照跑，只是不累積遙測。

## 呈現

ai_studio 前端「**Case 忠實度分析**」dashboard（權限與 MCP 呼叫分析一致：admin 預設可見）呈現 pass 率、assertion 覆蓋率、修復輪數、平台分布與每日趨勢。

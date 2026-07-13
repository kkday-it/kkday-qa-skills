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

---

# 品質遙測揭露（Locator Registry）

QA 自動化流程會**選配性地**與內部 ai_studio 後端交換「locator 候選」，做跨人共享 + 趨勢。**此為公開揭露、非隱藏蒐集。** 後端只是**儲存與共享層，不是真理來源**：取回的 locator 一律要「用前先驗」，驗不過標 stale 重挖（唯一入口 `scripts/get_verified_locator.py`，見 `locator_registry/README.md`）。

## 收什麼 / 送什麼（POST）

`scripts/send_locator_registry.py` 送到 `POST {AI_STUDIO_BASE}/api/qa-automation/locator-registry`，白名單欄位：

- `element`（語意名）、`page`、`component`、`flow`
- `selectors`（優先序候選陣列，每項 `{type, value, note}`）
- `platform`(web/mweb)、`env`(stage/prod)、`source`(來源 case id)
- `last_verified`(時間戳)、`status`(verified/stale)、`verify_url`
- `operator`（`KKDAY_TOOLS_USER_NAME`，稽核用）、`client_user`（`login@hostname`）

**不收**：測試碼內容、案件業務資料、任何個資（PII）。只有上述 locator 中繼資料與操作者識別。

## 取什麼（GET）

`scripts/fetch_locator_registry.py` 打 `GET {AI_STUDIO_BASE}/api/qa-automation/locator-registry?flow=…&page=…&platform=…&env=…`，回已知候選 + 業務語意 note + 該區域驗證方法論，當 skill 執行前的起手 hints。GET 由 `get_verified_locator.py` 內部呼叫，**agent 不單獨當「拿了直接用」**——拿回的候選一律先在當前 DOM cheap-verify。

## 怎麼運作

- **POST**：由 **Claude Code Stop hook** 在 agent 停止後**背景執行** `send_locator_registry.py`，讀主對話（經 `get_verified_locator.py --emit`）產出的 jsonl 送出。
- **GET**：在 case 執行**前**由 `get_verified_locator.py` 內部觸發，取回候選後強制逐一驗證。
- **不接原本的 `kkday-qa-tools` MCP**（獨立腳本），**不會出現在對話裡、不觸發權限提示、不干擾使用者操作**。
- **fail-safe**：POST 每筆最多 retry 5 次、全失敗放棄該筆；GET 後端不可達/查無資料回空、當第一次挖照原流程跑；任何錯誤靜默、`exit 0`，絕不影響主流程。

## 啟用（Stop hook 範例，POST 回寫）

在 `.claude/settings.json` 加（`<results-jsonl>` 換成 `get_verified_locator.py --emit` 寫的路徑）：

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 <repo>/scripts/send_locator_registry.py --infile <results-jsonl> --purge"
          }
        ]
      }
    ]
  }
}
```

環境變數：`AI_STUDIO_BASE`（預設 SIT ai_studio，與 case-fidelity 同一個）、`KKDAY_TOOLS_USER_NAME`（operator）。

## 關閉

- 移除上面的 Stop hook 即完全停止 POST 回寫。
- 不呼叫 `get_verified_locator.py` 即不觸發 GET；沒有 registry 時流程照跑，只是每次都從零挖、不享共享候選。

## 呈現

ai_studio 前端可依 `flow`/`page`/`platform`/`env` 呈現 locator 覆蓋、`stale` 比率與各元素 `last_verified` 新鮮度趨勢，幫團隊看「哪些區域的 locator 常腐爛」。

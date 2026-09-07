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

由 `scripts/install.sh` / `sync_hooks.py` 自動掛（見下方註）。手動示意：reviewer 把結果寫進
**目錄** `/tmp/case_fidelity_results.d/`（per case×平台一檔），sender 用 `--indir` 讀、且
**不帶 `--purge`**——結果檔是忠實度 gate 的證據，生命週期交給 gate（pass 才清），sender 先送不刪。

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 <repo>/scripts/send_case_fidelity.py --indir /tmp/case_fidelity_results.d"
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

QA 自動化流程會**選配性地**與內部 ai_studio 後端交換「locator 候選」，做跨人共享 + 趨勢。**此為公開揭露、非隱藏蒐集。** 後端只是**儲存與共享層，不是真理來源**：取回的 locator 一律要「用前先驗」，驗不過標 stale 重挖（唯一入口 `scripts/locator_valve.py`，見 `locator_registry/README.md`）。

## 收什麼 / 送什麼（POST）

`scripts/send_locator_registry.py` 送到 `POST {AI_STUDIO_BASE}/api/qa-automation/locator-registry`，白名單欄位：

- `element`（語意名）、`page`、`component`、`flow`
- `selectors`（優先序候選陣列，每項 `{type, value, note}`）
- `platform`(web/mweb)、`env`(stage/prod)、`source`(來源 case id)
- `last_verified`(時間戳)、`status`(verified/stale)、`verify_url`
- `operator`（`KKDAY_TOOLS_USER_NAME`，稽核用）、`client_user`（`login@hostname`）

**不收**：測試碼內容、案件業務資料、任何個資（PII）。只有上述 locator 中繼資料與操作者識別。

## 取什麼（GET）

`scripts/fetch_locator_registry.py` 打 `GET {AI_STUDIO_BASE}/api/qa-automation/locator-registry?flow=…&page=…&platform=…&env=…`，回已知候選 + 業務語意 note + 該區域驗證方法論，當 skill 執行前的起手 hints。GET 由 `locator_valve.py` 內部呼叫，**agent 不單獨當「拿了直接用」**——拿回的候選一律先在當前 DOM cheap-verify。

## 怎麼運作

- **POST**：由 **Claude Code Stop hook** 在 agent 停止後**背景執行** `send_locator_registry.py`，讀主對話（經 `locator_valve.py --emit`）產出的 jsonl 送出。
- **GET**：在 case 執行**前**由 `locator_valve.py` 內部觸發，取回候選後強制逐一驗證。
- **不接原本的 `kkday-qa-tools` MCP**（獨立腳本），**不會出現在對話裡、不觸發權限提示、不干擾使用者操作**。
- **fail-safe**：POST 每筆最多 retry 5 次、全失敗放棄該筆；GET 後端不可達/查無資料回空、當第一次挖照原流程跑；任何錯誤靜默、`exit 0`，絕不影響主流程。

## 啟用（Stop hook 範例，POST 回寫）

由 `scripts/install.sh` / `sync_hooks.py` 自動掛。手動示意：valve / 收成寫進**目錄**
`/tmp/locator_results.d/`（per-process 檔），sender 用 `--indir` 讀、**不帶 `--purge`**——
emit 檔是 locator gate 的證據，生命週期交給 gate（pass 才清；後端 upsert 冪等，重送無害）。

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 <repo>/scripts/send_locator_registry.py --indir /tmp/locator_results.d"
          }
        ]
      }
    ]
  }
}
```

環境變數：`AI_STUDIO_BASE`（預設 SIT ai_studio，與 case-fidelity 同一個）、`KKDAY_TOOLS_USER_NAME`（operator）。

## 讀取收據（read receipt）—— 只寫本機，不上傳

`fetch_locator_registry.py` / `locator_valve.py` / `get_verified_flow.py` 帶 `--case` 時，會在
**本機**寫一列讀取收據到 `/tmp/registry_reads.d/`（可用 `REGISTRY_READ_DIR` 覆寫）：

```json
{"kind":"locator","case":"KQT-T7172","platform":"ios","query":{...},"n":4,"hit":true,
 "endpoint":"/api/qa-automation/locator-registry/events","read_at":"2026-09-07T01:06:14+00:00"}
```

- **不 POST 到任何後端**，純粹是 Stop 的讀取硬 gate（`check_registry_read_gate.py`）的證據來源。
  後端沒有「誰讀過」的稽核端點（`/events` 回的是 entry 清單、不是存取記錄），所以「有沒有讀」
  在後端本來就查不出來 —— 這也是為什麼「只寫不讀」能靜默存在兩個月。
- 不含任何憑證 / 個人 email；`case` / `platform` / 查詢字串與筆數而已。
- 生命週期：gate 每次執行順手刪掉**超過 7 天**沒更新的收據檔。刻意**不**綁在 gate 的 pass/block
  上 —— 綁了就會出現「claimed 還在、收據被清掉」的假性卡死。
- 另有一份**本機 claim ledger** `/tmp/registry_read_claimed.<session>.jsonl`（`REGISTRY_READ_CLAIMED`
  可覆寫），內容只有 `{"case_id","platform"}`，同樣不上傳。它是讀取 gate 自己的 arm 訊號：
  claim 一出現就抄一份，只有本 gate pass 時才清。**不能直接沿用 locator gate 的 claimed 檔**
  —— 那支 pass 時會刪掉它，讀取 gate 就會變成「擋一次就失效」（實測踩過，見
  `docs/lessons-learned.md`）。

## 關閉

- 移除上面的 Stop hook 即完全停止 POST 回寫。
- 不呼叫 `locator_valve.py` 即不觸發 GET；沒有 registry 時流程照跑，只是每次都從零挖、不享共享候選。

---

# 使用量遙測揭露（Tool Usage）

QA 自動化流程會**選配性地**回傳「工具使用量/採用度」遙測到內部 ai_studio 後台，用來看「誰在用、用了什麼、跑到哪、卡在哪」。**此為公開揭露、非隱藏蒐集。**

## 收什麼 / 送什麼（POST）

`scripts/emit_tool_usage.py` 在「工具一叫用當下」寫一筆到 jsonl，`scripts/send_tool_usage.py`（掛 Stop hook）送到 `POST {AI_STUDIO_BASE}/api/qa-automation/tool-usage`，白名單欄位：

- `run_id`、`tool`、`outcome`(invoked/delivered/blocked/abandoned)、`interactive`
- `case_ids`、`platforms`、`case_count`
- `stage`（停在哪階段：fetch/plan/confirm/automate/gate/report）、`blocked_reason`（blocked/abandoned 的簡短原因）
- `note`（自由備註）
- `operator`（`KKDAY_TOOLS_USER_NAME`，稽核用）、`client_user`（`login@hostname`）
- ⚠️ **`request_text`（使用者原始輸入，逐字）** —— 見下方例外說明

## ⚠️ PII 例外：`request_text`（逐字原始輸入）

跟 case-fidelity / locator 的「**不收任何 PII**」不同，**本工具的 `request_text` 是一個經揭露、經團隊同意的例外**：它逐字記錄觸發本次的使用者輸入（如「KQT-T38189 實作」），可能夾帶 case ID 以外的關鍵詞、甚至業務資料/個資。存在理由：blocked 的紀錄若只有「有人用過」而不知道**當時打了什麼、要什麼**，就無法診斷使用者遇到什麼問題。

控管：

- **僅 admin-only dashboard 呈現**（權限與 MCP 呼叫分析一致：admin 預設可見），非全員可見。
- **性質是「使用診斷」不是「品質指標」**；不做他用。
- **可關閉**：移除 Stop hook 的 `send_tool_usage.py` 即完全停止上送；或在 emit 時不帶 `--request-text`（只送結構化欄位、不送逐字輸入），仍能看 case_ids/stage/reason。

**仍不收**：測試碼內容、access token/credential。`request_text` 以外不逐字側錄對話。

## 怎麼運作

- **POST**：由 **Claude Code Stop hook** 背景執行 `send_tool_usage.py`，讀 `emit_tool_usage.py` 產出的 jsonl 送出。
- **不接 `kkday-qa-tools` MCP**（獨立腳本），**不出現在對話裡、不觸發權限提示、不干擾操作**。
- **fail-safe + retry 5 次**：任何錯誤靜默、`exit 0`，絕不影響主流程。

環境變數：`AI_STUDIO_BASE`（與 case-fidelity 同一個）、`KKDAY_TOOLS_USER_NAME`（operator）。

## 呈現

ai_studio 前端可依 `flow`/`page`/`platform`/`env` 呈現 locator 覆蓋、`stale` 比率與各元素 `last_verified` 新鮮度趨勢，幫團隊看「哪些區域的 locator 常腐爛」。

# Repo 別名 + 預設 Test Cycle

第一個位置 token 用平台關鍵字代替 `repo=...`：

| 別名 | 對應 repo | Regression | Project | Trans |
| --- | --- | --- | --- | --- |
| `ios` | `kkday-ios-member` | `KQT-R1359` | `KQT-R1058` | `KQT-R1192` |
| `android` | `kkday-android-member` | `KQT-R1360` | `KQT-R1057` | `KQT-R1191` |
| `member-ci` | `kkday-member-ci` | `KQT-R929` (Web) | `KQT-R1056` | `KQT-R1189` |
| `mobile-member-ci` | `kkday-mobile-member-ci` | `KQT-R928` (MWeb) | `KQT-R1055` | `KQT-R1190` |
| `b2c-web` | `kkday-b2c-web` | `KQT-R929` (Web) | `KQT-R1056` | `KQT-R1189` |
| `b2c-api` | `kkday-b2c-api` | `KQT-R1106` | — | — |

## Cycle 分組意義

- **Regression** — 原本平台主要 regression cycle（舊有，本來就在跑）
- **Project** — 額外的專案功能測試 cycle（R1055-R1058）
- **Trans** — 額外的交通類測試 cycle（R1189-R1192）

來源：`ai_studio/ai-studio-project/src/constants/testCycles.ts`。

## 預設行為

**永遠自動跑該平台所有 cycle**（Regression + Project + Trans，b2c-api 只有 Regression）。每個 cycle 跑完一次 Step 4-5（get-test-cases + ai-analyze-impact），結果在輸出時按 cycle 分組——使用者**不需要選**。

**例外**（極少用，不主動暴露給使用者）：`cycle=none` 只跑 impact summary、不跑 regression。除非使用者明確說「不要跑 regression」/「只看 impact」才用，否則一律走預設。

API 呼叫格式：`{"github_repo": "kkday-ios-member"}`（**不要加 `kkday-it/` 前綴**）。

## member-ci / mobile-member-ci 特殊處理

web / mWeb 場景必須**同時抓 `kkday-b2c-web` 的 refs**。Mode A 列 refs 時，主 repo 用 T/B 編號，b2c-web 用 W 編號（W1, W2...），使用者選 base/target 時各帶兩個 ref（主 repo + b2c-web）。

**目前 background script 尚不支援 b2c-web 配對**，這兩個別名先走 wait 模式或請用戶用 `mode=wait` 強制。

別名沒對到時當成正常 repo 名稱處理；若 `tags=[]` & `branches=[]`，提示拼字錯誤或不在支援名單。

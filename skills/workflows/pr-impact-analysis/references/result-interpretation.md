# 結果解讀（Wait 跑完 / Background 通知 / `result <id>` 共用）

從 JSON / SSE result 拿到 `impact_summary` + `ai_results`。

## 階段 0：Claude 二次審核 + P0~P4 收斂（強制，過程不顯示給使用者）

後端 AI 對 MUST / SHOULD / CAN_SKIP 分類常有誤判，主因：
- `score=1.0` 常一律給滿（沒有分層）
- `globalComponents`（如「多語系/國際化」「通用基礎設施」「API 網絡層」「AB 測試」）顆粒度太粗 → 一個 cell 改了 AB toggle 就把所有用 AB 的 case 拉進 MUST
- AI 只看 reason / tags 字串配對，沒看真實 changed files
- 同一條 user flow 的多個變體（如「2C2P 信用卡 3D / 非 3D / 3D+APP」、「JR 訂購 1 大人 / 1 大人 1 童 / 指定座位」）全給滿分，但對 RD 來說跑一支代表就能驗到核心邏輯

**核心原則**：RD 跑不完 = 等於沒分類。**最終列給使用者的 case 數量動態決定**（依 cluster 數與改動規模），**跨 cycle 合計上限 20 支**。寧可漏報邊角，也不能淹沒核心。

**動態數量參考**（不是硬規則，依實際 cluster 抓密度）：
- 1 個 cluster（小改動）：3~5 支
- 2~3 個 cluster（中改動）：6~12 支
- 4+ 個 cluster（大改動）：12~20 支
- 上限 20，超過要再合併 variant 或降到 P2 不列

每個 cluster 通常配 1~3 支 P0（主流程代表）+ 0~3 支 P1（補強模組 / 邊界），cluster 內 variant 全合併。

### 審核步驟

1. 從 `gh api .../compare/...` 拿真實 changed file paths（task JSON `diff_meta` 只有 count，要從 GitHub 補）
2. 對 changed files 分類：
   - **runtime code**（`apps/*/pages/*`、`apps/*/server/*`、`apps/*/components/*`、`Solution/*/.swift`、`packages/*/src/*` 等）→ 真的會影響 module
   - **build-time only**（`scripts/*`、`*.config.{ts,js}`、`generate-*.ts`、`.changeset/*`、`CHANGELOG.md`、`package.json` version-only bump、`.xcodeproj` project file 變動、snapshot PNG）→ 不影響 runtime
3. 從 runtime 變更檔名抽出 **change clusters**（高顆粒關鍵字集合），例：
   - `InstantPurchaseInfoCard`, `InstantPurchaseCouponPoints`, `EligibilityChecker` → cluster: **InstantPurchase 改造**
   - `TapPayAftee*`, `PaymentStrategyManager`, `MakePayment` → cluster: **KKPayment 改造**
   - `TransCouponAPI`, `JapanRailways*`, `VtransAtServiceCarsDTO` → cluster: **Trans 改造**

### P0~P4 五級分類（取代 MUST / SHOULD / CAN_SKIP 三級）

- **P0 — 極核心關鍵路徑**（≤3 支）：直擊 cluster 主流程 + 關鍵 happy path，不跑等於沒驗。例：「立即訂購 → 付款 → 訂單成立」這條主軸
- **P1 — 核心 cluster 直擊**（≤5 支）：cluster 直擊但屬補強/邊界 case（特殊金流代表、Bottom Sheet 新模組、AB 開關）
- **P2 — Cluster 周邊**（不列）：直擊但與 P0/P1 同 cluster 同 user flow 變體，跑 P0/P1 過了就一起 cover
- **P3 — 間接 globalComponent only**（不列）：只透過 globalComponent（多語系、AB 測試、API 網絡層）關聯，沒直擊 cluster
- **P4 — 完全不沾**（不列）：runtime 改動沒交集

### 永遠不列的 case 類型（即使 AI 給 MUST 也跳過）

- **AB Test 開關類**：case 名含「AB Test」「AB 實驗」「A/B」等 — RD 跑這種 case 沒意義，新 toggle 預設 off / on 都會被主流程 case 自然覆蓋
- **純語系切換類**：case 名含「Ko -」「Ja -」「zh-tw -」前綴的同名 case → 只挑一支代表（zh-tw 優先）
- **純 UI 顯示驗證**（無互動）：case 名含「展示檢查」「UI 正常顯示」且沒有實際 user action → 降 P2 不列
- **分期付款（instalment）類**：case 名含「分期付款」「instalment」「BIN 檢核」等 — 分期是少數人才用，**不是主流程**；如果要挑「立即訂購 + 付款」happy path 代表，請挑**信用卡 3D**（非分期）的 case，例如 `TAPPAY - 信用卡付款 (3D)` (iOS 主流量) / `STRIPE - 信用卡付款` (海外)

### iOS / Android 信用卡主流量金流挑選優先序（給 reason 寫「信用卡 happy path 代表」時用）

1. **Tappay 3D**（台灣 / 香港主流量，KKday 最大宗）— 第一順位代表
2. **Stripe 3DS2 / 信用卡**（海外卡 / 國際市場）— 第二順位
3. **2C2P 3D**（馬來西亞市場 only，**不要拿來當 global 主流量代表**）
4. **TAPPAY 國民旅遊卡 / 銀聯 / GrabPay / Atome / KCP / PayPay / Toss / Payme / 八達通** — 區域性 / niche，視為 variant 不列

判斷依據：信用卡 happy path = 「Tappay 3D」（除非 cluster 明確只動到特定 region 金流，那才挑該 region 代表）

### Variant 合併（強制）

同 cluster 同 user flow 的多個變體**只挑一支代表**：
- 例：「2C2P 信用卡 3D」、「2C2P 信用卡 非 3D」、「2C2P 信用卡 3D + 跳轉 APP」→ 挑 3D（主流量 happy path）一支代表，其餘合併
- 例：「JR 訂購新幹線」、「JR 訂購 1 成人」、「JR 訂購指定座位」→ 挑 1 成人 happy path 一支代表
- 例：「Ko/Ja/zh-tw Email 註冊」（語系變體）→ 挑一支代表
- 判斷依據：tags 重疊 ≥ 60% 且 case 名前綴/cluster 相同 → 視為同 cluster 變體
- 代表挑選優先序：⚙️（有自動化）> 廣度最高（涵蓋主流量地區/幣別）> 名字最像 happy path

### 跨 cycle 去重

同 case ID 出現在多個 cycle（例：KQT-T20131 同時在 Regression & Project）→ 只保留 Project（≥ Regression > Trans 視 cluster 主場）

**最終得到 ≤10 支精選清單**（跨 3 cycle 合計，按 cycle 分組，每支標 P0/P1）。

### 輸出原則

- **不要**把「我重審了 N 支」「降級了 M 支」「合併了 N 個 variant」這類過程貼給使用者
- **只列 P0/P1**（≤10 支總計），按 cycle 分組
- **不列完整 MUST 清單**（即使 user 想看，請他自己 `cat /tmp/release_impact_<task_id>.json | jq` 看 raw）
- 在結果末尾用一行 footer 標：`> AI 原始 MUST X 支 → 精選 P0/P1 共 Y 支（已合併 variant、過濾間接關聯）`

### 何時跳過 / 必須觸發

跳過二次審核：
- `ai_results` 總數 < 10 支（沒膨脹空間）
- `ai_results` 為空（`cycle=none`）

必須觸發收斂：
- AI 原 MUST 總數 > 20 → 強制做 P0~P4 + variant 合併，輸出依動態規則（最多 20）
- AI 原 MUST 占 ai_results > 30% → 強制做 P0~P4 + variant 合併
- 兩者都不滿足 → 仍按 cycle 列原 MUST，不強制收斂（但仍 ≤ 20 支）

## 階段 1：輸出格式

**用人話講重點**（不貼 raw JSON）。**精選清單按 cycle 分組**（🔁 Regression / 📦 Project / 🚗 Trans），每支標 P0/P1。每個 cycle 區塊內**只列 P0 與 P1**，跨 cycle 合計 ≤ 10 支。

**⚙️ / 🖐 自動化標記**：列每支 case 時，把 `test_case_id` 跟 task JSON 的 `automated_case_ids` 比對：
- 命中 → 標 `⚙️`（有自動化）
- 沒命中 → 標 `🖐`（手動 only）

**格式要求**：
- 每支 case 一行：`[P0/P1] [⚙️/🖐] KQT-T<id> — <case 名> — <一句話為什麼>`
- **不要**多層 cluster tag、不要 emoji 裝飾、不要 score、不要 reason 全文
- **不要列「完整 MUST 清單」**

```
## 風險評估
- **high** — 訂購確認頁 + Instant Purchase 流程大改，貫穿付款、AB toggle、Bottom Sheet 新模組
- 主改動 cluster: InstantPurchase 改造 / KKPayment / Trans

## 精選 P0/P1（跨 cycle 共 N 支）

### 🔁 Regression — KQT-R929
- **P0** ⚙️ KQT-T7180 — 結帳金額計算 — fee 算法直接改動，主流量必跑
- **P0** ⚙️ KQT-T7203 — 優惠券折抵 — coupon API 改動
- **P1** 🖐 KQT-T6991 — 多商品結帳 — 多筆 cart 邏輯涵蓋邊界

### 📦 Project — KQT-R1056
- **P0** ⚙️ KQT-T8801 — 結帳功能新版 UI — 新 UI 元件直擊
- **P1** 🖐 KQT-T8815 — 結帳金額顯示 — 金額 footer 改動

### 🚗 Trans — KQT-R1189
- **P0** 🖐 KQT-T9012 — 接送結帳流程 — Trans 主流程直擊
- **P1** 🖐 KQT-T9020 — 接送優惠券折抵 — coupon API 變動

## 可一鍵觸發自動化的 case（must_run ∩ automated_case_ids）

> **iOS / Android（`kkday-ios-member` / `kkday-android-member`）跳過這段** — mobile single test run 暫時無法讓使用者直接觸發，列出來只會混淆。輸出時整段（含「自動化觸發 single test run」curl 範例）不顯示，⚙️/🖐 標記仍保留供識別用。

非 mobile repo（b2c-web / member-ci / mobile-member-ci / b2c-api）才印這段：

跨所有 cycle 合併、去重後共 N 支（complete list）：

KQT-T7180,KQT-T7203,KQT-T8801,...

可直接複製貼到 single test run trigger，platform 依 repo 選 `web` / `mweb` / `api`。

## 建議補測（gap 區，跨 cycle 共通）
- 沒有覆蓋「優惠券 + 多幣別」組合的 case
- 建議手動補一輪 SGD / JPY 結帳

完整 JSON：/tmp/release_impact_<task_id>.json
```

**選擇邏輯**：
- 跨 cycle 合計輸出 ≤ 10 支
- 每個 cycle 內以 P0 為主、P1 補強；P2/P3/P4 不顯示
- nice_to_have / skip / unknown：不列（如果使用者要看，請他自己讀 JSON）

**單一 cycle 場景**（`cycle=KQT-R...` 或 `cycle=none` 或 alias 走 b2c-api）：不分組，直接列「## 精選 P0/P1（共 N 支）」。

**`automated_case_ids` 拿不到**（網路問題或 API 壞掉）：task JSON 該欄位為空 list `[]` → 全部 case 一律不標 ⚙️/🖐，並在結尾加一行 `> 自動化平台暫時拿不到清單，本次未標記 ⚙️/🖐`。

**Cycle icon 約定**：🔁 Regression / 📦 Project / 🚗 Trans。

## 自動化觸發（single test run）

> **同上 — iOS / Android 跳過這段**。mobile single test run 平台還沒開放給一般使用者直接打。

對 ⚙️ 標記的 case（限 b2c-web / member-ci / mobile-member-ci / b2c-api），**不要在結果輸出貼 curl payload**，只在結尾用一行問句問使用者：

```
> 要直接觸發這 N 支自動化測試嗎？（KQT-T..., KQT-T..., ...）
```

使用者回「要 / yes / 觸發 / go」之類肯定詞後，再實際打 `POST $AUTOTEST/api/v1/automation/run`。打的時候：

- `platform` 依 repo 選：`b2c-web` / `member-ci` → `web`；`mobile-member-ci` → `mweb`；`b2c-api` → `api`；`ios` / `android` 不適用
- `team` / `subteam` / `device` 等欄位請使用者確認或填預設（ai-studio web UI Single Case Run 面板的預設值）
- 其他欄位（`environment=stage`、`is_regression=false`、`is_daily=true`、`send_slack_notification=false`、`repeat=1`、`execute_machine=AWS`、`priority=None`、`use_remote_driver=1`）走 default

打完回傳 run_id / 結果頁連結給使用者。**沒問就不要打**——避免誤觸發。

**結尾不要加「Token 使用」段** — 使用者不關心，純噪音。

## Follow-up Q&A

用戶基於既有結果再問問題，從 `/tmp/release_impact_<task_id>.json` / GitHub API 撈：

| 問題 | 怎麼答 |
| --- | --- |
| 「為什麼 KQT-T1234 是 must_run」 | 從 `ai_results` 找 `test_case_id == "KQT-T1234"`，回 `reason` + `tags` + `impact_score`，並配對到 `impact_summary.modules_impacted` 哪一個 |
| 「這個 commit 是誰寫的」 | `gh api repos/kkday-it/$repo/commits/$sha --jq '{author: .commit.author.name, date: .commit.author.date, message: .commit.message}'` |
| 「動了哪些檔案」 | 從 `diff_meta` 看不到完整 file list（script 只存 count）；要時直接打後端 `get-diff` 重拿 |
| 「為什麼 risk 是 high」 | 從 `impact_summary.summary` + `change_types` 推回去 |
| 「這個 module 跑哪些 case」 | `ai_results` 用 `tags` / `reason` 內含 module 名稱的篩出來 |
| 「重跑一次」 | 確認後重新跑 Pipeline；不要自動重跑 |

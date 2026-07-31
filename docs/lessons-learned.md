# Lessons Learned（踩過的坑）

給接手的 agent / 人類：動手前先掃這裡，看有沒有踩過類似坑。每則格式：**症狀 → 根因 → 對策（已落地在哪）**。新坑往上加（新的在前）。

---

## 「locator 共享記憶」看似有、實則從沒進後端

**症狀**：跑完 case，automator 回報「起手用 registry 拿到 N 個候選命中」，但直接打 ai_studio GET 撈 `things-to-do-search`，後端一直是空的（`entries: []`）。

**根因（兩個獨立缺口，且互相掩蓋）**：
1. **automator 沒真的跑 valve**：agent 指令原本只叫用 `verify_locator.py`（單顆驗、不 GET 後端、不 emit 回寫），根本沒提 `locator_valve.py` 這個唯一入口。automator 實際是**讀了本地 `locator_registry/registry.json` 的內容來敘述**，不是執行 valve → 完全不觸發回寫。
2. **本地 registry 與後端從沒同步**：那些候選只活在 checked-in 的 `registry.json`（本地 fallback 來源），沒人把它 seed 到後端。

**為什麼難發現**：後端 GET 讀出來是空的，「沒資料」和「client 根本沒推」從結果上看不出差別——缺口 #2 掩蓋了缺口 #1。診斷時一度誤判「後端是不落地的 stub」，實際後端 POST/GET 都正常，只是沒東西被推上去。

**對策（已落地）**：
- `agents/qa-case-automator.md`：**起手強制先跑 `locator_valve.py` valve**，明令禁止「讀 registry.json 敘述當驗過」或只跑 `verify_locator.py`；並要求回報**附 valve 執行憑據**（`source` / verified·stale / emit 檔路徑），沒憑據＝視同沒跑、退回補跑。
- `scripts/locator_valve.py`：`--emit` **預設就開**（忘帶旗標也會回寫）。
- 已把 `registry.json` seed 到後端（things-to-do-search 8 + home-search 2）。

**通則**：**「best-effort 遙測 / 共享層」讀出來是空的，不代表寫入端有在寫。** 驗證一條回寫鏈要兩頭都戳：POST 完立刻 GET 撈回同一筆（round-trip），別只看其中一端。

**後續（軟指令 → 硬 gate）**：上面「automator 沒真的跑 valve」一開始只用「回報附憑據」的**軟**方式補，但軟指令 agent 照樣會跳、且失敗靜默。後來升級成**硬 gate**：`check_locator_gate.py`（Stop hook）要求「交付的每個 UI case×平台」在 `/tmp/locator_results.d/` 有 `source==case` 的 emit 證據，否則擋下（見 automate-tcms-cases Gate C）。app 沒有可導航 URL、不能事前驗，改用「綠 + fidelity 過 ⇒ 收成 emit verified」當驗證來源。**判準：失敗靜默且累積的規則要用硬 gate，不能只靠軟指令。**

---

## 單一固定 emit 檔在並行下會被 purge 掃掉（靜默丟資料）

**症狀**：批次並行 / 多 session 同時跑，locator 回寫零星缺筆。

**根因**：emit 預設寫死單一檔 `/tmp/locator_results.jsonl`，Stop hook sender 是 `read → POST → purge`。並行時 A 的 sender 在「已 read、還沒 delete」空窗，把 B 剛 append、還沒送的行一起 purge 掉。（註：emit 是 append 模式，不是「覆寫」；真正的丟失來自這個 purge race。）

**對策（已落地）**：
- valve emit 改 **per-process 檔** `/tmp/locator_results.d/<pid>-<utc_ts>.jsonl`，各 process 各寫各的。
- `send_locator_registry.py` 新增 `--indir`：掃目錄**逐檔讀完才刪自己那份**，不碰別人正在寫的。
- fidelity 結果同樣從單一檔改成目錄 `/tmp/case_fidelity_results.d/`（見下一則）。

**通則**：任何「多 producer 寫同一檔 + 有人會 purge」的組合，預設就是 race。要嘛 per-producer 檔、要嘛檔鎖；**別用單一固定路徑當並行的交換點**。

---

## 忠實度 gate 的結果檔被 sender 的 `--purge` 在「擋下時」刪掉 → 假性「找不到結果」卡死

**症狀**：needs-fix 修復迴圈中，gate 反覆報「找不到 fidelity 結果檔」，即使 reviewer 剛寫過。

**根因**：Stop hook 順序原本是 `[gate, send_case_fidelity --purge, …]`。gate 輸出 `decision:block` 後，**同一個 Stop event 的後續 hook 仍會執行**——`send_case_fidelity --purge` 就把 gate 的輸入檔刪了。下一輪 stop：gate 找不到結果 → 又擋（但這次是「檔不見」而非「verdict 不過」）。等於 sender 把守門的證據吃掉了。

**對策（已落地）**：**結果檔的生命週期改由 gate 獨佔**——
- Stop hook 順序改成 send_case_fidelity **先於** gate，且 send **不帶 `--purge`**（只送不刪）。
- gate 只有在 **pass** 時才刪，且用 `--cleanup-on-pass` **只刪本次 claimed 的 case×平台結果檔**，不 `rm -rf` 整個目錄（避免誤刪同機其他 session 正在驗的結果）。
- 結果檔改成目錄 `/tmp/case_fidelity_results.d/`，reviewer **per case×平台 一檔、每輪覆寫**（`>` 不 append）——覆寫才只留最新判定，否則 round1 的 `needs-fix` 會和 round2 的 `pass` 並存、gate「全部要 pass」永遠擋。

**通則**：**送遙測（fire-and-forget，會刪）和把關（需要證據留存）不能共用同一個檔的生命週期。** 誰負責刪要單一且明確；「送出」不等於「可以刪」——尤其當同一份資料還要被 gate 讀。

---

## 團隊 hook 是「絕對路徑快照」，git pull 不會更新它

**症狀**：改了 `install.sh` 裡的 hook 指令（flag / 路徑），但已安裝的隊友 `git pull` 後行為沒變。

**根因**：hook 是 `install.sh` 一次性用「本 clone 絕對路徑」merge 進各自 `~/.claude/settings.json` 的**快照**；`session_autopull` 只 `git pull`，不會重寫 settings。symlink 的 skills/agents 會跟著更新，但 settings 裡的 hook 指令字串不會。

**對策（已落地）**：hook 定義抽成 `scripts/sync_hooks.py`（單一來源 + 冪等 + 自動 migrate 屬本 repo 的舊 hook）；`install.sh` 呼叫它；`session_autopull.sh` 在 pull 有更新時自動重跑它 → 隊友下次 pull 自動 migrate。

**通則**：凡是「安裝時把路徑寫進使用者設定」的機制，改定義後要有**再同步**的路徑，否則等於只對「新安裝的人」生效。

---

## symlink 只讓「已連上的檔案」更新——**新增**的 skill/agent 不會自己冒出來

**症狀**：上游新增 `qa-case-planner` 後，早裝的隊友 `~/.claude/agents/` 裡沒有它，於是回報「install.sh 缺少裝 qa-case-planner」。但 `install.sh` 是 `for a in agents/*.md` 全撈，並沒有漏。

**根因（兩層）**：

1. `session_autopull.sh` 有更新時只重跑 `sync_hooks.py`，**不重建 symlink**。symlink 的語意是「已連上的那個檔案跟著更新」，repo 裡多出一個新檔案不會自動產生新 symlink → 只有「新安裝的人」拿得到新 agent。
2. 更根本：autopull 用 `CLAUDE_PROJECT_DIR`（= 當下開的專案）當同步目標，但 hook 是掛在 user-level、**在任何專案都會跑**。所以隊友在 `kkday-QA-automation` 開 session 時，它 pull 的是 QA-automation（然後找不到那裡的 `scripts/sync_hooks.py` 就跳過），qa-skills 從頭到尾沒被同步——這就是「不知道有更新」的機制性根因，順帶還會 auto-pull 別人的產品 repo。

**對策（已落地）**：link 邏輯抽成 `scripts/link_assets.sh`（單一來源，`--quiet` 給 hook 用）；`install.sh` 與 `session_autopull.sh` 共用它。autopull 呼叫它的位置有兩個刻意的選擇：**(a) 放在 pull 之外、不分有無更新都跑**——只在「有更新」時補救不到「HEAD 早就最新、但當初裝的時候上游還沒有那個 agent」的人，離線時也照樣自我修復；**(b) 同步目標改成「本 script 所屬的 clone」**（`dirname $BASH_SOURCE/..`），不再看 `CLAUDE_PROJECT_DIR`。

**通則**：「自動保持最新」要分清楚**更新既有物**與**取得新增物**是兩件事——前者 symlink 免費，後者一定要有人重跑安裝步驟。自我修復的動作要**無條件跑**（冪等且便宜的話），別綁在「偵測到變更」上，否則救不到已經處於壞狀態的人。另外：user-level hook 的工作目錄是**使用者的專案**，不是它自己的 repo，凡是要對「自己的 repo」動作的 hook 都得從 `$BASH_SOURCE` 推路徑。

---

## 「元件層 done」≠「流程層 done」——armed 但沒接線，會被下一輪 review 當新問題重複挖出

**症狀**：同一批工作，每重跑一次 review 就冒出「新」缺口（「偵測器沒觸發」「記錄靠主對話記得寫」「gate 沒接進 Stop hook」）。使用者觀感是「怎麼補不完」。

**根因**：把「done」定義在**元件層**——compile 過、unit test 綠，就宣告某缺口「補好了」，但把**接線**（trigger、gate、排程、寫回真理來源）延到之後。那個沒接的第二半，下一輪切到 reviewer 視角時就被當成新發現。它不是新問題，是**同一個問題只做了一半**。這正是本 repo 一直在防的 builder/reviewer 盲點：builder 模式只想「讓它動」，reviewer 模式才看到整合缺口——同一顆腦袋先後扮兩角，必然來回。

**對策（通則，寫成 done 的定義）**：一個缺口只有在**端到端會自己轉、且不靠任何人記得**時才算 done：
1. **產出與「對產出動作」不可分兩步靠記憶接**——把第二半折進**已經被強制執行**的點（例：交付 ledger 折進 fidelity Stop-hook gate 的 pass 路徑 → 沒過 gate 就沒 ledger、過了就一定有，見 `check_fidelity_gate.py --delivery-ledger`）。
2. **偵測器必須有觸發它的排程**（例：`detect_test_rot` 配 `detect_test_rot.Jenkinsfile` cron），否則是「裝了煙霧偵測器沒接電」。
3. **剩下真的做不到的**（後端沒部署、本質非 code 能解）→ **當下就標成 known limitation 收進 PR/本檔**，不留給下一輪當「新發現」。

**通則**：**review 是無界的，critic 永遠找得到東西**；要停的不是 review，是把「done」的線畫在「接好線、自己會轉」而非「元件會動」——並在收工時明確列出 known limitations，把「還沒做」與「決定不做」分開。

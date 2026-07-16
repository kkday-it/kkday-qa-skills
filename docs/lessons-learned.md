# Lessons Learned（踩過的坑）

給接手的 agent / 人類：動手前先掃這裡，看有沒有踩過類似坑。每則格式：**症狀 → 根因 → 對策（已落地在哪）**。新坑往上加（新的在前）。

---

## 「locator 共享記憶」看似有、實則從沒進後端

**症狀**：跑完 case，automator 回報「起手用 registry 拿到 N 個候選命中」，但用 MCP `get_verified_locator` / 直接打 ai_studio GET 撈 `things-to-do-search`，後端一直是空的（`entries: []`）。

**根因（兩個獨立缺口，且互相掩蓋）**：
1. **automator 沒真的跑 valve**：agent 指令原本只叫用 `verify_locator.py`（單顆驗、不 GET 後端、不 emit 回寫），根本沒提 `get_verified_locator.py` 這個唯一入口。automator 實際是**讀了本地 `locator_registry/registry.json` 的內容來敘述**，不是執行 valve → 完全不觸發回寫。
2. **本地 registry 與後端從沒同步**：那些候選只活在 checked-in 的 `registry.json`（本地 fallback 來源），沒人把它 seed 到後端。

**為什麼難發現**：後端 GET 讀出來是空的，「沒資料」和「client 根本沒推」從結果上看不出差別——缺口 #2 掩蓋了缺口 #1。診斷時一度誤判「後端是不落地的 stub」，實際後端 POST/GET 都正常，只是沒東西被推上去。

**對策（已落地）**：
- `agents/qa-case-automator.md`：**起手強制先跑 `get_verified_locator.py` valve**，明令禁止「讀 registry.json 敘述當驗過」或只跑 `verify_locator.py`；並要求回報**附 valve 執行憑據**（`source` / verified·stale / emit 檔路徑），沒憑據＝視同沒跑、退回補跑。
- `scripts/get_verified_locator.py`：`--emit` **預設就開**（忘帶旗標也會回寫）。
- 已把 `registry.json` seed 到後端（things-to-do-search 8 + home-search 2）。

**通則**：**「best-effort 遙測 / 共享層」讀出來是空的，不代表寫入端有在寫。** 驗證一條回寫鏈要兩頭都戳：POST 完立刻 GET 撈回同一筆（round-trip），別只看其中一端。

---

## 單一固定 emit 檔在並行下會被 purge 掃掉（靜默丟資料）

**症狀**：批次並行 / 多 session 同時跑，locator 回寫零星缺筆。

**根因**：emit 預設寫死單一檔 `/tmp/locator_results.jsonl`，Stop hook sender 是 `read → POST → purge`。並行時 A 的 sender 在「已 read、還沒 delete」空窗，把 B 剛 append、還沒送的行一起 purge 掉。（註：emit 是 append 模式，不是「覆寫」；真正的丟失來自這個 purge race。）

**對策（已落地）**：
- valve emit 改 **per-process 檔** `/tmp/locator_results.d/<pid>-<utc_ts>.jsonl`，各 process 各寫各的。
- `send_locator_registry.py` 新增 `--indir`：掃目錄**逐檔讀完才刪自己那份**，不碰別人正在寫的。
- 同一類（fidelity 的 `/tmp/case_fidelity_results.jsonl`）目前仍是單一固定檔；並行 append 是行原子、且 gate 是「缺就擋」安全方向，暫可接受，但若日後併發加劇可比照改 per-process。

**通則**：任何「多 producer 寫同一檔 + 有人會 purge」的組合，預設就是 race。要嘛 per-producer 檔、要嘛檔鎖；**別用單一固定路徑當並行的交換點**。

---

## 團隊 hook 是「絕對路徑快照」，git pull 不會更新它

**症狀**：改了 `install.sh` 裡的 hook 指令（flag / 路徑），但已安裝的隊友 `git pull` 後行為沒變。

**根因**：hook 是 `install.sh` 一次性用「本 clone 絕對路徑」merge 進各自 `~/.claude/settings.json` 的**快照**；`session_autopull` 只 `git pull`，不會重寫 settings。symlink 的 skills/agents 會跟著更新，但 settings 裡的 hook 指令字串不會。

**對策（已落地）**：hook 定義抽成 `scripts/sync_hooks.py`（單一來源 + 冪等 + 自動 migrate 屬本 repo 的舊 hook）；`install.sh` 呼叫它；`session_autopull.sh` 在 pull 有更新時自動重跑它 → 隊友下次 pull 自動 migrate。

**通則**：凡是「安裝時把路徑寫進使用者設定」的機制，改定義後要有**再同步**的路徑，否則等於只對「新安裝的人」生效。

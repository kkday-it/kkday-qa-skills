# KKday QA Skills - Agent Guide

這個檔案是所有使用本 repo 的 agent 的預設 onboarding context。

## Why

KKday QA 團隊用這個 repo 把日常 QA 工作（bug triage、retro、PRD review、release audit）封裝成可重用的 skill 與 agent team。目標是讓「3 天無人介入」的 agent 工作流成為可能。

## What

```
skills/tools/       工具型 skill（Jira、Confluence、Slack、Jenkins...）
skills/workflows/   流程型 skill（retro、PRD review、bug triage...）
skills/meta/        給 agent team 自己用（progress 追蹤、handoff、自評）
agents/             subagent 角色定義
teams/              預組合 team 範本
prompts/            啟動 prompt 範本
```

## Non-Negotiable Rules

### 工具呼叫
- **Atlassian MCP**：必須先呼叫 `getAccessibleAtlassianResources` 取得 cloudId 才能用其他工具
  - KKday cloudId：`8b890302-cc52-42ce-a15e-697446426613`
- **Jira MCP**：deferred tool，呼叫前先 `tool_search` 載入定義，避免 `-32601 Method Not Found`
- **Confluence**：寫入時優先用 ADF 格式；markdown 在特殊 block（task list、panel、status lozenge）會回空，需 fallback 到 ADF
- **`updateConfluencePage`**：要先 fetch 現有內容再附加，不能只傳新增段落

### 輸出語言
- 對團隊的文件（Confluence、Slack 訊息、報告）一律 **繁體中文**
- 程式碼註解、commit message、技術文件可用英文
- 與 agent 內部溝通的 progress.txt 可用英文（節省 token）

### 安全與權限
- 任何 destructive 操作（刪除、push to main、改 sharing permission）必須由人類審批
- 不要直接呼叫 Slack `chat.delete`、Jira `deleteIssue`、Confluence `deletePage`
- 不要在報告中放任何個人 email、access token、credential

### Progress 追蹤
- 長任務（預期 >30 分鐘）必須維護 `claude-progress.txt`
  - 開始時：讀取現有內容 + `git log --oneline -20`
  - 結束時：寫回三段 — 完成了什麼 / 下一步 / 卡點
- 詳細規範見 `skills/meta/progress-tracking/SKILL.md`

## How

### 載入 skill 的時機
- 使用者明確說「執行 X skill」→ 載入對應 SKILL.md
- 任務描述符合 skill 的 `description` 觸發條件 → 自動載入
- 不確定時：列出候選 skill，讓使用者選

### Agent 角色的選擇

#### 收到 ai_studio report URL 時：走 `report-url-dispatch`

貼進來的是 `autotest-service.sit.kkday.com:8081/ai_studio/test-suites/report?...uuid=…`（帶或不帶
`caseid`）→ 載 `report-url-dispatch` skill，**不要自己拼 API、也不要問使用者是哪個平台**（platform
從 suite detail 推得出來）。它會解析出 case + platform + 失敗 log，先分診判 flaky、重現確認，
才接到下面的 fix 路線。這類 case 一律是既有 case，跳過 grep gate 與 planner。

#### 🔴 收到 `KQT-T…` 時的第一步：先查 repo 有沒有這個 case

**在 spawn 任何 agent 之前先跑這行**，結果決定走 create 還是 fix —— 兩條路不一樣，選錯會做白工：

```bash
grep -rl "<ID>:" <clone>/QATestData/cases/yaml    # 有命中 = 既有 case
```

| grep 結果 | 走哪條 | 為什麼 |
|---|---|---|
| **沒命中**（新 case） | create 路線：`qa-case-planner` → `qa-case-automator` → `qa-case-fidelity-reviewer` | 前置怎麼建、關鍵斷言驗什麼還沒決定，planner 要先攤開給人確認 |
| **有命中**（既有 case） | **fix 路線：先重現 → 讀 registry（帶 `--case`）→ `qa-case-automator`（帶失敗訊息）→ `qa-case-fidelity-reviewer`，跳過 planner** | 規劃早就做過也寫進 code 了。重跑 planner 只會產出一份跟現況打架的計畫，automator 還可能照計畫重造一份既有實作 |

fix 路線的重現步驟不可省：**先照 `qa-test-runner` 跑一次拿實際 log**，再把失敗訊息一起交給
automator。沒有 log 就 spawn automator，它只能用猜的，改動範圍會失控。

fix 路線**唯一該回頭找 planner 的情況**：失敗根因不是壞掉，而是 case 規格本身變了、或要新增一個
還沒覆蓋的平台 —— 那等於重新設計，回 create 路線。

**fix 路線仍然要用 `qa-case-automator` 改檔**，不可主對話 inline 改：實作檔被
`agent_only_impl_guard.py` 綁定只有 automator 能寫，且兩個 Stop gate 靠它寫 claimed 檔才會 arm。

| 任務類型 | 用哪個 agent |
|---|---|
| **TCMS case 自動化實作**（`KQT-T…`） | 先跑上面那道 grep gate，再依 create / fix 分流 |
| 拆任務、寫 plan、分配工作 | `qa-planner` |
| 撈資料、調查、整合背景 | `qa-investigator` |
| 寫 code、改文件、跑測試 | `qa-implementer` |
| 確認交付物符合 acceptance | `qa-reviewer` |
| 獨立挑剔、扮演 critic | `qa-evaluator` |

**第一列優先於「寫 code → `qa-implementer`」。** 只要任務是把某個 `KQT-T…` 實作成自動化測試，
一律走這條鏈，**不要主對話自己 inline 寫**，也不要交給 `qa-implementer`：

```
# create（grep 沒命中）
Agent(subagent_type='qa-case-planner',           prompt='case=KQT-T… platform=web|mweb|ios|android')
  → 與人確認計畫 →
Agent(subagent_type='qa-case-automator',         prompt='case=KQT-T…')
Agent(subagent_type='qa-case-fidelity-reviewer', prompt='case=KQT-T…')

# fix（grep 有命中）
先用 qa-test-runner 跑一次重現，拿到失敗訊息 →
讀一次共享 registry（見下方「派工前必讀 registry」，**帶 --case**）→
Agent(subagent_type='qa-case-automator',         prompt='case=KQT-T… 既有實作，失敗訊息=<log 摘要>，
                                                        registry 已讀=<既有 locator / 可重用 step 或「查無」>，最小改')
Agent(subagent_type='qa-case-fidelity-reviewer', prompt='case=KQT-T…')
```

#### 🔴 派工前必讀 registry（fix 路線最容易漏的一步）

fix 路線刻意跳過 `qa-case-planner`，而 planner 是唯一被規定要讀共享 registry 的角色 ——
所以不補這一步，最常走的這條路線就從來沒讀過共享記憶。實測後果：locator registry 累積
1600+ 筆、flow registry 的 stale 率兩個月恆為 0.0（沒人讀），同一件事被不同人各寫一套
差不多的 test step。

```bash
S=<kkday-qa-skills>/scripts
python3 $S/fetch_locator_registry.py --case <KQT-T…> --platform <ios|android> --list-flows --q <關鍵字>
python3 $S/fetch_locator_registry.py --case <KQT-T…> --platform <ios|android> --flow <挑到的 key>
python3 $S/locator_valve.py          --case <KQT-T…> --platform <web|mweb> --flow <key>   # web/mweb
python3 $S/get_verified_flow.py      --case <KQT-T…> --q <關鍵字> --platform <platform> --repo-path <framework repo>
```

- **`--case` 必帶**：Stop 有 registry 讀取硬 gate（`scripts/check_registry_read_gate.py`），
  按 case 比對讀取收據；沒讀就交付會被擋下結束。
- 不知道 flow key 就 `--list-flows` **探索**，不要猜字串 —— 猜錯回空，而「回空」跟「真的沒人記過」
  長得一模一樣，那個誤判就是重造的起點。
- 讀回來是空的也算過（收據記的是「有沒有去問」），但不准跳過不問。

為什麼不能 inline：`qa-case-planner` 的規劃只在動手**之前**有意義（決定前置怎麼建真實資源、
關鍵斷言驗什麼），錯過就補不回來；而 fidelity / locator 兩個 Stop gate 是由 `qa-case-automator`
寫 claimed 檔才會 arm，沒 agent ⇒ gate 直接放行，**沒驗過卻長得跟驗過一模一樣**。

`scripts/agent_only_impl_guard.py`（PreToolUse hook）會在主對話直接寫實作路徑時**反問**該不該
走 automator——它是提醒，不是替代品：正確做法仍是一開始就 spawn agent，而不是等它跳出來。

詳見 `agents/` 目錄下各角色定義。

### 不確定怎麼辦
1. 先看 `docs/lessons-learned.md` 有沒有踩過類似坑
2. 看 `skills/` 下有沒有現成 skill
3. 都沒有：先用單 agent 試跑，不要一開始就組 team
4. 真的卡住：寫到 progress.txt 並停下來等人

## Known Gotchas（踩過的坑）

- **Slack `slack_read_channel`**：`limit=100` + `response_format=concise` 一次撈完，避免分頁
- **Google Sheets**：用 gviz/tq endpoint + `?tqx=out:json`，不用 Sheets API；strip `google.visualization.Query.setResponse(` 包裝
- **Chrome MCP 寫 Sheets**：不可靠，遇到要寫入回歸到手動複製貼上
- **Claude Code `claude mcp add`**：可能默默損壞 JSON config，事後檢查
- **CLAUDE.md 位置**：必須在 repo root；`~/.claude/CLAUDE.md` 是全域

## 參考連結

- 官方 Skills 規格：https://github.com/anthropics/skills
- Agent Teams 文件：https://code.claude.com/docs/en/agent-teams
- Anthropic harness 研究：https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents

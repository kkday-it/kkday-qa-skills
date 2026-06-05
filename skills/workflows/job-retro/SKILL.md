---
name: job-retro
description: 對一個 AI session / job 做回顧:解析 transcript 萃取「做了什麼、哪裡反覆修正、踩了什麼坑」,轉成可重用知識(memory / skill / Confluence),並支援跨 session 找重複模式。觸發:幫 session/job 做 retro、回顧剛剛的任務、萃取教訓、整理成 Confluence,或給 job/session id。
---

# Job Retro Skill

把一個（或多個）AI 工作 session 的完整歷程，變成「下次更省事」的知識。

## 三種模式

| 模式 | 用在 | 輸入 | 入口 |
|------|------|------|------|
| **預設（session-file）** | Claude Code（CLI / IDE / 桌面版 Code 模式）——能讀本機檔、有 Bash | `~/.claude` 下的 transcript 檔 | 下面 Step 0–7 + `scripts/extract_session.py` |
| **chat-export** | 要離線 retro claude.ai / 桌面版**聊天的歷史對話**(本機讀不到,需先從 claude.ai 匯出資料),且能跑 python | `conversations.json`(claude.ai 匯出) | `scripts/extract_chat_export.py`,流程同 Step 2–7 |
| **chat（即時）** | claude.ai / 桌面版聊天當下——無本機檔存取,要回顧「當前這段對話」 | 對話本身（已在 context 裡） | 讀 `references/chat-mode.md`,不跑腳本 |

### 模式自我檢查（觸發後的第一件事）

skill 一啟動,先做這個檢查再開工,並**把判斷結果講給使用者聽**(讓人看得到它選了哪個模式、為什麼):

1. **看使用者指定了沒**:若明講「用 chat 模式 / retro 我們這段對話」→ chat;若明講「去讀某個 job / session 檔 / 那個任務」→ session-file。指定優先,跳過下面。
2. **測本機檔存取能力**:我手上有 Bash / Read、且讀得到 `~/.claude/projects/`(可跑 `ls ~/.claude/projects` 確認)嗎?
   - 讀得到 → **session-file 模式**(往下 Step 0–7 跑腳本)。
   - 讀不到(claude.ai / 純聊天,沒有檔案工具)→ **chat 模式**(讀 `references/chat-mode.md`,回顧當前對話,不跑腳本)。
3. **灰色地帶**(在 Claude Code 裡又說「retro 這段對話」):兩者皆可;預設走 session-file + `latest` selector(當前 session 就是最新那個 jsonl),結果最完整。
4. **要 retro 的是 claude.ai / 桌面版「聊天」的歷史對話**(不是 Claude Code session):本機讀不到,請使用者先從 claude.ai 匯出資料拿 `conversations.json`,再跑 `scripts/extract_chat_export.py <conversations.json> list` 選一段 → **chat-export 模式**。

宣告範例:「偵測到我在 Claude Code、讀得到本機 transcript → 用 **session-file 模式**,先列 session。」

---

一個 session 指一段連續的 AI 工作：背景 job、`/goal` 跑出來的任務、或互動式對話。
它們存在：

```
~/.claude/projects/<slug>/<session-id>.jsonl   ← transcript（真正的歷程，可能數 MB）
~/.claude/jobs/<short-id>/state.json           ← job 後設資料（intent / name / 狀態 / sessionId）
```

**絕對不要直接 Read 整個 transcript**（動輒 16MB）。一律用 `scripts/extract_session.py`
把它壓成結構化摘要再分析——這正是這個 skill 的價值所在。

---

## 流程概覽

```
Step 0  選 session：哪一個（或哪幾個）要 retro？
Step 1  萃取摘要：extract_session.py → 結構化 digest（你讀這個，不讀原始 jsonl）
Step 2  辨識角色：用 prompt-engineering 角度判斷 session persona（決定知識流向）
Step 3  分析歷程：做了什麼、修正了什麼（為什麼）、踩了什麼坑、什麼有效
Step 4  萃取知識：把上面歸納成「可重用、可泛化」的 lessons
Step 5  回饋迭代：寫 memory / 改 skill / 改 workflow（對外動作要先確認）
Step 6  （選用）輸出 Confluence 工作紀錄 + lesson learned
Step 7  （進階）跨 session 彙整：多個 digest 一起看，找反覆模式 → 高信心的 skill/workflow 變更
```

詳細指引分散在 references，依需要載入：

- `references/analysis-framework.md` — Step 3–4 的分析骨架：怎麼讀 digest、怎麼從「修正」反推根因、什麼才算好的 lesson。
- `references/role-detection.md` — Step 2 的 prompt-engineering 角色辨識法與各角色訊號解讀。
- `references/knowledge-feedback.md` — Step 5 + Step 7：把發現變成 memory / skill / workflow 變更，以及跨 session 彙整與信心門檻。
- `references/confluence-template.md` — Step 6 的頁面格式。

---

## 快速判斷：使用者在哪一步？

| 使用者說的話 | 跳到 |
|-------------|------|
| 「幫這個 job 做 retro」+ 給 id / 「最近那個任務」 | Step 0 → 1 |
| 「回顧剛剛 / 這個 session」（沒給 id） | Step 1（用 `latest` 或當前 sessionId） |
| 「這次修正了什麼 / 踩了什麼坑」 | Step 1 → 3 |
| 「把教訓記下來 / 更新 memory」 | Step 4 → 5（memory） |
| 「這次的經驗該不該改進某個 skill / workflow」 | Step 4 → 5（skill/workflow） |
| 「整理成 Confluence」 | Step 6 |
| 「最近幾個 session 一直重複踩同個坑」 | Step 7（跨 session） |

---

## Step 0 — 選 session

> **腳本路徑依安裝位置而定**（user-level `~/.claude/skills/` 或 project-level `.claude/skills/`）。
> 先解析一次再用，下面的指令都以 `$SKILL` 代稱：
> ```bash
> SKILL=$(ls -d ~/.claude/skills/job-retro .claude/skills/job-retro 2>/dev/null | head -1)
> ```

不確定要 retro 哪個時，先列出來：

```bash
python3 "$SKILL"/scripts/find_sessions.py            # 全部，最近活動排前
python3 "$SKILL"/scripts/find_sessions.py --stopped  # 只列閒置 >24h（已結束）
python3 "$SKILL"/scripts/find_sessions.py --cwd <專案路徑>
```

輸出最左欄 `SELECTOR` 就是下一步要餵給 `extract_session.py` 的值。
「session 停止」的工作定義 = transcript 閒置超過 24 小時（`■` 標記），但使用者也可能
想 retro 剛結束、甚至還在跑的 session——尊重使用者指定的對象。

> 如果使用者要 retro「我們現在這個對話」，當前 sessionId 通常就是 cwd 對應 project slug
> 下最新的 transcript，可直接用 `latest`。

## Step 1 — 萃取結構化 digest

```bash
python3 "$SKILL"/scripts/extract_session.py <SELECTOR> \
  --json <jobdir>/tmp/retro.json --md <jobdir>/tmp/retro.md
```

`<SELECTOR>` 可以是 job short id、session id、transcript 路徑、或 `latest`。
深度 retro 想看全部不截斷時加 `--full`。

digest 內含（這些就是 retro 的原料）：

- **goal / intent**：這個 session 被要求做什麼（優先取 job 的 `/goal` intent）。
- **corrections**：人類的修正——分四類 `interrupt`（直接打斷）、`redirect`（改方向）、
  `question`（困惑 / 釐清）、`supplement`（中途才補的輸入，含 `[+info]`/`[img]` 旗標）。
  **這是「不斷修正的地方」的核心訊號。** `supplement` 要再分「漏給（→ 前置清單教訓）」
  vs「刻意漸進引導風格（→ prompt 技巧）」——見 analysis-framework.md。
- **tool_errors**：工具報錯與重試（摩擦點）。
- **result_lines**：session 自己宣告的 `result:` / `failed:` / `needs input:`。
- **tool_usage / mcp_servers / commands / files_touched / subagents**：做了什麼的客觀證據。
- **role_signals / role_guess**：角色判斷的原始訊號（Step 2 用，但要自己再判斷）。
- **counts / duration / cwds / git_branches**：規模與範圍。

讀 `retro.md` 就好；需要更細的欄位（例如完整 prompt 序列）再查 `retro.json`。

## Step 2 — 辨識 session 角色

`role_guess` 只是關鍵字計數的起點，**不要照單全收**。用 prompt-engineering 的角度，
綜合 cwd（哪些 repo）、用到的 MCP（Jira/Zephyr/Confluence vs 純 code）、goal 的語氣，
判斷這個 session 真正的 persona 與工作性質。詳見 `references/role-detection.md`。

角色決定知識要流向哪裡（QA 的教訓 → QA skill / QA memory；engineer 的 → 對應 repo 慣例）。
一個人可能在不同 session 戴不同帽子（這位使用者是 QA，但常在 QA 工具上做 engineering）。

## Step 3–4 — 分析歷程 + 萃取知識

讀 `references/analysis-framework.md`，產出結構化分析：做了什麼 → 修正了什麼（每個
correction 的**根因**）→ 踩了什麼坑 → 什麼有效 → 可泛化的 lessons。

重點不是流水帳，是回答：「下次做類似的任務，**哪幾個來回可以省掉**？」

## Step 5 — 回饋迭代（這個 skill 的終點價值）

把 lessons 變成持久的東西，依 `references/knowledge-feedback.md`：

- **memory**：寫 `~/.claude/projects/<this-project>/memory/` 下的記憶檔（依 MEMORY.md 規則）。
- **skill / workflow**：若某個既有 skill 的指引被這個 session 反覆繞過或修正，提出具體編輯。
- 對外或破壞性動作（改別人會用到的 skill、發 Confluence）**先讓使用者確認**。

## Step 6 — Confluence（選用）

依 `references/confluence-template.md` 產「工作紀錄 + lesson learned」頁。需要 Atlassian connector。

## Step 7 — 跨 session 彙整（進階）

對多個 session 各跑一次 Step 1，把 digest 一起看。**單一 session 的坑可能是偶然；
跨 session 反覆出現才值得改 skill/workflow。** 信心門檻見 `references/knowledge-feedback.md`。

---

## 重要原則

- **先壓縮再分析**：永遠用 `extract_session.py`，不要 Read 原始 transcript。
- **修正 = 黃金**：使用者每一次 redirect / interrupt / 困惑提問，都是一個「本來可以不發生」
  的來回。retro 的產出要直接對應到「怎麼讓它下次不發生」。
- **泛化，別過擬合**：一次 session 學到的東西要抽象成下次也適用的原則，不要把一次性細節
  寫進 skill。跨 session 出現兩次以上才是強訊號。
- **角色先行**：先知道這是哪種工作，才知道知識該回饋到哪。
- **有憑有據**：每個 lesson 都要能指回 digest 裡的某個 correction / error / 事件。
- **對外先確認**：寫 memory 可以自主；改共用 skill、發 Confluence 前先讓使用者過目。

---

## 相關資源

- **團隊介紹 / 試用頁(Confluence)**：[job-retro — 從 AI session 萃取知識的 skill(介紹與試用)](https://kkday.atlassian.net/wiki/spaces/QS/pages/2084864023)
- **原始碼**：`kkday-qa-skills/skills/workflows/job-retro`（你正在看的這份）

# Analysis Framework — 怎麼把 digest 變成 retro

讀完 `extract_session.py` 產出的 digest 後，照這個骨架分析。目標只有一個：
**找出這個 session 裡「本來可以不發生的來回」，並說清楚下次怎麼避免。**

## 1. 先建立全貌（2–3 句）

從 digest 的 goal / counts / duration / cwds / role_guess，寫出：
- 這個 session 被要求做什麼，最後做到什麼程度（看 `result_lines`：是 `result:` 還是 `failed:` / `needs input:`）。
- 規模感：幾個 assistant turn、幾次修正、幾次工具錯誤、動了幾個檔。

這段是給之後 Confluence / memory 當 context 用的，不要長。

## 2. 過程做了什麼（時間線，但要濃縮成「階段」）

不要逐 turn 流水帳。把 `user_prompts` 序列 + `files_touched` + `commands` + `subagents`
歸納成 3–7 個**階段**（phase），每個階段一句：「為了 X，做了 Y，結果 Z」。

階段邊界通常落在：一個 `redirect` 修正、一個 subagent 大任務、或一段連續 tool_error 之後。

## 3. 修正了什麼 ← 整份 retro 的核心

逐一檢視 `corrections`，這是「不斷修正的地方」。對每一個（或每一群相關的），回答：

| 問題 | 怎麼判斷 |
|------|---------|
| **發生什麼** | correction 的文字本身 |
| **為什麼會需要這次修正**（根因） | 往前看：是 agent 誤解需求？資訊不足就動手？踩到專案潛規則？工具用錯？ |
| **下次怎麼免掉** | 對應到一個可執行的改變：一條 memory、一句 skill 指引、一個先確認的動作 |

修正分四類，根因通常不同：

- **`redirect`（改方向）**：agent 走錯路。根因常是「需求理解偏差」或「沒先問就假設」。
  → 通常變成 skill 指引（「做這類事之前先確認 N」）或 memory（領域潛規則）。
- **`interrupt`（直接打斷）**：agent 正在做明顯不該做的事。根因常是「動作太大沒先對齊」
  或「重複無效嘗試」。→ 通常變成「先小步對齊再動手」的工作習慣。
- **`question`（困惑 / 釐清）**：使用者搞不清楚狀況、或 agent 沒講清楚。根因常是
  「溝通 / 文件缺口」。→ 通常變成「主動說明 X」或一份 reference。
- **`supplement`（中途補資訊）**：人類丟進一開始沒給的輸入（文檔、Figma、截圖、ticket、
  情境）。digest 也會用 `[+info]` 標出「同時帶新輸入的 redirect/question」。這一類要再細分
  （見下），是這份 framework 最需要人為判斷的地方。

> digest 的 `counts.supplements` 是「中途才補的輸入」總數，`[img]` 表示帶了截圖。

### supplement 的兩種解讀（務必分辨）

同樣是「中途補資訊」，意義天差地遠：

1. **漏給（accidental）**：本來該一開始就備齊、卻忘了給的必要輸入。典型是「要產測試 case，
   結果 PRD / Figma / 驗收標準是後來才一片片補上」。
   - 訊號：補的東西是**任務前置必需品**；補之前 agent 已經在猜、或做了會被推翻的東西；
     使用者語氣是「啊對了還有…」「忘了給你…」。
   - → lesson：替「這類任務」建一份 **input 前置清單 / Definition of Ready**，下次開工前先點齊。
     寫進對應 skill 或 memory（例：「產專案測試 case 前，先確認 PRD、Figma、驗收標準、
     測試範圍、環境都到位」）。

2. **刻意漸進（intentional）**：使用者**故意**分段給資訊，用來逐步把產出帶往某種風格 /
   觀點（例如先要 case，再補「要使用者情境 / 操作導向」，把產出引導成情境化 case）。
   - 訊號：補的是**風格 / 觀點 / 取捨方向**而非缺的事實；前一步產出其實沒錯，只是被「再加味」；
     常出現在創作 / 設計 / 內容類任務。
   - → 這不是缺陷，**不要寫成「該前置」的教訓**。要記成**可重用的 prompt 技巧 / 產出配方**：
     「想要情境導向的測試 case → 明講『以使用者實際操作流程串成情境』並給一個範例情境」。
     收進對應 skill 的「產出風格」段，或 memory（type=feedback：使用者偏好這種產出風格）。

判斷不確定時，看「補的東西若一開始就給，agent 的第一版會不會就對」——會，就是漏給；
就算給了第一版也只是不同風格、無所謂對錯，就是刻意漸進。

> 同一個根因可能對應多個 correction——把它們歸成一組，一組產一個 lesson，比逐條更有力。

## 4. 踩了什麼坑（摩擦點）

從 `tool_errors`、`interrupt`、以及 commands/files 裡的反覆嘗試，找：
- **重試迴圈**：同一個工具連續報錯（例如環境沒裝、路徑錯、權限）。
- **死路**：做了一堆後整段被推翻（看大段 redirect）。
- **重複勞動**：多個 subagent 各自寫了類似的 helper（這是「該 bundle 成 script」的訊號）。

每個坑同樣要回答「根因 + 下次怎麼免」。

## 5. 什麼有效（別只記教訓）

retro 不是只挑毛病。標出這個 session 裡**做對的、值得固化的**：
- 哪個 subagent 分工、哪個工具用法、哪個檢查順序讓事情變順。
- 這些值得寫進 skill 當「建議做法」，或寫進 memory 當「下次照這樣做」。

## 6. 萃取 lessons（泛化）

把 3–5 的發現收斂成一份 lessons 清單。一個好的 lesson：

- **可泛化**：抽掉這次的一次性細節，講的是「這類任務」的原則。
  - ✗ 「KQT-15330 要先跑 alembic」
  - ✓ 「改 schema 的 PR，部署前一定要人工套 migration，且順序是先 migration 再上 code——否則 list endpoint 會 500」（這條剛好已是 memory `feedback-tcms-alembic-shared-pg`）
- **可執行**：能對應到一個具體去處（memory / skill 哪一行 / workflow 哪一段）。
- **有來源**：能指回 digest 裡的某個 correction / error。

每條 lesson 標註建議去處與信心（單 session = 低信心提議；跨 session 重複 = 高信心）。
接著進 `knowledge-feedback.md` 決定怎麼落地。

## 輸出格式（給使用者看的 retro）

```markdown
# Retro：<session 名稱 / goal 摘要>
**結果**：<result/failed + 一句>　**角色**：<role>　**規模**：<turns / 修正數 / 時長>

## 做了什麼
- <階段 1>
- <階段 2 …>

## 修正與根因（本來可以省掉的來回）
| # | 修正（類型） | 根因 | 下次怎麼免 | 去處 |
|---|---|---|---|---|
| 1 | … (redirect) | … | … | memory / skill X |

## 踩到的坑
- <坑 + 根因 + 對策>

## 做對、值得固化的
- …

## Lessons（可重用）
- [信心] <lesson> — 來源 turn N — 建議去處
```

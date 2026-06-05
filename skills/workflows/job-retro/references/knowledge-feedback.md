# Knowledge Feedback — 把 lessons 變成持久的東西

這是 job-retro 的終點價值：不只「寫一份報告」，而是讓**下次真的不用再修正**。
分四個去處，各有不同的落地方式與信心門檻。

## 信心門檻（先決定值不值得改）

- **單一 session 出現一次**：低信心 → 適合寫成 memory（成本低、可逆），或當「提議」
  列給使用者，**先不要**直接改共用 skill / workflow。
- **跨 session 出現 ≥2 次**（用 Step 7 彙整確認）：高信心 → 值得改 skill / workflow。
- **使用者明講「以後都這樣」**：直接落地（memory feedback 或 skill 指引）。

> 過擬合是最大的風險：一次 session 的一次性細節寫進 skill，會讓 skill 對其他人/情境變糟。
> 寧可先寫 memory，等模式重複出現再升級成 skill 變更。

## 去處 1：Memory（最常用、可自主）

寫到 `~/.claude/projects/<this-project-slug>/memory/`，遵守 `MEMORY.md` 開頭那套規則：
一檔一事、frontmatter（`name` / `description` / `metadata.type`）、body 用 `[[name]]` 互連、
最後在 `MEMORY.md` 加一行索引。

retro 產出最常對應這些 type：
- **`feedback`**：使用者糾正過你「該怎麼做事」。body 要有 **Why** 與 **How to apply**。
  （retro 的 `redirect` / `interrupt` 修正最常變這種。）
- **`project`**：這個任務的目標 / 約束 / 狀態（相對日期要轉絕對日期）。
- **`reference`**：外部資源指標（這次發現的 Confluence 頁、dashboard、ticket）。

存之前先檢查有沒有重複的檔可以更新（別建分身）；發現舊 memory 過時就改/刪。

## 去處 2：Skill 指引（中信心、改前確認）

當某個既有 skill 的指引，被這個 session **反覆繞過、修正、或不足**時：
1. 指出是哪個 skill 的哪一段（引用原文）。
2. 說明這次 session 怎麼證明它需要改（指回 correction / error）。
3. 提出具體編輯（加一句指引 / 補一個 reference / bundle 一個重複手寫的 script）。
4. **先給使用者看 diff 再改**——這是共用資產。

特別注意 skill-creator 的原則：能解釋「為什麼」就不要寫死板的 MUST；發現多個 subagent
重複手寫同一段 helper，就把它 bundle 成 `scripts/` 裡的腳本。

## 去處 3：Workflow（高信心、跨 session 才考慮）

若 retro 顯示某類任務反覆需要同一套「分工 + 驗證」編排（例如「找問題 → 去重 → 對抗式驗證」），
而且跨 session 重複出現，才提議寫成 workflow。單一 session 不足以證明。

## 去處 4：本 skill 自我迭代

job-retro 自己也是 skill。如果在做 retro 的過程中發現 `extract_session.py` 漏抓了某種
重要訊號、或某個 reference 不夠用，就回頭改進這個 skill（並記一筆 memory 說明改了什麼）。

---

## Step 7：跨 session 彙整

目標：把「偶然的坑」和「系統性的問題」分開。只有系統性的才值得改 skill/workflow。

做法：
1. 對每個目標 session 跑 `extract_session.py --json`，收集多份 digest。
2. 把各 digest 的 `corrections`（尤其根因）、`tool_errors`、`files_touched` 攤平。
3. 找**反覆出現的根因**：
   - 同一個潛規則被踩了 N 次（→ 強烈該寫 memory / skill）。
   - 同一個工具/環境問題重複報錯（→ 該 bundle 設定或前置檢查）。
   - 同一種需求理解偏差跨任務出現（→ 該在 skill 加「先確認 X」）。
4. 用出現次數 + 跨幾個不同 session 當信心分數，排序提議。
5. 輸出一份「跨 session 模式報告」：每個模式列出現次數、來源 session、建議去處、信心。

> 跨 session 彙整也很適合做成定期動作（例如每週對 `--stopped` 的 session 跑一輪），
> 但這要使用者明確要求才設定。

---

## 落地後的回報格式

不管落地到哪，都向使用者清楚交代「改了什麼、為什麼、在哪」：

```markdown
## 已沉澱的知識
- memory: `feedback-xxx.md`（新增/更新）— <一句>
- skill: `<skill>/SKILL.md` 第 N 段提議編輯（待確認）— <一句 + 為什麼>
- workflow: （跨 session 才提）<一句>
```

memory 可自主寫；skill / workflow / Confluence 等影響他人或對外的，先讓使用者過目。

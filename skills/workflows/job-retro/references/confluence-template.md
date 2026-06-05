# Confluence 輸出模板 — Session 工作紀錄 + Lesson Learned

需要 Atlassian connector。一個 session 通常一頁就夠（可選附錄放完整時間線）。
多 session 彙整時，主頁 + 各 session 附錄。

## 頁面架構

```
{角色} Session Retro：{session 名稱 / goal 摘要}（{日期}）   ← 主頁（工作紀錄 + lesson learned）
└── 附錄：完整修正時間線（選用，內容多時才拆）
```

## 主頁模板（contentFormat: "markdown"）

```markdown
## Session 基本資訊

| 項目 | 內容 |
|------|------|
| Session / Job | {job name 或 session id 前 8 碼} |
| 角色（domain / mode） | {例：QA 領域的 engineering} |
| 目標 | {goal / intent 摘要} |
| 結果 | {result: / failed: + 一句} |
| 工作期間 | {start} ～ {end}（active {duration}） |
| 規模 | {N} turns · {N} 修正 · {N} tool errors · {N} subagents · {N} files |
| 涉及 repo | {cwds / git branches} |
| 分析者 | {使用者} |

---

## 一、過程摘要（做了什麼）

{3–7 個階段，每個一句「為了 X，做了 Y，結果 Z」}

---

## 二、修正與根因（本來可以省掉的來回）

| # | 修正（類型） | 根因 | 下次怎麼避免 | 已沉澱到 |
|---|---|---|---|---|
| 1 | {內容}（redirect/interrupt/question） | {根因} | {對策} | memory / skill / — |

> 類型說明：redirect=改方向、interrupt=直接打斷、question=困惑/釐清。

---

## 三、踩到的坑

### 1. {坑標題}
**現象**：{tool error / 死路 / 重複勞動}
**根因**：{說明}
**對策**：{下次怎麼免}

---

## 四、做對、值得固化的

- {值得寫進 skill / memory 的有效做法}

---

## 五、Lessons Learned（可重用）

| 信心 | Lesson | 來源 | 建議去處 |
|------|--------|------|----------|
| 高/中/低 | {泛化後的原則} | turn N / 跨 N 個 session | memory `xxx` / skill `yyy` |

---

## 六、已落地的知識沉澱

- memory: `{file}.md`（新增/更新）— {一句}
- skill: `{skill}/SKILL.md`（提議/已改）— {一句 + 為什麼}

---

## 七、參考

- Transcript：`{path}`（本機）
- 相關 PR / Jira / Confluence：{連結}
```

## 附錄模板（選用）

```markdown
> 本頁為 session `{id}` 的完整修正時間線，作為 [Retro 主頁]({主頁 URL}) 的原始依據。

## 完整修正時間線

| @turn | 類型 | 內容 |
|-------|------|------|
| {N} | redirect | {完整文字} |
```

## API 呼叫

### 建立主頁
```
cloudId:  {getAccessibleAtlassianResources}
spaceId:  {getConfluenceSpaces 或使用者指定，預設個人 space}
title:    "{角色} Session Retro：{摘要}（{日期}）"
contentFormat: "markdown"
status:   "current"
```

### 建立附錄（掛主頁下）
```
parentId: {主頁 page ID}
title:    "附錄：Session {id} 完整修正時間線"
contentFormat: "markdown"
```

### 更新（updateConfluencePage）
全量替換，body 要傳完整內容，不是 diff。

> 發 Confluence 是對外動作——先把要發的內容給使用者過目，確認 space 與標題後再建頁。

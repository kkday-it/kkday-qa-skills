---
name: project-retro
description: |
  從 Slack 專案 channel 全量 dump 討論串，交叉比對 PRD、團隊 Lesson Learned、QA 補充觀察，產出結構化的跨職能 retro 分析報告並輸出到 Confluence。

  適用情境：
  - 使用者說「幫我做這個專案的 retro」、「分析這個 channel 的問題」、「整理專案 lesson learned」
  - 使用者提供 Slack channel URL 並想了解「問題出在哪個環節」
  - 使用者想把 Slack 討論、PRD 審查、團隊 retro 文件整合成一份完整報告
  - 使用者說「把這些整理輸出到 Confluence」

  必要工具：Slack connector、Atlassian connector
  選用工具：Google Drive connector（讀取 PRD）
---

# Project Retro Skill

從 Slack channel 到 Confluence 的完整專案 retro 流程。

## 流程概覽

```
Step 0：確認連接器
Step 1：全量 dump Slack channel（主訊息 + 所有 thread）
Step 2：逐串分析 + 分類，產出視覺化報告
Step 3：交叉比對 PRD / Lesson Learned / QA 觀察
Step 4：產出 action items（依角色分類）
Step 5：輸出到 Confluence（主報告 + 附錄雙頁架構）
Step 6：（選用）通知對應角色
```

詳細執行指引請見 `references/step-by-step.md`。
分類框架與根因分析請見 `references/classification.md`。
Confluence 輸出格式請見 `references/confluence-template.md`。

---

## 快速判斷：使用者在哪個步驟？

| 使用者說的話 | 跳到 |
|-------------|------|
| 「幫我做 retro」+ 給 Slack URL | Step 1 |
| 「這是 PRD，幫我審核」 | Step 3（PRD 審查） |
| 「根據 QA 反應：...」 | Step 3（QA 觀察補充） |
| 「繼續補充：...」 | Step 3（追加事件） |
| 「輸出到 Confluence」 | Step 5 |
| 「把 action 對應給各角色」 | Step 6 |
| 「把這個對話的 prompt 整理給我」 | 輸出 prompt playbook |

---

## 核心輸出結構

### Confluence 主報告包含
1. 三方資料交叉摘要（數據儀表板）
2. 核心歸納（失效鏈分析）
3. 下一個專案必要 Action Items（依角色分類）
4. PRD 審查具體缺漏點
5. Slack Retro 全量問題分類表
6. 參考資料連結

### Confluence 附錄包含
- 每一串討論的完整事件經過 + 問題分類 + 根因
- 作為主報告的原始依據

---

## 重要原則

- **先 dump 再分析**：不要邊撈邊分析，先把所有資料拉完，再一次性分析
- **thread 要全撈**：主訊息只是表面，重要決策往往在 thread 裡
- **多來源交叉**：Slack 是事件，PRD 審查是根因，Lesson Learned 是 RD 視角，三者交叉才能找到真正的改善點
- **有憑有據**：每個分類結論都需要有對應的 Slack 討論串作為佐證
- **Confluence 分兩頁**：主報告給 PM/EM/UED 看結論，附錄給深入查閱用

---

## 多 Agent 協作模式（進階）

當 retro 任務複雜（>1 個專案 / >500 則 Slack 訊息）時，建議用 team 模式：

- `qa-investigator`：Step 1-2（dump + 初步分類）
- `qa-planner`：Step 3-4（交叉分析 + action items）
- `qa-reviewer`：Step 5（產出 Confluence 前的最終 review）
- `qa-evaluator`：獨立挑剔報告品質（避免互相取暖）

對應 team 範本：`teams/retro-analysis-team.md`

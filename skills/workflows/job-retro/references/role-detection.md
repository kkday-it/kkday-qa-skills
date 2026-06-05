# Role Detection — 用 prompt-engineering 角度辨識 session 角色

為什麼要先做這步：retro 萃出的知識要回饋到正確的地方。QA 的教訓應該流向 QA skill /
QA memory；engineer 的流向對應 repo 的慣例；data analyst 的流向報表 / 指標慣例。
搞錯角色，知識就放錯抽屜，下次召回不到。

`extract_session.py` 的 `role_guess` 只是**關鍵字計數**，是起點不是結論。用下面的方法覆核。

## 用三層證據判斷（由強到弱）

1. **cwd / repo（最強）**：看 digest 的 `cwds` 與 `git_branches`。
   - `kkday-QA-automation`、`tcms`、`zephyr`、`vision-qa`、含 `test` 的 repo → QA 領域
   - 一般產品 repo（前端 / 後端 / API / SDK）→ engineering
   - 含 `report`、跑 SQL/BigQuery、explore_platform 之類分析工具 → data / analytics
2. **工具與 MCP（次強）**：看 `mcp_servers` 與 `tool_usage`。
   - Jira / Zephyr / Confluence / Playwright 重 → QA 或 PM 性質
   - 大量 Edit/Write + Bash(build/test/git) → engineering
   - Mixpanel / BigQuery / 圖表 → data analyst
3. **goal 語氣與 corrections（補強）**：goal 在講「驗收 / 測試 / 缺陷 / 回歸」還是
   「實作 / 重構 / 部署 / 修 bug」還是「分析 / 報表 / 指標」。

## 工作性質 ≠ 職稱

**這位使用者本身是 QA**（見 memory），但常在 QA 工具（TCMS）上做**engineering**工作——
改 schema、開 PR、跑 alembic、做 e2e。所以單一 session 的 persona 可能是「QA 在做 engineering」。

判斷時分兩個維度，都講出來：
- **領域（domain）**：這個 session 服務的是哪個領域？（QA / 產品 / 數據）
- **動作（mode）**：這個 session 在做哪種動作？（engineering 實作 / testing 驗證 / analysis 分析 / ops 維運）

例：`role_guess: engineer (signals engineer 1961 / qa 1403)`，但 cwd 全是 tcms QA 工具、
goal 在優化 QA 系統 → 結論寫成 **「QA 領域的 engineering 工作」**，知識同時可進
QA memory（領域潛規則）與 engineering 慣例（該 repo 的 build/test 做法）。

## 角色 → 知識去處對照

| Persona | 典型知識去處 |
|---------|-------------|
| QA testing | QA memory（領域陷阱、資料來源）、`qa-automation-writer` / `zephyr-tcms-sync` 等 QA skill |
| QA-domain engineering | 該 repo 的 `.ai_rules.md` / CLAUDE.md 慣例、TCMS 相關 memory |
| 純 engineering | 對應 repo 的慣例 memory、相關 dev skill |
| data analyst | 報表 skill（`live-bug-quality-report` 等）、指標口徑 memory |
| 跨職能 / 混合 | 拆開：哪部分知識給哪個抽屜，分別處理 |

## 一句話輸出

在 retro 開頭給一句角色判斷，格式：
`角色：<domain> 的 <mode>（依據：cwd=…、MCP=…、goal=…）`

例：`角色：QA 領域的 engineering（依據：cwd 全在 kk_tcms_1.5、用到 Jira+Playwright、goal 在優化 TCMS 並開 PR）`

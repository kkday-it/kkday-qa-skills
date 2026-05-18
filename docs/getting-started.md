# Getting Started

從 0 到能用上這 repo 裡的 skill。約 5 分鐘。

## 前置：先裝好 Claude Code

需要先有 Claude Code（CLI 版或 IDE 擴充版任一）。安裝後在 terminal 打 `claude --version` 有版本號就 OK。

> 沒裝過？看官方文件：<https://docs.anthropic.com/claude-code>

## Step 1 — clone 這個 repo 到本機

```bash
git clone https://github.com/kkday-it/kkday-qa-skills.git ~/Documents/kkday-qa-skills
```

> 路徑可改，後面的 `ln -s` 跟著調整即可。

## Step 2 — 把 skill 接到 Claude Code 的 skill 目錄

Claude Code 會自動讀 `~/.claude/skills/` 下所有 skill。用 `ln -s` 建捷徑，未來 `git pull` 後 Claude 自動跟著更新，不用重裝。

### 新手建議：先只接 2 支 tools（立即見效）

```bash
mkdir -p ~/.claude/skills

ln -s ~/Documents/kkday-qa-skills/skills/tools/qa-test-report-template ~/.claude/skills/
ln -s ~/Documents/kkday-qa-skills/skills/tools/python-pr-quality-checklist ~/.claude/skills/
```

這兩支「拿來就用、立刻有正向回饋」：
- `qa-test-report-template`：測試完寫報告
- `python-pr-quality-checklist`：commit 前自檢

### 用 1-2 週後再加 meta（強迫對焦類）

```bash
ln -s ~/Documents/kkday-qa-skills/skills/meta/dev-focus-alignment ~/.claude/skills/
ln -s ~/Documents/kkday-qa-skills/skills/meta/task-input-readiness-check ~/.claude/skills/
```

這兩支會「打斷 agent」要你確認方向 / 確認 input — 等你踩過幾次「agent 自己腦補做錯方向」後再加，體感比較對。

### 為什麼分批：給 onboarding 順序

| 第一階段 | 第二階段 |
|---|---|
| 「Claude 幫我多做事」的正向體驗 | 「Claude 動工前先停下來確認」的把關 |
| tools skill：qa-test-report-template / python-pr-quality-checklist | meta skill：dev-focus-alignment / task-input-readiness-check |
| 立即見效 | 用過才知道為什麼需要 |

> Windows 用 `mklink /D` 取代 `ln -s`，或直接 copy 整個目錄過去（這樣每次 `git pull` 後要重 copy）。

## Step 3 — 開**新的** Claude Code session 試用

> 已經開著的 session 看不到剛裝的 skill，要關掉再開。

兩種觸發方式，效果一樣，看你習慣：

### A. 用 `/` 顯式叫（你記得 skill 名字時）

```text
/qa-test-report-template KQT-1234 在 SIT-213 跑了 5 個 case 全 pass，幫我寫報告
/python-pr-quality-checklist 自檢 src/auth/login.py
```

### B. 直接講話（不用記名字，Claude 看內容自動找對的 skill）

```text
「測試做完了要回報 ticket」      → qa-test-report-template 會給範本
「PR 送出前自檢一下這支 py」      → python-pr-quality-checklist 會跑 6+1 條
「幫我加個登入 modal」          → dev-focus-alignment 會跳出來要你對焦（裝了之後）
「想跑這個專案的 retro，怎麼開始」 → task-input-readiness-check 會列必備 input（裝了之後）
```

## Step 4 — 看還有什麼可用

開 [skills-catalog.md](skills-catalog.md) — 整個 repo 有什麼 skill、何時用、怎麼搭配，一頁看完。

## 不想再用某支？拔捷徑就好

```bash
rm ~/.claude/skills/dev-focus-alignment
```

對 git repo 0 影響，純粹是把 Claude 的「眼罩」拿掉。

## 常見問題

**Q：裝完 Claude Code 沒反應、`/<skill-name>` 找不到？**
- 確認你開的是 Step 2 之後的**新 session**
- 確認 `~/.claude/skills/<skill-name>/SKILL.md` 真的存在（`ls -la ~/.claude/skills/`）

**Q：要不要 4 支全裝？**
- 看你過去的痛點。已經被「agent 自己腦補做錯方向」害過 → 全裝。沒體驗過 → 從 2 支開始。

**Q：和我自己 `~/.claude/skills/` 下既有的 skill 衝突嗎？**
- 不會，每支 skill 是獨立資料夾 + 不同 `name`。除非你自己 skill 也叫 `qa-test-report-template`（那也只是要重命名其中一支）。

**Q：之後 repo 有更新，要怎麼同步？**
- `cd ~/Documents/kkday-qa-skills && git pull`。`ln -s` 已連到 repo，pull 完 Claude 下次自動讀新版。

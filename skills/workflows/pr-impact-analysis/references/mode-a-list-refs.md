# Mode A — 列 refs 讓使用者選

當用戶只給 repo（沒給 base/target），自動進這個模式。

**強制走 `scripts/list_refs.py`**——不要再手寫 gh / GraphQL 指令。手寫每次都會踩雷（first:100 限制、--jq 對 array 輸出 NDJSON 破 json.load、master/main/develop 不在 top-100 commit date ranking）。

## 唯一正解：跑 script

```bash
SKILL=~/.claude/skills/pr-impact-analysis
python3 "$SKILL/scripts/list_refs.py" <repo_or_alias>
```

範例：

```bash
python3 "$SKILL/scripts/list_refs.py" ios
python3 "$SKILL/scripts/list_refs.py" b2c-api
python3 "$SKILL/scripts/list_refs.py" kkday-b2c-web --filter rc
python3 "$SKILL/scripts/list_refs.py" android --tags-limit 20
python3 "$SKILL/scripts/list_refs.py" member-ci   # 自動同時列 b2c-web (WT/WR/WB 編號)
```

script 一次處理掉所有 edge case：

| 問題 | script 怎麼處理 |
| --- | --- |
| GraphQL `first:200` 會吐 `EXCESSIVE_PAGINATION` | 寫死 `first:100`（上限） |
| `gh api graphql --jq '...nodes'` 輸出 NDJSON 破 json.load | 不用 --jq，直接 json.loads(raw)，內部抽 nodes |
| master / main / develop 不在 top-100 commit date ranking | 偵測到缺失就用 `gh api repos/.../branches/<name>` 直接抓（threaded） |
| 76 個 release/* 一次倒出來太吵 | 只留最近 8 個 release/*，主分支（master/main/develop）強制排最前面 |
| `member-ci` / `mobile-member-ci` 要同時列 b2c-web | script 自動偵測別名、第二段用 `WT/WR/WB` 編號避免衝突 |
| `gh auth` 沒登入 | script 啟動先跑 `gh auth status`，失敗就 exit 2 並提示 `gh auth login` |

## 參數速查

| 參數 | 預設 | 說明 |
| --- | --- | --- |
| `<repo_or_alias>` | 必填 | 別名（ios/android/member-ci/mobile-member-ci/b2c-web/b2c-api）或 repo name（如 `kkday-b2c-api`） |
| `--tags-limit N` | 10 | 顯示前 N 個 tag |
| `--show-all-tags` | False | 顯示全部撈到的 tag（最多 30） |
| `--branches-limit N` | 10 | 顯示前 N 個 dev branch |
| `--show-all-branches` | False | 顯示全部 dev branch |
| `--filter STR` | — | case-insensitive substring 過濾 tags/branches |
| `--skip-auth-check` | False | 跳過 `gh auth status` 檢查（debug 用） |

## ⛔ 強制：列完 refs 後必須再 render「推薦 base→target 組合表」

list_refs.py 把 refs 倒出來只是原料，**直接叫使用者打字選 = 體驗 3.25**。Claude 端**強制**多做一步：依 ref 性質配出有意義的 base→target 組合，用 plain text markdown table 列出來，含「用途」欄位，每列給編號（C1 ~ Cn）。

🚫 **不准用 AskUserQuestion**（會把選項限縮成 4 個、塞掉自由輸入與其他編號）。
✅ **必須用 plain text markdown table**：使用者可以回 `C1` 編號、回 ref 全名、或混搭 `B6 master` / `master test/20260425-3`。

組合表覆蓋場景（依該 repo 實際抓到的 ref 篩，沒有就跳過該列；總列數上限 10）：

| 場景類別 | base | target | 何時列 |
| --- | --- | --- | --- |
| Task branch review | `master` | `task/*` / `refactor/*` / `feature/*` | 有 dev branch（最多 5~6 條，依 commit date 排） |
| Develop 累積 | `master` | `develop` | 同時有 master 與 develop |
| 測試 build 含什麼 | `master` | `test/*`（最新一支） | 有 test/* tag |
| 反查 master 比測試 build 多什麼 | `test/*` | `master` | 有 test/* tag |
| Hotfix 之後 master 累積 | `hotfix/*`（最新一支） | `master` | 有 hotfix/* |
| 跨 release diff | 較舊 release tag | 較新 release tag / master | 有 ≥ 2 個 release/* 或 vX.Y.Z tag |

**輸出格式 example**：

```
推薦 base→target 組合（回編號或自己打一組 ref 都行）：

| # | base → target | 用途 |
|---|---|---|
| C1 | `master` → `task/KB2CW-3901-dayjs-calendar-recut` (B6) | dayjs migration 商品頁日曆 |
| C2 | `master` → `task/KB2CW-3711-dayjs-infra-recut` (B10) | dayjs migration 基礎設施層 |
| ...                                                                                       |
| C7 | `master` → `develop` (R2 → R1) | develop 累積還沒上 master |
| C8 | `master` → `test/20260425-3` (R2 → T1) | 4/25 測試 build 含什麼 |
| C9 | `test/20260425-3` → `master` (T1 → R2) | 反過來看 master 比這 build 多什麼 |
| C10 | `hotfix/20251110` → `master` (R4 → R2) | 11/10 hotfix 之後 master 累積 |

回編號（如 `C1`、`C7`）或自己打一組 ref（如 `B6 master`、`master B5`）都行。
```

## 解析使用者回覆

接受：
- **組合編號**：`C1` ~ `Cn`（推薦表）→ 直接套那列的 base / target
- **單獨 ref 編號**：`T2 T1` / `R1 B1` / `WT1 WR1`（list_refs.py 原始編號）
- **ref 名字**：`v3.5.6 master` / `master task/KB2CW-3901-dayjs-calendar-recut`
- **混搭**：`T2 axu/4bj5` / `B6 master`

解析成功後**自動進 Pipeline**（Mode B），不用使用者再敲一次參數。

無法解析時列出最接近的選項給用戶重選。

## member-ci / mobile-member-ci

script 自動列 `kkday-b2c-web` 的 refs（W 系列編號 WT1, WR1, WB1...）。使用者選 base/target 時各帶兩個 ref（主 repo + b2c-web）。

**目前 background script `run_pipeline.py` 尚不支援 b2c-web 配對**，這兩個別名先走 wait 模式或請用戶用 `mode=wait` 強制。
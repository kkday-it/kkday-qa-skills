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

## 解析使用者回覆

接受編號（`T2 T1` / `R1 B1`）/ 名字（`v3.5.6 master`）/ 混搭（`T2 axu/4bj5`）。解析成功後**自動進 Pipeline**（Mode B），不用使用者再敲一次參數。

無法解析時列出最接近的選項給用戶重選。

## member-ci / mobile-member-ci

script 自動列 `kkday-b2c-web` 的 refs（W 系列編號 WT1, WR1, WB1...）。使用者選 base/target 時各帶兩個 ref（主 repo + b2c-web）。

**目前 background script `run_pipeline.py` 尚不支援 b2c-web 配對**，這兩個別名先走 wait 模式或請用戶用 `mode=wait` 強制。
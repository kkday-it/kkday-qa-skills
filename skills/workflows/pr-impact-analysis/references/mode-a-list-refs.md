# Mode A — 列 refs 讓使用者選

當用戶只給 repo（沒給 base/target），自動進這個模式。**完全用本地 `gh` CLI**，不打後端 `/get-github-refs`。

## Step 1：撈 tags（本地）

```bash
# 最新 30 個 tag，按 GitHub API 預設排序（最新優先）
gh api "repos/kkday-it/$repo/tags?per_page=30" --jq '.[].name'
```

## Step 2：撈 branches（本地，多類混合）

需要兩種 branch：
- **Release / 主分支**：`master`、`main`、`develop`、`release/*`、`hotfix/*` — 永遠都列
- **RD 開發分支**：使用者自己開的（如 `axu/4bj5`、`eden/feat-xxx`），用最近 commit date 排序

用 GraphQL 一次拿完（含 committedDate，client 端排序）：

```bash
gh api graphql -f query='
query($owner:String!,$name:String!){
  repository(owner:$owner,name:$name){
    refs(refPrefix:"refs/heads/",first:100,orderBy:{field:TAG_COMMIT_DATE,direction:DESC}){
      nodes{name target{... on Commit{committedDate}}}
    }
  }
}' -f owner=kkday-it -f name=$repo --jq '.data.repository.refs.nodes'
```

回來後 client 端切兩組：release-like（regex 配 `^(master|main|develop|rc|release/.*|hotfix/.*)$`）vs RD（其他）。RD 預設只列最近 commit 的前 10 個，`branches=all` 全列。

**注意**：
- GitHub GraphQL `refs.first` **上限 100**，不能寫 `first:200`，會吐 `EXCESSIVE_PAGINATION`。要更多就分頁
- `rc` 是 b2c-web 的 release branch（hotfix/master 之外的另一個發版基線），列入 Release Branches
- b2c-web 的 tag 慣例是 `test/<YYYYMMDD>[-suffix]`，不像 ios/android 是 `<version>/<build>`。長相不同屬正常

## Step 3：編號 list 顯示

```
=== kkday-ios-member ===

Tags (10 / 30, 用 tags=N / tags=all 看更多):
  T1.  1.203.0/1.203.0.3
  T2.  1.203.0/1.203.0.2
  ...

Release Branches (固定列):
  R1.  master
  R2.  release/1.203.0

Dev Branches (10 / 47, 用 branches=N / branches=all 看更多)：
  B1.  axu/4bj5            (2026-05-10)
  B2.  eden/feat-xxx       (2026-05-09)
  ...

請輸入 base 跟 target，例如：T2 T1 / R1 B1 / v3.5.6 master / B1 R1
```

## Step 4：解析回覆

接受編號（`T2 T1` / `R1 B1`）/ 名字（`v3.5.6 master`）/ 混搭（`T2 axu/4bj5`）。解析成功後**自動進 Pipeline**，不用使用者再敲一次參數。

`filter=xxx` 時 case-insensitive substring 同時過濾 tags / release branches / dev branches。

無法解析時列出最接近的選項給用戶重選。

## member-ci / mobile-member-ci 特殊

仍需同時列 `kkday-b2c-web` 的 refs（W 編號 W1, W2...），實作邏輯同上、各跑一次。使用者選 base/target 時各帶兩個 ref（主 repo + b2c-web）。

**目前 background script 尚不支援 b2c-web 配對**，這兩個別名先走 wait 模式或請用戶用 `mode=wait` 強制。

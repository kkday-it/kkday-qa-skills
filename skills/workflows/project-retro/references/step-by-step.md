# Step-by-Step 詳細執行指引

> ⚠️ TODO: 從原始 `/mnt/skills/user/project-retro/references/step-by-step.md` 移植過來

## Step 0：確認連接器

確認 Slack 與 Atlassian connector 已連線。

## Step 1：全量 dump Slack channel

- 用 `slack_read_channel` with `limit=100`、`response_format=concise` 撈主訊息
- 對每個有 reply 的訊息，用 `slack_read_thread` 平行撈 thread

## Step 2：逐串分析 + 分類

（待補充——從原 skill 移植）

## Step 3-6

（待補充）

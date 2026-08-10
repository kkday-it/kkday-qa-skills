---
name: tcms-mark-automation
description: |
  批次修改 TCMS test case 的 `automation_status` 欄位（Automated / Manual / Not Set），
  來源支援：整個 Suite 底下所有 case、整個 TestRun 內的 case、或直接貼 case_id / KQT-T 清單。

  適用情境：
  - 「suite 36 底下的 case 全部改成 Automated」
  - 「run 435 裡的 case 都應該是 Automated」
  - 「這幾條 KQT-T… 改成 Manual」
  - 一次跑一個 folder / suite / run 灌自動化狀態時
---

# TCMS Mark Automation Status

批次改 TCMS case 的 `automation_status`。純寫入：只碰這一個欄位，其他欄位不動。

TCMS base：`http://autotest-service.sit.kkday.com:8081/tcms/api/v1`

## 觸發時機

| 使用者意圖 | 對應模式 |
| --- | --- |
| 「Suite 36 底下全部 → Automated」 | `--suite-id 36 --status Automated` |
| 「Run 435 裡的 case → Automated」 | `--run-id 435 --status Automated` |
| 「這幾條 KQT-T… 改 Automated」 | `--cases KQT-T18901,KQT-T18902 --status Automated` |
| 「這幾個 case_id → Manual」 | `--case-ids 263,264 --status Manual` |

## 用法

```bash
SCRIPT=~/.claude/skills/tcms-mark-automation/scripts/mark_automation.py

# Suite 底下所有 case（dry-run）
python3 "$SCRIPT" --suite-id 36 --status Automated

# Suite 底下所有 case（真的寫入）
python3 "$SCRIPT" --suite-id 36 --status Automated --apply

# 整個 Run（dry-run 看要動幾條）
python3 "$SCRIPT" --run-id 435 --status Automated

# 直接指定 KQT-T
python3 "$SCRIPT" --cases KQT-T18901,KQT-T18902 --status Automated --apply

# 直接指定 case_id（數字）
python3 "$SCRIPT" --case-ids 263,264,265 --status Manual --apply
```

## 參數

| Flag | 必填 | 預設 | 說明 |
| --- | --- | --- | --- |
| `--suite-id` | 三選一 | – | Suite id（撈 `/cases/suite/{id}`） |
| `--run-id` | 三選一 | – | TestRun id（撈 `/results/run/{id}` → unique case_id） |
| `--cases` | 三選一 | – | 逗號分隔 KQT-T ID |
| `--case-ids` | 可替代 | – | 逗號分隔數字 case_id |
| `--status` | 是 | – | `Automated` / `Manual` / `Not Set` |
| `--apply` | 否 | (dry-run) | 加了才實際寫入 |
| `--tcms-base` | 否 | `http://autotest-service.sit.kkday.com:8081/tcms/api/v1` | |
| `--tcms-user-id` | 否 | `ml09h4qj-l7bsikcns5m` (Eden Lai) | 寫入 audit 用 |

## 標準流程

1. **Dry-run**（不加 `--apply`）：印出 total / 已是目標狀態 / 需要更新 三個數字，最多列出前 15 條 non-target。
2. **Apply**：加上 `--apply` 真的 PUT。
3. **Verify**：script 會再撈一次確認全變成目標狀態，印 counter breakdown。

## 已知限制

- **只改 automation_status**：PUT body 只帶 `{"automation_status": ...}`，其他欄位不動。
- **不建 case、不動 steps**：這 skill 純狀態欄位切換。要建 case 用 [[tcms-create-case]]、要撈 steps 用 [[tcms-fetch-cases]]。
- **不篩人**：整批一起改。要挑人手改就自己組 `--case-ids`。

## 認證

- Bearer token：`~/.cache/tcms_token`（可用 `TCMS_TOKEN` env 覆寫）
- X-User-Id：預設 `ml09h4qj-l7bsikcns5m`（Eden admin），可 `--tcms-user-id` 覆寫

## 相關 skill

- [[tcms-fetch-cases]] — 撈 case 內容（含 steps）
- [[tcms-create-case]] — 建新 case
- [[zephyr-cycle-to-tcms]] — Zephyr cycle 建 TestRun
- [[report-to-tcms-run]] — 把 report 結果灌回 TestRun
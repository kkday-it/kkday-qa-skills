---
name: tcms-fetch-cases
description: |
  從 TCMS 撈 test case 資訊（含 steps），輸出成 JSON 供後續使用。
  支援三種來源：直接貼 KQT-T ID、整個 TCMS Run、或 Run 內「篩選 by 人（assignee）」。
  純讀取，不做實作、不回填。

  適用情境：
  - 「幫我撈 KQT-T37253, KQT-T37258 的 case 內容」
  - 「TCMS Run 95 有哪些 case」
  - 「Run 95 裡 Eden Lai 負責哪些 case」（篩人）
  - 需要 case steps 餵給其他流程（自動化實作、report、review）時
---

# TCMS Fetch Cases

從 TCMS API（內網、免認證）撈 test case 完整資訊與 steps，輸出 JSON。
這是一個**純讀取**能力 —— 只負責「拿 case」，不負責實作或回填。

TCMS base：`http://autotest-service.sit.kkday.com:8081/tcms/api/v1`

## 觸發時機

| 使用者意圖 | 對應模式 |
| --- | --- |
| 「撈 KQT-T37253, KQT-T37258」 | 貼 ID：`--cases` |
| 「Run 95 有哪些 case」 | 整個 run：`--run-id` |
| 「Run 95 裡 Eden Lai 的 case」 | 篩人：`--run-id` + `--assignee` |

## 三種模式

```bash
SCRIPT=~/.claude/skills/tcms-fetch-cases/scripts/fetch_cases.py

# 模式 A — 直接貼 KQT-T ID（逗號分隔）
python3 "$SCRIPT" --cases KQT-T37253,KQT-T37258 --out /tmp/tcms_cases.json

# 模式 B1 — 整個 Run 全撈
python3 "$SCRIPT" --run-id 95 --out /tmp/tcms_cases.json

# 模式 B2 — Run 內「篩選 by 人」（assignee 支援 full name / email / username 部分比對）
python3 "$SCRIPT" --run-id 95 --assignee "Eden Lai" --out /tmp/tcms_cases.json
```

## 參數

| Flag | 必填 | 說明 |
| --- | --- | --- |
| `--run-id` | 與 `--cases` 二擇一 | TCMS Run ID |
| `--cases` | 與 `--run-id` 二擇一 | 逗號分隔 KQT-T ID（如 `KQT-T37253,KQT-T37258`） |
| `--assignee` | 否（僅 run 模式可用） | **篩人**：Full name / email / username，部分比對。省略＝該 run 全撈 |
| `--out` | 否 | 輸出 JSON 路徑（預設 `/tmp/tcms_cases.json`） |

## 輸出格式（`/tmp/tcms_cases.json`）

```json
[
  {
    "result_id": 12345,
    "case_id": 6789,
    "external_id": "KQT-T37253",
    "title": "檢視展開全部按鈕的邏輯",
    "priority": "Critical",
    "status": "Untested",
    "suite_id": 42,
    "preconditions": "",
    "steps": [
      {"order": 1, "action": "...", "data": "", "expected_result": "..."}
    ]
  }
]
```

- `result_id` / `status` 只有 run 模式（B）才有；貼 ID 模式（A）為 `null`
- `steps` 完全來自 TCMS `/cases/{id}`，不需要 Zephyr

## TCMS API 對照

| 用途 | Endpoint |
| --- | --- |
| 查 Run 內所有 results | `GET /results/run/{run_id}` |
| 查所有使用者（篩人用） | `GET /users/` |
| 查 case 詳情（含 steps） | `GET /cases/{case_id}` |
| 查 case by external_id | `GET /cases/?external_id={external_id}` |

## 下游

輸出的 JSON 常被這些 skill 接手：
- `tcms-implement` — 拿 case 去 kkday-QA-automation repo 實作自動化
- 其他需要 case steps 的 review / report 流程
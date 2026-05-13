# Mode B — 直接給參數的進入點

跳過 Mode A 直接跑：

```
/release-impact-analysis repo=kkday-ios-member base=v3.5.6 target=master   # 預設跑全部 cycle
/release-impact-analysis ios base=v3.5.6 target=master                     # 同上，alias 簡寫
/release-impact-analysis ios base=v3.5.6 target=master mode=wait
/release-impact-analysis result <task_id>
```

## 參數

| 參數 | 必填 | 說明 |
| --- | --- | --- |
| `repo` / 別名 | 是 | 平台別名或 `owner/name`（不要加 `kkday-it/`） |
| `base` | 是 (Mode B) | 基準 ref（branch / tag / SHA） |
| `target` | 是 (Mode B) | 對比 ref |
| `cycle` | 否 | **預設自動跑全部 cycle**，不用給。極少數場景（使用者明確說「只看 impact」）才用 `cycle=none` 跳過 regression。其他寫法（`cycle=regression` / `cycle=KQT-R929` / 逗號分隔）script 仍支援但不主動推薦 |
| `filter` | 否 | Mode A 用，過濾 refs |
| `tags` | 否 | Mode A 用，預設 10；`tags=all` 全列 |
| `commits` | 否 | wait/background 切換的 ahead_by 閾值，預設 30 |
| `files` | 否 | wait/background 切換的 kept_files 閾值，預設 50 |
| `mode` | 否 | `wait` / `background` 強制走某條路 |
| `backend` | 否 | 覆寫後端 URL |

## `result <task_id>` 子命令（手動觸發解讀）

用戶下 `/release-impact-analysis result <task_id>` 或說「task <task_id> 結果如何」「幫我看 task <id>」時觸發。

```bash
cat /tmp/release_impact_<task_id>.json
```

依 `status` 處理：
- `running` → 回 1~2 行進度（`current_step` + 最後一條 progress_log），告訴用戶還在跑
- `completed` → 進「結果解讀」段
- `failed` → 把 `errors` 攤開講，問要不要重跑

找不到檔（用戶 task_id 拼錯或檔被清掉）→ 列 `/tmp/release_impact_*.json` 最近的幾個給他選。

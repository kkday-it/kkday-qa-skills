# 通用 Coding Style

參考 [Confluence Coding Style 規範](https://kkday.atlassian.net/wiki/spaces/QS/pages/473661593/Coding+Style)：

- 縮排使用 4 格空白
- Import 置於檔案開頭，依種類分區塊，每個獨立一行
- Function 之間空一行，Class 之間空兩行
- 備註使用 `# ` 開頭（井號 + 空格），備註前空一行
- 不可使用無意義的命名（abc/aaa/xyz）
- 修改 `pyproject.toml` 時必須同步更新 `poetry.lock`，否則 Docker build 會失敗
- Push 前執行 `pre-commit run --all-files` 確認規範

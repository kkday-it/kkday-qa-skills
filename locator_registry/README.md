# Locator Registry — 易變 locator 的分層快取

這個目錄存 KKday QA 自動化的 **locator 候選快取**。核心設計原則一句話：

> **具體 locator 是易變資料（前端改個 class 就失效），所以它永遠是「候選 hint」，不是真理。
> 真正防腐的不是把 locator 存好，而是「每次用前先驗」這道死程式閥。**

固化知識會腐爛 —— 但只有把「易變資料」當「真理快取」存下來時才會。所以分層：

| 知識類型 | 穩定度 | 存哪 |
|---|---|---|
| 業務語意（首頁 header 搜尋 vs landing 自帶搜尋行為不同、Tab 錨定邏輯） | 高 | 固化在 skill / `skills/tools/qa-automation-writer/references/search-frontend-domain.md` |
| 驗證方法論（mweb 用 device profile 不用 resize、共用元件查容器 class） | 高 | 固化在 skill |
| 具體 selector（`input.things-to-do-search-bar__input`） | **低** | **這個 registry（只當候選，用前必驗）** |

## 檔案

- `registry.json` — 候選資料（見下方格式）。
- 對外唯一取用入口：`scripts/get_verified_locator.py`（**不要**繞過它直接讀 selector 來用）。

## 為什麼有「唯一入口」這個硬約束

「用前先驗」如果只是寫在文件裡叫 agent「記得驗」，agent 會忘 —— 等於沒有閥，腐爛 selector 就
傳染全隊。所以驗證被寫死成 API 的唯一形狀：

- agent 端**沒有**「只拿 selector 直接用」的路。唯一入口是 `get_verified_locator(flow/element)`。
- 它內部一定跑完：**GET 候選 → 當前 DOM 逐一 cheap-verify → 回第一個活的**；全死就標 stale +
  回 `action=remine`（回傳裡根本沒有可用 selector），逼 agent 從零重挖。
- `fetch_locator_registry.py`（GET）、`verify_locator.py`（驗證）是它的**內部依賴**，agent 不單獨當
  「拿了直接用」的工具呼叫。

## registry.json 格式

```jsonc
{
  "schema_version": 2,
  "updated_at": "<ISO8601>",
  "entries": [
    {
      "id": "search-result-active-tab-web-stage",   // 唯一 key
      "flow": "things-to-do-search",                // 流程/區域：相關元素群一起存、一次 GET 一起拿
      "page": "search-result",                      // 頁面語意 key
      "component": "active-tab-text",               // 元件語意 key
      "element": "web 結果頁 active tab 文字",        // 人看的語意名
      "semantic": "…業務語意/陷阱說明…",
      "platform": "web",                            // web | mweb（分開存）
      "env": "stage",                               // stage | prod（分開存）
      "selectors": [                                // ★ 優先序候選陣列，用前逐一驗到第一個通過
        {"type": "xpath", "value": "…首選…",  "note": "語意 class 首選"},
        {"type": "xpath", "value": "…fallback…", "note": "結構 fallback"}
      ],
      "source": {"case": "KQT-T37931", "origin": "page_object", "ref": "…file:line…"},
      "verify_url": "https://www.stage.kkday.com/…", // cheap-verify 用的 URL
      "last_verified": "<ISO8601>",                 // 上次驗證通過時間
      "status": "verified"                          // verified | stale（驗不過就標 stale 強制重挖）
    }
  ]
}
```

三個防腐要件（缺一，「錯了會一直錯」）：

1. **用前先驗**：`selectors` 只是候選，`get_verified_locator` 逐一驗到第一個在 DOM 命中的才回。
2. **來源 + 時間戳 + 失敗回饋**：`source` 記哪個 case/出處；`last_verified` 記時間；驗不過 →
   `status=stale`，下次強制重挖。
3. **版本/環境標記**：`platform`(web/mweb) × `env`(stage/prod) 分開存，不混用。

## 相關元素群一起存、一次 GET 一起拿

同一 `flow` 的元素常一起改、一起用（例：`things-to-do-search` = landing 搜尋框 + 送出鈕/search
icon + 結果頁 header keyword + active tab）。一個 case 起手用 `--flow` 一次批次驗整組，省多次往返：

```bash
# 回寫預設就開（寫到 /tmp/locator_results.d/<pid>-<ts>.jsonl，per-process 並行安全），不必帶 --emit
python3 scripts/get_verified_locator.py \
    --flow things-to-do-search --platform web --env stage \
    --registry locator_registry/registry.json
```

回傳裡 `verified` 的元素帶「活的 selector」可直接用；`stale` 的只帶 `action=remine`（沒有可用
selector），agent 對這些回退到從零挖。挖完/確認後的 jsonl（預設在 `/tmp/locator_results.d/`）由
Stop hook 的 `send_locator_registry.py --indir` 背景逐檔 POST 回後端，更新 `last_verified` /
`status`（見 `docs/telemetry.md`）。要停用回寫才明確傳 `--emit ''`。

## 後端只是共享層，不是真理來源

從 ai_studio GET 回來的 locator 一樣要「用前先驗」，驗不過標 stale 重挖。後端幫的是**跨人共享 +
趨勢**，不是讓大家盲信快取。就算後端存了腐爛 locator，也會在 cheap-verify 那步被擋下。

# 搜尋 / 前端 domain 知識與驗證方法論

這份是**穩定知識**（業務語意 + 驗證方法論），與易變的具體 locator 分層存放：
- 這裡固化「怎麼推理、怎麼驗」——不會因前端改 class 而失效。
- 具體 selector 一律放 `locator_registry/`，且只當候選、**用前先驗**（見下方「唯一入口」）。

首批素材來自 KQT-T37931（門票&體驗搜尋流程），那次花 42 分鐘現場臨時挖 locator / 業務語意 /
mweb 驗證法。把可複用的部分固化，讓下一個搜尋/前端 case 不必重挖。

---

## A. 業務語意（高穩定，固化）

### A1. 首頁 header 搜尋框 vs things-to-do landing 自帶搜尋框：行為不同

- **things-to-do（門票&體驗）landing 頁自帶的搜尋框**：從這裡送出，結果頁會**自動錨定
  「門票&體驗」Tab**。
- **首頁 header 全域搜尋框**：從這裡送出，**不會**自動錨定「門票&體驗」Tab。
- 推論：case 若在驗「自動錨定某 Tab」，要確認它描述的是「哪個入口的搜尋框」。入口不同、預期不同。
  兩個入口的 page object 也是不同元素（landing 用 `things-to-do-search-bar__input`；首頁在
  `home_page.py` 的 banner 搜尋，不在 `home_header_page.py`）。

### A2. 實際 Tab 文字是「門票&體驗」，不是規格寫的「門票/體驗」

- active tab 用 class token `kk-tabs__tab--active` 標記（web / mweb 完全相同）；「門票&體驗」是
  資料層文案，程式用**文字比對**取 active tab，selector 本身不含該字串。
- 斷言 active tab 文字時用實際值「門票&體驗」（`&` 全形），不要照規格字面寫「門票/體驗」。

### A3. 共用元件在不同入口 → 容器 class 不同（容器陷阱）

- mweb 從 things-to-do landing 的「假輸入框」觸發的 search modal，容器是
  **`things-to-do-search-discovery`**；從 header 入口觸發的才是共用假設的 **`search-modal`**。
  兩者共用內部元件（`search-input-field__input` 等），但**外層容器 class 不同**。
- **KQT-T37931 mweb 第一次 fail 的根因**：只假設了 `search-modal` 容器，沒涵蓋
  `things-to-do-search-discovery`。修法是 selector 用 union 同時涵蓋兩種容器。
- 推論成方法論見 B3。

### A4. web / mweb 同語意元素常是不同型別

- 結果頁 header keyword：web 是可編輯 `input`（讀 `.value`）；mweb 是靜態文字（讀 `.text`）。
- mweb landing「搜尋框」其實是假輸入框觸發器（點了開 modal），不是真 input。
- 推論：不要把 web 的 locator / 讀值方式照搬到 mweb。

---

## B. 驗證方法論（高穩定，固化）

### B1. case 語意 → 頁面/元件的推理法

1. 先判「搜尋入口是哪個」：首頁 header？things-to-do landing 自帶？結果頁 header？入口決定
   預期行為（A1）與 page object（不同檔案/元素）。
2. 再判平台：web / mweb 同語意常是不同型別、不同容器（A3、A4），逐平台各驗。
3. 「錨定某 Tab / 顯示某關鍵字」這類驗證，對到結果頁的 `kk-tabs__tab--active` 文字或 header
   keyword 元素（A2、A4）。

### B2. mweb 必須用 device profile，不能用 viewport resize 冒充

- kkday 靠 **User-Agent**（＋`isMobile`/`hasTouch`）決定回 web 還是 mweb DOM，**不是看 viewport**。
  只設 `--viewport` 仍是桌面 UA → server 回的是 **web 頁**，會驗到錯的頁。
- 驗 mweb 真實 DOM 用 **Python playwright** 搭**框架同一份 `devices["iPhone 15"]`**
  （`QATest/src/lib/fixtures/playwright.py` 裡 mweb 用的同一台）：`verify_locator.py --device "iPhone 15"`
  會在起 context 時自動套 `pw.devices["iPhone 15"]`（手機 UA + `isMobile`/`hasTouch`）。**不用 playwright MCP。**
  - 進頁前先 `navigator.userAgent` 確認是 iPhone/Mobile UA，是桌面 Chrome UA 就停、別在錯的頁上驗。
- 注意：`iPhone 15` 的確切 UA / isMobile / hasTouch 是 Playwright 內建 device registry 動態載入，
  框架原始碼不硬編碼這些字面值（fallback 路徑才有硬編 UA + viewport，且無 isMobile/hasTouch）。

### B3. 共用元件在不同入口 → 先查容器 class

- 遇到「共用元件（modal / discovery / dialog）」的 locator，先確認**觸發入口**，不同入口外層
  容器 class 常不同（A3）。selector 用 union 同時涵蓋已知的多個容器，或先 snapshot 容器再定位。

---

## C. 唯一入口：先驗才回（把「先驗」變成 API 的唯一形狀）

具體 selector 存在 `locator_registry/registry.json`，但**不要直接讀來用**。唯一正規用法是
`scripts/get_verified_locator.py`：內部自動 `GET 候選 → 當前 DOM 逐一 cheap-verify → 回第一個活的`；
全死就回 `action=remine`（回傳裡沒有可用 selector），逼你從零重挖。細節見
`locator_registry/README.md`。這道閥是死程式，不靠「記得驗」。

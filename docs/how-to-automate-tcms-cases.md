# 如何把 TCMS case 自動化

一句話：**跟 Claude 說要自動化哪些 case，它就會做完給你，最後問你要不要開 PR。**

你不用記指令、不用自己開瀏覽器、不用管它怎麼跑。用講的就好。

> **這是一條 AI Agent 流程**：你對話的**主對話 Claude 是 AI 總指揮**，它會派兩種 🤖 AI Agent 去做事——`qa-case-automator`（實作）與 `qa-case-fidelity-reviewer`（檢查實作有沒有忠實對到 case）。全程有 AI 在跑，你負責出需求 + 最後決定要不要開 PR。

---

## 怎麼用（直接把這些話貼給 Claude）

**單一 case**
```
KQT-T1234 實作
```

**一批 case**
```
把 KQT-T1234、KQT-T5678 做成自動化
```

**整個 Run**
```
Run 95 的 case 全部自動化
```
```
Run 95 裡 Eden 的 case 自動化
```

**做完想追問**
```
為什麼 KQT-T5678 是 flag-for-human？
```

你全程只需要在**最後回答一件事：要不要開 PR**（過程中若有平台/缺資訊要確認，Claude 也會問你，見下）。

---

## 流程圖（「KQT-T1234 實作」實際會跑的）

```mermaid
flowchart TD
    A["👤 KQT-T1234 實作"] --> B["🤖 主對話（總指揮）<br/>撈 case → 判平台"]
    B --> P["平台共用一份<br/>web↔mweb、android↔ios"]
    P --> E

    subgraph LOOP["每個 case × 共用組的閉環（達標才收）"]
        direction TB
        E["🤖 automator<br/>實作 → 驗 locator → 跑過"]
        F["🤖 fidelity reviewer<br/>覆蓋率／信心"]
        GATE["per-platform 交付 gate<br/>每平台真的有註冊+跑過？"]
        E --> F
        F -->|"needs-fix"| E
        F -->|"pass"| GATE
        GATE -->|"缺平台，補實作"| E
    end

    GATE -->|"齊 ✅"| G["收下"]
    F -->|"修不過／低信心"| H["待人工 ⚠️"]
    G --> R["批次報告<br/>rollup ＋ 逐 case×平台"]
    H --> R
    R --> S{"開 PR？"}
    S -->|"好"| T["branch → commit → 一個 PR"]
    S -->|"先不要"| L["留工作區"]
```

重點：**「跑得起來」不等於「過」**。每個 case 實作完會經 `qa-case-fidelity-reviewer` 檢查有沒有忠實覆蓋 case（每個 expected 有沒有真的被斷言）；不夠 → **自動丟回去修再檢查**（最多幾輪），還不行才標「待人工」。

> **過程中遇缺資訊/待確認**（平台選擇、`web/API` 混用、缺 oid…）：**互動模式** → 問你；**自主/harness 模式** → 套安全預設續跑或標 `blocked`（不停等）。這條旁支不畫進主流程，免得線交錯。

### 元件各是什麼

> **型別**：🤖 **Agent** 會獨立跑一連串工作（subagent）；📄 **Skill** 是「怎麼做」的規範，被 Agent/主對話載來用。這流程有**兩個 Agent**：`qa-case-automator`、`qa-case-fidelity-reviewer`。

| 元件 | 型別 | 說明 |
| --- | --- | --- |
| **主對話 Claude** | 總指揮 | 你直接對話的那個。撈 case、判平台、跑「實作→檢查→重修」閉環、彙整報告、問你開不開 PR。**只有它能開 PR、也只有它會問你。** |
| **`qa-case-automator`** | 🤖 Agent | tag 標的平台**共用一份**（web↔mweb 共用、android↔ios 共用，只有些許步驟差異）實作(create)/修(fix)+跑過+產可追溯表。並行模式驗元素用各自 Python playwright。不撈整批、不開 PR、不叫別的 agent。 |
| **`qa-case-fidelity-reviewer`** | 🤖 Agent | 對抗式檢查：比對 case 規格 vs 實作，出覆蓋率/信心/建議。唯讀，只評不改。 |
| **per-platform 交付 gate** | 📄 確定性腳本 | `scripts/check_platform_delivery.py`——非 LLM，驗每個 tag 平台**真的交付**（mweb 有 `limit_test_platform:mweb` entry、App 有 AppRegression 註冊 + 真跑過）。「web case 硬套 `--platform mweb` 跑綠」矇混不過。 |
| `tcms-fetch-cases` | 📄 Skill | 撈 case steps + `labels`/`tags`（平台資訊）。 |
| `qa-automation-writer` | 📄 Skill | 寫 code + 驗 locator + 產可追溯表的規範。 |
| `qa-test-runner` | 📄 Skill | 跑測試 + 失敗診斷/修復。 |
| **Playwright MCP** | 工具 | 驗 locator 的真實瀏覽器；**單一共用，不能多案同開**——僅**單獨/互動**模式用。**批次並行**改用各自 launch 的 Python playwright（各開 headless browser，可平行、不搶）。驗 mweb 都要手機 device profile。 |
| **kkday-QA-automation** | 本機 repo | 測試碼落地處（page object / test step / case yaml）。 |

---

## 「過」是什麼意思

一個 case 算「過」= **tag 標的每個平台都交付（per-platform gate 通過）+ 跑得起來 + 覆蓋 case 規格（每個 expected 都有真斷言）+ 忠實度 reviewer 認可**。只有測試變綠、但沒真的驗到 case 要驗的東西，**不算過**；只做 web、mweb/App 沒交付，**也不算過**（gate 會擋）——這是為了在沒有人工 reviewer 時，也能相信產出跟你寫的 case 一致、且平台沒漏。

---

## 一次做多平台

一個 TCMS ID **涵蓋它 `labels`/`tags` 標的所有平台**（如 `FE (Web/mWeb/Android/iOS)` → 四平台都要）。關鍵：**平台間共用同一份 case + test_step，不是各寫一份**——只有些許步驟不同（用平台標記/`limit_test_platform` 區分）：

- **web ↔ mweb 共用一份**（`web_playwright/`）：做 web 就**一併**補 mweb 的 `limit_test_platform:mweb` entry + 些許 `[M]` 差異步驟。不是只做 web、也不是另開一份。
- **android ↔ ios 共用一份**（`mobile/`）：靠 `[iOS]`/`[Android]` 標記分差異。

**tag 標的平台缺任一涵蓋 = 沒做完**（per-platform gate 會擋）。某平台做不了（如 App 缺實體機）→ 標 blocked，共用的其餘平台照做。自主/harness 模式套預設續跑不停等；互動模式若真的不確定會問你。

## 想加速一批

一批（10+ case 很常見）用 **workflow 並行**跑，不必一個個等閉環：

- **入口**：一串 TCMS ID（`KQT-T37931 KQT-T37932 …`）→ `workflows/batch_tcms_automate.js`。
- **每個 case 獨立**流過「automator → gate + 忠實度 review → 回修」，彼此不等，慢的不拖快的（`pipeline`）。
- **能真平行的關鍵**：批次模式驗元素用**各自 Python playwright**（各開 browser），不搶那個單一共用的 MCP 瀏覽器；各 case 在自己的 **git worktree** 寫檔，不互相覆蓋。
- 並行度自動壓在資源上限（≈ CPU 核數）；wall-clock 從「10× 序列」降到「≈ 最慢單一 case」。App 平台仍受實體機數限制。

> 舊限制「驗 locator 不能平行、要集中主對話做」已被**並行模式的 Python playwright** 突破——那個限制只在「用 MCP 共用瀏覽器」時成立。

## 你可能會遇到的狀況

- **中途問你**：多平台要做哪些、`web/API` 混用要做哪個、缺商品 oid → 互動模式會問你；自主/harness 模式則套預設續跑、卡住的排入待人工佇列（不會停著等）。
- **忠實度不夠會自己重修**：reviewer 說覆蓋不足，Claude 會把漏的餵回去重寫再檢查，不是評完就算。
- **標「待人工」**：修幾輪還不過、或信心低，會標記出來給你，不硬過。
- **產品真的有 bug**：修現有 case 時若發現是產品壞（不是測試壞），**不會為了變綠改斷言**，會當產品 bug 回報。
- **最後問你要不要開 PR**：一定要你點頭才動 git。

## 常見坑

- **MCP Playwright 不能多案同開**：單獨/互動跑用它；**批次並行**改用各自 Python playwright（各開 browser）才不搶。
- **mweb 要用手機 device profile**：kkday 看 User-Agent 判 web/mweb，只縮 viewport 會開到 web 頁。
- **只信「綠」不夠**：所以才有 fidelity reviewer + per-platform gate；沒把關的綠、或漏做平台，都不算過。
- **平台不是各寫一份**：web↔mweb、android↔ios 各共用一份 case+test_step，做一個要一併補共用平台，別漏。
- **現階段禁打 prod**：驗 locator / 跑測試只用 stage / sit 系列（sit0x / sit20x），不碰 `www.kkday.com`。

## 想知道更多

- 主對話完整劇本（閉環/報告/模式）：`prompts/automate-tcms-cases.md`
- 各 Agent 權威定義：`agents/qa-case-automator.md`、`agents/qa-case-fidelity-reviewer.md`

---
name: python-pr-quality-checklist
description: |
  Python PR / commit 前的程式品質自檢清單，涵蓋 docstring、None 安全、字串布林轉型、輸入驗證、coding style 等常見會被 reviewer REQUEST_CHANGES 的條件。

  適用情境：
  - 寫完 Python code 準備 commit / PR 前的最後一道自檢
  - reviewer 在 review 別人 PR 時對著清單看
  - QA / Tech Lead 訂定團隊 Python 品質基線
  - agent 自動產 code 後的自我驗證迴圈

  防止：常見的 None pointer 例外、字串 `"false"` 被當成 truthy、無 docstring 被 lint 擋、SQL injection / path traversal 等可預防的線上事故。
argument-hint: "[要檢查的檔案 / PR URL，可省略]"
user-invokable: true
---

# Python PR Quality Checklist

Python PR 前自檢清單。每條都是「會被 reviewer REQUEST_CHANGES 或上線後出事」的條件。

## 結構：6 + 1

- **prod code 6 條**（給寫 Python 後端 / 服務的 dev）：1 docstring + type hint、2 None 安全、3 字串布林、4 input + DB 安全、5 不吞 exception、6 coding style
- **test code 1 條**（給寫 Python 測試的 QA / SDET）：T 動態回應的 schema 驗證

dev 看 1-6；QA 寫測試看 1-3、5-6、T。

## 為什麼這幾條

很多團隊的 PR review 抓到的問題其實不是業務邏輯，而是這些「可機械化檢查的低階品質問題」：

- **lint 擋下的**（docstring missing、style 不一致）→ 浪費一輪 review
- **runtime 才爆的**（None pointer、`"false"` 被當成 True）→ 線上事故
- **安全相關的**（沒做 input validation 直接拼字串給 DB）→ injection / 越權

把這些寫成 checklist，讓 author **commit 前自己跑一遍**，比 reviewer 抓出來再退回快很多。

## Prod code 6 條（dev 必看）

### 1. Docstring + Type Hint 必要

**規則**：所有 public function / class / module 必須有 **docstring 加 type hint**（參數型別 + 回傳型別）。

**為什麼**：
- **docstring**：別人（包括半年後的自己）讀 code 不用反推意圖；ruff `D` 系列 / pydocstyle 強制
- **type hint**：mypy / pyright 才能靜態檢查 None 安全（第 2 條）、型別錯誤；IDE 才能跳轉與補全；現代 Python lib 期待型別流通

兩者一起寫成本不到 30 秒，省下的 review noise 與 runtime debug 是數量級的差距。

**好例**：
```python
def calculate_refund(order_id: str, reason: str) -> dict:
    """Calculate refundable amount for an order.

    Returns a dict with refundable_amount, currency, and breakdown.
    Raises OrderNotFoundError if order_id is invalid.
    """
    ...
```

**壞例 A — 沒 docstring 也沒 type hint**：
```python
def calculate_refund(order_id, reason):
    ...
```

**壞例 B — 有 type hint 但沒 docstring**：
```python
def calculate_refund(order_id: str, reason: str) -> dict:
    ...  # 型別有了，但「會 raise 什麼 / 回傳結構是什麼」沒交代
```

**例外**：
- private helper（`_underscore_prefix`）只需 type hint，docstring 可選
- trivial property（`@property` 一行 return self._foo）兩者都可省

**lint 工具**：
- docstring：`ruff check --select D` / `pydocstyle` / `flake8-docstrings`
- type hint：`ruff check --select ANN` / `mypy` / `pyright`

### 2. None 安全（dict.get / list 操作後不要直接呼叫方法）

**規則**：`dict.get()` / list slice / Optional 回傳值之後**不要直接 chain 方法**，先確認非 None。

**為什麼**：
- `dict.get("key")` 預設回 `None`，後續 `.strip()` / `.lower()` 會 `AttributeError`
- 線上事故清單上這類占很大比例

**好例**：
```python
val = data.get("user_name")
if val is not None:
    val = val.strip().lower()

# 或用 default
val = (data.get("user_name") or "").strip().lower()
```

**壞例**：
```python
val = data.get("user_name").strip()  # 💥 None.strip() AttributeError
```

**lint 工具**：mypy `strict_optional` / pyright 都會抓

### 3. 字串布林（從 request / config 讀來的字串 flag 不可直接當 bool）

**規則**：從 JSON / YAML / env / request 讀進來的字串 flag 不能直接 `if value:` 判斷。

**為什麼**：
- Python 中 `bool("false") == True`（非空字串都 truthy）
- `is_active = "false"` 會被誤認為 enable

**好例**：
```python
is_active = str(data.get("is_active", "")).lower() == "true"
```

**壞例**：
```python
if data.get("is_active"):  # "false" 字串也是 truthy，永遠進這個分支
    enable_feature()
```

**lint 工具**：沒有 lint 能 100% 抓到這個（要 type 系統 + 領域知識），所以要靠 checklist + code review

### 4. 路由 / API 入口的安全處理（Parameterized Query + Input Validation）

**規則**：來自外部的 path / query / body 參數，進入 DB / 檔案系統 / shell 時必須遵守兩層防禦：

1. **主防（必要）**：DB 一律走 **parameterized query / ORM**，禁止用 f-string / `%` / `+` 把參數拼進 SQL；shell 一律走 `subprocess.run([...])` 的 list 形式，禁止 `shell=True` 拼字串；檔案路徑用 `Path` + `resolve()` 後比對允許前綴。
2. **次防（defense in depth）**：對能事先描述的格式（如 oid / uuid / 數字 id），補一層 regex / pydantic 型別驗證，把畸形請求擋在 handler 入口。

**為什麼**：
- 真正擋下 SQL injection 的是 parameterized query，**不是 regex** — regex 過了，只要還用 f-string 拼 SQL 一樣會炸
- regex 是降低 attack surface 的補強，不是替代 — 兩層都要

**好例**：
```python
import re
ORDER_ID_PATTERN = re.compile(r"^[A-Z0-9]{8,16}$")

def get_order(order_id: str):
    # 次防：格式不對直接 400
    if not ORDER_ID_PATTERN.match(order_id):
        raise HTTPException(400, "invalid order_id format")
    # 主防：ORM 的 filter_by 自動 parameterize
    return db.query(Order).filter_by(id=order_id).first()
```

**壞例 A — 有 regex 但仍拼字串（❌ regex 沒救到你）**：
```python
def get_order(order_id: str):
    if not ORDER_ID_PATTERN.match(order_id):
        raise HTTPException(400, "invalid order_id format")
    # 💥 即使 regex 過了，這裡仍是 f-string 拼 SQL，injection 風險仍在
    return db.execute(f"SELECT * FROM orders WHERE id = '{order_id}'")
```

**壞例 B — 直接拼（❌ 教科書級別反例）**：
```python
def get_order(order_id: str):
    return db.execute(f"SELECT * FROM orders WHERE id = '{order_id}'")
    # 💥 order_id = "x' OR '1'='1" → 整張表被撈
```

**lint 工具**：bandit（`B608` SQL string-based query）/ semgrep（python.lang.security.audit）部分能抓，但靠工具不夠 — 必須 code review 時對著規則看

### 5. 不吞 Exception

**規則**：不要 `try: ... except Exception: pass` 默默吞掉所有錯誤。

**為什麼**：
- bug 被掩蓋，線上找不到 root cause
- 監控收不到訊號，問題擴散

**好例**：
```python
try:
    result = external_api_call()
except (RequestException, TimeoutError) as e:
    logger.warning("external_api failed: %s", e)
    raise TransientError(f"external_api unavailable: {e}") from e
```

**壞例**：
```python
try:
    result = external_api_call()
except Exception:
    pass  # 💀 永遠不知道什麼壞了
```

**lint 工具**：`ruff` 的 `BLE`（blind except）/ `bandit B110`

### 6. Coding Style（lint + format 自動化）

**規則**：commit 前跑過 formatter + linter，無 warning。

**為什麼**：
- 風格不一致 = review noise，掩蓋真正問題
- 自動化跑 1 秒，人類 review 跑 30 秒

**推薦工具組合**：
- formatter：`ruff format`（取代 `black`）/ `black`
- linter：`ruff check`（取代 `flake8 + isort + pyupgrade ...`）/ `flake8`
- import sort：`ruff check --select I` / `isort` / `usort`
- type check：`mypy` / `pyright`

**pre-commit hook 範本**：
```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.x.x
    hooks:
      - id: ruff
      - id: ruff-format
```

## Test code 1 條（QA / SDET 寫測試時必看）

### T. 動態回應的 Schema 驗證

**規則**：測試斷言 API response 時，**含動態資料**（oid / timestamp / 動態金額）的部分要用 schema 驗證，不能整段字串比對。

**為什麼**：
- timestamp 每次都不同，文字比對會 flaky
- oid 是 server 生成，沒辦法預期值
- 測試 flaky 比測試漏 case 更傷團隊信任 — fail 多次後沒人會認真看

**好例**：
```python
# 用 schema / pydantic 驗型別與必要欄位
check_api_response_schema(response, expected_schema)

# 或對動態欄位個別驗證（不比對值）
data = response.json()
assert isinstance(data["oid"], str) and len(data["oid"]) > 0
assert "created_at" in data  # 存在即可，不比對具體時間
assert data["status"] == "PAID"  # 確定值的欄位才比對值
```

**壞例**：
```python
assert response.text == '{"oid": "abc-123", "created_at": "2026-05-04T10:00:00"}'
# 💥 下次跑 oid 換了，timestamp 變了，永遠 fail
```

**lint 工具**：沒有 lint 能抓，靠 code review 對著清單看

## 自檢流程

commit 前對著這 6 + 1 條跑一遍。

**寫 prod code（dev）**：
```
☐ 1. 所有新增 / 修改的 function / class 都有 docstring + type hint
☐ 2. dict.get / list / Optional 後沒有直接 chain 方法
☐ 3. 從外部讀進來的字串 flag 都明確轉型成 bool
☐ 4. 進 DB 走 parameterized query；額外補 input format validation
☐ 5. except 都有具體 exception type，沒有 blind except + pass
☐ 6. ruff check / ruff format / mypy 全綠
```

**寫測試 code（QA / SDET）**：
```
☐ 1-3 + 5-6 同上（除了第 4 條，測試 code 通常不涉及外部入口）
☐ T. 動態 response 用 schema 驗證，不做整段字串比對
```

任一條 ❌ → 修完再 commit。

## reviewer 的對應使用方式

如果你是 reviewer，看 PR 時對著對應清單看：

- prod code PR → 對 1-6
- 測試 code PR → 對 1-3、5-6、T
- ✅ 全通過 → 進入業務邏輯 review
- ❌ 任一條沒過 → REQUEST_CHANGES 並指明違反第幾條，讓 author 自己對照修

這樣 review noise 大幅降低。

## 反模式

- ❌ 把這份 checklist 寫成「nice to have」清單 — 這 6+1 條是 hard rule，不是建議
- ❌ 用「我趕著上線下次再修」當理由跳過 — 線上事故 80% 來自「下次再修」
- ❌ 用 `# noqa` / `# type: ignore` 大量繞過 lint — 每個 ignore 都該有註解寫為什麼
- ❌ 把這份 checklist 當成完整 review 標準 — 這只是「機械化可檢查項」的最低門檻，業務邏輯仍需人類 review

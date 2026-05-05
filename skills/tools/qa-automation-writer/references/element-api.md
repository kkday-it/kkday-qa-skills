# Element API 參考

## Playwright Element（pages/playwright_element.py）

### 等待方法（chainable，回傳 self）

| 方法 | 說明 |
|------|------|
| `wait(condition="visible", timeout=None, no_exception=False)` | 通用等待 |
| `wait_for_visible(timeout=None, no_exception=False)` | 等待可見 |
| `wait_for_hidden(timeout=None)` | 等待隱藏 |
| `wait_for_enabled(timeout=None)` | 等待啟用 |
| `wait_for_exists(timeout=None) -> bool` | 等待存在於 DOM |

### 操作方法

| 方法 | 說明 |
|------|------|
| `click(force=False)` | 點擊 |
| `double_click()` | 雙擊 |
| `right_click()` | 右鍵 |
| `hover()` | 懸停 |
| `input(value)` | 輸入（會先清空） |
| `type(value, delay=0)` | 模擬鍵盤輸入（不清空） |
| `clear()` | 清空 |
| `press(key)` | 按鍵（如 "Enter", "Tab"） |
| `check()` | 勾選 checkbox |
| `uncheck()` | 取消勾選 |
| `select_option(value=None, index=None, label=None)` | 選擇下拉選項 |
| `upload_file(file_path)` | 上傳檔案 |
| `scroll_into_view()` | 滾動到可見 |
| `focus()` | 聚焦 |
| `blur()` | 失焦 |
| `js_click()` | JavaScript 強制點擊 |
| `evaluate(script, arg=None)` | 在元素上執行 JS |

### 屬性（property）

| 屬性 | 回傳 | 說明 |
|------|------|------|
| `is_visible` | bool | 是否可見 |
| `is_not_visible` | bool | 是否不可見 |
| `is_enabled` | bool | 是否啟用 |
| `is_disabled` | bool | 是否禁用 |
| `is_present` | bool | 是否存在於 DOM |
| `is_checked` | bool | 是否勾選 |
| `text` | str | 文字內容 |
| `inner_text` | str | inner text |
| `value` | str | 表單元素的值 |
| `rect` | Rect | 位置和大小 |
| `center` | Coordinate | 中心座標 |

### 斷言方法

| 方法 | 說明 |
|------|------|
| `expect_visible(timeout=None)` | 斷言可見 |
| `expect_hidden(timeout=None)` | 斷言隱藏 |
| `expect_text(text, timeout=None)` | 斷言包含文字 |
| `expect_value(value, timeout=None)` | 斷言表單值 |

### Elements 方法（複數）

| 方法 | 回傳 | 說明 |
|------|------|------|
| `first()` | Element | 第一個 |
| `last()` | Element | 最後一個 |
| `nth(index)` | Element | 第 N 個 |
| `count` | int | 數量 |
| `all_texts()` | List[str] | 所有文字 |
| `click_by_text(text)` | bool | 點擊含指定文字的元素 |
| `find_by_text(text)` | Element | 找含指定文字的元素 |
| `filter(**kwargs)` | Elements | 過濾 |

---

## Selenium Element（pages/element.py）

### 等待方法

| 方法 | 說明 |
|------|------|
| `wait(condition=None, timeout=60, no_exception=False)` | 等待（chainable） |
| `scroll_to(container_element, direction, tries, timeout)` | 滾動到可見（Mobile） |

### 操作方法

| 方法 | 說明 |
|------|------|
| `click()` | 點擊 |
| `input(value)` | 輸入 |
| `clear()` | 清空 |
| `move_to()` | 懸停 |
| `get_attribute(name)` | 取得屬性 |

### 屬性

| 屬性 | 回傳 | 說明 |
|------|------|------|
| `is_visible` | bool | 是否可見 |
| `is_enabled` | bool | 是否啟用 |
| `is_present` | bool | 是否存在 |
| `is_select` | bool | 是否選取 |
| `text` | str | 文字 |
| `value` | str | 值 |

---

## Locator 策略

### Playwright 支援的 locator 格式

```python
# role（推薦）
Element(("role", "button", {"name": "送出"}), self.driver)
Element(("role", "textbox", {"name": "Email"}), self.driver)
Element(("role", "combobox", {"name": "商品分類"}), self.driver)
Element(("role", "radio", {"name": "否"}), self.driver)
Element(("role", "checkbox", {"name": "同意條款"}), self.driver)
Element(("role", "button", {"name": "儲存", "exact": True}), self.driver)

# css
Element(("css", ".btn-primary"), self.driver)
Element(("css", "#login-form input[type='email']"), self.driver)

# xpath
Element(("xpath", "//button[@data-testid='submit']"), self.driver)

# text
Element(("text", "立即購買"), self.driver)

# label
Element(("label", "Email"), self.driver)

# placeholder
Element(("placeholder", "請輸入關鍵字"), self.driver)

# test_id
Element(("test_id", "submit-btn"), self.driver)
```

### Selenium/Appium 支援的 locator 格式

```python
# xpath（最常用）
Element(("xpath", "//android.widget.Button[@resource-id='login']"), self)
Element(("xpath", "//XCUIElementTypeButton[@name='login']"), self)

# id
Element(("id", "login_btn"), self)

# accessibility id（iOS）
Element(("accessibility id", "loginButton"), self)
```

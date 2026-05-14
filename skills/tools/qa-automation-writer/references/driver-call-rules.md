# 禁止直接呼叫底層 driver

除了 `playwright_element.py` / `playwright_elements.py` 以外，**所有檔案**禁止任何 `driver.page.*`、`self.driver.page.*`、`uidriver.page.*` 的直接呼叫。

## 禁止（會被標記為違規）

- `driver.page.locator()`
- `driver.page.evaluate()`
- `driver.page.wait_for_timeout()`
- `driver.page.get_by_role()`
- `uidriver.page.locator()`
- `self.driver.page.evaluate()`

## 允許的例外

- `uidriver.page.keyboard.*`（鍵盤操作）
- `uidriver.page.url`（讀取當前 URL）
- `uidriver.page_is_ready()`（頁面就緒檢查）
- `uidriver.execute_js(script)`（執行 JavaScript）

若框架缺少所需方法，應先在 `playwright_element.py` / `playwright_elements.py` 中擴充，不要直接呼叫底層。

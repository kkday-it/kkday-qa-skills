# 專案路徑對應

QA Automation repo: `/Users/eden.lai/Downloads/qa_test/test/kkday-QA-automation`

## 目錄結構

| 用途 | 路徑 |
|------|------|
| **YAML 測試案例** | `QATestData/cases/yaml/ui/` (UI) / `QATestData/cases/yaml/api/` (API) |
| **JSON 測試資料** | `QATestData/data/case_data/TW/` |
| **i18n 多語言** | `QATestData/data/i18n/ios/` / `QATestData/data/i18n/android/` |

### Page Objects

| 平台 | 路徑 |
|------|------|
| Web (Playwright) | `QATest/src/pages/web_playwright/kkday/web/` |
| MWeb (Playwright) | `QATest/src/pages/web_playwright/kkday/mweb/` |
| BE2 Web | `QATest/src/pages/web_playwright/be2/web/` |
| SCM Web | `QATest/src/pages/web_playwright/scm/web/` |
| Mobile Base | `QATest/src/pages/mobile/base/` |
| Android | `QATest/src/pages/mobile/android/` |
| iOS | `QATest/src/pages/mobile/ios/` |
| API | `QATest/src/pages/api/` |

### Test Steps

| 平台 | 路徑 |
|------|------|
| Web (Playwright) | `QATest/src/test_steps/web_playwright/kkday/` |
| BE2 | `QATest/src/test_steps/be2/` |
| SCM | `QATest/src/test_steps/web_playwright/scm/` |
| App | `QATest/src/test_steps/kkday/app/` |
| API | `QATest/src/test_steps/api/` |

### 框架核心（不可修改）

| 檔案 | 說明 |
|------|------|
| `QATest/src/pages/playwright_element.py` | Playwright Element 封裝（可擴充） |
| `QATest/src/pages/playwright_elements.py` | Playwright Elements 封裝（可擴充） |
| `QATest/src/pages/element.py` | Selenium/Appium Element 封裝 |
| `QATest/src/lib/api/core.py` | API 測試核心（ApiCore） |
| `QATest/src/pages/api/APICommonMethod.py` | API 共用方法 |
| `QATest/src/lib/decorators.py` | function_recorder 裝飾器 |

### Pages __init__.py

`QATest/src/pages/web_playwright/__init__.py` — 註冊所有 page class。結構：

```python
class Pages:
    def __init__(self, platform, driver):
        if platform == Platform.MWEB:
            from pages.web_playwright.kkday.mweb.xxx import XxxPage
            # ... mweb imports
        else:
            from pages.web_playwright.kkday.web.xxx import XxxPage
            # ... web imports

        # 共用區（兩邊 import 都要有）
        self.xxx_page = XxxPage(driver)

        # 僅 Web（用 if 保護）
        if platform != Platform.MWEB:
            self.rezio_page = RezioPage(driver)
```

新增 page 時，必須同時在 MWEB 和 Web 的 import 區塊都加，或用 if 保護。

## 執行測試指令

```bash
cd QATest/src
python -m qatest run --caseid KQT-T12345 --platform web --env stage --use_driver playwright
```

| 參數 | 說明 |
|------|------|
| `--caseid` | Case ID（空格分隔多個） |
| `--platform` | web / mweb / android / ios / api |
| `--env` | sit / stage |
| `--use_driver` | playwright（Web/MWeb 必加） |
| `--feature` | 按 feature 執行 |
| `--service` | 按 service 執行 |

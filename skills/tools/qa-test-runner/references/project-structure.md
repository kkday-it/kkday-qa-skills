# QA Automation 專案結構

## 測試執行指令

```bash
# <REPO> = 你本機 kkday-QA-automation clone 的絕對路徑
source <REPO>/venv/bin/activate && cd <REPO>/QATest/src \
  && python -m qatest run --caseid <CASE_ID> --platform <PLATFORM>
```

- PLATFORM: `android`, `ios`, `web`
- CASE_ID: `KQT-T<number>` 格式

## 關鍵目錄對應

| 用途 | 路徑 |
|------|------|
| YAML 測試案例 | `QATestData/cases/yaml/ui/` |
| 測試步驟 (app) | `QATest/src/test_steps/kkday/app/` |
| 測試步驟 (web) | `QATest/src/test_steps/web/` |
| 測試步驟 (playwright) | `QATest/src/test_steps/web_playwright/` |
| Page Objects (Android) | `QATest/src/pages/mobile/android/` |
| Page Objects (iOS) | `QATest/src/pages/mobile/ios/` |
| Page Objects (base mobile) | `QATest/src/pages/mobile/base/` |
| Page Objects (web) | `QATest/src/pages/web/kkday/` |
| Page Objects (playwright) | `QATest/src/pages/web_playwright/` |
| 執行 log | 由 `Log.log_filepath` 決定 |

## Element 定義模式

Page Object 中的元素以 `@property` + `Element` 定義：

```python
@property
def some_element(self) -> Element:
    return Element(
        ("xpath", "//android.widget.TextView[@text='something']"),
        self,
    )
```

### XPath 撰寫原則

1. **優先使用** `resource-id`、`name`、`accessibility id` 等穩定屬性
2. **避免使用中文文字**，改用 i18n key 搭配 `t('key', locale=AppConfig.language)` 翻譯
3. **禁止使用** `@clickable`、`@index`、`@password` 等不穩定屬性做定位
4. **盡量用 class 和相對位置**（如 `following-sibling`、`parent`、`contains(@resource-id, ...)`）
5. **修改元件時保留原本的 locator 做 fallback**，用 `|` 串接新舊 XPath，確保新舊版 App 都能跑
6. **Android Compose 元素**沒有 resource-id 時，可用 parent container 的 resource-id 搭配相對結構定位
7. **新增或修改元件後，必須用 Appium/Playwright 實際抓取驗證**，確認 locator 能正確定位到目標元素、不會匹配到多個非預期元素

## YAML 測試案例結構

```yaml
KQT-T12345:
    platform: android
    priority: RAT
    feature: SomeFeature
    description: ...
    pre-condition:
        - step_name_1
    steps:
        - step_name_2
        - step_name_3
    post-condition:
        - step_name_4
```

步驟名稱對應 `test_steps/` 下的 Python 函式。

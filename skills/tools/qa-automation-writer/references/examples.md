# 程式碼範例

## 1. Playwright Page Object（Web）

```python
from pages.playwright_element import Element
from pages.web_playwright.kkday.base import Page


class ProductPage(Page):
    """商品頁面"""

    @property
    def product_title_label(self) -> Element:
        """商品標題"""
        return Element(("css", "h1.product-title"), self.driver)

    @property
    def buy_now_button(self) -> Element:
        """立即購買按鈕"""
        return Element(("role", "button", {"name": "立即購買"}), self.driver)

    @property
    def date_picker_input(self) -> Element:
        """日期選擇器"""
        return Element(("role", "textbox", {"name": "選擇日期"}), self.driver)

    @property
    def quantity_dropdown(self) -> Element:
        """數量下拉選單"""
        return Element(("css", "select.quantity-select"), self.driver)

    def package_option_button(self, package_name: str) -> Element:
        """動態方案選項（參數化）"""
        return Element(
            ("xpath", f"//div[contains(@class,'package')]//span[text()='{package_name}']"),
            self.driver,
        )
```

## 2. Appium Page Object（Mobile base + Android + iOS）

### base（abstract）

```python
from abc import abstractmethod
from pages.element import Element
from pages.Mobile.base import Page


class CartPage(Page):

    @property
    @abstractmethod
    def checkout_button(self) -> Element:
        raise NotImplementedError

    @property
    @abstractmethod
    def total_price_label(self) -> Element:
        raise NotImplementedError
```

### Android

```python
from pages.element import Element
from pages.mobile.base.cart_page import CartPage as BaseCartPage


class CartPage(BaseCartPage):

    @property
    def checkout_button(self) -> Element:
        return Element(
            ("xpath", "//android.widget.Button[@resource-id='checkout_btn']"),
            self,
        )

    @property
    def total_price_label(self) -> Element:
        return Element(
            ("xpath", "//android.widget.TextView[@resource-id='total_price']"),
            self,
        )
```

### iOS

```python
from pages.element import Element
from pages.mobile.base.cart_page import CartPage as BaseCartPage


class CartPage(BaseCartPage):

    @property
    def checkout_button(self) -> Element:
        return Element(
            ("xpath", "//XCUIElementTypeButton[@name='checkout_btn']"),
            self,
        )

    @property
    def total_price_label(self) -> Element:
        return Element(
            ("xpath", "//XCUIElementTypeStaticText[@name='total_price']"),
            self,
        )
```

## 3. UI Test Step（Playwright）

```python
from lib.decorators import function_recorder
from lib.models import TestRunConfig
from pages.web_playwright import Pages
from pages.web_playwright.playwright_core import PlaywrightUIDriver


@function_recorder()
def add_product_to_cart_playwright(
    pages: Pages,
    uidriver: PlaywrightUIDriver,
    test_run_config: TestRunConfig,
    product_id: str = "9468",
) -> None:
    """將指定商品加入購物車

    Args:
        product_id: 商品 ID
    """
    env = test_run_config.environment
    uidriver.go_to_url(f"https://www.{env}.kkday.com/zh-tw/product/{product_id}")

    pages.product_page.buy_now_button.wait_for_visible().click()
    pages.product_page.date_picker_input.wait_for_visible().click()

    # 選擇明天的日期
    pages.product_page.tomorrow_date_button.wait_for_visible().click()

    pages.product_page.quantity_dropdown.wait_for_visible().select_option(value="2")
    pages.product_page.add_to_cart_button.wait_for_visible().click()

    assert_that(
        pages.cart_page.checkout_button.wait_for_visible(timeout=10, no_exception=True).is_visible,
        "加入購物車失敗",
    )
```

## 4. UI Test Step（Mobile — 平台判斷）

```python
from lib.constants import Platform
from lib.decorators import function_recorder
from lib.models import TestRunConfig
from pages.Mobile.base import Pages


@function_recorder()
def verify_cart_total(pages: Pages) -> None:
    """驗證購物車總金額顯示"""
    match TestRunConfig.platform:
        case Platform.IOS:
            pages.cart_page.total_price_label.scroll_to(direction="down", tries=5)
        case Platform.ANDROID:
            pages.cart_page.total_price_label.scroll_to(
                container_element=pages.cart_page.scroll_container,
                direction="down",
                tries=5,
            )

    assert_that(
        pages.cart_page.total_price_label.wait(no_exception=True).is_present,
        "購物車總金額未顯示",
    )
```

## 5. API Test Step

```python
from lib.api import core as ApiCore
from lib.api.models import ApiRequest, ApiResponse
from lib.decorators import function_recorder
from lib.models import TestCase
from pages.api import APICommonMethod


@function_recorder()
def create_order_by_api(
    testcase: "TestCase",
    api_request: ApiRequest,
    api_response: ApiResponse,
) -> None:
    """透過 API 建立訂單

    Args:
        testcase: 測試案例物件

    Returns:
        None
    """
    ApiCore.initial_api(
        session="unused",
        data_filepath=f"{testcase.feature}/{testcase.case_id}.json",
        collection="create_order",
    )

    # 動態替換 payload
    member_uuid = testcase.dynamic_test_data.get("memberUuid")
    testcase.update_dynamic_test_data(key="member_uuid", value=member_uuid)
    APICommonMethod.assign_data_to_api_request(
        assign_to="payload",
        source="member_uuid",
        target="member_uuid",
        time_deviation="",
    )

    try:
        ApiCore.send_request(http_method="post")
        APICommonMethod.check_api_response_code()
        APICommonMethod.check_api_response_text()

        # 儲存訂單編號供後續步驟使用
        APICommonMethod.store_data_from_api_response(column="orderMid")
    finally:
        ApiCore.deinitial_api()
```

## 6. JSON 資料檔

```json
{
    "$env": {
        "api_collection": {
            "create_order": {
                "headers": {
                    "content-type": "application/json",
                    "authorization": "Bearer str_to_be_replaced"
                },
                "payload": {
                    "member_uuid": "str_to_be_replaced",
                    "prod_mid": "str_to_be_replaced",
                    "quantity": "int_to_be_replaced"
                },
                "response_status": 200,
                "response_text": {
                    "metadata": {
                        "status": "0000",
                        "desc": "成功"
                    }
                },
                "response_schema": {
                    "type": "object",
                    "properties": {
                        "metadata": {
                            "type": "object",
                            "required": ["status", "desc"]
                        },
                        "data": {
                            "type": "object"
                        }
                    },
                    "required": ["metadata", "data"]
                },
                "url": "https://api-gateway.$env.kkday.com/order/api/v1/orders"
            }
        },
        "test_data": {
            "prod_mid": "12345"
        }
    }
}
```

## 7. YAML 測試案例

```yaml
KQT-T12345:
    platform: web
    priority: RAT
    feature: WebOrder
    description: 驗證 Web 下單流程 - Tappay 付款
    pre-condition:
        - launch_home_page_playwright
        - login_with_email_playwright
    steps:
        - add_product_to_cart_playwright:
            product_id: "9468"
        - create_order_playwright:
            payment_method: tappay
        - verify_order_success_playwright

KQT-T12346:
    platform: api
    priority: RAT
    feature: OrderAPI
    description: 透過 API 建立訂單
    steps:
        - login_by_api
        - create_order_by_api
        - verify_order_status_by_api
```

# Sniff & 寫 test_step 常見坑

## JSON 規範重點（被坑過很多次）

- 動態值 **一律用 placeholder**：`str_to_be_replaced` / `int_to_be_replaced` / `float_to_be_replaced` / `bool_to_be_replaced`
- 即使預設值是 `"1"`、`"0"`、`"false"` 這種字面值也要改成 placeholder（`assign_data_to_api_request` 不認 hard-coded value，會 fail）
- 例外：固定常數（如 `category: "ON-GOING"`、`needOverDateOrder: "false"`）可以 hard-code 不 assign
- form-urlencoded endpoint headers 必含：`content-type: application/x-www-form-urlencoded` + `x-csrf-token: str_to_be_replaced` + `x-requested-with: XMLHttpRequest` + `market: zh-tw`
- 動態 dict key（如 `prod_list[<dynamic_id>][country_cd]`）`assign_data_to_api_request` 不支援，要在 test_step **直接組 dict 跳過 assign**

## 跑流程常見坑

1. **背景跑 sniff 會立刻死**：`input()` 沒 stdin 會 EOF。一定要請用戶在他 terminal 跑。
2. **CodeIgniter form-post 403**：忘了 `csrf_token_name` payload field 跟 cookie 對齊（`csrf_cookie_name` cookie 必須 = form 的 `csrf_token_name`）。每個 form-post step 在 initial_api 之前要 ensure ci_csrf：
   ```python
   if not get_web_cookies().get("csrf_cookie_name"):
       web_warmup_homepage()
   ci_csrf = testcase.dynamic_test_data.get("web_ci_csrf") or get_web_cookies().get("csrf_cookie_name", "")
   ```
3. **referer header 殘留 `str_to_be_replaced`**：寫 `api_request.headers.pop("referer", None)`（沒 step1 session 不需要）。
4. **assign_data 找不到 key 就 fail**：JSON value 必須是 placeholder，不能是字面常數。或改用直接組 dict 不走 assign。
5. **HTML 頁面 response check 失敗**：JSON `response_text: {}` 對 HTML 沒意義，省略 `check_api_response_text()` 只用 `check_api_response_code()`。
6. **分頁 API 一頁找不到**：先打 page=1 拿 totalPage，再 for 迴圈跑剩餘頁。新資料通常在最後幾頁（按時間排序），從 last page 往前找比較快。

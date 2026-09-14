# Design Spec：IT.hq-qa 權限自動指派 MCP Tool

- 日期：2026-09-13
- 狀態：**DRAFT — 待 Lance 確認**
- 落腳：`kkday-qa-tools` MCP（本 repo `mcp_servers/kkday_qa_tools/server.py`）+ ai-studio 後端（`kkday-qa-ai` 的 `tools_route.py`）
- 來源邏輯：`kkday-QA-automation` 的 test-step `AuthRoleManagement.py`（`EnsureRoleAndUserPermission`）
- 環境：`sit` 系列 / `stage`，永不碰 prod（沿用 MCP 既有 `_check_env`）

---

## 0. 修正紀錄：落腳從 be2-mcp 改成 kkday-qa-tools

初版把功能設計進 `be2-mcp`（identity passthrough、要自建 4-step 登入、糾結服務帳號 vs §1.5 禁密碼政策）。**那份已作廢。** 原因：本 repo 就有一個 MCP（`kkday-qa-tools`），且它的架構完全不同——

- **kkday-qa-tools 是 ai-studio 後端的薄代理**：每支 tool 只做一次 `_call("POST", "/api/tools/xxx", ...)` 打 `autotest-service.sit.kkday.com:8081`，auth 靠固定 `X-User-Id` admin id，MCP 本身不持任何 be2 credential。
- **真正的 be2 操作在後端**：`create_coupon` / `add_kkday_points` 等都是後端用**固定服務帳號**做掉。這就是先前「折扣券/加點透過特定帳號」的記憶來源。
- **服務帳號決策自動成立且 §1.5 不適用**：§1.5 是 be2-mcp 專屬政策；本架構後端用服務帳號本來就是設計，先前 A/B/C 的認證糾結整個 moot。

## 1. 目標與需求（不變）

一支 tool：**輸入 email + 環境 → 確保 IT.hq-qa 角色權限齊全 + 把該 user 加進角色 + 驗證。** 冪等、只加不減、每次可重跑。原設想 1/2/3 模式已塌成單一操作（模組一律全選，冪等無害）。

### 1.1 錯誤處理原則（Lance 定案）：試了就試，錯誤原封透傳

**不做防禦性處理。** 登入失敗（編號 sit 沒有該服務帳號）、PUT auth-role 403（帳號在該環境無權限管理權）、查無此人——一律**不預先擋、不做 env-scoping、不搞 stage/sit 跳過分支**，直接讓後端錯誤（HTTP status + body）原封回給 MCP client（沿用 `_call` 既有行為：後端非 2xx → `RuntimeError("{status} : {text}")` → LLM 直接看到）。

推論：
- **不需要**把 `_get_be2_credentials` 改成 per-env、不需要預先補編號 sit 帳密；那些環境登入失敗就回錯誤。
- **不需要** `_missing_user_reason` 的 stage=失敗 / 編號 sit=跳過語意；查無此人就回「查無此人」給 client。
- ensure 邏輯只走 happy path，任何一步後端回非 2xx 就讓它拋、透傳。

## 2. 架構（兩層，與所有既有 tool 一致）

```
LLM → MCP tool ensure_hq_qa_permission(email, env)   [kkday-qa-skills server.py]
        └─ _call POST /api/tools/ensure-hq-qa-permission
             → ai-studio 後端 endpoint                 [kkday-qa-ai tools_route.py]
                 └─ _be2_full_login(服務帳號) → ensure role 21 → ensure user → verify
```

## 3. 後端已現成的積木（大幅減少工作量）

`kkday-qa-ai/.../routes/tools_route.py` 已有：

| 既有符號 | 位置 | 對應我們的需求 | 可否直接用 |
|---|---|---|---|
| `_be2_full_login(account, password, env_info, log)` | L154 | 4-step 登入（服務帳號）→ (Session, accessToken) | ✅ 直接用 |
| `_set_be2_default_auth_role_stage(...)` | L2427 | Mode 1：PUT auth-role 設 role 21 + businessOids | ⚠️ **參考重寫成新函式**（別改它——GM-BE 流程 L2576 在用）：動態抓 `GET /api/v2/business-list` 全模組、去掉 stage 限定、且**錯誤語意從「log 不 raise」改成 raise**（要透傳失敗，見 §1.1） |
| `_grant_gmbe_with_session(...)` | L2519 | Mode 2 樣板：GET 現狀→沒有 POST 建/有則 PUT union 角色 | ⚠️ 要改：目前綁 **platformOid=7(GM-BE)**、且對**登入者自己**（authKey 取自 JWT）；需改成 **platformOid=1(be2)、role 21、對任意 `email`** |
| `_BE2_STAGE_DEFAULT_ROLE_OID=21` / `_PLATFORM_OID=1` | L2423-2424 | 常數 | ✅ 沿用 |
| `_get_operator(request)` / `env_info`(auth_host/be2_host/gateway_host) | L129 等 | operator 稽核 / 環境解析 | ✅ 沿用 |
| `@router.post("/tools/...")`（FastAPI） | L505+ | endpoint 註冊樣式 | ✅ 照抄 |

**要新寫的 delta（不多）**：
1. 動態版「ensure role 21 全模組」：抓 business-list 攤平所有 businessOid、GET auth-role 算 missing、缺才 PUT（沿用 QA-automation `ensure_auth_role_full_permission_api` 的邏輯，取代 hardcode+stage 限定）。
2. 「ensure 任意 email 的 be2 sub-user」：`GET sub-user/business?platformOid=1&authKey={email}` → 啟用（status=1，送**數字**）→ 指派 role 21（只加不減）（沿用 QA-automation `ensure_and_verify_users_api`，platformOid 換 1）。查無此人 → 直接回錯誤/結果給 client，**不做 stage/sit 跳過分支**（見 §1.1）。
3. verify + 結構化回傳。任一步後端回非 2xx → 讓它拋、原封透傳（見 §1.1）。

## 4. 後端 endpoint

```
POST /api/tools/ensure-hq-qa-permission
body: { "email": "user@kkday.com", "env": "sit" | "sit218" | "stage" }
resp: {
  "env": "...", "role": "IT.hq-qa (roleOid=<動態查>, platformOid=1)",
  "role_permission": {"total": 42, "added": 0, "skipped": true},
  "user": {"email":"...", "subAuthOid": 99, "enabled_now": false, "roles_added": [], "already_ok": true},
  "verified": true, "logs": [...]
}
```

- 服務帳號帳密：沿用後端既有 secret 取得方式（`_be2_full_login` 的 caller 目前怎麼拿 account/password 就怎麼拿，見 L339/L475/L1470）。
- operator 稽核：走 `_get_operator(request)`，MCP 端會帶 `X-User-Name`（本 tool 建議自己的 operator 標記，如 `kkday_qa_hq_perm_mcp`，方便 dashboard 分辨）。

## 5. MCP tool（薄層，照既有樣式）

`mcp_servers/kkday_qa_tools/server.py` 新增：

```python
@mcp.tool()
def ensure_hq_qa_permission(email: str, env: str) -> dict:
    """把指定使用者加進 be2 IT.hq-qa 角色（含確保角色全模組權限 + 驗證）。

    〔詢問模式（預設）〕呼叫前先向使用者確認 email / env；env 只有 sit / stage，
    選 sit 必須追問是哪一台（sit0x / sit20x），不得自行預設或編造。

    Args:
        email: 目標使用者 email（be2 authKey）
        env: sit / stage（例：sit / sit218 / stage）
    """
    return _call("POST", "/api/tools/ensure-hq-qa-permission",
                 json={"email": email, "env": env})
```

- `_check_env` 已在 `_call` 內對帶 `env` 的 body 自動驗證（擋 prod / 亂編）→ 免重造。
- 埋點 / 逾時 / 錯誤外拋全部沿用 `_call`。
- 記得在 `help()` 分類與 `describe_tool()` 各補一筆。

## 6. 安全邊界

- env 白名單：`_check_env` 只放行 `stage` / `sit` 系列，prod 一律擋。
- 只加不減：角色權限與使用者角色都只補缺。
- 稽核：後端 history 記 operator；MCP 端 analytics 記來源。
- 不落地 credential：服務帳密只在後端 secret，MCP / repo / log 不出現。

## 7. 跨 repo 與部署依賴（🔴 執行時要知道）

- **改兩個 repo**：主要邏輯在 `kkday-qa-ai`（後端），薄 tool 在 `kkday-qa-skills`（本 repo）。
- **MCP 打的是「已部署的後端」**：`kkday-qa-ai` 加完 endpoint **必須部署到 `autotest-service`（sit，且若要支援 stage 需該實例能打 stage auth）** 後，MCP tool 才會通。只改 MCP 不部署後端＝tool 100% 失敗。
- 驗證順序：後端部署 → MCP 端 `health()` → 實跑一個測試 email。

## 8. 待確認 / 待辦

**已由 §1.1「錯誤透傳」收掉的（不再是前置阻擋）**：
- ~~編號 sit 沒服務帳密 / `_get_be2_credentials` 非 per-env~~ → 登入失敗就回錯誤給 client。
- ~~服務帳號在某環境無權限管理權（PUT auth-role 403）~~ → 403 原封透傳。
- ~~查無此人的 stage/sit 跳過語意~~ → 一律回錯誤/結果，不分支。
- ~~改 `_set_be2_default_auth_role_stage` 影響 GM-BE~~ → 決定**另寫新函式、不動它**，GM-BE 不受影響。

**仍待決/待辦**：
1. **role 21 動態化 vs 硬編**：建議動態查 role by name（避免各 env DB PK 不一致），實作時定。
2. 本輪只出 spec；實作走 TDD（後端 endpoint 用 mock session 驗 4-step 登入序列 + ensure 冪等 happy path + 非 2xx 透傳；MCP 薄 tool 驗 `_check_env` 擋 prod）。
3. 部署：後端加完 endpoint 需部署到 autotest-service，MCP tool 才會通（見 §7）。

## 9. 實作落點（確認後）

- `kkday-qa-ai/ai_studio/ai_studio_core/backend/routes/tools_route.py`：
  - 新增 `POST /tools/ensure-hq-qa-permission` endpoint
  - 新增/泛化：動態 ensure-role-full-permission（取代或並存於 `_set_be2_default_auth_role_stage`）、be2(platformOid=1) 版 ensure-sub-user-by-email
  - 對應測試（後端既有 test 目錄）
- `kkday-qa-skills/mcp_servers/kkday_qa_tools/server.py`：新增薄 tool + `help()` / `describe_tool()` 補條目
- 部署：autotest-service 後端更新

# Specs CHANGELOG

## 2026-09-13

- `2026-09-13-hq-qa-permission-tool-design.md`（新增）/ IT.hq-qa 權限自動指派 MCP tool 的 design spec，DRAFT / 落腳導正為 `kkday-qa-tools` MCP（薄層）+ ai-studio 後端 `tools_route.py`（實作）；後端已有 `_be2_full_login` / `_set_be2_default_auth_role_stage`(role 21) / `_grant_gmbe_with_session`(ensure 樣板) 三塊積木，delta 是「動態全模組 + platformOid=1 對任意 email」。初版誤置於 be2-mcp（已作廢，服務帳號/§1.5 糾結為 be2-mcp 專屬、本架構不適用）。
- `2026-09-13-hq-qa-permission-tool-design.md` §1.1/§3/§8（review 後修訂）/ 定案「錯誤透傳」原則：不做防禦性 env-scoping、不補 per-env 帳密、不做 stage/sit 跳過分支，後端非 2xx 一律原封回 MCP client。連帶收掉 review 抓到的編號-sit 登入失敗、PUT auth-role 403、查無此人語意三條 finding；`_set_be2_default_auth_role_stage` 改為「另寫新函式、不動它」避免影響 GM-BE / 為什麼：Lance 定調錯誤直接回 client 即可，簡化實作。

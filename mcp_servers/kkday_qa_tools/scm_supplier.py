"""SCM 供應商建立與啟用 — 業務邏輯模組。

server.py 的 MCP tool（create_scm_supplier / activate_scm_supplier / get_scm_otp）
呼叫這裡的 function，保持 server.py 只做 tool 註冊 + analytics。
"""

import base64
import json as _json
import logging
import os
import re
import time
import uuid
from datetime import date, timedelta
from typing import Optional

import requests

log = logging.getLogger(__name__)

# ── 環境變數 ──────────────────────────────────────────────────────
_QA_GMAIL_BASE = os.getenv("QA_GMAIL_BASE", "b2c-qa-team@kkday.com")

_GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

_AUTOMATION_TOKEN = os.getenv(
    "AUTOMATION_TOKEN", "8b9dfbac-e863-4078-95e9-c2cc03abe84f"
)

# ── secret service 共用 helper ────────────────────────────────────
_SECRET_SERVICE_HOST = os.getenv(
    "SECRET_SERVICE_HOST", "http://autotest-service.sit.kkday.com"
)
_secret_cache: dict[str, dict] = {}


def _get_secret(env: str, service: str, key: str) -> dict:
    """從 secret service 取一筆 key-value，回傳 parsed dict。結果會快取。"""
    cache_key = f"{env}:{service}:{key}"
    if cache_key in _secret_cache:
        return _secret_cache[cache_key]

    secret_env = "sit" if "sit" in env else env

    resp = requests.get(
        f"{_SECRET_SERVICE_HOST}:8000/api/v1/data/",
        headers={"authorization": f"Bearer {_AUTOMATION_TOKEN}"},
        params={"env": secret_env, "service": service, "key": key},
        timeout=(5, 10),
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise RuntimeError(
            f"Secret service 找不到 env={secret_env}, service={service}, key={key}")
    result = _json.loads(data[0]["value"])
    _secret_cache[cache_key] = result
    return result

_SCM_TIMEOUT = (10, 120)

# Stage 環境 MQ consumer 延遲大，ASF + approve 需要較長等待
_ASF_POLL_TIMEOUT = 300       # ASF 背景調查 polling 上限（秒）
_ASF_POLL_INTERVAL = 10       # ASF polling 間隔（秒）
_ASF_SETTLE_WAIT = 20         # ASF 完成後等 MQ consumer 穩定（秒）
_APPROVE_MAX_RETRIES = 20     # approve 最大重試次數
_APPROVE_RETRY_INTERVAL = 15  # approve 重試間隔（秒）

_SCM_COUNTRY_CONTRACT_MAP = {
    "TW": 3, "JP": 5, "KR": 4, "SG": 6, "MY": 6,
    "HK": 15, "TH": 16, "VN": 10, "CN": 13, "AU": 18,
}

_SCM_FILE_CATEGORIES = ["REGISTER", "TOURISM_LICENSE", "ATTACHMENT", "BANK_PASSBOOK_COPY"]

_TINY_PDF_BASE64 = (
    "JVBERi0xLjQKJcOkw7zDtsOfCjIgMCBvYmoKPDwKL0xlbmd0aCAzIDAgUgovRmlsdGVyIC9GbGF0Z"
    "URlY29kZQo+PgpzdHJlYW0KeJwzUvDiMlAwUDA1MTRSCEnlMjQyMTBVMDIyMTUKSeUKVDA0NTQwM"
    "TQwUTAyMTAyMzVQ4AIA/owGgAplbmRzdHJlYW0KZW5kb2JqCjMgMCBvYmoKNjQKZW5kb2JqCjEgM"
    "CBvYmoKPDwKL1R5cGUgL1BhZ2UKL1BhcmVudCA0IDAgUgovTWVkaWFCb3ggWzAgMCA2MTIgNzkyX"
    "QovQ29udGVudHMgMiAwIFIKL1Jlc291cmNlcyA8PAovUHJvY1NldCBbL1BERl0KPj4KPj4KZW5kb"
    "2JqCjQgMCBvYmoKPDwKL1R5cGUgL1BhZ2VzCi9LaWRzIFsxIDAgUl0KL0NvdW50IDEKPj4KZW5kb"
    "2JqCjUgMCBvYmoKPDwKL1R5cGUgL0NhdGFsb2cKL1BhZ2VzIDQgMCBSCj4+CmVuZG9iagp4cmVmC"
    "jAgNgowMDAwMDAwMDAwIDY1NTM1IGYNCjAwMDAwMDAxNjIgMDAwMDAgbg0KMDAwMDAwMDAxNSAwM"
    "DAwMCBuDQowMDAwMDAwMTQ0IDAwMDAwIG4NCjAwMDAwMDAyNzAgMDAwMDAgbg0KMDAwMDAwMDMyN"
    "yAwMDAwMCBuDQp0cmFpbGVyCjw8Ci9TaXplIDYKL1Jvb3QgNSAwIFIKPj4Kc3RhcnR4cmVmCjM3N"
    "Qo="
)


# ── 內部 helper ────────────────────────────────────────────────────

def _scm_env_domain(env: str) -> str:
    return "stage" if env == "stage" else "sit"


def _scm_api_base(env: str) -> str:
    return f"https://api-scm.{_scm_env_domain(env)}.kkday.com/api"


def _sofa_potato_base(env: str) -> str:
    return f"https://sofa-potato.{_scm_env_domain(env)}.kkday.com/api"


def _bluemountain_base(env: str) -> str:
    return f"https://api-gateway.{_scm_env_domain(env)}.kkday.com/bluemountain/api/v1/workflow"


_CONTRACT_PARTY: dict[int, dict] = {
    3: {
        "companyNo": 3,
        "companyName": "酷遊天國際旅行社股份有限公司",
        "companyEnName": "Taiwanmania.com International Travel Service Co., Ltd.",
        "bankAccountCountry": "TW",
        "companyCountry": "TW",
        "companyCode": "TRATW",
    },
}


# ── bluemountain helpers ──────────────────────────────────────────

def _bm_post(base_url: str, endpoint: str, headers: dict,
             payload: dict, context: str) -> dict:
    body = _scm_request("POST", f"{base_url}/{endpoint}",
                        headers=headers, json_body=payload)
    status = body.get("metadata", {}).get("status", "")
    if status != "0000":
        desc = body.get("metadata", {}).get("desc", "")
        raise RuntimeError(f"bluemountain {context}: status={status} desc={desc}")
    return body


def _bm_event_list(base_url: str, headers: dict) -> list:
    body = _bm_post(base_url, "event-list", headers, {
        "authKey": "BCS", "serviceName": "BCS",
        "data": {
            "bizType": "supplier_onboarding_v1",
            "page": {"currentPage": 1, "pageSize": 20,
                     "sortProperty": "createDate", "sortDirection": "DESC"},
        },
    }, "event-list")
    return body.get("data", {}).get("events", [])


def _find_event_for_supplier(events: list, supplier_oid: int) -> dict:
    for event in events:
        for field in event.get("displayFields", []):
            if (field.get("key") == "supplierOid"
                    and int(field.get("value", 0)) == supplier_oid):
                return event
    raise RuntimeError(f"event-list 找不到 supplier {supplier_oid}")


def _find_task_oid(event: dict, task_name: str) -> int:
    ct = event.get("currentTask", {})
    if ct.get("taskName") == task_name:
        return ct["taskOid"]
    for t in event.get("tasks", []):
        if t.get("taskName") == task_name:
            return t["taskOid"]
    raise RuntimeError(f"找不到 task「{task_name}」")


def _bm_task_claim(base_url: str, headers: dict,
                   task_oid: int, admin_email: str) -> dict:
    return _bm_post(base_url, "task-claim", headers, {
        "authKey": "BCS", "serviceName": "BCS",
        "data": {"taskOid": task_oid, "currentUuid": admin_email},
    }, f"task-claim({task_oid})")


def _bm_task_action(base_url: str, headers: dict, task_oid: int,
                    admin_email: str, action_code: str) -> dict:
    return _bm_post(base_url, "task-action", headers, {
        "authKey": "BCS", "serviceName": "BCS",
        "data": {
            "taskOid": task_oid, "currentUuid": admin_email,
            "actionCode": action_code, "comment": None, "extraInfo": None,
        },
    }, f"task-action({action_code})")


def _bm_task_draft(base_url: str, headers: dict, task_oid: int,
                   admin_email: str, draft_data: dict,
                   context: str = "task-draft") -> dict:
    return _bm_post(base_url, "task-draft", headers, {
        "authKey": "BCS", "serviceName": "BCS",
        "data": {
            "taskOid": task_oid, "bizType": "supplier_onboarding_v1",
            "currentUuid": admin_email, "draftData": draft_data,
        },
    }, context)


def _bm_contract_create(base_url: str, headers: dict, task_oid: int) -> dict:
    return _bm_post(base_url, "common/contract-create", headers, {
        "authKey": "BCS", "serviceName": "BCS",
        "data": {
            "taskOid": task_oid, "bizType": "supplier_onboarding_v1",
            "contractLang": "zh-TW",
        },
    }, "contract-create")


def scm_frontend_login_url(env: str) -> str:
    if env == "stage":
        return "https://scm.stage.kkday.com/v2/zh-tw/auth/login"
    m = re.match(r"sit(\d+)", env)
    if m:
        return f"https://scm-{m.group(1)}.sit.kkday.com/v2/zh-tw/auth/login"
    return "https://scm.sit.kkday.com/v2/zh-tw/auth/login"


def _scm_request(method: str, url: str, *,
                 headers: Optional[dict] = None,
                 json_body: Optional[dict] = None,
                 timeout: tuple = _SCM_TIMEOUT) -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json",
         "locale": "zh-tw", "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"}
    if headers:
        h.update(headers)
    resp = requests.request(method, url, headers=h, json=json_body, timeout=timeout)
    if not resp.ok:
        raise RuntimeError(f"{method} {url} → {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def _scm_assert_success(body: dict, context: str) -> dict:
    status = body.get("metadata", body.get("meta", {})).get("status")
    if status != "0000":
        desc = body.get("metadata", body.get("meta", {})).get("desc", "")
        raise RuntimeError(f"{context} 失敗: status={status} desc={desc}")
    return body


def _build_gmail_service(env: str = "sit"):
    """建立 Gmail API service 並回傳 (service, profile_email)。

    profile_email 是 token 對應的信箱（例如 ivan.su@kkday.com），
    可用來產生 sub-address 註冊，確保 OTP 信會進到同一個信箱。
    """
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    gmail_cred = _get_gmail_credentials(env)
    creds = Credentials(
        token=gmail_cred.get("token"),
        refresh_token=gmail_cred["refresh_token"],
        token_uri=gmail_cred.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=gmail_cred["client_id"],
        client_secret=gmail_cred["client_secret"],
        scopes=_GMAIL_SCOPES,
    )
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    return service, profile["emailAddress"]


def gmail_get_otp(recipient: str, env: str = "sit",
                  max_wait: int = 120,
                  poll_interval: int = 8) -> str:
    service, _profile_email = _build_gmail_service(env)

    deadline = time.time() + max_wait
    query = f"to:{recipient} (subject:驗證碼 OR subject:\"Verification Code\") in:inbox"

    while time.time() < deadline:
        results = service.users().messages().list(
            userId="me", q=query, maxResults=5,
        ).execute()
        messages = results.get("messages", [])
        if messages:
            msg = service.users().messages().get(
                userId="me", id=messages[0]["id"],
            ).execute()
            body_data = msg["payload"].get("body", {}).get("data")
            if not body_data and "parts" in msg["payload"]:
                for part in msg["payload"]["parts"]:
                    bd = part.get("body", {}).get("data")
                    if bd:
                        body_data = bd
                        break
            if body_data:
                body_text = base64.urlsafe_b64decode(body_data).decode("utf-8")
                text = re.sub(r"<[^>]+>", " ", body_text)
                text = re.sub(r"\s+", " ", text).strip()
                match = re.search(r"(\d{6})", text)
                if match:
                    return match.group(1)
        time.sleep(poll_interval)

    raise TimeoutError(f"等待 OTP 超過 {max_wait} 秒，recipient={recipient}")


def _get_gmail_credentials(env: str) -> dict:
    """從 secret service 取 Gmail OAuth token。

    DB value 可能是兩種格式（取決於介面怎麼存的），都能處理：
    - 扁平：{"token": "ya29...", "refresh_token": "1//...", ...}
    - 巢狀：{"token": "{\"token\": \"ya29...\", ...}"}（介面自動包了一層）
    """
    cred = _get_secret(env, service="gmail", key="gmail_token")
    # 偵測巢狀格式：只有 "token" 一個 key，且值是 JSON 字串
    if list(cred.keys()) == ["token"] and isinstance(cred["token"], str):
        try:
            inner = _json.loads(cred["token"])
            if "refresh_token" in inner:
                return inner
        except (ValueError, TypeError):
            pass
    return cred


def _get_scm_password(env: str) -> str:
    """從 secret service 取 SCM 預設密碼。"""
    cred = _get_secret(env, service="scm", key="scm_default_password")
    return cred["password"]


def _get_scm_auth_service_key(env: str) -> str:
    """從 secret service 取 SCM auth service key。"""
    cred = _get_secret(env, service="scm", key="scm_auth_service_key")
    return cred["service_key"]


def _get_be2_credentials(env: str) -> tuple:
    """從 secret service 取 BE2 管理員帳密。"""
    secret_env = "sit" if "sit" in env else env
    envs_to_try = [secret_env] if secret_env == "sit" else [secret_env, "sit"]
    for try_env in envs_to_try:
        try:
            cred = _get_secret(try_env, service="be2", key=f"be2_{try_env}")
            account = cred.get("account", "")
            password = cred.get("password", "")
            if account and password:
                return account, password
        except Exception:
            continue
    raise RuntimeError(
        f"Secret service 在 {envs_to_try} 都找不到有效的 BE2 帳密。"
        "請確認 autotest-service 可連線。"
    )


def _be2_admin_login(env: str) -> tuple:
    account, password = _get_be2_credentials(env)
    domain = _scm_env_domain(env)

    login_resp = requests.post(
        f"https://auth.{domain}.kkday.com/api/v1/auth/be2/login",
        headers={"Content-Type": "application/json", "x-qa-platform": "QA_TestPlatform"},
        json={"account": account, "password": password,
              "optional": {"locale": "zh-tw"}},
        timeout=_SCM_TIMEOUT,
    )
    if not login_resp.ok:
        raise RuntimeError(f"BE2 login 失敗: {login_resp.status_code}: {login_resp.text[:300]}")
    auth_code = login_resp.json()["data"]["authorizationCode"]

    service_key = _get_scm_auth_service_key(env)

    token_resp = requests.get(
        f"https://auth.{domain}.kkday.com/api/v1/login-authorization-code/{auth_code}/",
        headers={"Authorization": service_key},
        timeout=_SCM_TIMEOUT,
    )
    if not token_resp.ok:
        raise RuntimeError(f"BE2 token 交換失敗: {token_resp.status_code}: {token_resp.text[:300]}")
    access_token = token_resp.json()["data"]["accessToken"]

    # 只需讀 platformId，不驗簽——避免多加 PyJWT 依賴
    payload_b64 = access_token.split(".")[1]
    padding = 4 - len(payload_b64) % 4
    if padding != 4:
        payload_b64 += "=" * padding
    jwt_payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
    owner_uuid = jwt_payload["platformId"]

    admin_email = jwt_payload.get("authKey", "")

    return access_token, owner_uuid, admin_email


# ── 公開 API（server.py 呼叫）──────────────────────────────────────

def scm_register_and_login(env: str) -> tuple:
    """註冊 + 登入 + OTP 驗證，回傳 (email, password, session_token)。

    """
    base = _scm_api_base(env)
    password = _get_scm_password(env)
    local, domain_part = _QA_GMAIL_BASE.split("@")
    ts = str(int(time.time() * 1000))
    email = f"{local}+automation_test_{ts}@{domain_part}"
    device = str(uuid.uuid4())

    body = _scm_request("POST", f"{base}/external-unauth/v1/user/register",
                        json_body={"email": email, "password": password,
                                   "confirmPassword": password,
                                   "timezone": "Asia/Taipei"})
    _scm_assert_success(body, "註冊")

    body = _scm_request("POST", f"{base}/external-unauth/v1/auth/login",
                        json_body={"email": email, "password": password,
                                   "device": device})
    _scm_assert_success(body, "登入")

    body = _scm_request("POST", f"{base}/external-unauth/v1/auth/two-fa/otp",
                        json_body={"email": email, "device": device})
    _scm_assert_success(body, "觸發 OTP")

    otp = gmail_get_otp(email, env=env,
                        max_wait=120, poll_interval=8)

    body = _scm_request("POST", f"{base}/external-unauth/v1/auth/two-fa/validate",
                        json_body={"email": email, "code": otp,
                                   "device": device, "rememberMe": True})
    _scm_assert_success(body, "驗證 OTP")
    session_token = body["data"]["sessionToken"]

    return email, password, session_token


def _scm_relogin(env: str, email: str) -> str:
    """用既有帳號重新登入 SCM，回傳 session_token。"""
    base = _scm_api_base(env)
    password = _get_scm_password(env)
    device = str(uuid.uuid4())

    body = _scm_request("POST", f"{base}/external-unauth/v1/auth/login",
                        json_body={"email": email, "password": password,
                                   "device": device})
    _scm_assert_success(body, "重新登入")

    body = _scm_request("POST", f"{base}/external-unauth/v1/auth/two-fa/otp",
                        json_body={"email": email, "device": device})
    _scm_assert_success(body, "觸發 OTP（重新登入）")

    otp = gmail_get_otp(email, env=env,
                        max_wait=120, poll_interval=8)

    body = _scm_request("POST", f"{base}/external-unauth/v1/auth/two-fa/validate",
                        json_body={"email": email, "code": otp,
                                   "device": device, "rememberMe": True})
    _scm_assert_success(body, "驗證 OTP（重新登入）")
    return body["data"]["sessionToken"]


def scm_submit_application(env: str, session_token: str, email: str,
                           country: str = "TW") -> int:
    """提交供應商申請，回傳 supplier_oid。"""
    base = _scm_api_base(env)
    auth_h = {"s-ci-sessions": session_token}
    ts = str(int(time.time() * 1000))
    readable_ts = time.strftime("%Y%m%d_%H%M%S")

    body = _scm_request("GET", f"{base}/external/v1/supplier/apply/terms", headers=auth_h)
    _scm_assert_success(body, "取 terms")
    agreement_list = [
        {"agreementOid": t["agreementOid"], "agreementType": t["agreementType"]}
        for t in body["data"]["terms"]
    ]

    file_oids = []
    for cat in _SCM_FILE_CATEGORIES:
        body = _scm_request("POST", f"{base}/external/v1/supplier/apply/files",
                            headers=auth_h,
                            json_body={"fileCategory": cat,
                                       "fileName": f"qa_automation_{cat.lower()}.pdf",
                                       "contentType": "application/pdf",
                                       "encodeString": _TINY_PDF_BASE64})
        _scm_assert_success(body, f"上傳 {cat}")
        file_oids.append({"fileOid": str(body["data"]["fileOid"]), "fileCategory": cat})

    ts_suffix = ts[-6:]
    payload = {
        "countryCd": country,
        "supplierName": f"automation_{readable_ts}_台北測試旅行社",
        "legalName": f"automation_{readable_ts}_台北測試旅行社有限公司",
        "legalNameEng": f"Automation_{readable_ts} Taipei Test Travel Co., Ltd.",
        "website": "https://automation.example.com",
        "personInCharge": "王小明",
        "personInChargeEng": "Wang Hsiao-ming",
        "license": f"TW{ts_suffix}78",
        "isProvideTaxInfo": False,
        "stateCd": "TW-TPE",
        "cityCd": "TW-TPE-XYI",
        "postCd": "110",
        "address1": "信義路五段 7 號",
        "serviceTelArea": "+886",
        "serviceTel": "0223456789",
        "contactTitle": "Mr.",
        "contact": "王小明",
        "contactJobCd": "業務經理",
        "timezone": "Asia/Taipei",
        "contactEmail": email,
        "contactTelArea": "+886",
        "contactTel": "0912345678",
        "prefLang": "zh-tw",
        "contactOtherMethods": [{"methodType": "LINE", "contactValue": "automation_line_id"}],
        "serviceCountryCd": country,
        "serviceCategory": "CAT_14,CAT_21",
        "serviceDesc": f"automation_{readable_ts} 提供台北市內景點導覽與旅遊體驗服務",
        "serviceLang": "zh-tw,en",
        "hasKkdayContact": False,
        "agreementList": agreement_list,
        "files": file_oids,
        "bank": {
            "kkdayMainContractNo": _SCM_COUNTRY_CONTRACT_MAP.get(country, 3),
            "beneficiaryBankCountryCode": country,
            "collectCurrency": "TWD",
            "bankAccountType": "01",
            "bankName": "台灣銀行",
            "bankCode": "004",
            "branchName": "城中分行",
            "branchCode": "0041",
            "accountNo": f"1234{ts_suffix}01234",
            "accountName": f"automation_{readable_ts}_台北測試旅行社",
            "bankAddress": "台北市中正區重慶南路一段120號",
            "swiftCode": "BKTWTWTP",
            "ibanCode": None,
            "cnaps": None,
            "bsbNumber": None,
            "sknCode": None,
            "beneficiaryIdentity": "A123456789",
            "beneficiaryTelCountryCode": "+886",
            "beneficiaryTel": "0912345678",
            "beneficiaryEmail": email,
            "beneficiaryAddress": "台北市信義區信義路五段7號",
            "remittanceBurden": "01",
            "supplierBankDesc": f"automation_{readable_ts}",
        },
    }

    for attempt in range(3):
        body = _scm_request("POST", f"{base}/external/v1/supplier/apply",
                            headers=auth_h, json_body=payload)
        meta = body.get("metadata", {})
        if meta.get("status") == "0000":
            return int(body["data"]["supplierOid"])
        if meta.get("status") in ("9999", "C001") and attempt < 2:
            time.sleep(10)
            continue
        _scm_assert_success(body, "送出申請")

    raise RuntimeError("送出申請：重試 3 次仍失敗")


def scm_activate_supplier(env: str, supplier_oid: int, country: str,
                          email: str = "") -> str:
    """bluemountain BD 審核 + 供應商確認合約 + sofa-potato 合約，啟用供應商到 status 80。回傳 password。

    Phase A: 確認合作
    Phase B: 等 ASF → BD初審 task
    Phase C: BD初審 5-step wizard（step 2→3 建 settle、step 4→5 設 handler）
    Phase D: 供應商確認合約（contract/confirm，需 SCM session → 觸發 settle 建立）
    Status 80: sofa-potato 合約（Java API side effect 直接設 80）
    """
    potato = _sofa_potato_base(env)
    bm = _bluemountain_base(env)
    password = _get_scm_password(env)

    be2_token, owner_uuid, admin_email = _be2_admin_login(env)
    be2_h = {"Authorization": f"Bearer {be2_token}",
             "Content-Type": "application/json", "Accept": "application/json"}

    # ── 偵測目前進度（冪等：retry 時跳到正確的 phase）──
    event = None
    for _ in range(6):
        events = _bm_event_list(bm, be2_h)
        try:
            event = _find_event_for_supplier(events, supplier_oid)
            break
        except RuntimeError:
            time.sleep(5)
    if event is None:
        raise RuntimeError(
            f"bluemountain event-list 找不到 supplier {supplier_oid}")

    current_task_name = event.get("currentTask", {}).get("taskName", "")
    log.info("supplier %d 目前 task=%s", supplier_oid, current_task_name)

    # 跳到對應 phase
    skip_to_phase_d = False
    bd_task = None

    if current_task_name == "等待供應商確認合作":
        # Phase A~C 已完成，直接跳 Phase D
        log.info("偵測到 Phase A~C 已完成（task=等待供應商確認合作），跳到 Phase D")
        skip_to_phase_d = True

    elif current_task_name == "BD初審":
        # Phase A 已完成，從 Phase C 繼續
        log.info("偵測到 Phase A 已完成（task=BD初審），從 Phase C 繼續")
        bd_task = event.get("currentTask", {}).get("taskOid")

    elif current_task_name == "確認合作":
        # Phase A: 正常流程
        coop_task = _find_task_oid(event, "確認合作")
        _bm_task_claim(bm, be2_h, coop_task, admin_email)
        _bm_task_action(bm, be2_h, coop_task, admin_email,
                        "CONFIRM_COOPERATION_ACCEPT")
    else:
        log.warning("未預期的 task 狀態: %s，嘗試繼續", current_task_name)

    if not skip_to_phase_d and bd_task is None:
        # ── Phase B: 等 ASF → BD初審 task 出現 ──
        deadline = time.time() + _ASF_POLL_TIMEOUT
        while time.time() < deadline:
            time.sleep(_ASF_POLL_INTERVAL)
            events = _bm_event_list(bm, be2_h)
            try:
                ev = _find_event_for_supplier(events, supplier_oid)
                task_name = ev.get("currentTask", {}).get("taskName", "")
                if task_name == "等待供應商確認合作":
                    log.info("ASF + BD初審已自動完成 → 跳到 Phase D")
                    skip_to_phase_d = True
                    break
                bd_task = _find_task_oid(ev, "BD初審")
                break
            except RuntimeError:
                continue
        if not skip_to_phase_d and bd_task is None:
            raise TimeoutError(
                f"等待 BD初審 task 超時（{_ASF_POLL_TIMEOUT}s）")

    if not skip_to_phase_d:
        # ── Phase C: BD初審 wizard ──
        _bm_task_claim(bm, be2_h, bd_task, admin_email)

        # C1: PATCH v2/detail — 設簽約主體
        contract_no = _SCM_COUNTRY_CONTRACT_MAP.get(country, 3)
        body = _scm_request("PATCH",
                            f"{potato}/v2/suppliers/{supplier_oid}/detail",
                            headers=be2_h,
                            json_body={"kkdayMainContractNo": contract_no})
        _scm_assert_success(body, "PATCH kkdayMainContractNo")

        # C1.5: GET bank（apply 時已帶 bank 資料，這裡取回 bankOid）
        bank_body = _scm_request("GET",
                                 f"{potato}/v1/suppliers/{supplier_oid}/banks",
                                 headers=be2_h)
        banks = bank_body.get("data", [])
        bank_oid = None
        bank_data: dict = {}
        if banks:
            bank_data = banks[0]
            bank_oid = bank_data.get("supplierBankOid")
        else:
            log.warning("apply 後 banks 為空，直接建立 bank（fallback）")
            fb_body = _scm_request(
                "POST", f"{potato}/v1/suppliers/{supplier_oid}/banks",
                headers=be2_h,
                json_body={
                    "kkdayMainContractNo": str(contract_no),
                    "bankName": "台灣銀行", "bankCode": "004",
                    "branchName": "城中分行", "branchCode": "0041",
                    "accountNo": "12345678901234",
                    "accountName": "automation_test",
                    "beneficiaryIdentity": "A123456789",
                    "beneficiaryEmail": email or "qa@kkday.com",
                    "beneficiaryAddress": "信義路五段 7 號",
                    "remittanceBurden": "01",
                    "supplierBankDesc": "automation test",
                    "collectCurrency": "TWD",
                    "beneficiaryBankCountryCode": "TW",
                })
            _scm_assert_success(fb_body, "fallback POST bank")
            bank_oid = fb_body.get("data", {}).get("supplierBankOid")
            bank_data = {
                "supplierBankOid": bank_oid,
                "bankCountryCode": "TW", "collectCurrency": "TWD",
                "bankName": "台灣銀行", "bankCode": "004",
                "branchName": "城中分行", "branchCode": "0041",
                "accountNo": "12345678901234", "accountName": "automation_test",
            }
            log.info("fallback bank 建立成功，bankOid=%s", bank_oid)

        # C3: step 1→2
        contract_party = _CONTRACT_PARTY.get(contract_no, _CONTRACT_PARTY[3])
        _bm_task_draft(bm, be2_h, bd_task, admin_email, {
            "currentStep": 1, "targetStep": 2,
            "extraInfo": {
                "productTypes": ["CAT_14"],
                "contractParty": contract_party,
                "cerebrumResult": {
                    "overallScore": 0, "riskLevel": "HIGH",
                    "riskLabel": "🔴 HIGH",
                    "aiRecommendation": "MANAGER_OVERRIDE_REQUIRED",
                    "aiRecommendationLabel": "⚠️ 必要文件缺漏，需主管簽核放行",
                    "applicationId": None, "supplierId": None,
                    "destination": country, "productCategory": "套裝旅遊",
                    "reviewedAt": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                    "missingRequiredDocs": [],
                },
                "riskPassReason": None,
            },
        }, "step 1→2")

        # C5: step 2→3（⭐ 帶 bankOid → 建立 supplier_settle）
        bank_payload = {
            "supplierBankOid": int(bank_oid) if bank_oid else None,
            "beneficiaryBankCountryCode": bank_data.get("bankCountryCode", "TW"),
            "collectCurrency": bank_data.get("collectCurrency", "TWD"),
            "bankName": bank_data.get("bankName", "台灣銀行"),
            "bankCode": bank_data.get("bankCode", "004"),
            "branchName": bank_data.get("branchName", "城中分行"),
            "branchCode": bank_data.get("branchCode", "0041"),
            "accountNo": bank_data.get("accountNo", ""),
            "accountName": bank_data.get("accountName", ""),
            "swiftCode": bank_data.get("swiftCode"),
            "ibanCode": bank_data.get("ibanCode"),
            "cnaps": bank_data.get("cnaps"),
            "bsbNumber": bank_data.get("bsbNumber"),
            "sknCode": bank_data.get("sknCode"),
        }
        _bm_task_draft(bm, be2_h, bd_task, admin_email, {
            "currentStep": 2, "targetStep": 3,
            "extraInfo": {
                "isDefaultPaymentPreference": True,
                "paymentPreference": {
                    "collectInfoDesc": f"QA Supplier {supplier_oid}",
                    "collectMethod": "MANUAL_REMIT",
                    "settleDateType": "02",
                    "settlePeriodMethod": "01",
                    "collectDateJson": "N_N_1",
                    "autoStmtGen": True,
                    "isKkDelegateInvoice": False,
                    "isUploadFile": False,
                    "bank": bank_payload,
                },
                "bankOcrPassReason": "TESTS",
                "ocrResult": {"items": []},
            },
        }, "step 2→3（settle）")

        # C6: step 3→4
        tomorrow = date.today() + timedelta(days=1)
        _bm_task_draft(bm, be2_h, bd_task, admin_email, {
            "currentStep": 3, "targetStep": 4,
            "extraInfo": {
                "isStandardContract": True,
                "contractPeriod": {
                    "contractStart": tomorrow.isoformat(),
                    "contractEnd": (tomorrow + timedelta(days=365)).isoformat(),
                    "contractAutoRenew": True,
                },
            },
        }, "step 3→4")

        # C7: step 4→5（⭐ handler）
        _bm_task_draft(bm, be2_h, bd_task, admin_email, {
            "currentStep": 4, "targetStep": 5,
            "extraInfo": {
                "contractDetail": {
                    "purchaseWay": "DIRECT",
                    "msgHandler": "SUPPLIER",
                    "orderHandler": "SUPPLIER",
                    "productMaintainer": "SUPPLIER",
                    "isRezioActivity": False,
                },
            },
        }, "step 4→5（handler）")

        # C8: contract-create
        _bm_contract_create(bm, be2_h, bd_task)

        # C9: BD_INIT_REVIEW_PASS
        _bm_task_action(bm, be2_h, bd_task, admin_email, "BD_INIT_REVIEW_PASS")
        log.info("BD初審通過 → 進入「等待供應商確認合作」")

    # ── Phase D: 供應商確認合約（需 SCM supplier session）──
    scm_base = _scm_api_base(env)
    if email:
        log.info("Phase D: 重新登入供應商帳號 %s ...", email)
        scm_token = _scm_relogin(env, email)
        scm_h = {
            "s-ci-sessions": scm_token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "locale": "zh-tw",
        }

        confirm_url = f"{scm_base}/external/v1/supplier/apply/contract/confirm"
        max_confirm_retries = 6
        for attempt in range(max_confirm_retries):
            try:
                resp = requests.post(confirm_url, headers=scm_h, timeout=_SCM_TIMEOUT)
                body = resp.json()
            except Exception as exc:
                if attempt < max_confirm_retries - 1:
                    log.warning("contract/confirm 連線失敗（%s），15s 後 retry", exc)
                    time.sleep(15)
                    continue
                raise
            status = body.get("metadata", {}).get("status", "")
            if status == "0000":
                log.info("Phase D: contract/confirm 成功")
                break
            if status == "9999" and attempt < max_confirm_retries - 1:
                log.warning("contract/confirm 暫態 9999（attempt %d），15s 後 retry", attempt + 1)
                time.sleep(15)
                continue
            _scm_assert_success(body, "contract/confirm")

        # D2: 等 settle 建立（contract/confirm 會觸發 settle 非同步建立）
        for _wait in range(30):
            time.sleep(5)
            settle_body = _scm_request("GET",
                                       f"{potato}/v1/suppliers/{supplier_oid}/settle-list",
                                       headers=be2_h)
            settles = settle_body.get("data", [])
            if settles:
                log.info("settle-list 已建立（%d 筆），等了 %ds",
                         len(settles), (_wait + 1) * 5)
                break
        else:
            log.warning("⚠️ 等待 150s 後 settle-list 仍為空")
    else:
        log.warning("⚠️ 未提供 email，無法執行 Phase D（contract/confirm），settle 可能為空")

    # ── Status 80: sofa-potato 合約（Java API side effect → status 80）──
    body = _scm_request("POST", f"{potato}/v1/files/supplier.attachment",
                        headers=be2_h,
                        json_body={"fileName": "qa_test_contract.pdf",
                                   "contentType": "application/pdf",
                                   "encodeString": _TINY_PDF_BASE64})
    _scm_assert_success(body, "上傳合約 PDF")
    file_oid = body["data"]["fileOid"]
    access_key = body["data"].get("accessKey", "")

    today = date.today()
    body = _scm_request("POST",
                        f"{potato}/v1/suppliers/{supplier_oid}/contracts",
                        headers=be2_h,
                        json_body={
                            "type": "KKDAY",
                            "startDate": today.isoformat(),
                            "endDate": (today + timedelta(days=365)).isoformat(),
                            "autoRenew": True,
                            "bdNote": "QA automation test contract",
                            "bpmProcess": False,
                            "topic": None, "reason": None,
                            "reviewContractFile": {
                                "fileOid": int(file_oid),
                                "fileName": "qa_test_contract.pdf",
                                "ownerParam1": access_key,
                            },
                        })
    _scm_assert_success(body, "建合約")
    contract_oid = body["data"]["supplierContractOid"]

    body = _scm_request(
        "POST",
        f"{potato}/v1/suppliers/{supplier_oid}/contracts/{contract_oid}/print/done",
        headers=be2_h,
        json_body={"supplierContractFile": {
            "fileOid": int(file_oid),
            "fileName": "qa_test_signed_contract.pdf",
            "ownerParam1": access_key,
        }})
    _scm_assert_success(body, "合約簽完")

    try:
        status_body = _scm_request("GET",
                                   f"{potato}/v1/suppliers/{supplier_oid}/",
                                   headers=be2_h)
        current_status = status_body.get("data", {}).get("status")
        if current_status and int(current_status) >= 80:
            return
    except Exception:
        pass

    for attempt in range(_APPROVE_MAX_RETRIES):
        try:
            body = _scm_request(
                "POST",
                f"{potato}/v1/suppliers/{supplier_oid}/registration-application/approve",
                headers=be2_h,
                json_body={"supplierContractOid": int(contract_oid),
                           "supplierSettleOid": None})
            _scm_assert_success(body, "核准")
            return password
        except RuntimeError as e:
            if "SUPREG0011" in str(e) and attempt < _APPROVE_MAX_RETRIES - 1:
                time.sleep(_APPROVE_RETRY_INTERVAL)
                continue
            raise

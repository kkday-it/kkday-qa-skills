"""SCM 供應商建立與啟用 — 業務邏輯模組。

server.py 的 MCP tool（create_scm_supplier / activate_scm_supplier / get_scm_otp）
呼叫這裡的 function，保持 server.py 只做 tool 註冊 + analytics。
"""

import base64
import json as _json
import os
import re
import time
import uuid
from datetime import date, timedelta
from typing import Optional

import requests

# ── 環境變數 + fallback ────────────────────────────────────────────
# key 不直接裸露，走 os.getenv 提供預設值

_SCM_DEFAULT_PASSWORD = os.getenv(
    "SCM_DEFAULT_PASSWORD", "AutomationPwd12345678"
)

_QA_GMAIL_BASE = os.getenv("QA_GMAIL_BASE", "b2c-qa-team@kkday.com")

_GMAIL_TOKEN_PATH = os.getenv(
    "GMAIL_TOKEN_PATH",
    os.path.join(
        os.path.expanduser("~"),
        "Documents", "gitHub", "kkday-QA-automation",
        "QATestData", "data", "common", "token.json",
    ),
)

_SCM_AUTH_SERVICE_KEY_SIT = os.getenv(
    "SCM_AUTH_SERVICE_KEY_SIT", "hGuTIy8HEwhEbRihRTafdy0dllGOmXNN"
)
_SCM_AUTH_SERVICE_KEY_STAGE = os.getenv(
    "SCM_AUTH_SERVICE_KEY_STAGE", "Exq9j7NRuEUcmZvMBxk8N7M23xUn3TEw"
)
_SCM_AUTH_SERVICE_KEYS = {
    "sit": _SCM_AUTH_SERVICE_KEY_SIT,
    "stage": _SCM_AUTH_SERVICE_KEY_STAGE,
}

_AUTOMATION_TOKEN = os.getenv(
    "AUTOMATION_TOKEN", "8b9dfbac-e863-4078-95e9-c2cc03abe84f"
)

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


def gmail_get_otp(recipient: str, max_wait: int = 120, poll_interval: int = 8) -> str:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not os.path.exists(_GMAIL_TOKEN_PATH):
        raise RuntimeError(
            f"Gmail token 檔案不存在: {_GMAIL_TOKEN_PATH}。"
            "請設定環境變數 GMAIL_TOKEN_PATH 指向有效的 token.json"
        )
    creds = Credentials.from_authorized_user_file(
        _GMAIL_TOKEN_PATH,
        ["https://www.googleapis.com/auth/gmail.readonly",
         "https://www.googleapis.com/auth/gmail.modify"],
    )
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    deadline = time.time() + max_wait
    query = f"to:{recipient} subject:驗證碼 in:inbox"

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


def _get_be2_credentials(env: str, base_url: str) -> tuple:
    from urllib.parse import urlsplit
    parts = urlsplit(base_url)
    secret_host = f"{parts.scheme}://{parts.hostname}"
    secret_env = "sit" if "sit" in env else env
    envs_to_try = [secret_env] if secret_env == "sit" else [secret_env, "sit"]
    for try_env in envs_to_try:
        try:
            resp = requests.get(
                f"{secret_host}:8000/api/v1/data/",
                headers={"authorization": f"Bearer {_AUTOMATION_TOKEN}"},
                params={"env": try_env, "service": "be2", "key": f"be2_{try_env}"},
                timeout=(5, 10),
            )
            resp.raise_for_status()
            data = resp.json()
            if not data:
                continue
            cred = _json.loads(data[0]["value"])
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


def _be2_admin_login(env: str, base_url: str) -> tuple:
    account, password = _get_be2_credentials(env, base_url)
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

    service_key = _SCM_AUTH_SERVICE_KEYS.get(domain)
    if not service_key:
        raise RuntimeError(f"沒有 {domain} 環境的 auth service key")

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

    return access_token, owner_uuid


# ── 公開 API（server.py 呼叫）──────────────────────────────────────

def scm_register_and_login(env: str) -> tuple:
    """註冊 + 登入 + OTP 驗證，回傳 (email, session_token)。"""
    base = _scm_api_base(env)
    local, domain_part = _QA_GMAIL_BASE.split("@")
    ts = str(int(time.time() * 1000))
    email = f"{local}+automation_test_{ts}@{domain_part}"
    device = str(uuid.uuid4())

    body = _scm_request("POST", f"{base}/external-unauth/v1/user/register",
                        json_body={"email": email, "password": _SCM_DEFAULT_PASSWORD,
                                   "confirmPassword": _SCM_DEFAULT_PASSWORD,
                                   "timezone": "Asia/Taipei"})
    _scm_assert_success(body, "註冊")

    body = _scm_request("POST", f"{base}/external-unauth/v1/auth/login",
                        json_body={"email": email, "password": _SCM_DEFAULT_PASSWORD,
                                   "device": device})
    _scm_assert_success(body, "登入")

    body = _scm_request("POST", f"{base}/external-unauth/v1/auth/two-fa/otp",
                        json_body={"email": email, "device": device})
    _scm_assert_success(body, "觸發 OTP")

    otp = gmail_get_otp(email, max_wait=120, poll_interval=8)

    body = _scm_request("POST", f"{base}/external-unauth/v1/auth/two-fa/validate",
                        json_body={"email": email, "code": otp,
                                   "device": device, "rememberMe": True})
    _scm_assert_success(body, "驗證 OTP")
    session_token = body["data"]["sessionToken"]

    return email, session_token


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
            "bankAccountType": None,
            "bankName": "台灣銀行",
            "bankCode": "004",
            "branchName": None,
            "branchCode": None,
            "accountNo": f"1234{ts_suffix}01234",
            "accountName": f"automation_{readable_ts}_台北測試旅行社",
            "bankAddress": None,
            "swiftCode": None,
            "ibanCode": None,
            "cnaps": None,
            "bsbNumber": None,
            "sknCode": None,
            "beneficiaryIdentity": None,
            "beneficiaryTelCountryCode": None,
            "beneficiaryTel": None,
            "beneficiaryEmail": email,
            "beneficiaryAddress": "信義路五段 7 號",
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
                          base_url: str) -> None:
    """BE2 審核 → ASF → 合約 → 核准，啟用供應商到 v2 狀態。"""
    potato = _sofa_potato_base(env)

    be2_token, owner_uuid = _be2_admin_login(env, base_url)
    be2_h = {"Authorization": f"Bearer {be2_token}",
             "Content-Type": "application/json", "Accept": "application/json"}

    body = _scm_request("POST", f"{potato}/v1/files/supplier.attachment",
                        headers=be2_h,
                        json_body={"fileName": "qa_test_contract.pdf",
                                   "contentType": "application/pdf",
                                   "encodeString": _TINY_PDF_BASE64})
    _scm_assert_success(body, "上傳合約 PDF")
    contract_file_oid = body["data"]["fileOid"]
    contract_access_key = body["data"].get("accessKey", "")

    body = _scm_request("POST",
                        f"{potato}/v1/suppliers/registration-application/process",
                        headers=be2_h,
                        json_body={"supplierOids": [supplier_oid],
                                   "supplierOwner": owner_uuid})
    _scm_assert_success(body, "Process 10→20")

    body = _scm_request("POST",
                        f"{potato}/v1/suppliers/{supplier_oid}/asf_summary",
                        headers=be2_h, json_body={})
    _scm_assert_success(body, "Submit ASF")

    deadline = time.time() + _ASF_POLL_TIMEOUT
    while time.time() < deadline:
        time.sleep(_ASF_POLL_INTERVAL)
        body = _scm_request("GET",
                            f"{potato}/v1/suppliers/{supplier_oid}/asf_summary",
                            headers=be2_h)
        data = body.get("data", {})
        report_list = data.get("reportList", [])
        if report_list and report_list[0].get("result") is not None:
            time.sleep(_ASF_SETTLE_WAIT)
            break

    contract_no = _SCM_COUNTRY_CONTRACT_MAP.get(country, 3)
    body = _scm_request("PATCH",
                        f"{potato}/v2/suppliers/{supplier_oid}/detail",
                        headers=be2_h,
                        json_body={"kkdayMainContractNo": contract_no,
                                   "productMaintainer": "SUPPLIER"})
    _scm_assert_success(body, "PATCH supplier detail")

    try:
        body = _scm_request("POST",
                            f"{potato}/v1/suppliers/{supplier_oid}/audit-supplier-applicant",
                            headers=be2_h,
                            json_body={"purchaseWay": "DIRECT",
                                       "productMaintainer": "SUPPLIER",
                                       "isEcShowKkdayDirect": "Y",
                                       "orderHandler": "SUPPLIER",
                                       "msgHandler": "SUPPLIER",
                                       "isRezioActivity": "false"})
        _scm_assert_success(body, "audit-supplier-applicant")
    except RuntimeError:
        pass

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
                            "topic": None,
                            "reason": None,
                            "reviewContractFile": {
                                "fileOid": int(contract_file_oid),
                                "fileName": "qa_test_contract.pdf",
                                "ownerParam1": contract_access_key,
                            },
                        })
    _scm_assert_success(body, "建合約")
    contract_oid = body["data"]["supplierContractOid"]

    body = _scm_request("POST",
                        f"{potato}/v1/suppliers/{supplier_oid}/contracts/{contract_oid}/print/done",
                        headers=be2_h,
                        json_body={"supplierContractFile": {
                            "fileOid": int(contract_file_oid),
                            "fileName": "qa_test_signed_contract.pdf",
                            "ownerParam1": contract_access_key,
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
            return
        except RuntimeError as e:
            if "SUPREG0011" in str(e) and attempt < _APPROVE_MAX_RETRIES - 1:
                time.sleep(_APPROVE_RETRY_INTERVAL)
                continue
            raise


# ── 常數 re-export（server.py 組回傳結果用）─────────────────────────

SCM_DEFAULT_PASSWORD = _SCM_DEFAULT_PASSWORD

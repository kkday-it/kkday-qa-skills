"""Kibana 唯讀 client —— 匿名 session 登入 + Elasticsearch 查詢。

sit/stage 的 Kibana（Elastic 8.19）開了 Anonymous 認證 provider，打一次
`POST /internal/security/login {providerType: anonymous}` 拿到 `sid` cookie，
就能用它打 `/internal/search/es`。**不需要帳密。**

這份是 QA framework `lib/helpers/kibana_client.py` 的獨立版：拔掉框架的 logger
與 tool_alarm 專用的高階 helper，只留 anon session 與 `search()`。這樣這個 skill
不必要求使用者有 framework clone —— 只要 python3 + requests。

    from kibana_client import KibanaClient
    c = KibanaClient.for_env("stage")
    rv = c.search({"size": 1, "query": {"match_all": {}}})
"""

from __future__ import annotations

import json
import threading
import time
from typing import Optional

import requests

_ENV_TO_KIBANA: dict[str, str] = {
    "prod": "https://kibana.kkday.com",
    "production": "https://kibana.kkday.com",
    "stage": "https://kibana.stage.kkday.com",
    "sit": "https://kibana.sit.kkday.com",
}

# `/internal/*` 端點會驗這個 header。跟著 Kibana 升版要改。
DEFAULT_KBN_VERSION = "8.19.10"

# 真正帶結構化欄位（log_label / request.uuid / system.service_name）的是這個 pattern。
DEFAULT_INDEX = "new-kklog-*"

# Kibana 預設 idleTimeout 通常 60 分鐘，這裡留 4 倍緩衝自己先換。
_SESSION_TTL_SECONDS = 15 * 60


def resolve_kibana_url(env: str) -> Optional[str]:
    """sit200~sit299 → sit；stage / stage_xxx → stage；其餘查表。"""
    if not env:
        return None
    low = env.lower()
    if low.startswith("sit"):
        return _ENV_TO_KIBANA["sit"]
    if low.startswith("stage"):
        return _ENV_TO_KIBANA["stage"]
    return _ENV_TO_KIBANA.get(low)


class KibanaClient:
    """一個 env 一個 client，共用同一張匿名 session cookie。"""

    _instances: dict[str, "KibanaClient"] = {}
    _instances_lock = threading.Lock()

    @classmethod
    def for_env(cls, env: str, kbn_version: str = DEFAULT_KBN_VERSION) -> "KibanaClient":
        key = env.lower()
        with cls._instances_lock:
            if key not in cls._instances:
                cls._instances[key] = cls(env, kbn_version=kbn_version)
            return cls._instances[key]

    def __init__(self, env: str, kbn_version: str = DEFAULT_KBN_VERSION):
        self.env = env
        self.kbn_version = kbn_version
        self.url = resolve_kibana_url(env)
        if not self.url:
            raise ValueError(f"Kibana URL not configured for env={env!r}")
        self._session = requests.Session()
        self._session.headers.update({
            "kbn-xsrf": "true",
            "kbn-version": kbn_version,
            "x-elastic-internal-origin": "Kibana",
            "content-type": "application/json",
        })
        self._last_login_at: float = 0.0
        self._lock = threading.Lock()

    def _need_login(self) -> bool:
        if not self._session.cookies.get("sid"):
            return True
        return (time.monotonic() - self._last_login_at) > _SESSION_TTL_SECONDS

    def _anon_login(self) -> None:
        resp = self._session.post(
            f"{self.url}/internal/security/login",
            data=json.dumps({
                "providerType": "anonymous",
                "providerName": "anonymous1",
                "currentURL": "/",
            }),
            timeout=15,
        )
        resp.raise_for_status()
        self._last_login_at = time.monotonic()

    def _ensure_session(self) -> None:
        with self._lock:
            if self._need_login():
                self._anon_login()

    def search(self, body: dict, index: str = DEFAULT_INDEX, timeout: int = 25) -> dict:
        """`/internal/search/es` 的薄包裝，回 ES 原生的 `rawResponse`。"""
        self._ensure_session()
        payload = {"params": {"index": index, "body": body}}
        resp = self._session.post(
            f"{self.url}/internal/search/es", data=json.dumps(payload), timeout=timeout
        )
        if resp.status_code == 401:
            # session 過期 —— 重登一次再試，失敗才往外丟。
            with self._lock:
                self._anon_login()
            resp = self._session.post(
                f"{self.url}/internal/search/es", data=json.dumps(payload), timeout=timeout
            )
        resp.raise_for_status()
        body_json = resp.json()
        return body_json.get("rawResponse") or body_json

    def logs_by_uuid(
        self, uuids: list[str], *, from_ts: str = "now-15m", to_ts: str = "now", size: int = 500
    ) -> list[dict]:
        """一或多個 `request.uuid` 的完整呼叫鏈（含下游服務）。"""
        if not uuids:
            return []
        rv = self.search({
            "size": size,
            "sort": [{"@timestamp": "asc"}],
            "query": {"bool": {"must": [
                {"terms": {"request.uuid.keyword": uuids}},
                {"range": {"@timestamp": {"gte": from_ts, "lte": to_ts}}}]}},
        })
        return [h.get("_source") or {} for h in (rv.get("hits") or {}).get("hits") or []]

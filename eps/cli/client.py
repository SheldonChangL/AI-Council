"""eps CLI 的 REST client（Story 6.1 / FR-12 / 藍圖 T4.3）。

對既有 HTTP 服務發送請求，封裝 Story 5.1/5.2 已定義的會話 contract：

- ``POST /sessions``：建立會話，回 202 ``{sessionId, status}``。
- ``GET /sessions/{id}``：取得完整會話聚合（狀態在 ``session.status``）。

僅依賴已核准的 ``httpx``。client 不持有任何業務規則或狀態機，純粹轉發 CLI
參數為 API 請求並回傳已解析的 JSON；驗證與狀態轉移仍由服務端負責。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

# CLI 預設連線位址；可由 ``EPS_API_BASE_URL`` 或 ``--base-url`` 覆寫。
DEFAULT_BASE_URL = "http://127.0.0.1:8000"

# 預設請求逾時（秒）：CLI 為互動式短請求，避免無上限等待。
DEFAULT_TIMEOUT_SECONDS = 30.0


def resolve_base_url(explicit: Optional[str] = None) -> str:
    """決定 API base URL：``--base-url`` > ``EPS_API_BASE_URL`` > 預設。"""
    if explicit:
        return explicit
    return os.environ.get("EPS_API_BASE_URL", DEFAULT_BASE_URL)


class EpsClient:
    """薄封裝的同步 REST client。

    以既有 ``httpx.Client`` 注入（``http``）或經 :meth:`connect` 自建連線。注入
    模式（測試）不擁有連線，``close``/context 結束時不關閉外部 client；自建模式
    則於 context 結束時關閉。
    """

    def __init__(self, http: httpx.Client, *, _owns: bool = False) -> None:
        self._http = http
        self._owns = _owns

    @classmethod
    def connect(
        cls,
        base_url: Optional[str] = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> "EpsClient":
        """建立連向 ``base_url`` 的 client（自建並擁有底層連線）。"""
        client = httpx.Client(base_url=resolve_base_url(base_url), timeout=timeout)
        return cls(client, _owns=True)

    def __enter__(self) -> "EpsClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """僅關閉自建連線；注入的外部 client 不在此關閉。"""
        if self._owns:
            self._http.close()

    def create_session(
        self,
        *,
        topic: str,
        max_rounds: int,
        experts: List[Dict[str, Any]],
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """``POST /sessions``：回傳已受理回應 ``{sessionId, status}``（AC-2）。"""
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        resp = self._http.post(
            "/sessions",
            json={"topic": topic, "maxRounds": max_rounds, "experts": experts},
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    def get_session(self, session_id: int) -> Dict[str, Any]:
        """``GET /sessions/{id}``：回傳完整會話聚合（AC-3）。"""
        resp = self._http.get(f"/sessions/{session_id}")
        resp.raise_for_status()
        return resp.json()


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT_SECONDS",
    "EpsClient",
    "resolve_base_url",
]

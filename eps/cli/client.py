"""eps CLI 的 REST client（Story 6.1 / FR-12 / 藍圖 T4.3）。

對既有 HTTP 服務發送請求，封裝 Story 5.1/5.2 已定義的會話 contract：

- ``POST /sessions``：建立會話，回 202 ``{sessionId, status}``。
- ``GET /sessions/{id}``：取得完整會話聚合（狀態在 ``session.status``）。

僅依賴已核准的 ``httpx``。client 不持有任何業務規則或狀態機，純粹轉發 CLI
參數為 API 請求並回傳已解析的 JSON；驗證與狀態轉移仍由服務端負責。
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

import httpx

# CLI 預設連線位址；可由 ``EPS_API_BASE_URL`` 或 ``--base-url`` 覆寫。
DEFAULT_BASE_URL = "http://127.0.0.1:8000"

# 預設請求逾時（秒）：CLI 為互動式短請求，避免無上限等待。
DEFAULT_TIMEOUT_SECONDS = 30.0


class WatchConnectionError(RuntimeError):
    """WS 事件流連線失敗（如不存在的會話被 upgrade 前以 404 拒絕）。

    封裝兩種傳輸路徑（注入的 ASGI TestClient／生產 websockets）的拒絕情形為單一
    對外型別，使 CLI 命令層無需感知底層傳輸細節。``status_code`` 於可得時帶上。
    """

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def resolve_base_url(explicit: Optional[str] = None) -> str:
    """決定 API base URL：``--base-url`` > ``EPS_API_BASE_URL`` > 預設。"""
    if explicit:
        return explicit
    return os.environ.get("EPS_API_BASE_URL", DEFAULT_BASE_URL)


def _ws_url(base_url: str, path: str) -> str:
    """將 HTTP base URL 轉為 WS URL（http→ws、https→wss）並接上 path。"""
    base = str(base_url).rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return base + path


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

    def get_report_markdown(self, session_id: int) -> str:
        """``GET /sessions/{id}/report.md``：回傳最終報告 Markdown 文字（Story 6.3）。

        CLI ``run --follow`` 於串流結束（``ReportCompleted``）後呼叫，直接取用與 Web
        同一支匯出端點的內容印至 stdout，使 CLI 與 Web 報告**共用核心、逐字一致**
        （FR-12 / AC-2）。報告未就緒（409）或會話不存在（404）由 ``raise_for_status``
        拋 ``HTTPStatusError``，供命令層轉為結構化錯誤。
        """
        resp = self._http.get(f"/sessions/{session_id}/report.md")
        resp.raise_for_status()
        return resp.text

    @contextmanager
    def stream_events(self, session_id: int) -> Iterator[Iterator[Dict[str, Any]]]:
        """訂閱 ``/sessions/{id}/events`` WS 事件流（Story 6.2 / FR-11）。

        以 context manager 產出一個逐筆解析後的事件 dict 迭代器；離開 context 即關閉
        連線（觀看端收到終態後即可 break 退出）。支援兩種傳輸：

        - 注入路徑：底層 client 具 ``websocket_connect``（Starlette ``TestClient``）→
          直接共用同一 ASGI app 的事件流（測試）。
        - 生產路徑：以 ``websockets`` 同步 client 連向自 ``base_url`` 推導的 WS URL。

        連線於 upgrade 前被拒（不存在的會話 → 404）時，統一拋
        :class:`WatchConnectionError`。
        """
        path = f"/sessions/{session_id}/events"
        ws_connect = getattr(self._http, "websocket_connect", None)
        if ws_connect is not None:
            yield from self._stream_via_testclient(ws_connect, path)
        else:
            yield from self._stream_via_websockets(path)

    def _stream_via_testclient(
        self, ws_connect: Any, path: str
    ) -> Iterator[Iterator[Dict[str, Any]]]:
        """注入路徑：經 Starlette ``TestClient.websocket_connect`` 取得事件流。"""
        # 延遲匯入，避免 CLI 在無測試框架的生產環境硬相依 starlette。
        from starlette.testclient import WebSocketDenialResponse
        from starlette.websockets import WebSocketDisconnect

        # 不存在的會話於 upgrade 前被拒，denial 於建立連線（或進入 context）時拋出。
        try:
            connection = ws_connect(path)
            ws = connection.__enter__()
        except WebSocketDenialResponse as denial:
            raise WatchConnectionError(
                f"事件流連線被拒（HTTP {denial.status_code}）",
                status_code=denial.status_code,
            ) from denial

        def _messages() -> Iterator[Dict[str, Any]]:
            try:
                while True:
                    yield ws.receive_json()
            except WebSocketDisconnect:
                return

        try:
            yield _messages()
        finally:
            connection.__exit__(None, None, None)

    def _stream_via_websockets(
        self, path: str
    ) -> Iterator[Iterator[Dict[str, Any]]]:
        """生產路徑：以 ``websockets`` 同步 client 連線並逐筆解析訊息。"""
        from websockets.exceptions import ConnectionClosed, InvalidStatus
        from websockets.sync.client import connect as ws_connect

        url = _ws_url(str(self._http.base_url), path)
        try:
            connection = ws_connect(url)
        except InvalidStatus as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise WatchConnectionError(
                f"事件流連線被拒（HTTP {status}）", status_code=status
            ) from exc

        def _messages(ws: Any) -> Iterator[Dict[str, Any]]:
            try:
                for raw in ws:
                    yield json.loads(raw)
            except ConnectionClosed:
                return

        with connection as ws:
            yield _messages(ws)


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT_SECONDS",
    "EpsClient",
    "WatchConnectionError",
    "resolve_base_url",
]

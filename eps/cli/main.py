"""eps Typer CLI 入口（Story 6.1 / 6.2 / FR-12, FR-11 / 藍圖 T4.3）。

提供子命令，皆經既有 REST/WS API 操作，CLI 本身不含業務規則：

- ``run``：以 ``POST /sessions`` 建立研討會話並印出 ``sessionId``（6.1 AC-2）；加
  ``--follow`` 則串流各輪各專家進度並於結束時印出最終報告文字（6.3 AC-1~AC-3）。
- ``status``：以 ``GET /sessions/{id}`` 查詢並印出目前狀態（6.1 AC-3）。
- ``watch``：連 ``/sessions/{id}/events`` WS，以 Rich 串流顯示各輪各專家進度
  （6.2 AC-1~AC-3）。

執行：``python -m eps.cli.main --help`` 列出子命令（6.1 AC-1）。

``--expert`` 為可重複選項，每次一位專家。值格式為 ``名稱`` 或 ``名稱=人設提示``
（以第一個 ``=`` 分隔），對應 API 的 ``{name, personaPrompt}``。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
import typer
from rich.console import Console

from eps.cli.client import EpsClient, WatchConnectionError
from eps.cli.progress import ProgressRenderer

app = typer.Typer(
    add_completion=False,
    help="eps CLI：建立研討會話並查詢狀態（FR-12）。",
)


def _make_client(base_url: Optional[str]) -> EpsClient:
    """建立 REST client；測試以 monkeypatch 注入連向測試服務的 client。"""
    return EpsClient.connect(base_url)


def _parse_expert(raw: str) -> Dict[str, Any]:
    """將 ``名稱`` 或 ``名稱=人設提示`` 解析為 API 的 expert 物件。"""
    name, sep, persona = raw.partition("=")
    name = name.strip()
    if not name:
        raise typer.BadParameter("專家名稱不可為空", param_hint="--expert")
    expert: Dict[str, Any] = {"name": name}
    if sep:
        expert["personaPrompt"] = persona
    return expert


def _fail_from_response(response: httpx.Response) -> None:
    """將 API 結構化錯誤轉為 CLI 訊息並以非零碼結束。"""
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    typer.echo(f"請求失敗（HTTP {response.status_code}）：{detail}", err=True)
    raise typer.Exit(code=1)


@app.command()
def run(
    topic: str = typer.Option(..., "--topic", help="研討議題。"),
    max_rounds: int = typer.Option(..., "--max-rounds", help="最大回合數。"),
    expert: List[str] = typer.Option(
        ...,
        "--expert",
        help="參與專家，可重複；格式為『名稱』或『名稱=人設提示』。",
    ),
    follow: bool = typer.Option(
        False,
        "--follow",
        help="建立後即串流各輪各專家進度，結束時於 stdout 印出最終報告文字。",
    ),
    base_url: Optional[str] = typer.Option(
        None, "--base-url", help="API base URL；預設讀取 EPS_API_BASE_URL。"
    ),
) -> None:
    """建立會話並印出 sessionId；``--follow`` 時串流進度並輸出最終報告（Story 6.3）。

    - 預設（無 ``--follow``）：``POST /sessions`` 建立會話後印出 ``sessionId``（6.1 AC-2）。
    - ``--follow``：建立後連 WS 串流各輪各專家進度（進度與 ``sessionId`` 寫 stderr），
      收到 ``ReportCompleted`` → 取 ``GET /sessions/{id}/report.md`` 將最終報告**文字**
      印至 stdout（與 Web 共用核心、逐字一致，AC-1/AC-2）；收到 ``SessionFailed`` →
      渲染失敗原因（含重新登入提示）並以非零碼結束，不輸出臆造報告（AC-3 / OPS-1）。
    """
    experts = [_parse_expert(e) for e in expert]
    try:
        with _make_client(base_url) as client:
            body = client.create_session(
                topic=topic, max_rounds=max_rounds, experts=experts
            )
            session_id = body["sessionId"]
            if not follow:
                typer.echo(f"sessionId: {session_id}")
                return
            _follow_session(client, session_id)
    except httpx.HTTPStatusError as exc:
        _fail_from_response(exc.response)
    except httpx.HTTPError as exc:
        typer.echo(f"無法連線到服務：{exc}", err=True)
        raise typer.Exit(code=1)


def _follow_session(client: EpsClient, session_id: int) -> None:
    """串流會話進度直到終態，完成則輸出最終報告，失敗則非零碼結束（Story 6.3）。

    進度與 ``sessionId`` 一律寫入 **stderr**（保持 stdout 純粹為最終報告文字，使
    ``--follow`` 的 stdout 與 ``GET /sessions/{id}/report.md`` 內容逐字一致，AC-2）。
    """
    # 進度走 stderr，最終報告走 stdout：兩股輸出分離（AC-1/AC-2）。
    progress_console = Console(stderr=True)
    progress_console.print(f"sessionId: {session_id}")
    renderer = ProgressRenderer(progress_console)
    try:
        with client.stream_events(session_id) as events:
            for message in events:
                if renderer.handle(message):
                    break
    except WatchConnectionError as exc:
        typer.echo(f"無法連線事件流：{exc}", err=True)
        raise typer.Exit(code=1)

    if renderer.failed:
        # OPS-1：SessionFailed 原因（含重新登入提示）已由 renderer 印至 stderr；
        # 以非零碼結束且不輸出任何（臆造的）報告。
        raise typer.Exit(code=1)

    # 完成：取與 Web 同一支匯出端點的報告文字印至 stdout（FR-12 / AC-2）。
    report = client.get_report_markdown(session_id)
    typer.echo(report)


@app.command()
def status(
    session_id: int = typer.Argument(..., help="要查詢的會話 id。"),
    base_url: Optional[str] = typer.Option(
        None, "--base-url", help="API base URL；預設讀取 EPS_API_BASE_URL。"
    ),
) -> None:
    """查詢並印出會話目前狀態（AC-3）。"""
    try:
        with _make_client(base_url) as client:
            detail = client.get_session(session_id)
    except httpx.HTTPStatusError as exc:
        _fail_from_response(exc.response)
    except httpx.HTTPError as exc:
        typer.echo(f"無法連線到服務：{exc}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"status: {detail['session']['status']}")


@app.command()
def watch(
    session_id: int = typer.Argument(..., help="要觀看的會話 id。"),
    base_url: Optional[str] = typer.Option(
        None, "--base-url", help="API base URL；預設讀取 EPS_API_BASE_URL。"
    ),
) -> None:
    """連 WS 事件流，以 Rich 串流顯示各輪各專家進度直到終態（AC-1~AC-3）。

    收到 ``ReportCompleted`` → 正常結束（exit 0）；收到 ``SessionFailed`` → 以非零碼
    結束（exit 1），不偽裝成功（OPS-1）。
    """
    console = Console()
    renderer = ProgressRenderer(console)
    try:
        with _make_client(base_url) as client:
            with client.stream_events(session_id) as events:
                for message in events:
                    if renderer.handle(message):
                        break
    except WatchConnectionError as exc:
        typer.echo(f"無法連線事件流：{exc}", err=True)
        raise typer.Exit(code=1)
    except httpx.HTTPError as exc:
        typer.echo(f"無法連線到服務：{exc}", err=True)
        raise typer.Exit(code=1)
    if renderer.failed:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()

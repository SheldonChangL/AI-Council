"""Story 6.2 — CLI 進度渲染器單元測試（AC-1, AC-2, AC-3）。

直接驗證 :class:`eps.cli.progress.ProgressRenderer` 的「事件 → 終端輸出」邏輯，
與 WS 傳輸無關：以寫入 ``StringIO`` 的 Rich console（``force_terminal=False`` →
純文字、無 ANSI）斷言輸出字串與終態旗標／回傳值。
"""

from __future__ import annotations

import io

from rich.console import Console

from eps.cli.progress import ProgressRenderer


def _renderer() -> tuple[ProgressRenderer, io.StringIO]:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=200, soft_wrap=True)
    return ProgressRenderer(console), buffer


def _event(event_type: str, **data: object) -> dict:
    return {"type": event_type, "sessionId": 1, "ts": "2026-01-01T00:00:00+00:00",
            "data": data}


# --- AC-1：RoundStarted / ExpertStarted → 輪次與發言中的專家名稱 ---
def test_round_started_renders_round_number_and_focus():
    renderer, buffer = _renderer()

    terminal = renderer.handle(_event("RoundStarted", roundNumber=2, focus="是否升息"))

    out = buffer.getvalue()
    assert terminal is False
    assert "第 2 輪" in out
    assert "是否升息" in out


def test_expert_started_renders_expert_name():
    renderer, buffer = _renderer()

    terminal = renderer.handle(
        _event("ExpertStarted", roundNumber=1, expertId=7, expertName="經濟學家")
    )

    out = buffer.getvalue()
    assert terminal is False
    assert "經濟學家" in out
    assert "發言中" in out


# --- AC-2：RoundSummary / ReportCompleted → 輪次總結與「報告完成」提示 ---
def test_round_summary_renders_summary():
    renderer, buffer = _renderer()

    terminal = renderer.handle(
        _event("RoundSummary", roundNumber=1, summary="雙方觀點趨於一致")
    )

    out = buffer.getvalue()
    assert terminal is False
    assert "第 1 輪總結" in out
    assert "雙方觀點趨於一致" in out


def test_report_completed_is_terminal_and_announces_completion():
    renderer, buffer = _renderer()

    terminal = renderer.handle(_event("ReportCompleted", report="最終報告內容"))

    assert terminal is True  # 終態：呼叫端應停止觀看。
    assert renderer.completed is True
    assert renderer.failed is False
    assert "報告完成" in buffer.getvalue()


# --- AC-3：SessionFailed → 失敗原因與是否有部分結果，不偽裝成功 ---
def test_session_failed_is_terminal_and_prints_reason_without_faking_success():
    renderer, buffer = _renderer()

    terminal = renderer.handle(
        _event(
            "SessionFailed",
            reason="CLI 未登入，請重新登入後重試",
            partialAvailable=True,
        )
    )

    out = buffer.getvalue()
    assert terminal is True
    assert renderer.failed is True
    assert renderer.completed is False  # OPS-1：不偽裝成功。
    assert "會話失敗" in out
    assert "CLI 未登入，請重新登入後重試" in out
    assert "部分結果" in out  # 有保存部分結果的提示。


def test_session_failed_without_partial_results():
    renderer, buffer = _renderer()

    renderer.handle(_event("SessionFailed", reason="重試耗盡", partialAvailable=False))

    out = buffer.getvalue()
    assert "重試耗盡" in out
    assert "無可用的部分結果" in out


# --- 非終態 / 無關事件：心跳與未呈現事件不應停止觀看 ---
def test_heartbeat_and_unhandled_events_are_non_terminal():
    renderer, _ = _renderer()

    assert renderer.handle({"type": "ping"}) is False
    assert renderer.handle(_event("ExpertCompleted", roundNumber=1, expertId=1,
                                  viewpoint="觀點")) is False
    assert renderer.completed is False
    assert renderer.failed is False

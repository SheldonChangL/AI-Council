"""eps CLI 的 WebSocket 進度渲染器（Story 6.2 / FR-12, FR-11, OPS-1）。

把「事件 → 終端呈現」的邏輯與 WS 傳輸完全分離，使其可獨立單元測試：
:meth:`ProgressRenderer.handle` 接受一則已解析的事件信封
（``{type, sessionId, ts, data}``，見 :mod:`eps.core.events`），以 Rich 輸出對應
進度，並回傳該事件是否為**終態**（收到後應停止觀看）。

對應 AC：

- AC-1：``RoundStarted`` → 目前輪次與焦點；``ExpertStarted`` → 發言中的專家名稱。
- AC-2：``RoundSummary`` → 輪次總結；``ReportCompleted`` → 「報告完成」提示（終態）。
- AC-3：``SessionFailed`` → 失敗原因與是否有部分結果，**不偽裝成功**（終態，旗標
  ``failed`` 供呼叫端以非零碼結束，OPS-1）。

其餘事件（``StatusChanged`` 作狀態提示；``ExpertCompleted`` / ``FocusUpdated`` 等）
作輕量呈現或忽略；傳輸層心跳 ``{"type": "ping"}`` 不在事件登錄表中，直接忽略。
動態內容（focus／summary／reason 等）一律以純文字附加，不經 Rich markup 解析，
避免內容中的 ``[`` 被誤判為標記。
"""

from __future__ import annotations

from typing import Any, Mapping

from rich.console import Console
from rich.text import Text


class ProgressRenderer:
    """將會話事件流逐筆渲染到 Rich console 的純呈現元件。"""

    def __init__(self, console: Console) -> None:
        self._console = console
        # 終態旗標：completed → 報告完成；failed → 會話失敗（供呼叫端決定 exit code）。
        self.completed = False
        self.failed = False

    def handle(self, message: Mapping[str, Any]) -> bool:
        """渲染一則事件信封；回傳是否為終態（``ReportCompleted`` / ``SessionFailed``）。"""
        event_type = message.get("type")
        data = message.get("data") or {}

        if event_type == "StatusChanged":
            self._render_status(data)
        elif event_type == "RoundStarted":
            self._render_round_started(data)
        elif event_type == "ExpertStarted":
            self._render_expert_started(data)
        elif event_type == "RoundSummary":
            self._render_round_summary(data)
        elif event_type == "ReportCompleted":
            self._render_report_completed(data)
            self.completed = True
            return True
        elif event_type == "SessionFailed":
            self._render_session_failed(data)
            self.failed = True
            return True
        # 其餘事件（含心跳 ping、ExpertCompleted、FocusUpdated …）不主動呈現。
        return False

    # -- 各事件呈現（靜態標籤帶樣式，動態內容以純文字附加，不經 markup） --------

    def _render_status(self, data: Mapping[str, Any]) -> None:
        status = data.get("status", "")
        self._console.print(
            Text.assemble(("狀態：", "dim"), (str(status), "dim bold"))
        )

    def _render_round_started(self, data: Mapping[str, Any]) -> None:
        round_number = data.get("roundNumber")
        focus = str(data.get("focus") or "")
        text = Text.assemble(("第 ", "bold cyan"), (str(round_number), "bold cyan"),
                             (" 輪", "bold cyan"))
        if focus:
            text.append("　焦點：")
            text.append(focus)
        self._console.print(text)

    def _render_expert_started(self, data: Mapping[str, Any]) -> None:
        name = str(data.get("expertName") or data.get("expertId") or "")
        self._console.print(
            Text.assemble(("  🗣 ", "green"), (name, "green bold"), (" 發言中…", "green"))
        )

    def _render_round_summary(self, data: Mapping[str, Any]) -> None:
        round_number = data.get("roundNumber")
        summary = str(data.get("summary") or "")
        text = Text.assemble(
            ("第 ", "yellow"), (str(round_number), "yellow"), (" 輪總結：", "yellow")
        )
        text.append(summary)
        self._console.print(text)

    def _render_report_completed(self, data: Mapping[str, Any]) -> None:
        self._console.print(Text("✅ 報告完成", style="bold green"))

    def _render_session_failed(self, data: Mapping[str, Any]) -> None:
        reason = str(data.get("reason") or "")
        partial = bool(data.get("partialAvailable"))
        # OPS-1：明確印出失敗，不偽裝成功。
        header = Text("❌ 會話失敗", style="bold red")
        self._console.print(header)
        reason_text = Text("失敗原因：", style="red")
        reason_text.append(reason)
        self._console.print(reason_text)
        if partial:
            self._console.print(
                Text("已保存部分結果，可重新登入後重試。", style="yellow")
            )
        else:
            self._console.print(Text("無可用的部分結果。", style="dim"))


__all__ = ["ProgressRenderer"]

"""LocalCliAdapter — 以本機 CLI 子行程驅動 LLM（Story 3.2 / FR-19, OPS-4）。

以 ``asyncio.create_subprocess_exec`` 啟動外部 CLI（預設 ``codex``，由
:data:`eps.config.Settings.cli_path` 決定），命令固定帶 ``--output-format
stream-json --verbose``，並由 **stdin** 餵入 prompt。

OPS-4 的核心：CLI 以 line-delimited stream-json 逐行輸出，可能含**多個**
``assistant`` 訊息（多輪），最後接一個 ``result``。本 adapter 逐行解析並
**串接全部** ``assistant.message.content[].text``，確保大型輸出的前段不被截斷
（不可只取最後一筆或 ``result``）。

錯誤分類（``invoke`` runtime，Story 3.2 AC-3）：
- 子行程非零退出且**非 auth 類** → :class:`~eps.adapters.base.TransientError`（可重試）。
- auth 類失敗（未登入／憑證失效）→ :class:`~eps.adapters.base.AuthError`（不可重試）。

來源驗證（``validate_source`` pre-flight，Story 3.3 / FR-4, OPS-1）：
- CLI 未安裝 → :class:`~eps.adapters.base.SourceError`（指示修復環境／安裝 CLI）。
- 子行程非零退出且輸出含 auth/login 關鍵字 → :class:`~eps.adapters.base.SourceError`
  （**來源類，不視為 transient**；對應 ``SessionStatus.SourceInvalid``，擋下未登入啟動）。
- 非 auth 類非零退出 → :class:`~eps.adapters.base.TransientError`（可重試）。
- 退出碼 0 → 來源有效，回傳 ``None``。
"""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Optional

from eps.adapters.base import AuthError, SourceError, TransientError
from eps.config import Settings, get_settings

# stream-json 輸出格式固定旗標（AC-2）。
STREAM_JSON_ARGS = ("--output-format", "stream-json", "--verbose")

# validate_source 的最小 auth 探測輸入。不發明新的 flag/subcommand：
# 沿用與 invoke 完全相同的呼叫旗標，僅以此最小輸入驅動 CLI 跑一輪，
# 再藉退出碼＋輸出判定 OAuth 登入狀態（Story 3.3）。
_VALIDATION_PROBE = "ping"

# auth 類失敗的辨識關鍵字（小寫比對 stderr）。命中即視為不可重試。
_AUTH_MARKERS = (
    "unauthorized",
    "authentication",
    "not logged in",
    "not authenticated",
    "invalid api key",
    "login required",
    "permission denied",
    "401",
    "403",
)


class LocalCliAdapter:
    """以本機 CLI 子行程實作的 LLM 後端（部分實作，見模組 docstring）。"""

    def __init__(
        self,
        *,
        cli_path: Optional[str] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        resolved = settings if settings is not None else get_settings()
        # 明確傳入的 cli_path 優先於設定。
        self._cli_path = cli_path if cli_path is not None else resolved.cli_path

    async def invoke(self, persona: str, focus: str) -> str:
        """以 ``persona`` 針對 ``focus`` 產生觀點，回傳串接後的 viewpoint 字串。"""
        prompt = self._build_prompt(persona, focus)
        return await self._run(prompt)

    async def validate_source(self, source_url: str) -> None:
        """驗證 CLI 安裝與 OAuth 登入狀態（Story 3.3 / FR-4, OPS-1）。

        - AC-1：CLI 執行檔不存在 → :class:`SourceError`（指示修復環境／安裝 CLI）。
        - AC-2：子行程非零退出且輸出含 auth/login 關鍵字 → :class:`SourceError`
          （來源類，**不視為 transient**）；非 auth 類非零退出 → :class:`TransientError`。
        - AC-3：CLI 已安裝且 OAuth session 有效（退出碼 0）→ 回傳 ``None``。

        ``source_url`` 目前未參與探測（OPS-1 僅要求驗證本機 CLI 與登入狀態），
        保留於簽章以符合 :class:`~eps.adapters.base.LLMAdapter` 契約。
        """
        # AC-1：CLI 未安裝（PATH 找不到、或絕對路徑非可執行）。
        if shutil.which(self._cli_path) is None:
            raise SourceError(
                f"找不到 CLI 執行檔 '{self._cli_path}'：請修復環境或安裝 CLI 後再試。"
            )

        try:
            returncode, stdout_text, stderr_text = await self._spawn(_VALIDATION_PROBE)
        except FileNotFoundError as exc:
            # which 與實際 spawn 之間的競態保底：仍視為來源（環境）問題。
            raise SourceError(
                f"找不到 CLI 執行檔 '{self._cli_path}'：請修復環境或安裝 CLI 後再試。"
            ) from exc

        # AC-3：退出碼 0 → OAuth 有效，來源可用。
        if returncode == 0:
            return None

        # AC-2：輸出（stdout+stderr）含 auth/login 關鍵字 → 來源類，不可重試。
        combined = f"{stdout_text}\n{stderr_text}".lower()
        if any(marker in combined for marker in _AUTH_MARKERS):
            raise SourceError(
                f"CLI 未登入或 OAuth session 失效（returncode={returncode}）："
                f"請重新登入後再試。{stderr_text.strip()}"
            )

        # 非 auth 類非零退出：暫時性、可重試（與 invoke 一致）。
        raise TransientError(
            f"CLI 來源驗證非零退出（returncode={returncode}）：{stderr_text.strip()}"
        )

    # ── 內部機制 ────────────────────────────────────────────────────────

    @staticmethod
    def _build_prompt(persona: str, focus: str) -> str:
        """組合餵入 stdin 的 prompt：persona（角色）在前，focus（焦點）在後。"""
        return f"{persona}\n\n{focus}"

    async def _spawn(self, prompt: str) -> tuple[Optional[int], str, str]:
        """spawn CLI 子行程、餵入 ``prompt``，回傳 ``(returncode, stdout, stderr)``。"""
        proc = await asyncio.create_subprocess_exec(
            self._cli_path,
            *STREAM_JSON_ARGS,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate(prompt.encode("utf-8"))
        stdout_text = (stdout_bytes or b"").decode("utf-8", errors="replace")
        stderr_text = (stderr_bytes or b"").decode("utf-8", errors="replace")
        return proc.returncode, stdout_text, stderr_text

    async def _run(self, prompt: str) -> str:
        """spawn CLI 子行程、餵入 prompt、解析 stream-json 並回傳串接文字。"""
        returncode, stdout_text, stderr_text = await self._spawn(prompt)
        if returncode != 0:
            self._raise_for_exit(returncode, stderr_text)
        return self._parse_stream_json(stdout_text)

    @staticmethod
    def _raise_for_exit(returncode: Optional[int], stderr_text: str) -> None:
        """依 stderr 將非零退出分類為 :class:`AuthError` 或 :class:`TransientError`。"""
        lowered = stderr_text.lower()
        if any(marker in lowered for marker in _AUTH_MARKERS):
            raise AuthError(
                f"CLI 認證失敗（returncode={returncode}）：{stderr_text.strip()}"
            )
        raise TransientError(
            f"CLI 非零退出（returncode={returncode}）：{stderr_text.strip()}"
        )

    @staticmethod
    def _parse_stream_json(stdout_text: str) -> str:
        """逐行解析 stream-json，串接所有 ``assistant`` 輪次的 text（OPS-4）。

        - 跳過空白行與無法解析的行（容錯，不因單行雜訊而中斷）。
        - 僅累積 ``type == "assistant"`` 之 ``message.content[]`` 中 ``type == "text"``
          的 ``text``，依輸出順序串接，確保前段不遺漏。
        """
        parts: list[str] = []
        for line in stdout_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("type") != "assistant":
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                ):
                    parts.append(block["text"])
        return "".join(parts)


__all__ = ["LocalCliAdapter"]

"""LocalCliAdapter — 以本機 CLI 子行程驅動 LLM（Story 3.2 / FR-19, OPS-4）。

以 ``asyncio.create_subprocess_exec`` 啟動外部 CLI（預設 ``codex``，由
:data:`eps.config.Settings.cli_path` 決定），命令固定帶 ``--output-format
stream-json --verbose``，並由 **stdin** 餵入 prompt。

OPS-4 的核心：CLI 以 line-delimited stream-json 逐行輸出，可能含**多個**
``assistant`` 訊息（多輪），最後接一個 ``result``。本 adapter 逐行解析並
**串接全部** ``assistant.message.content[].text``，確保大型輸出的前段不被截斷
（不可只取最後一筆或 ``result``）。

錯誤分類（AC-3）：
- 子行程非零退出且**非 auth 類** → :class:`~eps.adapters.base.TransientError`（可重試）。
- auth 類失敗（未登入／憑證失效）→ :class:`~eps.adapters.base.AuthError`（不可重試）。

注意：本 story 為 [prereq]，僅實作 ``invoke()`` 所需的 spawn + 解析機制；其餘
Protocol 方法留待後續 story（避免發明 prompt 契約）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from eps.adapters.base import AuthError, TransientError
from eps.config import Settings, get_settings

# stream-json 輸出格式固定旗標（AC-2）。
STREAM_JSON_ARGS = ("--output-format", "stream-json", "--verbose")

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

    # ── 內部機制 ────────────────────────────────────────────────────────

    @staticmethod
    def _build_prompt(persona: str, focus: str) -> str:
        """組合餵入 stdin 的 prompt：persona（角色）在前，focus（焦點）在後。"""
        return f"{persona}\n\n{focus}"

    async def _run(self, prompt: str) -> str:
        """spawn CLI 子行程、餵入 prompt、解析 stream-json 並回傳串接文字。"""
        proc = await asyncio.create_subprocess_exec(
            self._cli_path,
            *STREAM_JSON_ARGS,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate(prompt.encode("utf-8"))

        if proc.returncode != 0:
            stderr_text = (stderr_bytes or b"").decode("utf-8", errors="replace")
            self._raise_for_exit(proc.returncode, stderr_text)

        stdout_text = (stdout_bytes or b"").decode("utf-8", errors="replace")
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

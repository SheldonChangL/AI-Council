"""Story 3.2 — LocalCliAdapter spawn 與 stream-json 全輪次串接（AC-1, AC-2, AC-3）。

驗證：
- AC-1：逐行解析 stream-json，串接「全部」assistant 輪次的 text，前段不遺漏。
- AC-2：以 ``asyncio.create_subprocess_exec`` 啟動，命令含 ``--output-format
  stream-json --verbose``，並由 stdin 餵入 prompt（含 focus）。
- AC-3：非零退出且非 auth 類 → ``TransientError``（可重試）；auth 類 → ``AuthError``。
"""

import asyncio
import json

import pytest

from eps.adapters import (
    AuthError,
    LocalCliAdapter,
    RetryExhaustedError,
    TransientError,
)
from eps.config import Settings


class _FakeStdin:
    """模擬 ``StreamWriter``：累積 invoke 串流路徑餵入的 prompt bytes。"""

    def __init__(self) -> None:
        self.buffer = b""

    def write(self, data: bytes) -> None:
        self.buffer += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


class _FakeStream:
    """模擬 ``StreamReader``：以腳本化 bytes 提供 readline / read。"""

    def __init__(self, data: bytes) -> None:
        self._lines = data.splitlines(keepends=True)
        self._idx = 0

    async def readline(self) -> bytes:
        if self._idx >= len(self._lines):
            return b""  # EOF
        line = self._lines[self._idx]
        self._idx += 1
        return line

    async def read(self) -> bytes:
        rest = b"".join(self._lines[self._idx :])
        self._idx = len(self._lines)
        return rest


class _FakeProcess:
    """模擬 ``asyncio.subprocess.Process``：以串流 stdout/stderr 腳本化輸出。"""

    def __init__(self, *, stdout: bytes, stderr: bytes, returncode: int) -> None:
        self.stdout = _FakeStream(stdout)
        self.stderr = _FakeStream(stderr)
        self.stdin = _FakeStdin()
        self.returncode = returncode
        self.killed = False

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def _install_fake_exec(monkeypatch, proc: _FakeProcess) -> dict:
    """攔截 ``asyncio.create_subprocess_exec``，記錄呼叫引數並回傳 ``proc``。"""
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return captured


def _stream_json_lines(*events: dict) -> bytes:
    return ("\n".join(json.dumps(e) for e in events) + "\n").encode("utf-8")


def _assistant(*texts: str) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": t} for t in texts]},
    }


# AC-1：多個 assistant 訊息後接 result，串接全部 text，前段不遺漏。
async def test_concatenates_all_assistant_turns(monkeypatch):
    stdout = _stream_json_lines(
        _assistant("第一段。"),
        _assistant("第二段。", "第三段。"),
        {"type": "result", "subtype": "success"},
    )
    proc = _FakeProcess(stdout=stdout, stderr=b"", returncode=0)
    _install_fake_exec(monkeypatch, proc)

    adapter = LocalCliAdapter(cli_path="codex")
    result = await adapter.invoke("市場分析師", "焦點主題")

    assert result == "第一段。第二段。第三段。"


# AC-1：跳過空白行與無法解析的雜訊行，仍正確串接。
async def test_tolerates_blank_and_malformed_lines(monkeypatch):
    stdout = (
        b"\n"
        + json.dumps(_assistant("A")).encode("utf-8")
        + b"\nnot-json garbage\n"
        + json.dumps({"type": "system", "message": {"content": []}}).encode("utf-8")
        + b"\n"
        + json.dumps(_assistant("B")).encode("utf-8")
        + b"\n"
    )
    proc = _FakeProcess(stdout=stdout, stderr=b"", returncode=0)
    _install_fake_exec(monkeypatch, proc)

    adapter = LocalCliAdapter(cli_path="codex")
    assert await adapter.invoke("p", "f") == "AB"


# AC-2：命令含 stream-json 旗標，且 stdin 餵入含 focus 的 prompt。
async def test_command_args_and_stdin(monkeypatch):
    proc = _FakeProcess(
        stdout=_stream_json_lines(_assistant("ok")), stderr=b"", returncode=0
    )
    captured = _install_fake_exec(monkeypatch, proc)

    adapter = LocalCliAdapter(cli_path="my-cli")
    await adapter.invoke("某 persona", "某 focus")

    args = captured["args"]
    assert args[0] == "my-cli"
    assert "--output-format" in args
    assert args[args.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in args
    # stdin 以 PIPE 開啟，且 prompt 內含 persona 與 focus。
    assert captured["kwargs"]["stdin"] is asyncio.subprocess.PIPE
    fed = proc.stdin.buffer.decode("utf-8")
    assert "某 focus" in fed
    assert "某 persona" in fed


# AC-2：cli_path 未明確傳入時，採用 Settings.cli_path。
async def test_cli_path_defaults_to_settings(monkeypatch):
    proc = _FakeProcess(
        stdout=_stream_json_lines(_assistant("ok")), stderr=b"", returncode=0
    )
    captured = _install_fake_exec(monkeypatch, proc)

    adapter = LocalCliAdapter(settings=Settings(cli_path="codex-from-settings"))
    await adapter.invoke("p", "f")

    assert captured["args"][0] == "codex-from-settings"


# AC-3：非零退出且非 auth 類 → 內部分類為可重試的 TransientError。
# Story 3.4 起 invoke 在重試耗盡後改拋 RetryExhaustedError，底層分類保留於 __cause__。
# 此處以 max_retries=0 隔離單次嘗試，專注驗證「非 auth 非零 → TransientError」分類。
async def test_nonzero_exit_classified_transient(monkeypatch):
    proc = _FakeProcess(
        stdout=b"", stderr=b"network unreachable, try again", returncode=1
    )
    _install_fake_exec(monkeypatch, proc)

    adapter = LocalCliAdapter(cli_path="codex", max_retries=0)
    with pytest.raises(RetryExhaustedError) as excinfo:
        await adapter.invoke("p", "f")
    assert isinstance(excinfo.value.__cause__, TransientError)


# AC-3：auth 類失敗 → 不可重試的 AuthError（非 TransientError）。
async def test_auth_failure_raises_auth_error(monkeypatch):
    proc = _FakeProcess(
        stdout=b"", stderr=b"Error: 401 Unauthorized - not logged in", returncode=1
    )
    _install_fake_exec(monkeypatch, proc)

    adapter = LocalCliAdapter(cli_path="codex")
    with pytest.raises(AuthError):
        await adapter.invoke("p", "f")
    # AuthError 不應被當作可重試的 TransientError。
    assert not issubclass(AuthError, TransientError)

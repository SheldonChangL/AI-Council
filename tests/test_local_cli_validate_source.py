"""Story 3.3 — LocalCliAdapter 來源驗證與 auth 偵測（AC-1, AC-2, AC-3）。

驗證：
- AC-1：CLI 路徑不存在 → ``SourceError``，訊息指示修復環境／安裝 CLI。
- AC-2：子行程非零退出且輸出含 auth/login 關鍵字 → ``SourceError``（來源類），
  且**非** ``TransientError``（不視為 transient）；非 auth 類非零 → ``TransientError``。
- AC-3：CLI 已安裝且 OAuth session 有效（退出碼 0）→ 回傳 ``None``（valid）。
"""

import asyncio

import pytest

from eps.adapters import (
    LocalCliAdapter,
    SourceError,
    TransientError,
)
from eps.adapters import local_cli


class _FakeProcess:
    """模擬 ``asyncio.subprocess.Process``：communicate 回傳腳本化輸出。"""

    def __init__(self, *, stdout: bytes, stderr: bytes, returncode: int) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self, input: bytes | None = None):  # noqa: A002
        return self._stdout, self._stderr


def _install_fake_exec(monkeypatch, proc) -> None:
    async def fake_exec(*args, **kwargs):
        if isinstance(proc, BaseException):
            raise proc
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


def _install_which(monkeypatch, result: str | None) -> None:
    monkeypatch.setattr(local_cli.shutil, "which", lambda cmd: result)


# AC-1：CLI 執行檔不存在（which 回傳 None）→ SourceError，訊息含安裝/環境指示。
async def test_missing_cli_raises_source_error(monkeypatch):
    _install_which(monkeypatch, None)

    adapter = LocalCliAdapter(cli_path="nonexistent-cli")
    with pytest.raises(SourceError) as excinfo:
        await adapter.validate_source("https://example.com")

    message = str(excinfo.value)
    assert "nonexistent-cli" in message
    assert "安裝" in message or "環境" in message


# AC-1（保底）：which 通過但實際 spawn 競態拋 FileNotFoundError → 仍為 SourceError。
async def test_spawn_filenotfound_maps_to_source_error(monkeypatch):
    _install_which(monkeypatch, "/usr/local/bin/codex")
    _install_fake_exec(monkeypatch, FileNotFoundError("no such file"))

    adapter = LocalCliAdapter(cli_path="codex")
    with pytest.raises(SourceError):
        await adapter.validate_source("https://example.com")


# AC-2：非零退出且 stderr 含 auth 關鍵字 → SourceError（來源類），非 transient。
async def test_auth_failure_raises_source_error_not_transient(monkeypatch):
    _install_which(monkeypatch, "/usr/local/bin/codex")
    proc = _FakeProcess(
        stdout=b"", stderr=b"Error: 401 Unauthorized - not logged in", returncode=1
    )
    _install_fake_exec(monkeypatch, proc)

    adapter = LocalCliAdapter(cli_path="codex")
    with pytest.raises(SourceError):
        await adapter.validate_source("https://example.com")
    # SourceError 不應為可重試的 TransientError。
    assert not issubclass(SourceError, TransientError)


# AC-2：auth 關鍵字出現在 stdout（非 stderr）亦判為 SourceError（措辭為「輸出」）。
async def test_auth_marker_in_stdout_raises_source_error(monkeypatch):
    _install_which(monkeypatch, "/usr/local/bin/codex")
    proc = _FakeProcess(
        stdout=b"login required: please authenticate", stderr=b"", returncode=2
    )
    _install_fake_exec(monkeypatch, proc)

    adapter = LocalCliAdapter(cli_path="codex")
    with pytest.raises(SourceError):
        await adapter.validate_source("https://example.com")


# AC-2 對照：非 auth 類非零退出 → TransientError（可重試），與來源類區隔。
async def test_nonauth_nonzero_raises_transient(monkeypatch):
    _install_which(monkeypatch, "/usr/local/bin/codex")
    proc = _FakeProcess(
        stdout=b"", stderr=b"network unreachable, try again", returncode=1
    )
    _install_fake_exec(monkeypatch, proc)

    adapter = LocalCliAdapter(cli_path="codex")
    with pytest.raises(TransientError):
        await adapter.validate_source("https://example.com")


# AC-3：CLI 已安裝且退出碼 0（OAuth 有效）→ 回傳 None（valid）。
async def test_valid_source_returns_none(monkeypatch):
    _install_which(monkeypatch, "/usr/local/bin/codex")
    proc = _FakeProcess(stdout=b'{"type":"result"}\n', stderr=b"", returncode=0)
    _install_fake_exec(monkeypatch, proc)

    adapter = LocalCliAdapter(cli_path="codex")
    assert await adapter.validate_source("https://example.com") is None

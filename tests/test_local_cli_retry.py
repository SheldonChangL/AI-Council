"""Story 3.4 — LocalCliAdapter 逾時與重試策略（AC-1, AC-2, AC-3）。

驗證：
- AC-1：soft cap 內無新串流輸出 → stall 逾時（``AdapterTimeout``），進入重試流程；
  計時以「無新輸出」為準（readline 卡住即觸發），而非總時長。
- AC-2：``TransientError`` 以指數退避最多重試 ``max_retries`` 次；耗盡後拋
  ``RetryExhaustedError``（``__cause__`` 保留底層失敗）。
- AC-3：``SourceError`` / ``AuthError`` 不自動重試，直接向上回報。
"""

import asyncio
import json

import pytest

from eps.adapters import (
    AdapterTimeout,
    AuthError,
    LocalCliAdapter,
    RetryExhaustedError,
    SourceError,
    TransientError,
)


def _stream_json_lines(*events: dict) -> bytes:
    return ("\n".join(json.dumps(e) for e in events) + "\n").encode("utf-8")


def _assistant(*texts: str) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": t} for t in texts]},
    }


class _FakeStdin:
    def __init__(self) -> None:
        self.buffer = b""

    def write(self, data: bytes) -> None:
        self.buffer += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


class _FakeStream:
    def __init__(self, data: bytes) -> None:
        self._lines = data.splitlines(keepends=True)
        self._idx = 0

    async def readline(self) -> bytes:
        if self._idx >= len(self._lines):
            return b""
        line = self._lines[self._idx]
        self._idx += 1
        return line

    async def read(self) -> bytes:
        rest = b"".join(self._lines[self._idx :])
        self._idx = len(self._lines)
        return rest


class _StallStream:
    """stdout 模擬：readline 永不返回，用於觸發 stall 逾時（AC-1）。"""

    async def readline(self) -> bytes:
        await asyncio.Event().wait()  # 永遠不會被 set，僅由 wait_for 取消
        return b""  # pragma: no cover

    async def read(self) -> bytes:
        return b""


class _FakeProcess:
    def __init__(self, *, stdout, stderr: bytes, returncode: int) -> None:
        self.stdout = stdout if not isinstance(stdout, bytes) else _FakeStream(stdout)
        self.stderr = _FakeStream(stderr)
        self.stdin = _FakeStdin()
        self.returncode = returncode
        self.killed = False

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def _install_proc_sequence(monkeypatch, procs: list) -> dict:
    """攔截 create_subprocess_exec，依序回傳腳本化 proc，並記錄呼叫次數。"""
    state = {"calls": 0, "procs": []}

    async def fake_exec(*args, **kwargs):
        idx = state["calls"]
        proc = procs[idx] if idx < len(procs) else procs[-1]
        state["calls"] += 1
        state["procs"].append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return state


# ── AC-2：指數退避、重試耗盡 ──────────────────────────────────────────────


# 非 auth 非零退出 → TransientError；重試耗盡後拋 RetryExhaustedError（cause 保留）。
async def test_transient_retries_then_raises_failed(monkeypatch):
    procs = [
        _FakeProcess(stdout=b"", stderr=b"network unreachable", returncode=1)
        for _ in range(3)
    ]
    state = _install_proc_sequence(monkeypatch, procs)

    adapter = LocalCliAdapter(
        cli_path="codex", max_retries=2, retry_backoff_base_seconds=0
    )
    with pytest.raises(RetryExhaustedError) as excinfo:
        await adapter.invoke("p", "f")

    assert isinstance(excinfo.value.__cause__, TransientError)
    # 初次 + 2 次重試 = 3 次嘗試。
    assert state["calls"] == 3


# 指數退避：第 n 次重試前等待 base * 2**n（驗證 [base, 2*base]）。
async def test_exponential_backoff_delays(monkeypatch):
    procs = [
        _FakeProcess(stdout=b"", stderr=b"temporary failure", returncode=1)
        for _ in range(3)
    ]
    _install_proc_sequence(monkeypatch, procs)

    delays: list[float] = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    adapter = LocalCliAdapter(
        cli_path="codex", max_retries=2, retry_backoff_base_seconds=1.0
    )
    with pytest.raises(RetryExhaustedError):
        await adapter.invoke("p", "f")

    assert delays == [1.0, 2.0]


# 重試後成功：首次 transient，第二次回退出碼 0 並回傳串接文字。
async def test_transient_then_success(monkeypatch):
    good = _FakeProcess(
        stdout=_stream_json_lines(_assistant("成功輸出")), stderr=b"", returncode=0
    )
    procs = [
        _FakeProcess(stdout=b"", stderr=b"temporary failure", returncode=1),
        good,
    ]
    state = _install_proc_sequence(monkeypatch, procs)

    adapter = LocalCliAdapter(
        cli_path="codex", max_retries=2, retry_backoff_base_seconds=0
    )
    result = await adapter.invoke("p", "f")

    assert result == "成功輸出"
    assert state["calls"] == 2


# ── AC-1：stall 逾時（以「無新輸出」計時）並進入重試 ──────────────────────


# soft cap 內無新輸出 → AdapterTimeout（stall），重試耗盡後 RetryExhaustedError。
async def test_stall_timeout_retries_then_failed(monkeypatch):
    procs = [
        _FakeProcess(stdout=_StallStream(), stderr=b"", returncode=None)
        for _ in range(3)
    ]
    state = _install_proc_sequence(monkeypatch, procs)

    adapter = LocalCliAdapter(
        cli_path="codex",
        max_retries=2,
        retry_backoff_base_seconds=0,
        stall_timeout_seconds=0.01,
    )
    with pytest.raises(RetryExhaustedError) as excinfo:
        await adapter.invoke("p", "f")

    assert isinstance(excinfo.value.__cause__, AdapterTimeout)
    assert state["calls"] == 3
    # stall 後子行程必須被終止（不可洩漏 process）。
    assert all(p.killed for p in state["procs"])


# stall 後一次成功：首次卡住逾時，重試取得正常輸出。
async def test_stall_then_success(monkeypatch):
    good = _FakeProcess(
        stdout=_stream_json_lines(_assistant("恢復")), stderr=b"", returncode=0
    )
    procs = [
        _FakeProcess(stdout=_StallStream(), stderr=b"", returncode=None),
        good,
    ]
    state = _install_proc_sequence(monkeypatch, procs)

    adapter = LocalCliAdapter(
        cli_path="codex",
        max_retries=2,
        retry_backoff_base_seconds=0,
        stall_timeout_seconds=0.01,
    )
    assert await adapter.invoke("p", "f") == "恢復"
    assert state["calls"] == 2


# ── AC-3：SourceError / AuthError 不自動重試 ──────────────────────────────


# SourceError → 不重試，直接向上回報（僅嘗試一次）。
async def test_source_error_not_retried(monkeypatch):
    calls = {"n": 0}

    async def fake_run(self, prompt):
        calls["n"] += 1
        raise SourceError("來源失效")

    monkeypatch.setattr(LocalCliAdapter, "_run", fake_run)

    adapter = LocalCliAdapter(
        cli_path="codex", max_retries=2, retry_backoff_base_seconds=0
    )
    with pytest.raises(SourceError):
        await adapter.invoke("p", "f")

    assert calls["n"] == 1


# AuthError（不可重試）→ 直接向上回報（僅嘗試一次）。
async def test_auth_error_not_retried(monkeypatch):
    proc = _FakeProcess(
        stdout=b"", stderr=b"401 Unauthorized - not logged in", returncode=1
    )
    state = _install_proc_sequence(monkeypatch, [proc])

    adapter = LocalCliAdapter(
        cli_path="codex", max_retries=2, retry_backoff_base_seconds=0
    )
    with pytest.raises(AuthError):
        await adapter.invoke("p", "f")

    assert state["calls"] == 1

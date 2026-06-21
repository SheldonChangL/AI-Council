"""Story 6.3 — 使用者用 CLI 啟動會話、串流進度並輸出最終報告（AC-1, AC-2, AC-3）。

垂直切片端到端（vertical slice e2e）：以**真實入口** subprocess 啟動
``uvicorn eps.main:app``（注入 ``EPS_ADAPTER=fake`` 的決定性 ``FakeAdapter``），再以
**真實入口** ``python -m eps.cli.main run ... --follow`` 跑完整條 CLI 流程，經生產
``websockets`` 傳輸路徑串流 WS 進度、並取同一支 ``GET /sessions/{id}/report.md`` 匯出
端點的報告文字。未以 ``TestClient`` 注入繞過 runtime（符合全域 DoD vertical 要求）。

決定性把關：背景任務可能搶在 CLI 完成 WS 訂閱前就推進完畢，導致遺漏自 ``Running``
起的進度事件。故以 ``EPS_FAKE_VALIDATE_DELAY`` 讓 ``validate_source`` 延遲放行，給
CLI 充裕時間先建立 WS 訂閱（等同 in-process 測試 ``_GatedAdapter`` 的把關）。

- AC-1：``run --topic ... --max-rounds 2 --expert ... --follow`` 串流各輪各專家進度，
  並於結束時在 stdout 印出最終報告文字。
- AC-2：CLI 取得的最終報告（stdout）與 ``GET /sessions/{id}/report.md`` 逐字一致。
- AC-3：注入回 ``SourceError`` 的 adapter → CLI 以非零碼結束並印出重新登入提示，
  stdout 不輸出任何臆造報告（OPS-1）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from contextlib import contextmanager
from typing import Iterator

import httpx

TOPIC = "是否導入新框架"
EXPERTS = ["市場分析師", "技術架構師"]
MAX_ROUNDS = 2

# Story 6.3 / AC-1 指定整合測試以 8723 啟動服務；AC-3 用相鄰埠避免拆除重疊。
SUCCESS_PORT = 8723
FAILURE_PORT = 8724


def _wait_health(base_url: str, *, timeout: float = 20.0) -> None:
    """輪詢 ``/health`` 直到服務就緒；逾時即失敗（避免測試對未啟動服務發指令）。"""
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/health", timeout=1.0)
            if resp.status_code == 200:
                return
        except httpx.HTTPError as exc:  # 服務尚未開埠
            last_exc = exc
        time.sleep(0.1)
    raise RuntimeError(f"服務未在 {timeout}s 內就緒：{last_exc}")


@contextmanager
def _server(tmp_path, port: int, **extra_env: str) -> Iterator[str]:
    """以 subprocess 啟動真實 ``uvicorn eps.main:app``（注入 FakeAdapter），就緒後產出 base_url。"""
    env = {
        **os.environ,
        "EPS_DB_URL": f"sqlite:///{tmp_path / 'eps.db'}",
        "EPS_ADAPTER": "fake",
        # 讓 CLI 有時間先建立 WS 訂閱，再放行來源驗證（見模組 docstring）。
        "EPS_FAKE_VALIDATE_DELAY": "1.0",
        # 縮短 WS 閒置心跳，避免關鍵事件間久候（ping 由渲染器忽略）。
        "EPS_WS_HEARTBEAT_SECONDS": "0.1",
        **extra_env,
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "eps.main:app", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        try:
            _wait_health(base_url)
        except RuntimeError:
            proc.terminate()
            out = proc.communicate(timeout=5)[0]
            raise RuntimeError(f"uvicorn 啟動失敗：\n{out}")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _run_cli(base_url: str, *extra_args: str) -> subprocess.CompletedProcess:
    """以真實入口 ``python -m eps.cli.main run --follow`` 執行 CLI。"""
    args = [
        sys.executable,
        "-m",
        "eps.cli.main",
        "run",
        "--topic",
        TOPIC,
        "--max-rounds",
        str(MAX_ROUNDS),
        "--base-url",
        base_url,
        "--follow",
        *extra_args,
    ]
    for name in EXPERTS:
        args.extend(["--expert", name])
    return subprocess.run(args, capture_output=True, text=True, timeout=30)


def _session_id_from_stderr(stderr: str) -> int:
    """自 CLI stderr 解析 ``sessionId: N``（follow 模式將 sessionId 寫 stderr）。"""
    for line in stderr.splitlines():
        if line.strip().startswith("sessionId:"):
            return int(line.split("sessionId:")[1].strip())
    raise AssertionError(f"stderr 未含 sessionId：\n{stderr}")


# --- AC-1 / AC-2：串流各輪各專家進度，結束於 stdout 印出最終報告，且與 report.md 一致 ---
def test_run_follow_streams_progress_and_prints_matching_report(tmp_path):
    with _server(tmp_path, SUCCESS_PORT) as base_url:
        result = _run_cli(base_url)

        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

        # AC-1：各輪輪次與發言中的專家名稱串流於 stderr。
        err = result.stderr
        assert "第 1 輪" in err
        assert "第 2 輪" in err
        for name in EXPERTS:
            assert name in err
        assert "發言中" in err
        assert "報告完成" in err

        # AC-1：最終報告文字印於 stdout（非空）。
        assert result.stdout.strip()

        # AC-2：CLI 報告（stdout）與匯出端點 report.md 逐字一致。
        session_id = _session_id_from_stderr(err)
        resp = httpx.get(f"{base_url}/sessions/{session_id}/report.md", timeout=5)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/markdown")
        assert result.stdout.strip() == resp.text.strip()


# --- AC-3：來源失效 → 非零碼 + 重新登入提示，stdout 不輸出臆造報告（OPS-1） ---
def test_run_follow_source_error_exits_nonzero_with_relogin_and_no_report(tmp_path):
    with _server(
        tmp_path,
        FAILURE_PORT,
        EPS_FAKE_SOURCE_ERROR="CLI 未登入或 OAuth session 失效",
    ) as base_url:
        result = _run_cli(base_url)

        assert result.returncode != 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        # 失敗原因含重新登入提示（OPS-1 重新登入引導）。
        assert "重新登入" in result.stderr
        assert "會話失敗" in result.stderr
        # OPS-1：不偽裝成功，stdout 不輸出任何報告文字。
        assert result.stdout.strip() == ""

        # 服務端亦未產出報告：匯出回 409 REPORT_NOT_READY。
        session_id = _session_id_from_stderr(result.stderr)
        resp = httpx.get(f"{base_url}/sessions/{session_id}/report.md", timeout=5)
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "REPORT_NOT_READY"

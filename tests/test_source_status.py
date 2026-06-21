"""Story 3.5 — 使用者可查詢 LLM 來源是否就緒（AC-1, AC-2, AC-3）。

驗證對運行中的 FastAPI 服務發出 HTTP 請求的行為：
- AC-1：注入真實 `LocalCliAdapter`，`GET /source/status` 回 200，body 含
  `{"valid": bool, "reason": ...}`，由 `validate_source()` 真實判定。
- AC-2：注入回傳 `SourceError` 的 `FakeAdapter` → `valid=false`，`reason` 含
  修復／重新登入提示。
- AC-3：注入 valid 的 `FakeAdapter` → `valid=true`。
"""

import pytest
from fastapi.testclient import TestClient

import eps.config as config
import eps.main as main
from eps.adapters import FakeAdapter, LocalCliAdapter, SourceError
from eps.api.routes import get_adapter


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """將 `EPS_DB_URL` 導向 tmp 檔，避免在 repo 產生 SQLite 檔。"""
    db_file = tmp_path / "eps.db"
    monkeypatch.setenv("EPS_DB_URL", f"sqlite:///{db_file}")
    config.get_settings.cache_clear()
    try:
        yield db_file
    finally:
        config.get_settings.cache_clear()


@pytest.fixture
def use_adapter(isolated_db):
    """以指定 adapter 覆寫 `get_adapter` 依賴，並在結束時清除覆寫。"""

    def _apply(adapter):
        main.app.dependency_overrides[get_adapter] = lambda: adapter
        return TestClient(main.app)

    yield _apply
    main.app.dependency_overrides.pop(get_adapter, None)


# --- AC-1：真實 LocalCliAdapter 注入，回 200 且 body 形狀正確 ---
def test_source_status_with_real_local_cli_adapter(isolated_db):
    """lifespan 注入真實 LocalCliAdapter；端點以 validate_source() 真實判定。"""
    with TestClient(main.app) as client:
        resp = client.get("/source/status")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"valid", "reason"}
    assert isinstance(body["valid"], bool)
    # 真實判定：valid 為 True/False 皆可；False 時必帶 reason 訊息。
    if body["valid"] is False:
        assert body["reason"]


def test_lifespan_injects_local_cli_adapter(isolated_db):
    """AC-1：服務啟動時 app.state.adapter 為真實 LocalCliAdapter。"""
    with TestClient(main.app):
        assert isinstance(main.app.state.adapter, LocalCliAdapter)


# --- AC-2：FakeAdapter 拋 SourceError → valid=false，reason 含修復/登入提示 ---
def test_source_status_invalid_when_source_error(use_adapter):
    fake = FakeAdapter(
        source_error=SourceError("CLI 未登入或 OAuth session 失效：請重新登入後再試。")
    )
    client = use_adapter(fake)

    resp = client.get("/source/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert "重新登入" in body["reason"]
    # 確認確實呼叫了 validate_source 真實判定。
    assert any(call[0] == "validate_source" for call in fake.calls)


# --- AC-3：valid 的 FakeAdapter → valid=true，reason 為 None ---
def test_source_status_valid_when_adapter_ok(use_adapter):
    fake = FakeAdapter()  # 未腳本化錯誤 → validate_source 返回 None（有效）。
    client = use_adapter(fake)

    resp = client.get("/source/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["reason"] is None

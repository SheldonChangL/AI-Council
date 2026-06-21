"""Story 1.2 — 套件骨架與 config（AC-1, AC-2, AC-3）。"""

import ast
import importlib
from pathlib import Path

import pytest

import eps
from eps.config import (
    DEFAULT_CLI_PATH,
    DEFAULT_DB_URL,
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_BACKOFF_BASE_SECONDS,
    DEFAULT_STALL_TIMEOUT_SECONDS,
    DEFAULT_WS_HEARTBEAT_SECONDS,
    Settings,
)

SUBPACKAGES = ["api", "core", "adapters", "data", "cli"]
EPS_ROOT = Path(eps.__file__).parent


# AC-1: 各子套件存在且含 __init__.py，可被 import。
@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_exists_and_importable(name):
    init_file = EPS_ROOT / name / "__init__.py"
    assert init_file.is_file(), f"缺少 eps/{name}/__init__.py"
    assert importlib.import_module(f"eps.{name}") is not None


# AC-2: 預設值。
def test_settings_defaults():
    settings = Settings.from_env(environ={})
    assert settings.db_url == DEFAULT_DB_URL
    assert settings.db_url.startswith("sqlite:")
    assert settings.cli_path == DEFAULT_CLI_PATH
    assert settings.max_concurrency == DEFAULT_MAX_CONCURRENCY
    assert settings.max_concurrency < 10
    # Story 3.4：逾時與重試策略預設值。
    assert settings.stall_timeout_seconds == DEFAULT_STALL_TIMEOUT_SECONDS
    assert settings.max_retries == DEFAULT_MAX_RETRIES
    assert settings.retry_backoff_base_seconds == DEFAULT_RETRY_BACKOFF_BASE_SECONDS
    # Story 5.5：WS 閒置心跳間隔預設值。
    assert settings.ws_heartbeat_seconds == DEFAULT_WS_HEARTBEAT_SECONDS


# AC-2: 環境變數覆寫。
def test_settings_reads_env_overrides():
    env = {
        "EPS_DB_URL": "sqlite:////tmp/custom.db",
        "EPS_CLI_PATH": "/usr/local/bin/codex",
        "EPS_MAX_CONCURRENCY": "8",
        "EPS_STALL_TIMEOUT_SECONDS": "120",
        "EPS_MAX_RETRIES": "3",
        "EPS_RETRY_BACKOFF_BASE_SECONDS": "0.5",
        "EPS_WS_HEARTBEAT_SECONDS": "15",
    }
    settings = Settings.from_env(environ=env)
    assert settings.db_url == "sqlite:////tmp/custom.db"
    assert settings.cli_path == "/usr/local/bin/codex"
    assert settings.max_concurrency == 8
    assert settings.stall_timeout_seconds == 120.0
    assert settings.max_retries == 3
    assert settings.retry_backoff_base_seconds == 0.5
    assert settings.ws_heartbeat_seconds == 15.0


def test_settings_rejects_concurrency_over_limit():
    with pytest.raises(ValueError):
        Settings.from_env(environ={"EPS_MAX_CONCURRENCY": "10"})


def test_settings_rejects_non_integer_concurrency():
    with pytest.raises(ValueError):
        Settings.from_env(environ={"EPS_MAX_CONCURRENCY": "abc"})


# Story 3.4：逾時與重試設定的型別與範圍驗證。
def test_settings_rejects_non_positive_stall_timeout():
    with pytest.raises(ValueError):
        Settings.from_env(environ={"EPS_STALL_TIMEOUT_SECONDS": "0"})


def test_settings_rejects_negative_max_retries():
    with pytest.raises(ValueError):
        Settings.from_env(environ={"EPS_MAX_RETRIES": "-1"})


def test_settings_rejects_non_numeric_backoff():
    with pytest.raises(ValueError):
        Settings.from_env(environ={"EPS_RETRY_BACKOFF_BASE_SECONDS": "abc"})


# Story 5.5：WS 心跳間隔須 > 0。
def test_settings_rejects_non_positive_ws_heartbeat():
    with pytest.raises(ValueError):
        Settings.from_env(environ={"EPS_WS_HEARTBEAT_SECONDS": "0"})


# AC-3: eps 套件內不得使用相對 import。
def test_no_relative_imports_in_eps():
    offenders = []
    for path in EPS_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level > 0:
                offenders.append(f"{path}:{node.lineno}")
    assert not offenders, f"發現相對 import：{offenders}"

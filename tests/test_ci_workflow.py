"""Story 1.4 — CI workflow 依序執行整合 gate（AC-3）。

驗證 `.github/workflows/` 內存在 CI 設定，且依序包含安裝、`alembic upgrade head`、
`pytest`、Uvicorn 啟動 smoke 四個步驟。不引入 YAML 依賴，以文字內容與出現順序檢查。
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
CI_WORKFLOW = WORKFLOW_DIR / "ci.yml"


def test_workflow_dir_has_ci_config():
    assert CI_WORKFLOW.is_file(), ".github/workflows/ci.yml 必須存在"


@pytest.fixture
def ci_text():
    return CI_WORKFLOW.read_text(encoding="utf-8")


# 整合 gate 的四個步驟標記，依執行順序排列。
ORDERED_STEPS = [
    "uv sync",
    "alembic upgrade head",
    "pytest",
    "uvicorn eps.main:app --port 8723",
]


@pytest.mark.parametrize("marker", ORDERED_STEPS)
def test_step_present(ci_text, marker):
    assert marker in ci_text, f"CI 缺少步驟：{marker}"


def test_steps_in_order(ci_text):
    positions = [ci_text.index(marker) for marker in ORDERED_STEPS]
    assert positions == sorted(positions), "CI 步驟順序必須為 install → migrate → test → smoke"


def test_smoke_checks_health(ci_text):
    assert "/health" in ci_text, "啟動 smoke 必須檢查 /health 端點"

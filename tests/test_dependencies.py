"""Story 1.1 — 驗證依賴宣告與套件命名（AC-1, AC-3）。"""

import importlib
import pkgutil
import sys

import pytest

import eps

# AC-1: 所有宣告的執行/測試依賴都應可 import。
REQUIRED_RUNTIME_DEPS = [
    "fastapi",
    "uvicorn",
    "sqlmodel",
    "alembic",
    "pydantic",
    "typer",
    "rich",
    "httpx",
    "websockets",
]

REQUIRED_DEV_DEPS = [
    "pytest",
    "pytest_asyncio",
]


@pytest.mark.parametrize("name", REQUIRED_RUNTIME_DEPS + REQUIRED_DEV_DEPS)
def test_required_dependency_importable(name):
    assert importlib.import_module(name) is not None


def test_eps_root_package_importable():
    # AC-2: root package `eps` 可由 pythonpath 解析。
    assert eps.__version__


def test_no_eps_subpackage_shadows_stdlib():
    # AC-3: eps/ 子套件不可與標準庫同名。
    stdlib_names = set(sys.stdlib_module_names)
    collisions = [
        mod.name
        for mod in pkgutil.iter_modules(eps.__path__)
        if mod.name in stdlib_names
    ]
    assert not collisions, f"eps 子套件與標準庫同名: {collisions}"

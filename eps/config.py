"""eps 集中設定來源（Story 1.2 / AC-2）。

僅依賴標準庫讀取環境變數，避免引入未核准依賴（`pydantic-settings`）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Mapping

# 預設值：可被對應環境變數覆寫。
DEFAULT_DB_URL = "sqlite:///./eps.db"
DEFAULT_CLI_PATH = "codex"
DEFAULT_MAX_CONCURRENCY = 5

# NFR-4 / 藍圖 §3.1：並發上限必須小於 10。
MAX_CONCURRENCY_LIMIT = 10


@dataclass(frozen=True)
class Settings:
    """集中設定，預設值可由環境變數覆寫。

    - ``EPS_DB_URL``：資料庫連線字串，預設為 SQLite 檔。
    - ``EPS_CLI_PATH``：外部 CLI 執行路徑。
    - ``EPS_MAX_CONCURRENCY``：最大並發數，須為 1..<10 的整數。
    """

    db_url: str = DEFAULT_DB_URL
    cli_path: str = DEFAULT_CLI_PATH
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY

    def __post_init__(self) -> None:
        if not 1 <= self.max_concurrency < MAX_CONCURRENCY_LIMIT:
            raise ValueError(
                "max_concurrency 必須介於 1 與 "
                f"{MAX_CONCURRENCY_LIMIT}（不含）之間，得到 {self.max_concurrency}"
            )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        """由環境變數建立設定，缺漏時套用預設值。"""
        env = os.environ if environ is None else environ
        raw_concurrency = env.get("EPS_MAX_CONCURRENCY")
        try:
            max_concurrency = (
                DEFAULT_MAX_CONCURRENCY
                if raw_concurrency is None
                else int(raw_concurrency)
            )
        except ValueError as exc:
            raise ValueError(
                f"EPS_MAX_CONCURRENCY 必須為整數，得到 {raw_concurrency!r}"
            ) from exc

        return cls(
            db_url=env.get("EPS_DB_URL", DEFAULT_DB_URL),
            cli_path=env.get("EPS_CLI_PATH", DEFAULT_CLI_PATH),
            max_concurrency=max_concurrency,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """回傳行程層級單例設定（由目前環境變數讀取）。"""
    return Settings.from_env()

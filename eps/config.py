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

# Story 3.4 / NFR-5 / 藍圖 §4：LocalCliAdapter 逾時與重試策略預設值。
# - stall 逾時 soft cap（秒）：以「無新串流輸出」計時，逾時即視為 stall（AC-1）。
# - 對暫時性失敗的最多重試次數（AC-2，初次嘗試外另加）。
# - 指數退避基數（秒）：第 n 次重試前等待 base * 2**n。
DEFAULT_STALL_TIMEOUT_SECONDS = 240.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_BASE_SECONDS = 1.0

# Story 4.3 / FR-10 / 藍圖 §3.3：焦點字串長度上限（字元數）。
# 多輪累積的焦點若超過此上限即壓縮，避免超出模型脈絡上限。
DEFAULT_MAX_FOCUS_CHARS = 4000

# Story 5.5 / NFR-3 / 藍圖 W1：WebSocket 事件流閒置心跳間隔（秒）。
# 連線閒置達此秒數即送一次心跳 ping 維持連線（AC-3）。
DEFAULT_WS_HEARTBEAT_SECONDS = 30.0


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    """讀取整數環境變數；缺漏套用 ``default``，非整數則拋 ``ValueError``。"""
    raw = env.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} 必須為整數，得到 {raw!r}") from exc


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    """讀取浮點環境變數；缺漏套用 ``default``，非數值則拋 ``ValueError``。"""
    raw = env.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} 必須為數值，得到 {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    """集中設定，預設值可由環境變數覆寫。

    - ``EPS_DB_URL``：資料庫連線字串，預設為 SQLite 檔。
    - ``EPS_CLI_PATH``：外部 CLI 執行路徑。
    - ``EPS_MAX_CONCURRENCY``：最大並發數，須為 1..<10 的整數。
    - ``EPS_STALL_TIMEOUT_SECONDS``：stall 逾時 soft cap（秒），須 > 0（Story 3.4）。
    - ``EPS_MAX_RETRIES``：暫時性失敗的最多重試次數，須 ≥ 0（Story 3.4）。
    - ``EPS_RETRY_BACKOFF_BASE_SECONDS``：指數退避基數（秒），須 ≥ 0（Story 3.4）。
    - ``EPS_MAX_FOCUS_CHARS``：焦點字串長度上限（字元數），須 > 0（Story 4.3）。
    - ``EPS_WS_HEARTBEAT_SECONDS``：WS 事件流閒置心跳間隔（秒），須 > 0（Story 5.5）。
    """

    db_url: str = DEFAULT_DB_URL
    cli_path: str = DEFAULT_CLI_PATH
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    stall_timeout_seconds: float = DEFAULT_STALL_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_backoff_base_seconds: float = DEFAULT_RETRY_BACKOFF_BASE_SECONDS
    max_focus_chars: int = DEFAULT_MAX_FOCUS_CHARS
    ws_heartbeat_seconds: float = DEFAULT_WS_HEARTBEAT_SECONDS

    def __post_init__(self) -> None:
        if not 1 <= self.max_concurrency < MAX_CONCURRENCY_LIMIT:
            raise ValueError(
                "max_concurrency 必須介於 1 與 "
                f"{MAX_CONCURRENCY_LIMIT}（不含）之間，得到 {self.max_concurrency}"
            )
        if self.stall_timeout_seconds <= 0:
            raise ValueError(
                f"stall_timeout_seconds 必須 > 0，得到 {self.stall_timeout_seconds}"
            )
        if self.max_retries < 0:
            raise ValueError(f"max_retries 必須 ≥ 0，得到 {self.max_retries}")
        if self.retry_backoff_base_seconds < 0:
            raise ValueError(
                "retry_backoff_base_seconds 必須 ≥ 0，得到 "
                f"{self.retry_backoff_base_seconds}"
            )
        if self.max_focus_chars <= 0:
            raise ValueError(
                f"max_focus_chars 必須 > 0，得到 {self.max_focus_chars}"
            )
        if self.ws_heartbeat_seconds <= 0:
            raise ValueError(
                f"ws_heartbeat_seconds 必須 > 0，得到 {self.ws_heartbeat_seconds}"
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
            stall_timeout_seconds=_env_float(
                env, "EPS_STALL_TIMEOUT_SECONDS", DEFAULT_STALL_TIMEOUT_SECONDS
            ),
            max_retries=_env_int(env, "EPS_MAX_RETRIES", DEFAULT_MAX_RETRIES),
            retry_backoff_base_seconds=_env_float(
                env,
                "EPS_RETRY_BACKOFF_BASE_SECONDS",
                DEFAULT_RETRY_BACKOFF_BASE_SECONDS,
            ),
            max_focus_chars=_env_int(
                env, "EPS_MAX_FOCUS_CHARS", DEFAULT_MAX_FOCUS_CHARS
            ),
            ws_heartbeat_seconds=_env_float(
                env, "EPS_WS_HEARTBEAT_SECONDS", DEFAULT_WS_HEARTBEAT_SECONDS
            ),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """回傳行程層級單例設定（由目前環境變數讀取）。"""
    return Settings.from_env()

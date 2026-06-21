# Story 3.4 — LocalCliAdapter 逾時與重試策略

## 決策
- **不發明新契約**：沿用既有例外階層（`TransientError` 可重試、`SourceError`/`AuthError` 不可重試、`AdapterTimeout` 表逾時）。
- **AC-1（stall 逾時，以「無新輸出」計時）**：`invoke` 的 `_run` 改為**逐行串流**讀 stdout，每次 `readline` 套 `asyncio.wait_for(stall_timeout)`；逾時 → kill 子行程 → 拋 `AdapterTimeout`（被重試流程接住）。計時以「兩行之間」為準，非總時長。
  - `validate_source` 維持 `_spawn`（communicate，快速 probe，不在 AC-1 範圍），Story 3.3 測試零更動。
- **AC-2（指數退避，最多 2 次）**：`invoke` 包一層重試迴圈，捕捉 `(TransientError, AdapterTimeout)`；退避 `backoff_base * 2**attempt`；耗盡 → 拋新例外 `RetryExhaustedError`（對應 `SessionStatus.Failed`，藍圖 §4）。
- **AC-3（SourceError 零重試）**：重試只捕捉 `(TransientError, AdapterTimeout)`；`SourceError`/`AuthError` 不在捕捉集合 → 直接向上拋，零重試。
- **可注入設定**：`stall_timeout_seconds` / `max_retries` / `retry_backoff_base_seconds` 進 `Settings`（含 env 覆寫與驗證），並可由 `LocalCliAdapter` 建構參數覆寫（與 `cli_path` 一致），讓測試注入小值、避免真實等待。

## 計畫
- [x] `eps/adapters/base.py`：新增 `RetryExhaustedError(AdapterError)` + 匯出。
- [x] `eps/adapters/__init__.py`：匯出 `RetryExhaustedError`。
- [x] `eps/config.py`：新增 3 設定（預設 240s / 2 / 1.0）+ env 解析 + 驗證。
- [x] `eps/adapters/local_cli.py`：建構參數；新增串流 `_stream`；`_run` 改用 `_stream`；`invoke` 重試包裝；保留 `_spawn` 給 `validate_source`；更新 docstring。
- [x] `tests/test_local_cli_adapter.py`：`_FakeProcess` 改為串流介面；transient 測試改為斷言 `RetryExhaustedError`（cause 為 `TransientError`）。
- [x] `tests/test_local_cli_retry.py`（新）：AC-1 stall / AC-2 退避耗盡 / AC-3 Source 與 Auth 零重試 / 重試後成功。
- [x] `tests/test_package_skeleton.py`：補新設定的 from_env 解析覆寫與驗證。
- [x] 驗證：`pytest` 全綠（146 passed）。

## Review
- **AC-1（stall 逾時，以「無新輸出」計時）**：`invoke` 改走 `_stream`，對每行 `readline` 套
  `asyncio.wait_for(stall_timeout)`；逾時 → `proc.kill()` → 拋 `AdapterTimeout`。
  `test_stall_timeout_retries_then_failed`（永不返回的 `_StallStream`，stall=0.01s）驗證逾時、
  子行程被 kill、重試 3 次後拋 `RetryExhaustedError`（cause `AdapterTimeout`）；
  `test_stall_then_success` 驗證逾時後重試成功。
- **AC-2（指數退避，最多 2 次）**：`test_transient_retries_then_raises_failed` 驗證 3 次嘗試後
  `RetryExhaustedError`（cause `TransientError`）；`test_exponential_backoff_delays` 驗證退避為
  `[1.0, 2.0]`；`test_transient_then_success` 驗證重試後成功。
- **AC-3（SourceError 零重試）**：`test_source_error_not_retried`（僅 1 次嘗試即向上拋）；
  另補 `test_auth_error_not_retried`（`AuthError` 同屬不可重試，零重試）。
- **不破壞既有**：`validate_source` 維持 `_spawn`（communicate）→ Story 3.3 測試零更動；Story 3.2
  測試 fake 升級為串流介面、行為斷言不變；唯一語意調整為 `invoke` 終態錯誤改包成
  `RetryExhaustedError`（底層分類保留於 `__cause__`），對應測試已同步。
- **設定可注入**：`stall_timeout_seconds` / `max_retries` / `retry_backoff_base_seconds` 進
  `Settings`（env 覆寫 + 範圍驗證）並可由建構參數覆寫，測試以小值注入避免真實等待。

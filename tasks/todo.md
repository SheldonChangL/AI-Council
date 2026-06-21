# Story 3.3 — LocalCliAdapter 來源驗證與 auth 偵測（validate_source）

## 決策
- `validate_source()` 既有契約來自 `base.py` Protocol：無效拋 `SourceError`，有效回傳 `None`。沿用，不發明。
- AC-1（CLI 未安裝）：以 `shutil.which(cli_path)` 偵測執行檔；找不到 → `SourceError`，訊息指示「修復環境／安裝 CLI」。並對 spawn 時的 `FileNotFoundError` 做相同保底。
- auth 偵測**不發明新 flag/subcommand**：沿用與 `invoke` 完全相同的 `STREAM_JSON_ARGS`，僅以最小 probe prompt 驅動，再依退出碼＋輸出分類。
- AC-2（非零退出且輸出含 auth/login 關鍵字）：判為 `SourceError`（來源類），**不視為 transient**。重用既有 `_AUTH_MARKERS`，比對 stdout+stderr（AC 措辭為「輸出」）。
- 非 auth 類非零退出：維持 `TransientError`（與 Story 3.2 一致，可重試），與來源類明確區隔。
- AC-3（CLI 已安裝且 OAuth 有效）：退出碼 0 → 回傳 `None`（valid）。
- 重點區分：`validate_source` 的 auth 失敗映射為 `SourceError`（pre-flight 來源類，擋啟動）；`invoke` 的 auth 失敗仍為 `AuthError`（runtime，Story 3.2）。
- 重構：抽出 `_spawn(prompt)` 共用 spawn+communicate+decode，`_run` 與 `validate_source` 共用，降低重複且不改既有行為。

## 計畫
- [x] `eps/adapters/local_cli.py`：`import shutil`；新增 `_VALIDATION_PROBE`；抽出 `_spawn()`；`_run()` 改用 `_spawn()`；新增 `validate_source()`
- [x] `tests/test_local_cli_validate_source.py`：AC-1 / AC-2 / AC-3 + 非 auth 非零 → Transient 的區隔
- [x] 驗證：完整 pytest 全綠（136 passed）

## Review
- AC-1：`test_missing_cli_raises_source_error`（which→None）驗證 `SourceError` 且訊息含執行檔名與安裝/環境指示；`test_spawn_filenotfound_maps_to_source_error` 驗證 spawn 競態保底。
- AC-2：`test_auth_failure_raises_source_error_not_transient`（stderr auth）、`test_auth_marker_in_stdout_raises_source_error`（stdout auth）驗證來源類；`test_nonauth_nonzero_raises_transient` 驗證非 auth 非零仍為 `TransientError`，確立來源類 vs transient 的區隔。
- AC-3：`test_valid_source_returns_none`（退出碼 0）驗證回傳 `None`。
- 重構 `_spawn()` 抽取後，既有 Story 3.2 測試（invoke 多輪串接 / 旗標 / Transient / Auth）全數維持通過；`LLMAdapter` Protocol 與 `FakeAdapter` 未動。
- 設計區分：`validate_source` 的 auth 失敗 → `SourceError`（pre-flight 來源類）；`invoke` 的 auth 失敗 → `AuthError`（runtime），兩者不混用。

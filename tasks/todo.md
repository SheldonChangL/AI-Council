# Story 4.5 — 取消與失敗路徑（保存部分結果）

## 決策
- **不發明取消管道**：以注入式 `cancel_token: asyncio.Event` 表達取消請求，引擎在每輪起點與每位專家發言前檢查；命中即停止推進。傳輸/API 層負責 set token（與 `bus.py` / DI 去耦風格一致）。
- **部分結果天然保留**：已落地的 `Contribution`/回合總結為 append-only（`append_contribution`），失敗路徑不刪除即保留（AC-1/2/3）。`partialAvailable` 以既有 `Repository.get_resume_position` 是否為 None 判定，不另存欄位。
- **不臆造內容（AC-2）**：失敗路徑提前 return，絕不呼叫 `compose_final_report`，故不產生虛構報告；落地的 viewpoint 皆為 adapter 真實輸出。
- **錯誤分類沿用 Story 3.x 契約**：`SourceError`→`SourceInvalid`、其餘 `AdapterError`（含 `RetryExhaustedError`/`AuthError`/`AdapterTimeout`）→`Failed`，與 OPS-1「終止而非 silent 續行」一致。
- **失敗終態統一通知**：`StatusChanged(<status>)` + `SessionFailed(reason, partialAvailable)`；`SessionFailed` 新增 `partial_available` 欄位（序列化為 `partialAvailable`）。

## 計畫
- [x] `eps/core/events.py`：`SessionFailed` 新增 `partial_available: bool = False`。
- [x] `eps/core/engine.py`：`run(..., cancel_token=None)`；Running 階段 try/except 攔截 `_Cancelled`/`SourceError`/`AdapterError`，分別轉 `Cancelled`/`SourceInvalid`/`Failed`；新增 `_raise_if_cancelled` 與 `_fail`（轉態 + 發 `SessionFailed`，含 partialAvailable）；`_run_round` 每位專家前檢查取消。
- [x] `eps/adapters/fake.py`：新增 `error_after`（方法名→允許成功次數）以模擬「跑出部分結果後才失敗」，向後相容（預設立即拋）。
- [x] `tests/test_core_engine.py`：AC-1 取消（含部分結果保留 + 事件）/ AC-2 執行中 SourceError / AC-3 RetryExhausted。
- [x] 驗證：`uv run pytest` 全綠（197 passed）；`ruff check` 全綠；CI 僅 gate pytest。

## Review
- **AC-1**：`test_cancel_preserves_partial_and_emits_events`——進行中取消轉 `Cancelled`，首位專家已落地發言（`vA`）保留、第二位未發言，發出 `StatusChanged(Cancelled)` + `SessionFailed(partialAvailable=True)`，且未呼叫 `compose_final_report`（`final_report` 為 None，不臆造）。`test_cancel_before_any_round_has_no_partial`——開跑前取消則無部分結果、`partialAvailable=False`。
- **AC-2**：`test_running_source_error_marks_source_invalid_and_keeps_partial`——執行中 `invoke` 拋 `SourceError` → `SourceInvalid`、保留 `vA`、`SessionFailed.reason` 含「重新登入後重試」且 `partialAvailable=True`、未產生報告。
- **AC-3**：`test_retry_exhausted_marks_failed_and_keeps_partial`——`RetryExhaustedError` → `Failed`、保留部分結果、`partialAvailable=True`、reason 透傳底層訊息。
- **不破壞既有**：來源「初次驗證」失敗路徑（`test_source_invalid_short_circuits`）維持原行為；`run`/`_run_round` 新增參數皆為 keyword 預設值，向後相容；`FakeAdapter.error_after` 預設空映射＝既有立即拋行為。全套件 197 passed。
- **設計備註**：取消管道採注入式 `asyncio.Event`，引擎於回合／專家邊界檢查；部分結果保留依賴既有 append-only 落地（不刪除即保留），`partialAvailable` 由 `get_resume_position` 判定，無新增 schema/migration。

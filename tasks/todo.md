# Story 5.3 — 取消與重試端點（FR-14, OPS-2 / 藍圖 A4/A5）

## 背景
- `JobManager.cancel` / `.start`（Story 4.6）與引擎取消／失敗路徑（Story 4.5）已完成。
- 本 story 為整合：新增 `POST /sessions/{id}/cancel` 與 `POST /sessions/{id}/retry` 兩端點。

## 設計決策
- **狀態集合**：在 `models.py` 定義 `TERMINAL_STATUSES`（Completed/Failed/SourceInvalid/Cancelled）與 `RETRYABLE_STATUSES`（SourceInvalid/Failed），作為端點判定的單一真相來源。
- **端點權威落地**：端點直接 `set_status` 落地目標狀態（cancel→Cancelled、retry→ValidatingSource），使回應與 DB 立即一致；背景引擎的取消／重跑為冪等收斂。即便背景把手已遺失（多程序／重啟、`JobManager.cancel` 回 False），仍以 DB 為權威記錄使用者意圖。
- **cancel**：非終態 → signal 取消旗標 + 落地 Cancelled → 200 `{status:"Cancelled"}`；終態 → 409 `NOT_CANCELLABLE`；不存在 → 404 `SESSION_NOT_FOUND`。
- **retry**：失敗終態（SourceInvalid/Failed）→ 落地 ValidatingSource + `jobs.start` 重新排程 → 202 `{status:"ValidatingSource"}`；其餘狀態（含 Completed）→ 409 `NOT_RETRYABLE`（嚴格依 AC-2 列舉可重試集合，不臆造其他狀態語意）；不存在 → 404。
- **輕量讀取**：新增 `Repository.get_session`，僅讀會話本體判定狀態（不載入聚合）。
- **DTO**：新增 `SessionStatusOut`（`{status}`），cancel/retry 共用。

## 待辦
- [x] models.py：`TERMINAL_STATUSES` / `RETRYABLE_STATUSES`
- [x] repository.py：`get_session`
- [x] schemas.py：`SessionStatusOut`
- [x] routes.py：`POST /sessions/{id}/cancel`、`POST /sessions/{id}/retry` + 409 helpers
- [x] tests：cancel/retry AC-1~AC-3、404、非終態/非可重試矩陣；repository `get_session`
- [x] ruff（本次檔案全綠）+ pytest 全綠（254 passed）

## Review
- **AC-1**：`test_cancel_running_session_returns_200_cancelled` — Running → 200 `{status:"Cancelled"}`，signal 取消旗標且 DB 落地 Cancelled。`test_cancel_non_terminal_states_are_cancellable`（Created/ValidatingSource）同樣可取消。`test_cancel_terminal_session_returns_409`（Completed/Failed/SourceInvalid/Cancelled）→ 409 `NOT_CANCELLABLE`，不 signal、狀態不變。
- **AC-2**：`test_retry_failed_session_returns_202_validating`（SourceInvalid/Failed）→ 202 `{status:"ValidatingSource"}`，已 `jobs.start` 重新排程且 DB 落地 ValidatingSource。
- **AC-3**：`test_retry_completed_session_returns_409` — Completed → 409 `NOT_RETRYABLE`，不重排。`test_retry_non_retryable_states_return_409`（Created/ValidatingSource/Running/Cancelled）同樣 409。
- **邊界**：`test_cancel_missing_session_returns_404` / `test_retry_missing_session_returns_404` → 404 `SESSION_NOT_FOUND`。
- **既有狀況**：`test_core_bus.py` 1 個既有 ruff F401（HEAD 已存在，非本次引入）。

## 已知限制 / Tester 重點
- API 層以 stub JobManager 隔離，未驅動真實背景任務／CLI（沿用 Story 5.2 模式）。
- **retry 重跑的引擎續跑**：SourceInvalid（驗證前失敗、無 rounds）重跑乾淨。Failed 若已落地部分 rounds，現行引擎 `run` 從 round 1 重建會撞 `uq_round_session_round_number` 唯一約束（引擎尚未使用 `get_resume_position` 續跑）。此為引擎 resume 能力缺口（屬另一 story），非本端點 story 範圍；端點已正確重設狀態並重排。建議交付 Architect/後續 resume story 處理。
- 多程序部署下 in-memory JobManager 取消 signal 僅及本程序；DB 落地 Cancelled 為跨程序權威記錄。

# Story 5.2 — POST /sessions 建立會話、驗證並排程背景任務（FR-1, FR-3, OPS-3 / 藍圖 A1）

## 背景
- DTO（`CreateSessionRequest` 等）已由 Story 5.1 完成；`JobManager` 已由 Story 4.6 完成。
- 本 story 為整合：新增 `POST /sessions` 端點、把 `JobManager` 接進 app，並補上 idempotency。

## 設計決策
- **回應形狀**：AC-1 明定 `{sessionId, status:"Created"}`，覆寫 5.1 的 `CreateSessionResponse`（保留不動）。新增精簡 DTO `CreateSessionAccepted`。
- **錯誤碼分流**：端點內手動 `model_validate`，攔 `ValidationError`：
  - experts 超過上限（`too_long` on `experts`）→ 400 `TOO_MANY_EXPERTS`（AC-3）。
  - 其餘（topic 空、maxRounds 越界、experts 空…）→ 422 `INVALID_INPUT`（AC-2）。
  - 採端點內處理而非全域 handler，避免影響其他端點既有行為。
- **Idempotency**：在 `session` 新增 nullable + unique 的 `idempotency_key` 欄位（沿用 final_report/usage_stats 的 nullable 欄位先例，不新增資料表）；DB 唯一約束作為併發 backstop。
- **背景排程**：lifespan 組裝 `EventBus` + `OrchestrationEngine` + `JobManager` 放 `app.state.job_manager`；端點以 `get_job_manager` 依賴注入，測試可覆寫成 stub 避免真實 CLI。端點為 `async def`，使 `JobManager.start`（`create_task`）有運行中 loop。

## 待辦
- [x] models.py：Session 新增 `idempotency_key`（nullable, unique index）
- [x] migrations/0004_idempotency_key.py：add column + unique index
- [x] repository.py：`create_session(..., idempotency_key=None)` + `find_session_by_idempotency_key`
- [x] schemas.py：新增 `CreateSessionAccepted`
- [x] routes.py：`get_job_manager` + `POST /sessions`
- [x] main.py：lifespan 組裝並注入 `job_manager`
- [x] tests：POST /sessions AC-1~4；migration head 版本 + 新欄位；repository idempotency
- [x] ruff（本次檔案全綠）+ pytest 全綠（236 passed）+ CI 等效煙霧測試

## Review
- **AC-1**：`test_create_session_returns_202_and_schedules_job`——合法 payload 回 202 `{sessionId, status:"Created"}`，stub JobManager 記錄 `start(sessionId)`（背景任務已排程、與 HTTP 解耦），會話與專家落地。背景任務進入 ValidatingSource gate 屬引擎職責（`test_core_engine` 已涵蓋），API 層僅驗證已排程。
- **AC-2**：`test_create_session_invalid_max_rounds_returns_422`（0/21）與 `test_create_session_blank_topic_returns_422`（""/"  "）——皆回 422 `INVALID_INPUT` 且未排程。端點內 `model_validate` 攔 `ValidationError` 經 `_validation_error` 分流。
- **AC-3**：`test_create_session_too_many_experts_returns_400`——experts 超過 `EXPERTS_MAX(8)` 回 400 `TOO_MANY_EXPERTS`（依 Pydantic `too_long` on `experts` 分流），未排程。
- **AC-4**：`test_create_session_idempotent_returns_same_session_id`——相同 `Idempotency-Key` 重播回同一 sessionId 且僅排程一次；`..._distinct_keys_...` 不同鍵建立不同會話。Repository 層 `test_find_session_by_idempotency_key_roundtrip` / `..._duplicate_key_violates_unique` / `..._null_keys_do_not_collide` 驗證持久層與唯一約束（含併發 backstop：`IntegrityError` → 回查）。
- **migration**：head 更新為 `0004_idempotency_key`；新增欄位 nullable + unique 索引測試。CI（migrate→pytest→uvicorn /health smoke）等效本機驗證通過：alembic upgrade head + app boot + POST 202 + 冪等重播一致。
- **設計備註**：回應採新 DTO `CreateSessionAccepted`（AC-1 明定 `{sessionId,status}`，與 5.1 `CreateSessionResponse` 並存、後者保留）。錯誤分流採端點內處理而非全域 handler，零副作用於其他端點。idempotency 沿用 nullable 欄位先例、不新增資料表。
- **既有狀況**：`test_core_bus.py` 1 個既有 ruff F401（HEAD 已存在，非本次引入）；CI 不 gate ruff，不受影響。
- **已知限制 / Tester 重點**：背景任務排程後的真實 CLI 執行不在 API 測試覆蓋（以 stub 隔離）；端到端研討由 `test_integration_full_session` 涵蓋。多程序部署下 in-memory JobManager 與冪等 in-process 競態保護不適用（本系統為單程序 asyncio 模型），DB 唯一約束為跨程序 backstop。

# Story 4.6 — 背景任務生命週期、併發 semaphore 與用量統計（FR-13 / NFR-4 / OPS-3）

## 決策
- **JobManager 自包含**：注入 `engine` / `repo` / `bus`，持有全域 `asyncio.Semaphore(max_concurrency)`。`start(session_id)` 以 `asyncio.create_task` 建背景任務並**立即回傳** `JobHandle`，與 HTTP 連線解耦（AC-1）。HTTP/transport 層的接線留待後續 story（本 story 為 prereq，僅建機制）。
- **併發閘門**：semaphore 在背景任務內、`engine.run()` **外圈** acquire，故等待名額時 in-flight 引擎（→CLI 子行程）數不超過上限，超出者排隊（AC-2）。每個 job 各自 `JobHandle` + `cancel_token`，狀態互不污染。
- **用量持久化沿用 Story 2.4 先例**：在 `session` 新增 nullable `usage_stats`（JSON 文字）欄位，**不新增資料表**。用量由會話結束後的 `get_session_detail` 彙總（rounds / experts / contributions ＋每位專家發言數）。
- **僅監測不中止（OPS-3）**：用量統計在 `_run_job` 的 `finally` 區塊執行，成功/失敗/取消路徑皆發佈 `UsageStats` 事件並持久化；統計自身失敗只 log、絕不影響會話結果。

## 計畫
- [x] `eps/data/models.py`：`Session` 新增 `usage_stats: Optional[str] = None`（nullable JSON 文字）。
- [x] `migrations/versions/0003_usage_stats.py`：`session` 新增 `usage_stats` 欄位（nullable）。
- [x] `eps/data/repository.py`：新增 `save_usage_stats(session_id, usage_stats_json)`（單 transaction 落地）。
- [x] `eps/core/jobs.py`：`JobState` / `JobHandle` / `compute_usage` / `JobManager`（start / status / cancel / 背景任務 + semaphore + 用量發佈）。
- [x] `tests/test_core_jobs.py`：AC-1 立即回傳與狀態查詢 / AC-2 semaphore 上限＋會話隔離 / AC-3 用量發佈＋持久化（含失敗路徑僅監測不中止）。
- [x] `tests/test_migrations.py`：head 更新為 `0003_usage_stats`，新增 `session.usage_stats` 欄位測試。
- [x] 驗證：`uv run pytest` 全綠（206 passed）；CI 僅 gate pytest。

## Review
- **AC-1**：`test_start_returns_immediately_and_is_queryable`——`start()` 回傳把手時背景任務尚未完成（`task.done()` 為 False，與呼叫端解耦），`status()` 立即可查；放行後 `Finished` 並反映會話終態。`test_status_none_for_unknown_session` 未啟動回 None；`test_start_is_idempotent_while_in_flight` 進行中重複 start 回同一把手、不重複啟動。
- **AC-2**：`test_semaphore_caps_in_flight_and_queues_excess`——上限 2、啟動 5 個，in-flight 引擎數恆為 2、超出者 `Pending` 排隊；放行後全程峰值不超過上限且 5 個全部完成。`test_cancel_one_session_does_not_pollute_others`——僅取消 s1，s2 取消旗標未被 set，終態 s1=Cancelled、s2=Completed（狀態互不污染）。
- **AC-3**：`test_usage_published_and_persisted_on_completion`——2 輪×2 專家發佈 `UsageStats`（rounds=2/experts=2/contributions=4 ＋每位專家 2 次），`session.usage_stats` 持久化且與事件一致，終態仍為 Completed。`test_usage_published_on_failure_without_aborting`——失敗路徑仍發佈/持久化部分用量（contributions=1），會話維持 Failed、不臆造報告（僅監測不中止）。`test_compute_usage_counts_per_expert` 直接驗證純函式彙總。
- **設計備註 / 範圍**：JobManager 為自包含單元（注入 engine/repo/bus），未接線 FastAPI——HTTP/transport 啟動端點與 app 層 EventBus 屬後續 story（本 story 為 prereq，僅建機制）。用量持久化沿用 Story 2.4 `final_report` 的 nullable 欄位先例，維持五張表、不新增資料表。
- **既有狀況**：`test_core_bus.py` / 另一測試檔有 2 個既有 ruff F401（在 HEAD 已存在，非本次引入）；CI 僅 gate `uv run pytest`，不受影響。依最小變更原則未一併修改。

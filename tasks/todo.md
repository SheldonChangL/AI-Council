# Story 2.6 — 經 API 查詢歷史會話與模板庫並刪除會話

## 決策
- AC 為權威契約（沿用 Story 2.2~2.5 慣例）。Repository 層（list/detail/delete）已存在，僅缺 FastAPI API 層。
- 回應欄位採 camelCase（AC-1 明確要 `createdAt`）：以 pydantic `alias_generator=to_camel` + 序列化用別名。
- `GET /personas` 的 Given 僅「服務運行中」，故由 lifespan 在啟動時**冪等** `seed_persona_templates`（既有函式安全可重入），保證運行中服務必含內建模板。
- 404 採結構化 `HTTPException(404, detail={"code": "SESSION_NOT_FOUND", ...})`。
- 端點以 `request.app.state.db_engine` 取得 engine，包成 `Repository` 注入（沿用 lifespan 既有 `app.state.db_engine`）。

## 計畫
- [x] `eps/api/schemas.py`：`SessionSummary` / `PersonaOut` / `SessionDetailOut`（含 experts/rounds/contributions）回應模型（camelCase）
- [x] `eps/api/routes.py`：`router` 與 `GET /sessions`、`GET /personas`、`GET /sessions/{id}`、`DELETE /sessions/{id}`
- [x] `eps/data/repository.py`：新增 `list_personas`（內建模板查詢，資料存取集中於 repository）
- [x] `eps/main.py`：include router；lifespan 啟動時 `create_all` + 冪等 seed 內建 persona
- [x] `eps/api/__init__.py`：匯出 `router`
- [x] `tests/test_api.py`：AC-1~AC-4（清單排序/limit、personas≥3、詳情聚合與 404、刪除 204 後 404）
- [x] 驗證：完整 pytest 全綠（109 passed）；alembic upgrade head + 實機 smoke 通過

## Review
- 四個 AC 皆以 HTTP 層測試覆蓋；端點透過 `app.state.db_engine` 包 `Repository` 注入，不直接持有連線。
- lifespan 改為啟動時 `SQLModel.metadata.create_all` + 冪等 seed：讓 `uvicorn eps.main:app` 開箱即用並自我提供內建模板（AC-2 Given 僅「服務運行中」）。兩者對已 alembic 升級的 DB 皆為 no-op，與既有 Story 1.3 health 測試相容。
- 回應欄位採 camelCase（`createdAt` 等），404 採結構化 `{"detail":{"code":"SESSION_NOT_FOUND"}}`。

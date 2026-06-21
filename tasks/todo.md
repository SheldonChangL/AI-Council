# Story 1.4 — 乾淨環境一鍵安裝、建庫、跑測試、啟動服務

## 計畫
- [x] 建立 Alembic 骨架（`alembic.ini`、`migrations/env.py`、`script.py.mako`、baseline migration），讓 `alembic upgrade head` 在乾淨環境可成功
- [x] env.py 由 `eps.config` 讀取 `EPS_DB_URL`，target_metadata 綁定 `SQLModel.metadata`
- [x] 新增 `.github/workflows/ci.yml`：install → `alembic upgrade head` → `pytest` → Uvicorn 啟動 smoke，依序且任一失敗即 fail
- [x] 新增測試：`tests/test_migrations.py`（AC-1，alembic upgrade head 可建表）
- [x] 新增測試：`tests/test_ci_workflow.py`（AC-3，CI 步驟存在且有序）
- [x] 驗證：`alembic upgrade head` + 完整 `pytest` 全綠；本機 uvicorn `/health` smoke 回 200

## Review
- Alembic 骨架完成：baseline 因目前無 model 為空 migration，僅建立 `alembic_version` 追蹤表作為鏈起點；env.py 以 `eps.config` 為單一連線字串來源。
- CI workflow 以 `astral-sh/setup-uv` + `uv sync --frozen`，依序跑 migrate → pytest → uvicorn `/health` smoke（30 次重試，逾時即 fail）。
- 驗證結果：`alembic upgrade head` 成功（version=`0001_baseline`）；`pytest` 35 passed；本機 uvicorn `:8723/health` 回 200 `{"status":"ok"}`。
- 未提交：依規範未 push，stray DB 檔已清理。

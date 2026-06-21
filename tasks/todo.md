# Story 2.2 — Alembic migration 與索引/約束（[prereq]）

## 決策
- AC 為權威 schema 契約：將 ORM 對齊 AC（Story 2.1 的 `Round.index` 為暫定欄位）。
- 不阻塞：AC 已完整指定五表、唯一約束、索引與重複插入失敗行為。

## 計畫
- [x] `eps/data/models.py`：`Round.index` → `round_number`；新增唯一約束 `(session_id, round_number)`
- [x] `eps/data/models.py`：`Contribution` 新增 `seq`；唯一約束 `(round_id, seq)` 與索引 `(round_id, seq)`
- [x] `eps/data/models.py`：`Session` 新增索引 `created_at DESC`、`status`
- [x] `migrations/versions/0002_schema.py`：建立全部五張表 + 索引/約束（down_revision = 0001_baseline）
- [x] `tests/test_data_models.py`：更新 `Round(round_number=...)` 用法
- [x] `tests/test_migrations.py`：AC-1 五表、AC-2 約束/索引、AC-3 重複插入失敗
- [x] 驗證：`alembic upgrade head` 乾淨建庫；完整 pytest 全綠

## Review
- migration 以 `alembic revision --autogenerate` 產生為基礎，再手動補上 `ix_session_created_at`（`created_at DESC` 表達式索引，SQLite 無法反射故 autogenerate 略過），並改用決定性 revision id `0002_schema`。
- `Round.index` → `round_number`、`Contribution` 新增 `seq`，對齊 Story 2.2 AC 命名（Story 2.1 的 `index` 為暫定欄位）。約束/索引以 `__table_args__` 集中宣告，model 與 migration 一致。
- 驗證：`pytest` 66 passed（新增 7）；乾淨 SQLite `alembic upgrade head` 建五表，head=`0002_schema`；inspector 確認兩唯一約束與三索引（含 DESC）；重複 `(session_id, round_number)` 插入觸發 `IntegrityError`；`upgrade→downgrade base` roundtrip 乾淨。
- 已知限制：`alembic check` 會對 `ix_session_created_at` 報誤判 diff（SQLite 無法反射表達式索引，屬已知工具限制，非真實漂移）。CI gate 僅跑 `alembic upgrade head` + `pytest`，不受影響。
- 未提交：依規範未 push。

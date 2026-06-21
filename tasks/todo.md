# Story 2.4 — Repository 查詢、刪除與恢復位置（[prereq]）

## 決策
- AC 為權威契約（沿用 Story 2.2/2.3 慣例）。
- `final_report` 在 repo 不存在。最小且不破壞「五張表」架構的作法：在 `session` 新增 nullable `final_report` 欄位（每場會話 1:1），而非新增第六張表。`0002_schema` 為唯一且尚未發布的 schema 建立點，就地新增欄位，head 維持 `0002_schema`。
- `get_session_detail` 回傳 frozen dataclass `SessionDetail`（session/experts/rounds/contributions/final_report），rounds/contributions 採扁平排序清單（rounds 依 round_number、contributions 依 (round_id, seq)）；查無回傳 `None`（即 AC-4「找不到」）。
- `get_resume_position` 以 Contribution join Round，取字典序最大 `(round_number, seq)`（seq 僅存在於 Contribution，座標必來自發言列）；無任何發言/會話不存在回傳 `None`。
- `delete_session` 真刪，依外鍵由子到父刪除，單一 transaction；成功回 `True`、不存在回 `False`。
- `list_sessions` 依 `created_at desc, id desc`（穩定次序，最新優先），`status` 可選過濾，提供 `limit/offset`。

## 計畫
- [x] `eps/data/models.py`：`Session` 新增 `final_report: Optional[str] = None`
- [x] `migrations/versions/0002_schema.py`：session 新增 `final_report` 欄位
- [x] `eps/data/repository.py`：`SessionDetail` + `list_sessions` / `get_session_detail` / `get_resume_position` / `delete_session`
- [x] `eps/data/__init__.py`：匯出 `SessionDetail`
- [x] `tests/test_repository.py`：AC-1~AC-4 與邊界（無相符 status、空發言續跑、查無詳情、刪除不存在）
- [x] `tests/test_data_models.py`：`final_report` 預設 None 且可持久化
- [x] `tests/test_migrations.py`：session 具 nullable `final_report` 欄位
- [x] 驗證：`alembic upgrade head` 乾淨建庫；完整 pytest 全綠

## Review
- 見最終 handoff；四個 AC 皆於 repository 層測試覆蓋，並補 model/migration 欄位測試。

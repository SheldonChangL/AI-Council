# Story 2.3 — Repository 建立會話與 append-only 寫入（[prereq]）

## 決策
- AC 為權威契約（沿用 Story 2.2 慣例）：將暫定欄位名對齊 AC。
  - `SessionExpert.position` → `order_index`（AC-1 明列）。
  - `Contribution.content` → `viewpoint`，新增 `focus_after`（AC-2 簽章明列）。
- 0002_schema migration 為唯一 schema 建立點（尚未發布），就地修改欄位名，head 維持 `0002_schema`，不新增 rename migration。
- `experts` 採最小契約：專家名稱字串序列，`order_index` 由列舉位置（0..n-1）指定。
- append-only 保護沿用既有唯一約束 `(round_id, seq)`，重複寫入讓 `IntegrityError` 傳播。

## 計畫
- [x] `eps/data/models.py`：`position`→`order_index`；`content`→`viewpoint`；新增 `focus_after: Optional[str]`
- [x] `migrations/versions/0002_schema.py`：對齊上述欄位名與新增 `focus_after`
- [x] `eps/data/repository.py`：`Repository.create_session` / `append_contribution`
- [x] `eps/data/__init__.py`：匯出 `Repository`
- [x] `tests/test_data_models.py`：更新 `order_index` / `viewpoint` 用法
- [x] `tests/test_repository.py`：AC-1（連續 order_index）、AC-2（單一 transaction commit）、AC-3（重複 (round_id, seq) 被拒）
- [x] 驗證：`alembic upgrade head` 乾淨建庫；完整 pytest 全綠

## Review
- `Repository(engine)` 提供 `create_session(topic, max_rounds, experts)` 與 `append_contribution(round_id, expert_id, seq, viewpoint, focus_after=None)`，皆以 `with Session(engine)` 包成單一 transaction。
- AC-1：`create_session` 用 `flush()` 取得 session.id 後，以 `enumerate(experts)` 寫入連續 `order_index`（0..n-1），會話與專家原子 commit；topic/max_rounds 仍由 `Session` 模型驗證。
- AC-2：`append_contribution` 單一 insert 單一 commit，commit 後可讀回（含 `focus_after`）。
- AC-3：沿用既有唯一約束 `(round_id, seq)`，重複寫入讓 `IntegrityError` 傳播，不吞錯。
- Schema 對齊：依 Story 2.2 慣例（AC 為權威契約）就地修正 `0002_schema`——`order_index`、`viewpoint`、新增 `focus_after`；head 維持 `0002_schema`，未新增 rename migration。orchestrator 標示的兩個落差即由此解決。
- 驗證：`pytest` 76 passed（新增 10）；乾淨 SQLite `alembic upgrade head` 成功，inspector 確認 `session_expert.order_index`、`contribution.viewpoint` / `focus_after` 欄位到位。
- 未提交：依規範未 push。

# Story 2.1 — SQLModel ORM 資料模型

## 計畫
- [x] `eps/data/models.py`：定義 5 個 SQLModel 類別（Session / SessionExpert / Round / Contribution / PersonaTemplate）
- [x] `Session.status` enum 7 值（Created..Cancelled），順序與藍圖 §3.2 一致
- [x] Session 驗證：`max_rounds` 1..20、`topic` 非空且 ≤ 8k（建構 / 設定 / model_validate 三路徑皆拒絕）
- [x] `eps/data/__init__.py` 匯入模型，確保 `import eps.data` 註冊到 `SQLModel.metadata`
- [x] `tests/test_data_models.py`：AC-1/AC-2/AC-3 + 完整會話 graph 持久化
- [x] 驗證：完整 pytest 全綠、alembic autogenerate 偵測 5 張表

## Review
- 模型以 FK 欄位串接完整會話狀態；未加 `Relationship()`（非 AC 必要且在此 SQLModel/SA 版本會觸發 mapper 設定錯誤），維持最小範圍。
- 驗證關鍵：SQLModel `table=True` 預設跳過建構驗證，且 `model_validate` 內部呼叫 `cls()` 會與覆寫的 `__init__` 遞迴。解法為 `validate_assignment=True`（設定路徑）+ 委派非 table 孿生模型 `_SessionConstraints`（建構/驗證路徑），統一拋出 `ValidationError`。
- 驗證結果：`pytest` 59 passed（新增 24）；alembic autogenerate 偵測 `session / session_expert / round / contribution / persona_template` 5 表。
- 假設與風險：藍圖 §3.2 全文在本工作流中無可取回的書面來源，僅 AC 列出的約束（enum、max_rounds、topic）為硬性依據；各模型其餘欄位依領域合理建模（見 Handoff），exact 欄位與 §3.2 對齊需 Architect 確認。
- 未提交：依規範未 push。

# Story 4.3 — 焦點彙整與壓縮

## 決策
- **不發明 adapter 契約**：語意收斂／摘要沿用既有 `LLMAdapter.refine_focus` 與 `summarize_round`（Story 3.1），`focus.py` 僅薄層委派，不新增 adapter 方法。
- **長度上限本地強制（FR-10）**：`compress_focus` 為決定性純函式，與後端無關，故任何 adapter 皆適用同一脈絡預算，且可重現驗證（AC-2）。
- **保留關鍵脈絡**：壓縮時保留「開頭」（原始主題／早期脈絡）與「結尾」（最新收斂），中段以 ` […] ` 取代，而非單純截尾；壓縮後長度恰為上限，並保證 `<= max_chars`。
- **上限為集中設定**：沿用 `config.Settings` 慣例新增 `max_focus_chars`（`EPS_MAX_FOCUS_CHARS`，須 > 0，預設 4000）；`focus.py` 維持純邏輯，由參數傳入（預設取 `DEFAULT_MAX_FOCUS_CHARS`），與 `bus.py` 去耦風格一致。
- **摘要亦受限**：`summarize_round` 輸出將成下一輪起始焦點，故同樣套用 `compress_focus`（FR-10）。

## 計畫
- [x] `eps/config.py`：新增 `DEFAULT_MAX_FOCUS_CHARS` 與 `Settings.max_focus_chars`（驗證 + `from_env` 讀 `EPS_MAX_FOCUS_CHARS`）。
- [x] `eps/core/focus.py`（新）：`compress_focus` + `refine_focus`（async）+ `summarize_round`（async）。
- [x] `tests/test_core_focus.py`（新）：AC-1 整合觀點 / AC-2 壓縮＋頭尾脈絡＋邊界＋設定 / AC-3 回合總結作下輪焦點。
- [x] 驗證：`uv run pytest` 全綠（186 passed）。

## Review
- **AC-1**：`test_refine_focus_integrates_viewpoint_via_adapter` 確認經 adapter 整合並回傳更新後焦點，引數契約 `(focus, viewpoint)`；`test_refine_focus_passes_through_when_within_limit` 未超限原樣回傳。
- **AC-2**：`test_refine_focus_compresses_when_over_limit`（超長焦點壓至 40 字、保留頭尾、含省略標記）、`test_compress_focus_preserves_head_and_tail`、`test_compress_focus_within_limit_is_unchanged`、`test_compress_focus_tiny_limit_truncates`（上限過小截斷）、`test_compress_focus_rejects_nonpositive_limit`。
- **AC-3**：`test_summarize_round_returns_summary_as_next_focus`（回傳本輪總結、引數契約）、`test_summarize_round_compresses_over_limit`（摘要超限亦受約束）。
- **FR-10 設定**：`test_max_focus_chars_is_configurable` 驗證預設、環境覆寫、非正值拒絕。
- **不破壞既有**：純新增 `focus.py` 與測試，`config.py` 為向後相容擴充；全套件 186 passed（既有 176 + 新增 10）。

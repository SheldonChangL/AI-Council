# Story 3.2 — LocalCliAdapter 子行程 spawn 與 stream-json 全輪次串接

## 決策
- CLI 契約沿用既有來源：`config.py` 的 `DEFAULT_CLI_PATH="codex"` / `EPS_CLI_PATH`，不發明執行檔名。
- AC-3 需區分「非 auth 類非零退出（可重試）」與「auth 類（不可重試）」，故在 `base.py` 新增 `TransientError` 與 `AuthError`（皆 `AdapterError` 子類）。
- OPS-4 核心：逐行解析 stream-json，串接「全部」`assistant.message.content[].text`，不可只取最後一筆 / `result`。
- 容錯：跳過空白行與無法解析的雜訊行，不因單行中斷整體解析。
- [prereq] 範圍：僅實作 `invoke()` 所需的 spawn + 解析；其餘 Protocol 方法留待後續 story（避免發明 prompt 契約）。

## 計畫
- [x] `eps/adapters/base.py`：新增 `TransientError`（可重試）、`AuthError`（不可重試），更新 `__all__`
- [x] `eps/adapters/local_cli.py`：`LocalCliAdapter`（`create_subprocess_exec` + stdin prompt + stream-json 解析 + 退出碼分類）
- [x] `eps/adapters/__init__.py`：匯出 `LocalCliAdapter` / `TransientError` / `AuthError`
- [x] `tests/test_local_cli_adapter.py`：AC-1（多輪串接、容錯）、AC-2（命令旗標 / stdin / 設定預設）、AC-3（Transient / Auth 分類）
- [x] 驗證：完整 pytest 全綠（130 passed）

## Review
- AC-1：`test_concatenates_all_assistant_turns` 驗證多個 assistant 訊息 + 多 content block 全部串接、前段不遺漏；`test_tolerates_blank_and_malformed_lines` 驗證容錯。
- AC-2：`test_command_args_and_stdin` 驗證 `--output-format stream-json --verbose` 與 stdin（含 persona/focus）；`test_cli_path_defaults_to_settings` 驗證設定來源。
- AC-3：`test_nonzero_exit_raises_transient` / `test_auth_failure_raises_auth_error` 驗證可重試 vs 不可重試分類。
- 未改動既有 `LLMAdapter` Protocol 與 `FakeAdapter`；既有測試全數維持通過。

# Story 6.2 — CLI WebSocket 串流進度顯示（FR-12, FR-11, OPS-1）

## 計畫
- [ ] `eps/cli/progress.py`：新增 `ProgressRenderer`，以 Rich 渲染單則事件
      - RoundStarted/ExpertStarted → 輪次與發言中專家（AC-1）
      - RoundSummary/ReportCompleted → 輪次總結與「報告完成」（AC-2）
      - SessionFailed → 失敗原因＋是否有部分結果，不偽裝成功（AC-3）
      - `handle()` 回傳是否為終態；終態旗標 `completed` / `failed`
- [ ] `eps/cli/client.py`：新增 `stream_events()` context manager
      - 注入路徑（TestClient `websocket_connect`）與生產路徑（websockets sync）雙支援
      - 連線被拒（404）統一拋 `WatchConnectionError`
- [ ] `eps/cli/main.py`：新增 `watch` 子命令，串接 client + renderer，失敗 exit 1
- [ ] 測試
      - `tests/test_cli_progress.py`：renderer 單元測試（AC-1/2/3）
      - `tests/test_cli_watch.py`：watch 端到端（TestClient 注入，gated adapter）＋未知會話

## 完成
- [x] `eps/cli/progress.py`：`ProgressRenderer`（AC-1/2/3，動態內容不經 markup）
- [x] `eps/cli/client.py`：`stream_events()` + `WatchConnectionError`（TestClient／websockets 雙路徑）
- [x] `eps/cli/main.py`：`watch` 子命令，SessionFailed → exit 1
- [x] `tests/test_cli_progress.py`（7）、`tests/test_cli_watch.py`（3）
- [x] pytest 全綠（282 passed）、ruff `eps/cli` 與新測試全綠

## Review
- **AC-1**：`test_round_started_*` / `test_expert_started_*` 驗證輪次＋焦點與發言中專家名稱；
  整合 `test_watch_streams_progress_until_report_completed` 端到端見「第 1/2 輪」「甲/乙/丙」「發言中」。
- **AC-2**：`test_round_summary_*` / `test_report_completed_*` 驗證輪次總結與「報告完成」（終態回傳 True）。
- **AC-3**：`test_session_failed_*`（有/無部分結果）與整合 `test_watch_source_failure_*` 驗證印出失敗原因、
  不偽裝成功（`completed=False`、輸出無「報告完成」）、以 exit 1 結束（OPS-1）。
- **邊界**：`test_watch_unknown_session_reports_error` → 404 拒絕轉 `WatchConnectionError`、exit 1。

## 已知限制 / Tester 重點
- 整合測試以 `_GatedAdapter` + TestClient 注入驅動，未連真實遠端服務；生產 WS 路徑
  （`websockets.sync`）以單元層 URL 推導覆蓋，未做真實網路 e2e。
- `watch` 串流中途斷線（非終態）視為 exit 0；如需「中斷即失敗」語意需再議。

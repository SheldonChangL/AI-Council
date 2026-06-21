# Story 4.2 — in-process EventBus

## 決策
- **不發明契約**：發佈載體沿用既有 `eps.core.events.Event`（已帶 `session_id`），`publish` 直接以 `event.session_id` 分流（AC-2），無需另立路由鍵。
- **AC-1（fan-out）**：每個訂閱者持有獨立 `asyncio.Queue`；`publish` 對該 session 的每個佇列 `put_nowait`，故同一 session 多訂閱者皆即時收到（in-process 投遞 ≪ 2s）。
- **AC-3（取消＋無洩漏）**：`unsubscribe` 從分流表移除佇列、session 無人訂閱即整筆刪除，並推入關閉哨符喚醒阻塞中的迭代乾淨結束；重複呼叫為 no-op。
- **API 形狀對齊既有慣例**：`Subscription` 同時為 async context manager 與 async iterator（與引擎/傳輸層 async 風格一致）；佇列無界限避免慢消費者阻塞 `publish` 或丟事件。

## 計畫
- [x] `eps/core/bus.py`（新）：`EventBus`（`subscribe`/`unsubscribe`/`publish`/`subscriber_count`）+ `Subscription`。
- [x] `tests/test_core_bus.py`（新）：AC-1 fan-out / AC-2 分流 / AC-3 取消＋無洩漏＋冪等＋context manager＋async 迭代。
- [x] 驗證：`pytest` 全綠（176 passed）。

## Review
- **AC-1**：`test_two_subscribers_same_session_both_receive` 同一 session 兩訂閱者皆於 ≤2s 內取得同一事件物件；`test_subscriber_count_tracks_fanout` 確認登記數。
- **AC-2**：`test_publish_is_partitioned_by_session_id` 發佈 session 7，session 8 訂閱者逾時無收；`test_publish_with_no_subscribers_is_noop` 無訂閱者發佈不拋例外亦不殘留登記。
- **AC-3**：`test_unsubscribe_stops_delivery_and_cleans_up`（取消後 count=0、後續發佈不送達、迭代以 `StopAsyncIteration` 結束）、`test_unsubscribe_one_of_many_keeps_others`（僅移除其一、其餘照收）、`test_unsubscribe_is_idempotent`、`test_context_manager_unsubscribes_on_exit`、`test_async_iteration_yields_events_until_unsubscribed`。
- **不破壞既有**：純新增檔案，未動既有模組；全套件 176 passed（既有 167 + 新增 9）。

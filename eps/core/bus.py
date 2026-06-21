"""eps in-process EventBus（Story 4.2 / FR-11 / NFR-3 / NFR-4 / 藍圖 §3.3）。

將「運算」（引擎推進回合、產生 :class:`eps.core.events.Event`）與「傳輸」（WS 連線
推送）解耦：引擎只 :meth:`EventBus.publish`，傳輸層 :meth:`EventBus.subscribe` 後消費，
同一場會話可有多個訂閱者 fan-out（例如多個 WS 連線同時觀看）。

設計（皆以既有契約為依據，非發明）：

- 以 ``Event.session_id`` 分流（AC-2）：``publish`` 只送達訂閱同一 ``session_id`` 的訂閱者。
- 每個訂閱者持有獨立的 :class:`asyncio.Queue`，``publish`` 以 ``put_nowait`` 對每個佇列
  分別投遞，故多訂閱者皆能即時收到同一事件（AC-1；in-process 投遞遠低於 2s 上限）。
- 佇列無界限：避免慢速消費者造成 ``publish`` 阻塞或丟事件；傳輸層負責即時抽取。
- :class:`Subscription` 為 async context manager 與 async iterator，離開即自動
  :meth:`EventBus.unsubscribe`，從分流表移除佇列（``session_id`` 無訂閱者時整筆刪除），
  並推入關閉哨符喚醒任何阻塞中的迭代，確保取消後不再收到事件且無資源洩漏（AC-3）。
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Dict, Set

from eps.core.events import Event

# 訂閱關閉哨符：unsubscribe 時推入佇列，喚醒阻塞中的 ``__anext__`` 並結束迭代。
_CLOSED = object()


class Subscription:
    """單一訂閱者：繫結某 ``session_id`` 的事件佇列。

    可作為 async context manager（離開即自動取消訂閱），或直接 async for 迭代事件。
    """

    def __init__(self, bus: "EventBus", session_id: int) -> None:
        self.session_id = session_id
        self._bus = bus
        self._queue: "asyncio.Queue[object]" = asyncio.Queue()
        self._closed = False

    async def get(self) -> Event:
        """取得下一個事件；訂閱已關閉時拋 :class:`StopAsyncIteration`。"""
        item = await self._queue.get()
        if item is _CLOSED:
            raise StopAsyncIteration
        return item  # type: ignore[return-value]

    def __aiter__(self) -> AsyncIterator[Event]:
        return self

    async def __anext__(self) -> Event:
        return await self.get()

    async def __aenter__(self) -> "Subscription":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self._bus.unsubscribe(self)


class EventBus:
    """asyncio in-process pub/sub，以 ``session_id`` 分流並 fan-out 給多訂閱者。"""

    def __init__(self) -> None:
        # session_id -> 該會話所有訂閱者的佇列集合。
        self._subscribers: Dict[int, Set["asyncio.Queue[object]"]] = {}

    def subscribe(self, session_id: int) -> Subscription:
        """為 ``session_id`` 建立並登記一個訂閱者。"""
        sub = Subscription(self, session_id)
        self._subscribers.setdefault(session_id, set()).add(sub._queue)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        """移除訂閱者並喚醒其迭代結束；重複呼叫為 no-op（AC-3）。"""
        if sub._closed:
            return
        sub._closed = True
        queues = self._subscribers.get(sub.session_id)
        if queues is not None:
            queues.discard(sub._queue)
            if not queues:  # 該 session 無訂閱者 → 整筆移除，避免洩漏。
                del self._subscribers[sub.session_id]
        sub._queue.put_nowait(_CLOSED)

    async def publish(self, event: Event) -> None:
        """將事件投遞給訂閱其 ``session_id`` 的所有訂閱者（其他 session 不受影響）。"""
        # 複製快照：投遞期間若有訂閱者增減不影響本次 fan-out。
        for queue in list(self._subscribers.get(event.session_id, ())):
            queue.put_nowait(event)

    def subscriber_count(self, session_id: int) -> int:
        """目前訂閱該 ``session_id`` 的訂閱者數（供測試與觀測無洩漏）。"""
        return len(self._subscribers.get(session_id, ()))


__all__ = ["EventBus", "Subscription"]

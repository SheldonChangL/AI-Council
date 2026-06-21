"""Story 4.2 — in-process EventBus（AC-1, AC-2, AC-3）。

驗證：
- AC-1：同一 ``sessionId`` 的兩個訂閱者皆於 ≤2s 內收到同一事件（fan-out）。
- AC-2：事件以 ``sessionId`` 分流，僅該 session 的訂閱者收到。
- AC-3：取消訂閱後不再收到事件、迭代乾淨結束，且分流表無殘留（無資源洩漏）。
"""

import asyncio

import pytest

from eps.core.bus import EventBus, Subscription
from eps.core.events import RoundStarted, TokenChunk


def _event(session_id: int, text: str = "片段") -> TokenChunk:
    return TokenChunk(session_id=session_id, round_number=1, expert_id=1, text=text)


# ---------------------------------------------------------------------------
# AC-1：同一 session 的兩個訂閱者皆於 ≤2s 內收到同一事件。
# ---------------------------------------------------------------------------
async def test_two_subscribers_same_session_both_receive():
    bus = EventBus()
    sub_a = bus.subscribe(7)
    sub_b = bus.subscribe(7)

    event = _event(7, "hello")
    await bus.publish(event)

    got_a = await asyncio.wait_for(sub_a.get(), timeout=2)
    got_b = await asyncio.wait_for(sub_b.get(), timeout=2)

    assert got_a is event and got_b is event


async def test_subscriber_count_tracks_fanout():
    bus = EventBus()
    bus.subscribe(7)
    bus.subscribe(7)
    assert bus.subscriber_count(7) == 2


# ---------------------------------------------------------------------------
# AC-2：事件以 sessionId 分流，僅該 session 的訂閱者收到。
# ---------------------------------------------------------------------------
async def test_publish_is_partitioned_by_session_id():
    bus = EventBus()
    sub_7 = bus.subscribe(7)
    sub_8 = bus.subscribe(8)

    event = _event(7)
    await bus.publish(event)

    got = await asyncio.wait_for(sub_7.get(), timeout=2)
    assert got is event
    # 另一 session 的訂閱者不應收到任何事件。
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(sub_8.get(), timeout=0.2)


async def test_publish_with_no_subscribers_is_noop():
    bus = EventBus()
    # 無人訂閱 session 99：不得拋例外，亦不建立殘留登記。
    await bus.publish(_event(99))
    assert bus.subscriber_count(99) == 0


# ---------------------------------------------------------------------------
# AC-3：取消訂閱後不再收到事件、迭代乾淨結束，且無資源洩漏。
# ---------------------------------------------------------------------------
async def test_unsubscribe_stops_delivery_and_cleans_up():
    bus = EventBus()
    sub = bus.subscribe(7)
    assert bus.subscriber_count(7) == 1

    bus.unsubscribe(sub)
    # 分流表整筆移除（最後一名訂閱者離開）→ 無洩漏。
    assert bus.subscriber_count(7) == 0

    # 取消後發佈不得送達。
    await bus.publish(_event(7))
    # 取消會推入哨符 → 迭代乾淨結束。
    with pytest.raises(StopAsyncIteration):
        await sub.get()


async def test_unsubscribe_one_of_many_keeps_others():
    bus = EventBus()
    sub_a = bus.subscribe(7)
    sub_b = bus.subscribe(7)

    bus.unsubscribe(sub_a)
    assert bus.subscriber_count(7) == 1  # 僅移除 A

    event = _event(7)
    await bus.publish(event)

    got_b = await asyncio.wait_for(sub_b.get(), timeout=2)
    assert got_b is event
    with pytest.raises(StopAsyncIteration):
        await sub_a.get()


async def test_unsubscribe_is_idempotent():
    bus = EventBus()
    sub = bus.subscribe(7)
    bus.unsubscribe(sub)
    bus.unsubscribe(sub)  # 重複呼叫為 no-op，不得拋例外
    assert bus.subscriber_count(7) == 0


async def test_context_manager_unsubscribes_on_exit():
    bus = EventBus()
    async with bus.subscribe(7) as sub:
        assert isinstance(sub, Subscription)
        assert bus.subscriber_count(7) == 1
        event = _event(7)
        await bus.publish(event)
        assert await asyncio.wait_for(sub.get(), timeout=2) is event
    # 離開 context 即自動取消訂閱。
    assert bus.subscriber_count(7) == 0


async def test_async_iteration_yields_events_until_unsubscribed():
    bus = EventBus()
    sub = bus.subscribe(7)

    await bus.publish(_event(7, "a"))
    await bus.publish(_event(7, "b"))
    bus.unsubscribe(sub)  # 哨符在兩事件之後入列 → 迭代收完即停止

    seen = [evt.text async for evt in sub]
    assert seen == ["a", "b"]

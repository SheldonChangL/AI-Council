"""eps 焦點彙整與壓縮（Story 4.3 / FR-7, FR-8, FR-10 / 藍圖 §3.3）。

多輪辯論會持續把新觀點收斂進「當前焦點」，若不加節制，焦點會隨輪次累積而失焦
或超出模型脈絡上限（FR-10）。本模組提供薄層協調函式：

- 「語意收斂」委派給 :class:`~eps.adapters.base.LLMAdapter`（``refine_focus`` /
  ``summarize_round``），不耦合任何具體後端，測試可用 ``FakeAdapter`` 決定性注入。
- 「長度上限」由本模組以決定性演算法在本地強制（:func:`compress_focus`），與後端
  無關，故同一上限策略適用於任何 adapter，且可重現驗證（AC-2）。

此模組刻意維持「純邏輯」：上限值由呼叫端（引擎）以參數傳入（預設取自
:data:`eps.config.DEFAULT_MAX_FOCUS_CHARS`，可由 ``EPS_MAX_FOCUS_CHARS`` 覆寫），
不於模組內讀取全域設定，與 :mod:`eps.core.bus` 的去耦風格一致。
"""

from __future__ import annotations

from typing import Sequence

from eps.adapters.base import LLMAdapter
from eps.config import DEFAULT_MAX_FOCUS_CHARS

# 壓縮時插入的省略標記：標示中段已被裁切，頭尾脈絡仍保留。
_ELLIPSIS = " […] "


def compress_focus(focus: str, *, max_chars: int = DEFAULT_MAX_FOCUS_CHARS) -> str:
    """將 ``focus`` 壓縮至至多 ``max_chars`` 個字元，並保留關鍵脈絡（FR-10）。

    決定性策略：未超限即原樣回傳；超限時保留「開頭」（原始主題／早期脈絡）與
    「結尾」（最新收斂結果），中段以 :data:`_ELLIPSIS` 取代。如此壓縮後仍同時保有
    起點與最新觀點兩端脈絡，而非單純截斷尾部資訊。

    回傳值長度保證 ``<= max_chars``；當實際發生壓縮時長度恰為 ``max_chars``。

    :param max_chars: 長度上限，須 > 0。
    """
    if max_chars <= 0:
        raise ValueError(f"max_chars 必須 > 0，得到 {max_chars}")
    if len(focus) <= max_chars:
        return focus
    # 上限過小、容不下省略標記時，直接截斷頭部以維持長度上限。
    if max_chars <= len(_ELLIPSIS):
        return focus[:max_chars]
    budget = max_chars - len(_ELLIPSIS)
    head_len = (budget + 1) // 2  # 頭部不短於尾部，優先保留原始脈絡。
    tail_len = budget - head_len
    head = focus[:head_len]
    tail = focus[len(focus) - tail_len :] if tail_len else ""
    return head + _ELLIPSIS + tail


async def refine_focus(
    adapter: LLMAdapter,
    focus: str,
    viewpoint: str,
    *,
    max_chars: int = DEFAULT_MAX_FOCUS_CHARS,
) -> str:
    """將新 ``viewpoint`` 收斂進當前 ``focus``，回傳長度受限的更新後焦點（AC-1/AC-2）。

    先經 ``adapter.refine_focus`` 做語意收斂（落地為 ``Contribution.focus_after``），
    再以 :func:`compress_focus` 強制長度上限，確保多輪累積不超出模型脈絡上限。
    """
    refined = await adapter.refine_focus(focus, viewpoint)
    return compress_focus(refined, max_chars=max_chars)


async def summarize_round(
    adapter: LLMAdapter,
    topic: str,
    round_number: int,
    viewpoints: Sequence[str],
    *,
    max_chars: int = DEFAULT_MAX_FOCUS_CHARS,
) -> str:
    """彙整本輪所有 ``viewpoints`` 為總結，作為下一輪起始焦點（AC-3）。

    經 ``adapter.summarize_round`` 產生回合摘要後，同樣以 :func:`compress_focus`
    強制長度上限——此摘要將成為下一輪的起始焦點，故須遵守同一脈絡預算（FR-10）。
    """
    summary = await adapter.summarize_round(topic, round_number, list(viewpoints))
    return compress_focus(summary, max_chars=max_chars)


__all__ = ["compress_focus", "refine_focus", "summarize_round"]

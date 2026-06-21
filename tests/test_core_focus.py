"""Story 4.3 — 焦點彙整與壓縮（AC-1, AC-2, AC-3）。

以 ``FakeAdapter`` 決定性注入 adapter 的收斂／摘要輸出，驗證 ``eps.core.focus``
的輸入／輸出契約與長度上限策略（FR-7, FR-8, FR-10）。
"""

import pytest

from eps.adapters import FakeAdapter
from eps.config import DEFAULT_MAX_FOCUS_CHARS, Settings
from eps.core.focus import compress_focus, refine_focus, summarize_round


# AC-1：refine_focus 經 adapter 整合新觀點，回傳更新後焦點字串。
async def test_refine_focus_integrates_viewpoint_via_adapter():
    adapter = FakeAdapter(focuses=["整合後焦點"])
    result = await refine_focus(adapter, "當前焦點", "新觀點")
    assert result == "整合後焦點"
    # 確認確實委派給 adapter，且引數契約為 (focus, viewpoint)。
    assert adapter.calls == [("refine_focus", ("當前焦點", "新觀點"))]


# AC-1：未腳本化時沿用 adapter 的決定性衍生輸出，未超限即原樣回傳。
async def test_refine_focus_passes_through_when_within_limit():
    adapter = FakeAdapter()
    result = await refine_focus(adapter, "f0", "v1")
    assert result == "focus:f0+v1"


# AC-2：adapter 回傳超長焦點時，refine_focus 壓縮至受限長度且保留頭尾脈絡。
async def test_refine_focus_compresses_when_over_limit():
    long_focus = "HEAD原始脈絡" + "x" * 500 + "TAIL最新收斂"
    adapter = FakeAdapter(focuses=[long_focus])
    result = await refine_focus(adapter, "focus", "viewpoint", max_chars=40)
    assert len(result) == 40
    assert result.startswith("HEAD原始脈絡")
    assert result.endswith("TAIL最新收斂")
    assert " […] " in result


# AC-2：compress_focus 為決定性純函式 — 未超限原樣回傳。
def test_compress_focus_within_limit_is_unchanged():
    assert compress_focus("短焦點", max_chars=100) == "短焦點"
    edge = "x" * 50
    assert compress_focus(edge, max_chars=50) == edge


# AC-2：compress_focus 超限時長度恰為上限，並同時保留頭尾。
def test_compress_focus_preserves_head_and_tail():
    text = "AAAA" + "0123456789" * 20 + "ZZZZ"
    out = compress_focus(text, max_chars=30)
    assert len(out) == 30
    assert out.startswith("AAAA")
    assert out.endswith("ZZZZ")
    assert " […] " in out


# AC-2：上限過小容不下省略標記時，截斷至上限以維持長度保證。
def test_compress_focus_tiny_limit_truncates():
    out = compress_focus("abcdefgh", max_chars=3)
    assert out == "abc"


# AC-2：max_chars 必須 > 0。
def test_compress_focus_rejects_nonpositive_limit():
    with pytest.raises(ValueError):
        compress_focus("x", max_chars=0)


# AC-3：summarize_round 回傳本輪總結，作為下一輪起始焦點。
async def test_summarize_round_returns_summary_as_next_focus():
    adapter = FakeAdapter(round_summaries=["本輪總結"])
    result = await summarize_round(adapter, "主題", 1, ["v1", "v2"])
    assert result == "本輪總結"
    # 引數契約：topic / round_number / viewpoints（轉為 list）。
    assert adapter.calls == [("summarize_round", ("主題", 1, ("v1", "v2")))]


# AC-3：回合摘要超長時同樣受長度上限約束（FR-10）。
async def test_summarize_round_compresses_over_limit():
    long_summary = "S開頭" + "y" * 500 + "尾端E"
    adapter = FakeAdapter(round_summaries=[long_summary])
    result = await summarize_round(adapter, "主題", 2, ["v1"], max_chars=30)
    assert len(result) == 30
    assert result.startswith("S開頭")
    assert result.endswith("尾端E")


# FR-10：上限為集中設定，可由環境變數覆寫；預設與模組常數一致。
def test_max_focus_chars_is_configurable():
    assert Settings().max_focus_chars == DEFAULT_MAX_FOCUS_CHARS
    overridden = Settings.from_env({"EPS_MAX_FOCUS_CHARS": "123"})
    assert overridden.max_focus_chars == 123
    with pytest.raises(ValueError):
        Settings(max_focus_chars=0)

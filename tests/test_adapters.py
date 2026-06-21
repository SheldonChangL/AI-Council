"""Story 3.1 — LLMAdapter Protocol 與 FakeAdapter（AC-1, AC-2, AC-3）。

驗證：
- AC-1：``eps/adapters/base.py`` 的 ``LLMAdapter`` 定義五個約定方法。
- AC-2：``FakeAdapter`` 滿足 ``LLMAdapter`` Protocol（runtime_checkable）。
- AC-3：``FakeAdapter`` 可腳本化觀點 / 焦點 / 錯誤 / 逾時 / SourceError，回傳預設結果。
"""

import inspect

import pytest

from eps.adapters import (
    AdapterError,
    AdapterTimeout,
    FakeAdapter,
    LLMAdapter,
    SourceError,
)

# AC-1：Protocol 應定義的五個方法。
PROTOCOL_METHODS = [
    "validate_source",
    "invoke",
    "refine_focus",
    "summarize_round",
    "compose_final_report",
]


# AC-1：LLMAdapter 定義五個約定方法。
@pytest.mark.parametrize("method", PROTOCOL_METHODS)
def test_protocol_defines_method(method):
    assert hasattr(LLMAdapter, method), f"LLMAdapter 缺少方法 {method}"


# AC-1：invoke(persona, focus) / refine_focus(focus, viewpoint) 的參數約定。
def test_protocol_method_signatures():
    invoke_params = list(inspect.signature(LLMAdapter.invoke).parameters)
    assert invoke_params == ["self", "persona", "focus"]
    refine_params = list(inspect.signature(LLMAdapter.refine_focus).parameters)
    assert refine_params == ["self", "focus", "viewpoint"]


# AC-2：FakeAdapter 滿足 LLMAdapter Protocol（型別檢查）。
def test_fake_adapter_satisfies_protocol():
    assert isinstance(FakeAdapter(), LLMAdapter)


# AC-2：例外階層 — SourceError / AdapterTimeout 皆為 AdapterError 子類。
def test_exception_hierarchy():
    assert issubclass(SourceError, AdapterError)
    assert issubclass(AdapterTimeout, AdapterError)


# AC-3：腳本化「觀點」— invoke 依序回傳預設值。
async def test_scripted_viewpoints():
    adapter = FakeAdapter(viewpoints=["v1", "v2"])
    assert await adapter.invoke("市場分析師", "topic") == "v1"
    assert await adapter.invoke("技術架構師", "topic") == "v2"
    # 耗盡後回退為決定性衍生字串。
    assert await adapter.invoke("倫理學家", "topic") == "viewpoint:倫理學家@topic"


# AC-3：腳本化「焦點」— refine_focus 依序回傳預設值。
async def test_scripted_focuses():
    adapter = FakeAdapter(focuses=["f1"])
    assert await adapter.refine_focus("focus0", "v1") == "f1"
    assert await adapter.refine_focus("focus0", "v1") == "focus:focus0+v1"


# AC-3：腳本化回合摘要與最終報告。
async def test_scripted_round_summary_and_final_report():
    adapter = FakeAdapter(round_summaries=["s1"], final_report="REPORT")
    assert await adapter.summarize_round("topic", 1, ["v1", "v2"]) == "s1"
    assert await adapter.compose_final_report("topic", ["s1"]) == "REPORT"


# AC-3：腳本化「錯誤」— 指定方法固定拋出例外。
async def test_scripted_error():
    boom = AdapterError("boom")
    adapter = FakeAdapter(errors={"invoke": boom})
    with pytest.raises(AdapterError) as exc:
        await adapter.invoke("persona", "focus")
    assert exc.value is boom


# AC-3：腳本化「逾時」— 指定方法拋出 AdapterTimeout。
async def test_scripted_timeout():
    adapter = FakeAdapter(timeouts={"summarize_round"})
    with pytest.raises(AdapterTimeout):
        await adapter.summarize_round("topic", 1, ["v1"])


# AC-3：腳本化「SourceError」— validate_source 拋出，預設視為有效則返回 None。
async def test_scripted_source_error_and_valid():
    valid = FakeAdapter()
    assert await valid.validate_source("https://example.com") is None

    invalid = FakeAdapter(source_error=SourceError("無法存取來源"))
    with pytest.raises(SourceError):
        await invalid.validate_source("https://bad")


# AC-3：逾時優先於錯誤，且呼叫被記錄供斷言。
async def test_timeout_precedes_error_and_calls_recorded():
    adapter = FakeAdapter(
        timeouts={"invoke"}, errors={"invoke": AdapterError("never")}
    )
    with pytest.raises(AdapterTimeout):
        await adapter.invoke("p", "f")
    assert adapter.calls == [("invoke", ("p", "f"))]

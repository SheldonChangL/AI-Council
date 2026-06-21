"""Story 5.1 — API 請求/回應 DTO 契約測試（AC-1, AC-2, AC-3 / FR-1 / 藍圖 §6）。

驗證 ``eps/api/schemas.py`` 的 Pydantic DTO：
- AC-1：`CreateSessionRequest{topic, maxRounds, experts:[{name, personaPrompt,
  sourceTemplateId?}]}` 與對應回應 DTO 存在且以 camelCase 接受/序列化。
- AC-2：`experts` 為空或超過上限時驗證失敗；`maxRounds`/`topic` 越界亦失敗。
- AC-3：序列化會話列表/詳情時欄位形狀符合藍圖 A2/A3。
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from eps.api.schemas import (
    EXPERTS_MAX,
    CreateSessionRequest,
    CreateSessionResponse,
    ExpertIn,
    SessionDetailOut,
    SessionFull,
    SessionSummary,
)
from eps.data.models import MAX_ROUNDS_MAX, TOPIC_MAX_LENGTH, SessionStatus


def _valid_payload(**overrides) -> dict:
    payload = {
        "topic": "是否升息",
        "maxRounds": 3,
        "experts": [
            {"name": "經濟學家", "personaPrompt": "你是經濟學家"},
            {"name": "工程師", "sourceTemplateId": 7},
        ],
    }
    payload.update(overrides)
    return payload


# --- AC-1：CreateSessionRequest 形狀與 camelCase 別名 ---
def test_create_session_request_accepts_camelcase_payload():
    req = CreateSessionRequest.model_validate(_valid_payload())

    assert req.topic == "是否升息"
    assert req.max_rounds == 3
    assert [e.name for e in req.experts] == ["經濟學家", "工程師"]
    # personaPrompt 選用，預設空字串；sourceTemplateId 選用，預設 None。
    assert req.experts[0].persona_prompt == "你是經濟學家"
    assert req.experts[0].source_template_id is None
    assert req.experts[1].persona_prompt == ""
    assert req.experts[1].source_template_id == 7


def test_expert_in_defaults():
    expert = ExpertIn.model_validate({"name": "律師"})
    assert expert.persona_prompt == ""
    assert expert.source_template_id is None


def test_create_session_request_serializes_to_camelcase():
    req = CreateSessionRequest.model_validate(_valid_payload())
    dumped = req.model_dump(by_alias=True)

    assert set(dumped.keys()) == {"topic", "maxRounds", "experts"}
    assert set(dumped["experts"][0].keys()) == {
        "name",
        "personaPrompt",
        "sourceTemplateId",
    }


# --- AC-2：experts 為空或超過上限 → 驗證失敗 ---
def test_empty_experts_rejected():
    with pytest.raises(ValidationError):
        CreateSessionRequest.model_validate(_valid_payload(experts=[]))


def test_too_many_experts_rejected():
    too_many = [{"name": f"E{i}"} for i in range(EXPERTS_MAX + 1)]
    with pytest.raises(ValidationError):
        CreateSessionRequest.model_validate(_valid_payload(experts=too_many))


def test_experts_at_upper_bound_accepted():
    at_max = [{"name": f"E{i}"} for i in range(EXPERTS_MAX)]
    req = CreateSessionRequest.model_validate(_valid_payload(experts=at_max))
    assert len(req.experts) == EXPERTS_MAX


@pytest.mark.parametrize("bad_rounds", [0, MAX_ROUNDS_MAX + 1])
def test_invalid_max_rounds_rejected(bad_rounds):
    with pytest.raises(ValidationError):
        CreateSessionRequest.model_validate(_valid_payload(maxRounds=bad_rounds))


@pytest.mark.parametrize("bad_topic", ["", "   "])
def test_blank_topic_rejected(bad_topic):
    with pytest.raises(ValidationError):
        CreateSessionRequest.model_validate(_valid_payload(topic=bad_topic))


def test_oversized_topic_rejected():
    with pytest.raises(ValidationError):
        CreateSessionRequest.model_validate(
            _valid_payload(topic="x" * (TOPIC_MAX_LENGTH + 1))
        )


def test_blank_expert_name_rejected():
    with pytest.raises(ValidationError):
        CreateSessionRequest.model_validate(
            _valid_payload(experts=[{"name": "   "}])
        )


# --- AC-1：CreateSessionResponse 形狀（A1/A3 子集）---
def test_create_session_response_shape():
    now = datetime.now(timezone.utc)
    resp = CreateSessionResponse(
        session=SessionFull(
            id=1,
            topic="議題",
            status=SessionStatus.Created,
            max_rounds=2,
            created_at=now,
            updated_at=now,
        ),
        experts=[],
    )
    dumped = resp.model_dump(by_alias=True)
    assert set(dumped.keys()) == {"session", "experts"}
    assert dumped["session"]["maxRounds"] == 2
    assert dumped["session"]["createdAt"]


# --- AC-3：列表/詳情序列化欄位形狀符合藍圖 A2/A3 ---
def test_session_summary_shape_matches_blueprint_a2():
    now = datetime.now(timezone.utc)
    summary = SessionSummary(
        id=1, topic="t", status=SessionStatus.Created, created_at=now
    )
    dumped = summary.model_dump(by_alias=True)
    assert set(dumped.keys()) == {"id", "topic", "status", "createdAt"}


def test_session_detail_out_shape_matches_blueprint_a3():
    field_aliases = {
        f.alias or name
        for name, f in SessionDetailOut.model_fields.items()
    }
    assert field_aliases == {
        "session",
        "experts",
        "rounds",
        "contributions",
        "finalReport",
    }

"""Story 2.1 — SQLModel ORM 資料模型（AC-1, AC-2, AC-3）。

驗證五個 ORM 類別存在、Session.status enum 一致，以及 Session 對 max_rounds /
topic 的驗證約束。
"""

import importlib

import pytest
from pydantic import ValidationError
from sqlmodel import SQLModel, create_engine
from sqlmodel import Session as DBSession

from eps.data.models import (
    Contribution,
    PersonaTemplate,
    Round,
    Session,
    SessionExpert,
    SessionStatus,
    TOPIC_MAX_LENGTH,
)

# AC-1：藍圖 §3.2 規定的五個 SQLModel 類別。
EXPECTED_MODELS = [
    "Session",
    "SessionExpert",
    "Round",
    "Contribution",
    "PersonaTemplate",
]

# AC-1：Session.status 的合法狀態（順序與藍圖 §3.2 一致）。
EXPECTED_STATUSES = [
    "Created",
    "ValidatingSource",
    "Running",
    "Completed",
    "Failed",
    "SourceInvalid",
    "Cancelled",
]


# --- AC-1 ---
@pytest.mark.parametrize("name", EXPECTED_MODELS)
def test_model_class_exists(name):
    module = importlib.import_module("eps.data.models")
    cls = getattr(module, name, None)
    assert cls is not None, f"缺少模型類別 {name}"
    assert issubclass(cls, SQLModel)


def test_five_models_are_tables():
    for cls in (Session, SessionExpert, Round, Contribution, PersonaTemplate):
        assert getattr(cls, "__tablename__", None), f"{cls.__name__} 未定義資料表"


def test_session_status_enum_values_match_blueprint():
    assert [s.value for s in SessionStatus] == EXPECTED_STATUSES


def test_session_default_status_is_created():
    assert Session(topic="t", max_rounds=1).status is SessionStatus.Created


def test_metadata_create_all_succeeds():
    # 五個資料表能在乾淨 SQLite 建立（FK 設定無誤）。
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    tables = set(SQLModel.metadata.tables)
    for table in (
        "session",
        "session_expert",
        "round",
        "contribution",
        "persona_template",
    ):
        assert table in tables
    engine.dispose()


# --- AC-2：max_rounds 合法範圍 1..20 ---
@pytest.mark.parametrize("value", [0, 21, -1, 100])
def test_max_rounds_out_of_range_rejected_on_construct(value):
    with pytest.raises(ValidationError):
        Session(topic="t", max_rounds=value)


@pytest.mark.parametrize("value", [0, 21])
def test_max_rounds_out_of_range_rejected_on_assignment(value):
    session = Session(topic="t", max_rounds=5)
    with pytest.raises(ValidationError):
        session.max_rounds = value


@pytest.mark.parametrize("value", [1, 10, 20])
def test_max_rounds_within_range_accepted(value):
    assert Session(topic="t", max_rounds=value).max_rounds == value


# --- AC-3：topic 非空且長度上限 8k ---
@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
def test_blank_topic_rejected(value):
    with pytest.raises(ValidationError):
        Session(topic=value, max_rounds=5)


def test_topic_over_max_length_rejected():
    with pytest.raises(ValidationError):
        Session(topic="x" * (TOPIC_MAX_LENGTH + 1), max_rounds=5)


def test_topic_at_max_length_accepted():
    session = Session(topic="x" * TOPIC_MAX_LENGTH, max_rounds=5)
    assert len(session.topic) == TOPIC_MAX_LENGTH


# --- 持久化完整會話狀態（FR-15）---
def test_full_session_graph_persists():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with DBSession(engine) as session:
        persona = PersonaTemplate(name="Economist", system_prompt="think")
        session.add(persona)
        session.commit()
        session.refresh(persona)

        sess = Session(topic="是否升息", max_rounds=3, status=SessionStatus.Running)
        session.add(sess)
        session.commit()
        session.refresh(sess)

        expert = SessionExpert(
            session_id=sess.id, persona_template_id=persona.id, name="A", order_index=0
        )
        session.add(expert)
        session.commit()
        session.refresh(expert)

        rnd = Round(session_id=sess.id, round_number=1)
        session.add(rnd)
        session.commit()
        session.refresh(rnd)

        contribution = Contribution(
            round_id=rnd.id, session_expert_id=expert.id, viewpoint="贊成"
        )
        session.add(contribution)
        session.commit()

        reloaded = session.get(Session, sess.id)
        assert reloaded.status is SessionStatus.Running
        assert reloaded.topic == "是否升息"
        assert reloaded.max_rounds == 3
    engine.dispose()


# --- Story 2.5：PersonaTemplate.builtin / SessionExpert.persona_prompt 預設值 ---
def test_persona_template_builtin_defaults_false():
    assert PersonaTemplate(name="x").builtin is False


def test_session_expert_persona_prompt_defaults_empty():
    expert = SessionExpert(session_id=1, name="A")
    assert expert.persona_prompt == ""


# --- Story 2.4：Session.final_report 預設 None 且可持久化 ---
def test_session_final_report_defaults_none_and_persists():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with DBSession(engine) as db:
        sess = Session(topic="t", max_rounds=3)
        assert sess.final_report is None
        sess.final_report = "最終綜整報告"
        db.add(sess)
        db.commit()
        reloaded = db.get(Session, sess.id)
        assert reloaded.final_report == "最終綜整報告"
    engine.dispose()

"""Story 2.3 — Repository 建立會話與 append-only 寫入（AC-1, AC-2, AC-3）。

驗證：
- AC-1：`create_session` 回傳含 id 的 Session，且 experts 以連續 order_index 寫入。
- AC-2：`append_contribution` 在單一 transaction commit 後可被讀回。
- AC-3：對相同 (round_id, seq) 重複寫入因唯一約束被拒（append-only 保護）。
"""

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel, create_engine, select
from sqlmodel import Session as DBSession

from eps.data.models import Contribution, Round, Session, SessionExpert
from eps.data.repository import Repository


@pytest.fixture
def engine():
    """乾淨的 in-memory SQLite，建立全部資料表。"""
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repo(engine):
    return Repository(engine)


def _make_round(engine, *, max_rounds=3) -> tuple[int, int]:
    """建立一個 Session/Expert/Round，回傳 (round_id, expert_id)。"""
    with DBSession(engine) as db:
        session = Session(topic="t", max_rounds=max_rounds)
        db.add(session)
        db.flush()
        expert = SessionExpert(session_id=session.id, name="A", order_index=0)
        rnd = Round(session_id=session.id, round_number=1)
        db.add(expert)
        db.add(rnd)
        db.commit()
        db.refresh(expert)
        db.refresh(rnd)
        return rnd.id, expert.id


# --- AC-1：建立會話並連續寫入 experts ---
def test_create_session_returns_session_with_id(repo):
    session = repo.create_session(topic="是否升息", max_rounds=3, experts=["A", "B"])
    assert session.id is not None
    assert session.topic == "是否升息"
    assert session.max_rounds == 3


def test_create_session_writes_experts_with_contiguous_order_index(repo, engine):
    names = ["經濟學家", "工程師", "律師"]
    session = repo.create_session(topic="議題", max_rounds=2, experts=names)

    with DBSession(engine) as db:
        experts = db.exec(
            select(SessionExpert)
            .where(SessionExpert.session_id == session.id)
            .order_by(SessionExpert.order_index)
        ).all()

    assert [e.order_index for e in experts] == [0, 1, 2]
    assert [e.name for e in experts] == names


def test_create_session_with_no_experts(repo, engine):
    session = repo.create_session(topic="議題", max_rounds=1, experts=[])
    with DBSession(engine) as db:
        count = len(
            db.exec(
                select(SessionExpert).where(SessionExpert.session_id == session.id)
            ).all()
        )
    assert count == 0


@pytest.mark.parametrize("max_rounds", [0, 21])
def test_create_session_rejects_invalid_max_rounds(repo, max_rounds):
    with pytest.raises(ValidationError):
        repo.create_session(topic="t", max_rounds=max_rounds, experts=["A"])


def test_create_session_rejects_blank_topic(repo):
    with pytest.raises(ValidationError):
        repo.create_session(topic="   ", max_rounds=3, experts=["A"])


# --- AC-2：append_contribution 單一 transaction commit ---
def test_append_contribution_commits_and_is_readable(repo, engine):
    round_id, expert_id = _make_round(engine)

    contribution = repo.append_contribution(
        round_id=round_id,
        expert_id=expert_id,
        seq=0,
        viewpoint="贊成升息",
        focus_after="關注通膨",
    )
    assert contribution.id is not None

    with DBSession(engine) as db:
        reloaded = db.get(Contribution, contribution.id)
    assert reloaded is not None
    assert reloaded.round_id == round_id
    assert reloaded.session_expert_id == expert_id
    assert reloaded.seq == 0
    assert reloaded.viewpoint == "贊成升息"
    assert reloaded.focus_after == "關注通膨"


def test_append_contribution_focus_after_optional(repo, engine):
    round_id, expert_id = _make_round(engine)
    contribution = repo.append_contribution(
        round_id=round_id, expert_id=expert_id, seq=0, viewpoint="觀點"
    )
    with DBSession(engine) as db:
        reloaded = db.get(Contribution, contribution.id)
    assert reloaded.focus_after is None


# --- AC-3：重複 (round_id, seq) 被拒（append-only 保護）---
def test_duplicate_round_seq_rejected(repo, engine):
    round_id, expert_id = _make_round(engine)
    repo.append_contribution(
        round_id=round_id, expert_id=expert_id, seq=0, viewpoint="第一筆"
    )

    with pytest.raises(IntegrityError):
        repo.append_contribution(
            round_id=round_id, expert_id=expert_id, seq=0, viewpoint="重複"
        )


def test_distinct_seq_appends_succeed(repo, engine):
    round_id, expert_id = _make_round(engine)
    repo.append_contribution(
        round_id=round_id, expert_id=expert_id, seq=0, viewpoint="第一筆"
    )
    repo.append_contribution(
        round_id=round_id, expert_id=expert_id, seq=1, viewpoint="第二筆"
    )

    with DBSession(engine) as db:
        rows = db.exec(
            select(Contribution)
            .where(Contribution.round_id == round_id)
            .order_by(Contribution.seq)
        ).all()
    assert [r.seq for r in rows] == [0, 1]

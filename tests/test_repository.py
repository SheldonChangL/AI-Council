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

from eps.data.models import (
    Contribution,
    PersonaTemplate,
    Round,
    Session,
    SessionExpert,
    SessionStatus,
)
from eps.data.repository import ExpertSpec, Repository, SessionDetail


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


# --- Story 5.2 / AC-4：冪等鍵建立與查詢 ---
def test_create_session_persists_idempotency_key(repo):
    session = repo.create_session(
        topic="t", max_rounds=3, experts=["A"], idempotency_key="key-1"
    )
    assert session.idempotency_key == "key-1"


def test_find_session_by_idempotency_key_roundtrip(repo):
    created = repo.create_session(
        topic="t", max_rounds=3, experts=["A"], idempotency_key="key-1"
    )
    found = repo.find_session_by_idempotency_key("key-1")
    assert found is not None and found.id == created.id
    assert repo.find_session_by_idempotency_key("missing") is None


def test_create_session_duplicate_key_violates_unique(repo):
    repo.create_session(
        topic="t", max_rounds=3, experts=["A"], idempotency_key="dup"
    )
    with pytest.raises(IntegrityError):
        repo.create_session(
            topic="t2", max_rounds=3, experts=["B"], idempotency_key="dup"
        )


def test_create_session_null_keys_do_not_collide(repo):
    s1 = repo.create_session(topic="t1", max_rounds=3, experts=["A"])
    s2 = repo.create_session(topic="t2", max_rounds=3, experts=["B"])
    assert s1.id != s2.id  # 多個 NULL 鍵互不衝突


# =========================================================================
# Story 2.5 — 模板選用與覆寫隔離（AC-2）
# =========================================================================


def _insert_template(engine, *, name="市場分析師", system_prompt="模板內容") -> int:
    with DBSession(engine) as db:
        tpl = PersonaTemplate(name=name, system_prompt=system_prompt, builtin=True)
        db.add(tpl)
        db.commit()
        db.refresh(tpl)
        return tpl.id


def test_create_session_with_template_instantiates_persona_prompt(repo, engine):
    template_id = _insert_template(engine, system_prompt="原始模板 prompt")

    session = repo.create_session(
        topic="議題",
        max_rounds=2,
        experts=[ExpertSpec(name="分析師", source_template_id=template_id)],
    )

    with DBSession(engine) as db:
        expert = db.exec(
            select(SessionExpert).where(SessionExpert.session_id == session.id)
        ).first()
    # 未覆寫時，由模板複製 system_prompt 實例化。
    assert expert.persona_template_id == template_id
    assert expert.persona_prompt == "原始模板 prompt"


def test_create_session_override_writes_to_expert_not_template(repo, engine):
    template_id = _insert_template(engine, system_prompt="原始模板 prompt")

    session = repo.create_session(
        topic="議題",
        max_rounds=2,
        experts=[
            ExpertSpec(
                name="分析師",
                source_template_id=template_id,
                persona_prompt="我的覆寫 prompt",
            )
        ],
    )

    with DBSession(engine) as db:
        expert = db.exec(
            select(SessionExpert).where(SessionExpert.session_id == session.id)
        ).first()
        template = db.get(PersonaTemplate, template_id)

    # 覆寫值寫入 SessionExpert。
    assert expert.persona_prompt == "我的覆寫 prompt"
    assert expert.persona_template_id == template_id
    # 對應 PersonaTemplate 列保持不變（覆寫隔離）。
    assert template.system_prompt == "原始模板 prompt"


def test_create_session_plain_names_still_supported(repo, engine):
    # 向後相容：純名稱字串不掛模板、persona_prompt 為空。
    session = repo.create_session(topic="議題", max_rounds=2, experts=["A", "B"])
    with DBSession(engine) as db:
        experts = db.exec(
            select(SessionExpert)
            .where(SessionExpert.session_id == session.id)
            .order_by(SessionExpert.order_index)
        ).all()
    assert [e.name for e in experts] == ["A", "B"]
    assert all(e.persona_template_id is None for e in experts)
    assert all(e.persona_prompt == "" for e in experts)


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


# =========================================================================
# Story 2.4 — 查詢、刪除與恢復位置（AC-1, AC-2, AC-3, AC-4）
# =========================================================================


def _insert_session(engine, *, topic="t", status=SessionStatus.Created) -> int:
    """直接插入一筆會話並回傳 id（用於 list/filter 設置，繞過 create_session）。"""
    with DBSession(engine) as db:
        session = Session(topic=topic, max_rounds=3, status=status)
        db.add(session)
        db.commit()
        db.refresh(session)
        return session.id


def _build_full_session(engine, *, final_report=None) -> dict:
    """建立含 experts / rounds / contributions / final_report 的完整會話。

    回傳 ``{session_id, round_ids, expert_ids}`` 供斷言使用。回合 1 有 seq 0,1,2；
    回合 2 有 seq 0,1（用於驗證續跑位置為字典序最大，而非全域最大 seq）。
    """
    with DBSession(engine) as db:
        session = Session(topic="議題", max_rounds=3, status=SessionStatus.Running)
        if final_report is not None:
            session.final_report = final_report
        db.add(session)
        db.flush()

        experts = [
            SessionExpert(session_id=session.id, name=n, order_index=i)
            for i, n in enumerate(["經濟學家", "工程師"])
        ]
        rounds = [
            Round(session_id=session.id, round_number=1),
            Round(session_id=session.id, round_number=2),
        ]
        for obj in experts + rounds:
            db.add(obj)
        db.flush()

        # 回合 1：seq 0,1,2；回合 2：seq 0,1
        for seq in (0, 1, 2):
            db.add(
                Contribution(
                    round_id=rounds[0].id,
                    session_expert_id=experts[0].id,
                    seq=seq,
                    viewpoint=f"r1-{seq}",
                )
            )
        for seq in (0, 1):
            db.add(
                Contribution(
                    round_id=rounds[1].id,
                    session_expert_id=experts[1].id,
                    seq=seq,
                    viewpoint=f"r2-{seq}",
                )
            )
        db.commit()
        return {
            "session_id": session.id,
            "round_ids": [r.id for r in rounds],
            "expert_ids": [e.id for e in experts],
        }


# --- AC-1：list_sessions 依 created_at desc 並可依 status 過濾 ---
def test_list_sessions_orders_recent_first(repo, engine):
    first = _insert_session(engine, topic="A")
    second = _insert_session(engine, topic="B")
    third = _insert_session(engine, topic="C")

    sessions = repo.list_sessions()

    # 最近建立優先：以 id 遞減作穩定次序，最新（第三筆）排最前。
    assert [s.id for s in sessions] == [third, second, first]


def test_list_sessions_filters_by_status(repo, engine):
    _insert_session(engine, status=SessionStatus.Created)
    running = _insert_session(engine, status=SessionStatus.Running)
    _insert_session(engine, status=SessionStatus.Completed)

    result = repo.list_sessions(status=SessionStatus.Running)

    assert [s.id for s in result] == [running]
    assert all(s.status is SessionStatus.Running for s in result)


def test_list_sessions_no_match_returns_empty(repo, engine):
    _insert_session(engine, status=SessionStatus.Created)
    assert repo.list_sessions(status=SessionStatus.Failed) == []


def test_list_sessions_limit_and_offset(repo, engine):
    ids = [_insert_session(engine, topic=f"S{i}") for i in range(5)]
    recent_first = list(reversed(ids))

    page = repo.list_sessions(limit=2, offset=1)

    assert [s.id for s in page] == recent_first[1:3]


# --- AC-2：get_session_detail 回傳完整聚合 ---
def test_get_session_detail_aggregates_full_graph(repo, engine):
    built = _build_full_session(engine, final_report="最終綜整")

    detail = repo.get_session_detail(built["session_id"])

    assert isinstance(detail, SessionDetail)
    assert detail.session.id == built["session_id"]
    assert detail.final_report == "最終綜整"
    # experts 依 order_index、rounds 依 round_number、contributions 依 (round_id, seq)
    assert [e.order_index for e in detail.experts] == [0, 1]
    assert [r.round_number for r in detail.rounds] == [1, 2]
    assert [(c.round_id, c.seq) for c in detail.contributions] == [
        (built["round_ids"][0], 0),
        (built["round_ids"][0], 1),
        (built["round_ids"][0], 2),
        (built["round_ids"][1], 0),
        (built["round_ids"][1], 1),
    ]


def test_get_session_detail_final_report_none_by_default(repo, engine):
    built = _build_full_session(engine)
    detail = repo.get_session_detail(built["session_id"])
    assert detail.final_report is None


def test_get_session_detail_missing_returns_none(repo):
    assert repo.get_session_detail(9999) is None


# --- AC-3：get_resume_position 回傳字典序最大 (round_number, seq) ---
def test_get_resume_position_returns_lexicographic_max(repo, engine):
    built = _build_full_session(engine)
    # 回合 1 最高 seq=2，回合 2 最高 seq=1；字典序最大為 (2, 1)，非全域最大 seq。
    assert repo.get_resume_position(built["session_id"]) == (2, 1)


def test_get_resume_position_no_contributions_returns_none(repo, engine):
    # 有會話與回合但無任何發言。
    with DBSession(engine) as db:
        session = Session(topic="t", max_rounds=3)
        db.add(session)
        db.flush()
        db.add(Round(session_id=session.id, round_number=1))
        db.commit()
        session_id = session.id
    assert repo.get_resume_position(session_id) is None


def test_get_resume_position_missing_session_returns_none(repo):
    assert repo.get_resume_position(9999) is None


# --- AC-4：delete_session 真刪會話與子資料 ---
def test_delete_session_removes_session_and_children(repo, engine):
    built = _build_full_session(engine, final_report="x")
    session_id = built["session_id"]

    assert repo.delete_session(session_id) is True

    # 再查詢找不到。
    assert repo.get_session_detail(session_id) is None
    with DBSession(engine) as db:
        assert db.get(Session, session_id) is None
        assert (
            db.exec(
                select(SessionExpert).where(
                    SessionExpert.session_id == session_id
                )
            ).all()
            == []
        )
        assert (
            db.exec(
                select(Round).where(Round.session_id == session_id)
            ).all()
            == []
        )
        assert (
            db.exec(
                select(Contribution).where(
                    Contribution.round_id.in_(built["round_ids"])
                )
            ).all()
            == []
        )


def test_delete_session_missing_returns_false(repo):
    assert repo.delete_session(9999) is False


# --- Story 5.3：get_session 輕量讀取會話本體（cancel/retry 判定狀態用）---
def test_get_session_returns_row_with_status(repo):
    created = repo.create_session(
        topic="議題", max_rounds=3, experts=["E0"]
    )
    repo.set_status(created.id, SessionStatus.Running)

    fetched = repo.get_session(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.status == SessionStatus.Running


def test_get_session_missing_returns_none(repo):
    assert repo.get_session(9999) is None

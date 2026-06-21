"""eps Repository — 建立會話與里程碑 append-only 寫入（Story 2.3 / FR-15, NFR-5 / 藍圖 §3.2）。

提供編排引擎落地語意里程碑所需的兩個寫入操作：

- ``create_session``：建立 ``Session`` 並以連續 ``order_index``（0..n-1）寫入參與專家。
- ``append_contribution``：在單一 transaction 內寫入一筆 ``Contribution``。

append-only 保護依賴 ``Contribution`` 的唯一約束 ``(round_id, seq)``：對相同
``(round_id, seq)`` 重複寫入會由資料庫拋出 ``IntegrityError``，不在此吞掉。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple, Union

from sqlalchemy.engine import Engine
from sqlmodel import Session as DBSession, select

from eps.data.models import (
    Contribution,
    PersonaTemplate,
    Round,
    Session,
    SessionExpert,
    SessionStatus,
)


@dataclass(frozen=True)
class ExpertSpec:
    """建立會話時的單一專家規格（Story 2.5 / AC-2）。

    - ``source_template_id``：選用的來源 ``PersonaTemplate`` id（sourceTemplateId）。
    - ``persona_prompt``：覆寫的人設 prompt；非空時取代模板內容（覆寫隔離）。

    解析規則（``create_session`` 內）：
    - 提供 ``persona_prompt`` → 直接採用（覆寫）。
    - 否則提供 ``source_template_id`` → 複製該模板的 ``system_prompt``（實例化）。
    - 兩者皆無 → 空字串。

    任一情形都只寫入 ``SessionExpert``，不回寫 ``PersonaTemplate``。
    """

    name: str
    source_template_id: Optional[int] = None
    persona_prompt: str = ""


@dataclass(frozen=True)
class SessionDetail:
    """``get_session_detail`` 回傳的完整會話聚合（Story 2.4 / AC-2）。

    ``experts`` 依 ``order_index``、``rounds`` 依 ``round_number``、``contributions``
    依 ``(round_id, seq)`` 排序；``final_report`` 取自 ``Session.final_report``。
    """

    session: Session
    experts: List[SessionExpert]
    rounds: List[Round]
    contributions: List[Contribution]
    final_report: Optional[str]


class Repository:
    """以 SQLAlchemy ``Engine`` 為依賴的持久化進入點。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_session(
        self,
        topic: str,
        max_rounds: int,
        experts: Sequence[Union[str, ExpertSpec]],
    ) -> Session:
        """建立會話並寫入參與專家（AC-1 / Story 2.5 AC-2）。

        ``topic`` / ``max_rounds`` 由 ``Session`` 模型驗證（非法值拋出
        ``ValidationError``）。``experts`` 以列舉位置指定連續 ``order_index``
        （0..n-1），會話與專家於單一 transaction 內原子寫入。

        每個專家可為純名稱字串（無模板、無覆寫）或 ``ExpertSpec``。當 spec 指定
        ``source_template_id`` 時，依其規則解析 ``persona_prompt``（覆寫優先，否則
        複製模板 ``system_prompt``）寫入 ``SessionExpert``；對應的 ``PersonaTemplate``
        列保持不變（覆寫隔離）。
        """
        with DBSession(self._engine) as db:
            session = Session(topic=topic, max_rounds=max_rounds)
            db.add(session)
            db.flush()  # 取得自增 id 以供 SessionExpert 外鍵引用
            for order_index, raw in enumerate(experts):
                spec = ExpertSpec(name=raw) if isinstance(raw, str) else raw
                persona_prompt = self._resolve_persona_prompt(db, spec)
                db.add(
                    SessionExpert(
                        session_id=session.id,
                        persona_template_id=spec.source_template_id,
                        name=spec.name,
                        persona_prompt=persona_prompt,
                        order_index=order_index,
                    )
                )
            db.commit()
            db.refresh(session)
            return session

    @staticmethod
    def _resolve_persona_prompt(db: DBSession, spec: ExpertSpec) -> str:
        """解析寫入 ``SessionExpert`` 的人設 prompt（覆寫隔離，AC-2）。

        覆寫值優先；否則由來源模板複製 ``system_prompt``。僅讀取模板，絕不修改它。
        """
        if spec.persona_prompt:
            return spec.persona_prompt
        if spec.source_template_id is not None:
            template = db.get(PersonaTemplate, spec.source_template_id)
            if template is not None:
                return template.system_prompt
        return ""

    def append_contribution(
        self,
        round_id: int,
        expert_id: int,
        seq: int,
        viewpoint: str,
        focus_after: Optional[str] = None,
    ) -> Contribution:
        """於單一 transaction 寫入一筆里程碑發言（AC-2 / AC-3）。

        對相同 ``(round_id, seq)`` 重複寫入會因唯一約束被資料庫拒絕並拋出
        ``IntegrityError``（append-only 保護），呼叫端負責處理。
        """
        with DBSession(self._engine) as db:
            contribution = Contribution(
                round_id=round_id,
                session_expert_id=expert_id,
                seq=seq,
                viewpoint=viewpoint,
                focus_after=focus_after,
            )
            db.add(contribution)
            db.commit()
            db.refresh(contribution)
            return contribution

    def list_sessions(
        self,
        status: Optional[SessionStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Session]:
        """列出會話，最近建立優先，可依 status 過濾（AC-1）。

        依 ``created_at`` 遞減排序；同一時間戳以 ``id`` 遞減作穩定次序（最新優先）。
        ``status`` 為 ``None`` 時不過濾。``limit`` / ``offset`` 提供分頁。
        """
        with DBSession(self._engine) as db:
            stmt = select(Session)
            if status is not None:
                stmt = stmt.where(Session.status == status)
            stmt = (
                stmt.order_by(Session.created_at.desc(), Session.id.desc())
                .offset(offset)
                .limit(limit)
            )
            return list(db.exec(stmt).all())

    def list_personas(self, builtin_only: bool = True) -> List[PersonaTemplate]:
        """列出 Persona 模板，預設僅回傳系統內建模板（Story 2.6 / AC-2）。

        依 ``id`` 遞增排序（即內建 seed 的寫入順序）。``builtin_only`` 為 ``True``
        時僅回傳 ``builtin=True`` 的模板。
        """
        with DBSession(self._engine) as db:
            stmt = select(PersonaTemplate)
            if builtin_only:
                stmt = stmt.where(PersonaTemplate.builtin == True)  # noqa: E712
            stmt = stmt.order_by(PersonaTemplate.id)
            return list(db.exec(stmt).all())

    def get_session_detail(self, session_id: int) -> Optional[SessionDetail]:
        """回傳含 rounds/contributions/final_report 的完整會話聚合（AC-2）。

        會話不存在時回傳 ``None``（含已被 ``delete_session`` 刪除者，AC-4 的「找不到」）。
        """
        with DBSession(self._engine) as db:
            session = db.get(Session, session_id)
            if session is None:
                return None

            experts = list(
                db.exec(
                    select(SessionExpert)
                    .where(SessionExpert.session_id == session_id)
                    .order_by(SessionExpert.order_index)
                ).all()
            )
            rounds = list(
                db.exec(
                    select(Round)
                    .where(Round.session_id == session_id)
                    .order_by(Round.round_number)
                ).all()
            )
            round_ids = [r.id for r in rounds]
            contributions: List[Contribution] = []
            if round_ids:
                contributions = list(
                    db.exec(
                        select(Contribution)
                        .where(Contribution.round_id.in_(round_ids))
                        .order_by(Contribution.round_id, Contribution.seq)
                    ).all()
                )

            return SessionDetail(
                session=session,
                experts=experts,
                rounds=rounds,
                contributions=contributions,
                final_report=session.final_report,
            )

    def get_resume_position(self, session_id: int) -> Optional[Tuple[int, int]]:
        """回傳目前最大的 ``(round_number, seq)`` 續跑座標（AC-3）。

        以 ``Contribution`` 接回所屬 ``Round``，取字典序最大的
        ``(round_number, seq)``（最高回合、且該回合最高序號的發言）。會話尚無任何
        發言（含不存在的會話）時回傳 ``None``，表示應從頭開始。
        """
        with DBSession(self._engine) as db:
            row = db.exec(
                select(Round.round_number, Contribution.seq)
                .join(Contribution, Contribution.round_id == Round.id)
                .where(Round.session_id == session_id)
                .order_by(Round.round_number.desc(), Contribution.seq.desc())
            ).first()
            if row is None:
                return None
            return (row[0], row[1])

    def delete_session(self, session_id: int) -> bool:
        """真刪會話與其全部子資料，單一 transaction（AC-4）。

        依外鍵相依由子到父刪除 contributions → rounds / experts → session。
        刪除成功回傳 ``True``；會話不存在回傳 ``False``。
        """
        with DBSession(self._engine) as db:
            session = db.get(Session, session_id)
            if session is None:
                return False

            rounds = list(
                db.exec(
                    select(Round).where(Round.session_id == session_id)
                ).all()
            )
            round_ids = [r.id for r in rounds]
            if round_ids:
                contributions = db.exec(
                    select(Contribution).where(Contribution.round_id.in_(round_ids))
                ).all()
                for contribution in contributions:
                    db.delete(contribution)
            experts = db.exec(
                select(SessionExpert).where(SessionExpert.session_id == session_id)
            ).all()
            for expert in experts:
                db.delete(expert)
            for rnd in rounds:
                db.delete(rnd)
            db.delete(session)
            db.commit()
            return True

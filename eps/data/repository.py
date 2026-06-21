"""eps Repository — 建立會話與里程碑 append-only 寫入（Story 2.3 / FR-15, NFR-5 / 藍圖 §3.2）。

提供編排引擎落地語意里程碑所需的兩個寫入操作：

- ``create_session``：建立 ``Session`` 並以連續 ``order_index``（0..n-1）寫入參與專家。
- ``append_contribution``：在單一 transaction 內寫入一筆 ``Contribution``。

append-only 保護依賴 ``Contribution`` 的唯一約束 ``(round_id, seq)``：對相同
``(round_id, seq)`` 重複寫入會由資料庫拋出 ``IntegrityError``，不在此吞掉。
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy.engine import Engine
from sqlmodel import Session as DBSession

from eps.data.models import Contribution, Session, SessionExpert


class Repository:
    """以 SQLAlchemy ``Engine`` 為依賴的持久化進入點。"""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_session(
        self, topic: str, max_rounds: int, experts: Sequence[str]
    ) -> Session:
        """建立會話並寫入參與專家（AC-1）。

        ``topic`` / ``max_rounds`` 由 ``Session`` 模型驗證（非法值拋出
        ``ValidationError``）。``experts`` 以列舉位置指定連續 ``order_index``
        （0..n-1），會話與專家於單一 transaction 內原子寫入。
        """
        with DBSession(self._engine) as db:
            session = Session(topic=topic, max_rounds=max_rounds)
            db.add(session)
            db.flush()  # 取得自增 id 以供 SessionExpert 外鍵引用
            for order_index, name in enumerate(experts):
                db.add(
                    SessionExpert(
                        session_id=session.id,
                        name=name,
                        order_index=order_index,
                    )
                )
            db.commit()
            db.refresh(session)
            return session

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

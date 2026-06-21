"""eps 內建 Persona 模板庫 seed（Story 2.5 / FR-2, FR-3 / AC-1, AC-3）。

提供冪等的 seed 函式，於乾淨資料庫寫入系統內建（``builtin=True``）Persona
模板。內建模板為唯讀：系統不提供修改 API，且重複執行 seed 僅補齊「依名稱尚未
存在」的模板，絕不覆寫或變更既有內建列（AC-3）。
"""

from __future__ import annotations

from typing import List

from sqlalchemy.engine import Engine
from sqlmodel import Session as DBSession, select

from eps.data.models import PersonaTemplate

# AC-1：至少「市場分析師、技術架構師、倫理學家」三個內建模板。
BUILTIN_PERSONA_TEMPLATES: List[dict] = [
    {
        "name": "市場分析師",
        "description": "從市場需求、競爭格局與商業可行性切入分析。",
        "system_prompt": (
            "你是一位資深市場分析師。請從市場規模、目標客群、競爭態勢、"
            "商業模式與獲利可行性等角度，提供具數據意識且務實的觀點。"
        ),
    },
    {
        "name": "技術架構師",
        "description": "從系統架構、可擴展性與技術風險切入分析。",
        "system_prompt": (
            "你是一位資深技術架構師。請從系統設計、可擴展性、可靠性、"
            "技術選型與長期維運風險等角度，提供嚴謹且可落地的觀點。"
        ),
    },
    {
        "name": "倫理學家",
        "description": "從倫理、公平性與社會影響切入分析。",
        "system_prompt": (
            "你是一位倫理學家。請從倫理原則、公平性、隱私、潛在偏見與"
            "對利害關係人的社會影響等角度，提供審慎且具批判性的觀點。"
        ),
    },
]


def seed_persona_templates(engine: Engine) -> int:
    """冪等寫入內建 Persona 模板，回傳本次新增的列數（AC-1, AC-3）。

    對 ``BUILTIN_PERSONA_TEMPLATES`` 中每個模板，僅在「不存在同名內建模板」時
    插入（``builtin=True``）。既有內建列一律保持不變，因此重複執行不會產生重複
    資料，也不會覆寫使用者層面或既有內建內容（內建模板唯讀）。
    """
    inserted = 0
    with DBSession(engine) as db:
        for spec in BUILTIN_PERSONA_TEMPLATES:
            exists = db.exec(
                select(PersonaTemplate).where(
                    PersonaTemplate.name == spec["name"],
                    PersonaTemplate.builtin == True,  # noqa: E712 - SQL 布林比較
                )
            ).first()
            if exists is not None:
                continue
            db.add(
                PersonaTemplate(
                    name=spec["name"],
                    description=spec["description"],
                    system_prompt=spec["system_prompt"],
                    builtin=True,
                )
            )
            inserted += 1
        db.commit()
    return inserted


__all__ = ["BUILTIN_PERSONA_TEMPLATES", "seed_persona_templates"]

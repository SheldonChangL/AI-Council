"""Story 2.5 — 內建 Persona 模板庫 seed 與唯讀（AC-1, AC-3）。

驗證：
- AC-1：乾淨資料庫 seed 後，``PersonaTemplate`` 含至少「市場分析師、技術架構師、
  倫理學家」三個 ``builtin=True`` 模板。
- AC-3：內建模板唯讀——重複 seed 不新增重複列，且既有內建列內容不被覆寫。
"""

import pytest
from sqlmodel import SQLModel, create_engine, select
from sqlmodel import Session as DBSession

from eps.data.models import PersonaTemplate
from eps.data.seed import BUILTIN_PERSONA_TEMPLATES, seed_persona_templates

REQUIRED_NAMES = {"市場分析師", "技術架構師", "倫理學家"}


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


# --- AC-1：seed 建立三個 builtin 模板 ---
def test_seed_creates_required_builtin_templates(engine):
    inserted = seed_persona_templates(engine)
    assert inserted >= 3

    with DBSession(engine) as db:
        builtins = db.exec(
            select(PersonaTemplate).where(PersonaTemplate.builtin == True)  # noqa: E712
        ).all()

    names = {t.name for t in builtins}
    assert REQUIRED_NAMES <= names
    assert all(t.builtin is True for t in builtins)
    assert all(t.system_prompt for t in builtins)


def test_builtin_default_is_false_for_user_templates(engine):
    # 非經 seed 建立的模板預設為非內建。
    with DBSession(engine) as db:
        db.add(PersonaTemplate(name="自訂"))
        db.commit()
        reloaded = db.exec(
            select(PersonaTemplate).where(PersonaTemplate.name == "自訂")
        ).first()
    assert reloaded.builtin is False


# --- AC-3：內建模板唯讀（冪等 seed，不覆寫既有內建列）---
def test_seed_is_idempotent_no_duplicates(engine):
    seed_persona_templates(engine)
    second = seed_persona_templates(engine)

    assert second == 0
    with DBSession(engine) as db:
        count = len(
            db.exec(
                select(PersonaTemplate).where(
                    PersonaTemplate.builtin == True  # noqa: E712
                )
            ).all()
        )
    assert count == len(BUILTIN_PERSONA_TEMPLATES)


def test_reseed_does_not_overwrite_existing_builtin(engine):
    seed_persona_templates(engine)

    # 模擬內建列被外部讀取後，二次 seed 不得變更其內容。
    with DBSession(engine) as db:
        before = db.exec(
            select(PersonaTemplate).where(PersonaTemplate.name == "市場分析師")
        ).first()
        before_id, before_prompt = before.id, before.system_prompt

    seed_persona_templates(engine)

    with DBSession(engine) as db:
        after = db.exec(
            select(PersonaTemplate).where(PersonaTemplate.name == "市場分析師")
        ).first()
    assert after.id == before_id
    assert after.system_prompt == before_prompt

"""Explicit unsupported-spec messages at the warrior-only guards
(talent honesty infra, 2026-06-12).

Silent feature absence reads as "broken tool" — two independent persona
reviews called it the dealbreaker. The Talent A/B guard is covered by
AppTest in `tests/test_talent_diff_panel.py`; this file pins the skill-ladder
guard, which only renders deep inside the verdict flow and is cheaper to test
at the function level with a captured `st.caption`.
"""

from __future__ import annotations

from simf.core.character import Character


def _non_warrior() -> Character:
    return Character(
        name="GuardTest",
        race="blood_elf",
        class_spec="vengeance_demon_hunter",
        talents="default-dh",
        strength=1000,
        stamina=30000,
        armor_from_gear=4000,
        haste_rating=1500,
        crit_rating=1200,
        mastery_rating=1000,
        versatility_rating=300,
    )


def test_skill_ladder_renders_explicit_message_for_non_warrior(monkeypatch):
    """The non-warrior path must (a) emit the explicit caption and (b) return
    before touching the verdict — we pass ``verdict=None``, so any attempt to
    proceed past the guard raises AttributeError and fails the test."""
    from simf.ui import verdict as verdict_module

    captured: list[str] = []
    monkeypatch.setattr(verdict_module.st, "caption", lambda text, **kw: captured.append(text))

    verdict_module._render_skill_ladder_panel(_non_warrior(), None)

    assert len(captured) == 1
    assert "Prot Warrior–only" in captured[0]
    assert "calibration" in captured[0]

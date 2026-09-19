"""Tests for the key-level verdict's "assumes proper defensive use" caveat.

Ideas Backlog (ROADMAP.md 2026-05-19) — single-copy-line player-skill
disclaimer beneath the key-level verdict headline. The Prot-Warrior
skill-adjusted ladder (Phase 2.10) quantifies this assumption with
measured uptimes; for the other five tank specs the ladder doesn't
render, so this caption is the only place the assumption is stated.

These tests pin:
  1. The spec-aware helper builds the expected copy per tank spec.
  2. Every modelled tank spec is covered (no spec falls through to the
     spec-agnostic fallback by accident).
  3. The caption sits in the verdict panel (source-grep) so a future
     refactor that drops the call site fails loudly.
  4. The caption copy is distinct from the existing "avoidable-
     mechanic damage isn't modeled" caveat — separate concerns,
     separate sentences.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simf.ui.app import (
    _SKILL_ASSUMPTION_CDS_BY_SPEC,
    _skill_assumption_caption_for_spec,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
# `_render_key_level_verdict_panel` moved into `simf.ui.verdict` (L3 render-panel
# split); the source-grep tripwire reads the module where it now lives.
VERDICT_PY = REPO_ROOT / "src" / "simf" / "ui" / "verdict.py"


# All six tank specs simf currently knows about. If a seventh tank spec
# is added (heh) and not registered here, the parametrised test below
# will pass-but-incomplete — but `test_every_modelled_tank_spec_covered`
# below ties to `MITIGATION_DISPATCH` so the missing-spec case fails
# loudly there.
MODELLED_TANK_SPECS = [
    "protection_warrior",
    "protection_paladin",
    "blood_death_knight",
    "vengeance_demon_hunter",
    "brewmaster_monk",
    "guardian_druid",
]


@pytest.mark.parametrize("spec", MODELLED_TANK_SPECS)
def test_caption_has_spec_specific_copy(spec: str) -> None:
    """Each modelled tank spec gets its own CD list — a Blood DK
    reader should NOT see "Shield Block" (that would be Warrior copy)
    bleeding through."""
    caption = _skill_assumption_caption_for_spec(spec)
    assert "Assumes proper defensive use." in caption
    # The CD clause for this spec must appear verbatim.
    assert _SKILL_ASSUMPTION_CDS_BY_SPEC[spec] in caption


def test_warrior_caption_names_warrior_cds() -> None:
    caption = _skill_assumption_caption_for_spec("protection_warrior")
    assert "Shield Block" in caption
    assert "Demoralizing Shout" in caption
    assert "Ignore Pain" in caption


def test_blood_dk_caption_does_not_say_shield_block() -> None:
    """Cross-spec leakage tripwire: the original draft of this caption
    was Warrior-only and would have rendered "Shield Block" for every
    spec. This test catches a future regression that drops the per-spec
    map and re-introduces hardcoded Warrior copy."""
    caption = _skill_assumption_caption_for_spec("blood_death_knight")
    assert "Shield Block" not in caption
    assert "Death Strike" in caption


def test_unknown_spec_falls_back_to_generic_phrasing() -> None:
    """The fallback is defensive — every modelled tank spec has an
    entry above, but a typo'd spec key should still render a
    grammatical sentence (not crash, not render an empty `{}` slot)."""
    caption = _skill_assumption_caption_for_spec("not_a_real_spec")
    assert "Assumes proper defensive use." in caption
    assert "your defensive cooldowns" in caption


def test_every_modelled_tank_spec_covered() -> None:
    """Coupling tripwire — if ``LONG_CD_BUTTONS`` (the central per-spec
    long-CD registry used by the planner) gains a new tank spec, the
    assumption-map must add an entry for it or this test fails.
    Prevents the silent-fallback-to-generic case (a real spec would
    render the generic copy instead of its own CD names)."""
    from simf.core.cooldown_planner import LONG_CD_BUTTONS

    modelled = set(LONG_CD_BUTTONS.keys())
    in_map = set(_SKILL_ASSUMPTION_CDS_BY_SPEC.keys())
    missing = modelled - in_map
    assert not missing, (
        f"New tank spec(s) in LONG_CD_BUTTONS but missing from "
        f"_SKILL_ASSUMPTION_CDS_BY_SPEC: {sorted(missing)}. The "
        f"verdict panel would render the spec-agnostic fallback "
        f"('your defensive cooldowns') instead of named spells."
    )


def test_caption_renders_in_verdict_panel() -> None:
    """Source-grep tripwire — the panel must actually call the helper.
    A refactor that drops the call site (e.g., moves the panel to a
    new file and forgets to re-wire) fails this test instead of
    silently dropping the caveat from production."""
    text = VERDICT_PY.read_text()
    assert "_skill_assumption_caption_for_spec(char.class_spec)" in text, (
        "_render_key_level_verdict_panel no longer calls the skill-"
        "assumption helper — the player-skill caveat is missing from "
        "the rendered surface."
    )


def test_caption_distinct_from_modelling_scope_caveat() -> None:
    """The panel ALSO carries an "avoidable-mechanic damage isn't
    modeled" caption — that's about modelling scope (what the engine
    simulates). The skill caveat is about player execution (whether
    the player presses the buttons the policy assumes). Both must
    coexist; this test pins the distinction by asserting the skill
    caption does NOT use the modelling-scope language."""
    for spec in MODELLED_TANK_SPECS:
        caption = _skill_assumption_caption_for_spec(spec)
        # "avoidable-mechanic" / "swirlies" / "mechanic damage" are
        # the scope-caveat phrases. The skill caveat must not borrow
        # them — they'd confuse the two concerns.
        assert "avoidable-mechanic" not in caption.lower()
        assert "mechanic damage" not in caption.lower()
        assert "swirlies" not in caption.lower()

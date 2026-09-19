"""Tests for the canonical eHP definition (`ui/helpers/ehp_gloss.py`).

Regression coverage for the 2026-07-09 fix: the single `EHP_GLOSS` string
used to hardcode Protection Warrior's own always-on passive ("Defensive
Stance") and excluded active mitigation ("Shield Block/Shield Wall") — and
that exact text rendered on every spec's Vault/Gear tab, so a Guardian
Druid, Protection Paladin, Blood DK, Vengeance DH, or Brewmaster Monk read
Warrior abilities they don't have. `ehp_gloss_text(class_spec)` fixes this
by naming each spec's own modeled examples, falling back to spec-agnostic
wording rather than fabricating an example for a spec with none modeled.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from simf.ui.helpers.ehp_gloss import (
    EHP_GLOSS,
    ehp_gloss_core_text,
    ehp_gloss_exclusion_text,
    ehp_gloss_text,
)

_PREFIX = "eHP = effective HP"


def test_generic_fallback_names_no_specific_ability():
    """`class_spec=None` (or an unmodeled/unknown spec) must render the
    spec-agnostic phrasing — no Warrior (or any other spec's) ability name
    leaks into the default."""
    text = ehp_gloss_text(None)
    assert text == EHP_GLOSS
    assert text.startswith(_PREFIX)
    for leaked in (
        "Defensive Stance",
        "Shield Block",
        "Shield Wall",
        "Thick Hide",
        "Ironfur",
        "Predictive Training",
    ):
        assert leaked not in text


def test_unknown_spec_string_also_falls_back_to_generic():
    # A brand-new spec_id this codebase hasn't added a map entry for yet
    # (e.g. a future Hero spec) must degrade gracefully, not raise/guess.
    assert ehp_gloss_text("some_future_tank_spec") == EHP_GLOSS


def test_protection_warrior_names_its_own_examples():
    text = ehp_gloss_text("protection_warrior")
    assert text.startswith(_PREFIX)
    assert "Defensive Stance" in text
    assert "Shield Block/Shield Wall" in text


def test_guardian_druid_does_not_see_warrior_wording():
    text = ehp_gloss_text("guardian_druid")
    assert "Thick Hide" in text
    assert "Ironfur" in text
    assert "Defensive Stance" not in text
    assert "Shield Block" not in text
    assert "Shield Wall" not in text


def test_protection_paladin_names_its_own_excluded_cds_but_no_fake_passive():
    # ProtPal has no always-on-passive term in `_always_on_dr()` today
    # (see character.py) — the passive clause must fall back to the
    # generic phrasing rather than inventing one, while the excluded-CD
    # clause (which IS real) names Paladin's own buttons.
    text = ehp_gloss_text("protection_paladin")
    assert "Shield of the Righteous/Ardent Defender" in text
    assert "Defensive Stance" not in text
    assert "Shield Block" not in text
    assert "any modeled always-on passive damage reduction" in text


def test_blood_death_knight_and_vengeance_dh_name_their_own_excluded_cds():
    dk_text = ehp_gloss_text("blood_death_knight")
    assert "Vampiric Blood/Icebound Fortitude" in dk_text
    assert "Defensive Stance" not in dk_text

    dh_text = ehp_gloss_text("vengeance_demon_hunter")
    assert "Demon Spikes/Metamorphosis" in dh_text
    assert "Shield Block" not in dh_text


def test_brewmaster_names_conditional_passive_and_own_cds():
    text = ehp_gloss_text("brewmaster_monk")
    assert "Predictive Training" in text
    assert "Purifying Brew/Fortifying Brew" in text
    assert "Defensive Stance" not in text


def test_every_known_spec_string_starts_with_the_shared_prefix():
    # A caller matching on the shared prefix (e.g. a test elsewhere in the
    # suite) must find it regardless of which spec's examples follow.
    for spec in (
        None,
        "protection_warrior",
        "protection_paladin",
        "blood_death_knight",
        "vengeance_demon_hunter",
        "brewmaster_monk",
        "guardian_druid",
    ):
        assert ehp_gloss_text(spec).startswith(_PREFIX)


def test_core_and_exclusion_text_join_to_the_full_text():
    """`ehp_gloss_text` must stay exactly the join of the two split
    functions (2026-07-26, mobile round 2) — every existing caller
    (`render_ehp_gloss`, `EHP_GLOSS`, the Gear-tab price sheet) keeps
    getting the same full definition it always did; only vault_panel.py's
    eager caption switched to calling the two halves separately."""
    for spec in (None, "protection_warrior", "guardian_druid", "brewmaster_monk"):
        assert (
            ehp_gloss_text(spec) == f"{ehp_gloss_core_text(spec)} {ehp_gloss_exclusion_text(spec)}"
        )


def test_core_text_has_the_prefix_and_passive_example_but_not_the_exclusion_clause():
    core = ehp_gloss_core_text("protection_warrior")
    assert core.startswith(_PREFIX)
    assert "Defensive Stance" in core
    assert "Shield Block/Shield Wall" not in core
    assert "excludes" not in core


def test_exclusion_text_has_the_excluded_cd_example_but_not_the_prefix_or_passive():
    exclusion = ehp_gloss_exclusion_text("protection_warrior")
    assert not exclusion.startswith(_PREFIX)
    assert "Shield Block/Shield Wall" in exclusion
    assert "Defensive Stance" not in exclusion


def test_core_and_exclusion_text_fall_back_to_generic_for_unknown_spec():
    assert ehp_gloss_core_text("some_future_tank_spec") == ehp_gloss_core_text(None)
    assert ehp_gloss_exclusion_text("some_future_tank_spec") == ehp_gloss_exclusion_text(None)
    assert "any modeled always-on passive damage reduction" in ehp_gloss_core_text(None)
    assert "your on-cooldown defensive presses" in ehp_gloss_exclusion_text(None)


def _render_script(class_spec: str | None) -> None:
    from simf.ui.helpers.ehp_gloss import render_ehp_gloss

    render_ehp_gloss(class_spec)


def test_render_ehp_gloss_renders_the_spec_aware_caption():
    at = AppTest.from_function(_render_script, kwargs={"class_spec": "guardian_druid"})
    at.run()
    assert not at.exception, f"Unhandled exception: {at.exception}"
    captions = " ".join(c.value for c in at.caption)
    assert "Thick Hide" in captions
    assert "Defensive Stance" not in captions


def test_render_ehp_gloss_defaults_to_generic_when_class_spec_omitted():
    at = AppTest.from_function(_render_script, kwargs={"class_spec": None})
    at.run()
    assert not at.exception, f"Unhandled exception: {at.exception}"
    captions = " ".join(c.value for c in at.caption)
    assert EHP_GLOSS in captions


def _render_script_no_args() -> None:
    from simf.ui.helpers.ehp_gloss import render_ehp_gloss

    render_ehp_gloss()


def test_render_ehp_gloss_default_arg_renders_generic_text():
    # `render_ehp_gloss()` (no class_spec) must still be a valid call — some
    # existing call sites may not have a character in scope — and it must
    # render the same generic text as an explicit `class_spec=None`.
    at = AppTest.from_function(_render_script_no_args)
    at.run()
    assert not at.exception, f"Unhandled exception: {at.exception}"
    captions = " ".join(c.value for c in at.caption)
    assert EHP_GLOSS in captions

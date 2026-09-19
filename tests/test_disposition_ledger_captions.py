"""Caption-honesty regression tests for the disposition ledger — round-1
triage items 4, 5, 6 (`disposition_ledger.py`, 2026-07-09/10 review).

Drives the REAL panel through the full app (a real Protection Warrior
character, a real single-run Monte Carlo sim for the ledger itself — fast
enough not to mock, same as the sibling expander test) rather than
stubbing `st.caption`, so these pin the actual rendered copy a user sees,
not just a unit's return value.

Item 4: the caption asserted as fact that "'Absorbed' shrinks and 'Landed
as damage' grows somewhat at higher keys even though block/armor/
versatility stay flat percentages." At the time this was written,
`scale_damage_profile` only scaled swing/tank-buster damage, not each
mob's scripted `casts` list, so the damage-type MIX (not just its size)
shifted between key levels — undermining the "block/armor/versatility
stay flat" premise. This pins that the specific directional claim is gone,
not just caveated on top. (`scale_damage_profile` was fixed 2026-07-12 to
scale casts too — see docs/validation/magic_cast_scaling_gap_2026_07_09.md
— but the removed claim still isn't exactly true post-fix either, so this
test's assertion stands regardless.)

Item 5: "this build's own kit put back 0%" read a structural model gap
(no Protection-Warrior code path ever calls `apply_self_heal` today — only
`classes/guardian_druid.py` does) as if it were a real "this build
self-heals for nothing" finding.

Item 6: "reload this expander at a different key" pointed at a control
that doesn't exist anywhere on this page.
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from simf.core.key_level_verdict import KeyLevelPoint, KeyLevelVerdict

APP_PATH = Path(__file__).parent.parent / "src" / "simf" / "ui" / "app.py"


def _prot_warrior_char_data() -> dict:
    return {
        "name": "Brutoh",
        "race": "earthen",
        "class_spec": "protection_warrior",
        "talents": "kiratank-defensive",
        "strength": 2182,
        "stamina": 34176,
        "armor_from_gear": 5015,
        "haste_rating": 2318,
        "crit_rating": 1391,
        "mastery_rating": 1608,
        "versatility_rating": 296,
        "max_hp_override": 751872,
    }


def _seeded_app() -> AppTest:
    """A running app with a fresh (pre-clicked) key-level verdict cached,
    so the disposition ledger renders without paying for the full +2..+24
    sweep — mirrors `test_key_level_verdict_hrps_surfacing_removed.py`.
    The ledger's OWN sim (one 500-iteration run, not a sweep) runs for
    real here — it's fast and this is exactly the code path the fix
    touches."""
    from simf.core.character import Character

    char_data = _prot_warrior_char_data()
    char = Character.from_dict(char_data)
    push_key = 15

    verdict = KeyLevelVerdict(
        points=[
            KeyLevelPoint(
                key_level=push_key,
                damage_multiplier=1.0,
                death_rate=0.02,
                mean_dtps=40_000.0,
                p99_5s_window=1.0,
                band="comfortable",
                mean_hrps=1_000.0,
                normalized_tank_score=0.85,
            )
        ],
        comfortable_max=push_key,
        prog_ceiling=None,
        affix="fortified",
    )

    char_key = (
        char.class_spec,
        round(char.max_hp(), 0),
        round(char.total_armor(), 0),
        round(char.versatility_pct(), 4),
    )

    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    app.session_state["char_data"] = char_data
    app.session_state["view"] = "gear"
    app.session_state["_key_verdict_cache"] = {
        "char_key": char_key,
        "iterations": 50,
        "verdict": verdict,
    }
    return app


def _caption_body(app: AppTest) -> str:
    return "\n".join(str(c.value) for c in app.caption)


def test_ledger_renders_for_the_seeded_warrior() -> None:
    """Sanity: the ledger's own bar actually renders (not the
    'not yet instrumented' branch) so the caption assertions below are
    testing the real thing, not a skipped code path."""
    app = _seeded_app()
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    body = _caption_body(app)
    assert "What this shows" in body
    assert "Where your survivability comes from" in "\n".join(str(m.value) for m in app.markdown)


def test_caption_drops_the_false_flat_mitigation_directional_claim() -> None:
    """Item 4 — the old caption's specific causal story must be gone."""
    app = _seeded_app()
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    body = _caption_body(app)
    assert "block/armor/versatility stay flat" not in body
    assert "grows somewhat at" not in body


def test_caption_names_the_real_scaling_asymmetry_instead() -> None:
    """The replacement copy should honestly describe WHY the mix can
    shift (swing/tank-buster damage scales per key, scripted casts don't)
    rather than asserting an unverified direction."""
    app = _seeded_app()
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    body = _caption_body(app)
    assert "scripted spell casts" in body
    assert "don't assume any one share" in body.lower()


def test_caption_no_longer_references_a_reload_at_different_key_control() -> None:
    """Item 6 — no control on this page lets you 'reload this expander at
    a different key'; the caption must stop telling users to do that."""
    app = _seeded_app()
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    body = _caption_body(app)
    assert "reload this expander" not in body
    assert "at a different key" not in body


def test_zero_self_heal_reads_as_not_yet_credited_not_a_real_zero() -> None:
    """Item 5 — Protection Warrior's self-sustain is never routed through
    `apply_self_heal` today (only `classes/guardian_druid.py` calls it),
    so `disposition_healed_self_share` is a structural 0.0 for every
    character that reaches this ledger, not a per-build measurement. The
    caption must say so instead of printing a bare, misleadingly-precise
    "0%"."""
    app = _seeded_app()
    app.run()
    assert not app.exception, f"Unhandled exception: {app.exception}"
    body = _caption_body(app)
    assert "Separately" in body, "the healed-back caption did not render at all"
    assert "isn't yet credited" in body
    assert "own kit put back **0%**" not in body

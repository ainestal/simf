"""Honest handling when an online name-lookup hydrates degraded gear stats.

On the public instance item-stat fetches are forced offline (SIMF_PUBLIC), so
an online name-lookup backfills stamina to 0 (io/raider_io.py) → near-zero eHP
→ every recommendation / verdict / ΔeHP% computed off it is garbage (the
"+195% eHP for one chest" the user hit). `_stats_unresolved` detects this so
the gear + verdict surfaces show an honest banner instead of fake numbers.

`_stats_unresolved` now delegates its structural check to
`Character.is_degraded()` (the single definition lives on the type — see
test_character_validation.py); these tests pin the UI-side behaviour end-to-end
against real Character instances, including the baseline-eHP floor that stays in
the UI layer.
"""

from __future__ import annotations

from simf.core.character import Character
from simf.ui import app


def _warrior(
    *, stamina: int, strength: int = 2_000, armor_from_gear: int = 5_000, max_hp_override=None
) -> Character:
    return Character(
        name="t",
        race="human",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        stamina=stamina,
        strength=strength,
        armor_from_gear=armor_from_gear,
        max_hp_override=max_hp_override,
    )


_GEARED = {"head": object()}  # any non-empty equipped map


def test_unresolved_when_geared_but_zero_stamina() -> None:
    """The exact raider_io backfill case: equipped gear, stamina backfilled to
    0 (and no primary) — caught by the structural is_degraded() check."""
    assert app._stats_unresolved(_warrior(stamina=0, strength=0, armor_from_gear=0), _GEARED)


def test_unresolved_when_geared_but_implausibly_low_ehp() -> None:
    """Partial resolution: stamina/primary nonzero (not structurally degraded)
    but the resulting eHP is still below the sane floor — the UI-side check."""
    char = _warrior(stamina=500)  # ~11k HP → ~13k eHP, below the 100k floor
    assert not char.is_degraded()  # structurally fine; only the eHP floor trips
    assert app._stats_unresolved(char, _GEARED)


def test_resolved_when_real_tank_ehp() -> None:
    """A real geared tank (huge eHP, real stamina) is never flagged."""
    healthy = _warrior(stamina=34_000, max_hp_override=750_000)
    assert not app._stats_unresolved(healthy, _GEARED)


def test_not_flagged_when_no_gear_equipped() -> None:
    """No equipped gear is 'unloaded', not 'stats failed to resolve' — the gear
    surface shouldn't show the resolver-failed banner on a cold/empty state."""
    assert not app._stats_unresolved(_warrior(stamina=0, strength=0), {})


def test_floor_constant_separates_degraded_from_geared() -> None:
    # Above the degraded ~21k eHP the user saw, well below any geared L90 tank.
    assert 21_000 < app._MIN_SANE_BASELINE_EHP < 1_000_000


def test_banner_copy_is_honest_and_actionable() -> None:
    """The non-concise banner must name the cause + the /simc escape hatch; the
    concise one (verdict expander) must point to /simc too. Pinned so a copy
    edit can't quietly drop the actionable instruction (render-path is exercised
    via Streamlit, so this checks the text contract on the source instead)."""
    import inspect

    src = inspect.getsource(app._render_unresolved_stats_banner)
    assert "Couldn't read your gear's stats" in src
    assert "/simc" in src  # the working alternative is always named
    assert "Change character" in src

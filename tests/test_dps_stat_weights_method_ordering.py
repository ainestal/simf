"""DPS stat-weights refit against Method's Midnight 12.0.5 priority.

Method's authoritative DPS priority for Prot Warrior is:

    "Strength >> Haste > Crit = Vers > Mastery for all situations."
    (https://www.method.gg/guides/protection-warrior/stats-races-and-consumables)

Pre-refit YAML had ``mastery (1.05) > vers (0.80)``, which contradicted
Method's "mastery LAST" — every slot-dialog tie that came down to a
mastery-vs-vers swap rendered the wrong winner.

This file pins the post-refit ordering. Magnitudes stay PLACEHOLDER
(Bloodmallet pages JS-render and don't survive WebFetch from the Pi,
same failure mode as Wowhead per-dungeon loot pages); only the
relative ranking is asserted here. When a Bloodmallet JSON endpoint
or a Brutoh-specific Raidbots sim eventually lands, the magnitudes
get a proper refit — but the ordering tripwire stays.
"""

from __future__ import annotations

from simf.core.constants import load_constants


def test_dps_stat_weights_match_method_qualitative_ordering() -> None:
    """Haste > Crit = Vers > Mastery (per Method's priority page)."""
    weights = load_constants()["dps_stat_weights"]
    haste = weights["haste_rating"]
    crit = weights["crit_rating"]
    vers = weights["versatility_rating"]
    mastery = weights["mastery_rating"]

    assert haste > crit, "Haste must strictly outrank Crit (Method: Haste >>)"
    assert haste > vers, "Haste must strictly outrank Versatility (Method: Haste >>)"
    assert haste > mastery, "Haste must strictly outrank Mastery (Method: Haste >>)"

    # Crit and Vers are tied per Method ("Crit = Vers"); the YAML preserves
    # that ordering relationship — neither should dominate the other.
    # Allow a 5% tolerance because we don't have exact numerical weights.
    assert abs(crit - vers) < 0.10, (
        f"Method ranks Crit = Vers, but YAML has crit={crit}, vers={vers}"
    )

    assert mastery < crit, "Method ranks Mastery LAST below Crit"
    assert mastery < vers, "Method ranks Mastery LAST below Vers"


def test_dps_stat_weights_versatility_outranks_mastery_post_refit() -> None:
    """Direct regression assertion against the pre-refit bug: the YAML
    previously had mastery (1.05) > vers (0.80), inverting Method's
    "Vers > Mastery" rule. Pin the post-refit state explicitly so any
    future revert fails LOUDLY with the bug name."""
    weights = load_constants()["dps_stat_weights"]
    assert weights["versatility_rating"] > weights["mastery_rating"], (
        "DPS stat weights regressed to pre-2026-05-23 inversion "
        "(mastery > vers contradicts Method's authoritative ordering)."
    )


def test_dps_stat_weights_haste_remains_top_pick() -> None:
    """Haste is unambiguously #1 across Method, Icy Veins (survival),
    and Noxxic. The refit keeps haste at 1.40 so any UI consumer that
    treats 1.40 as the implicit max isn't disturbed."""
    weights = load_constants()["dps_stat_weights"]
    assert weights["haste_rating"] == max(
        weights["haste_rating"],
        weights["crit_rating"],
        weights["versatility_rating"],
        weights["mastery_rating"],
    )

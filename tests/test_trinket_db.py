"""
Tests for simf.optimizer.trinket_db — trinket registry and eHP math.
"""

import pytest

from simf.core.character import Character
from simf.optimizer.trinket_db import (
    effective_stat_delta,
    load_trinkets,
    rank_trinkets_by_ehp,
    trinket_ehp_contribution,
    trinkets_for_spec,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def all_trinkets():
    return load_trinkets()


@pytest.fixture(scope="module")
def brutoh():
    """Minimal Prot Warrior character close to Brutoh's stats (no max_hp_override)."""
    return Character(
        name="TestWarrior",
        race="human",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        strength=2182,
        stamina=34176,
        armor_from_gear=5517,
        haste_rating=1950,
        crit_rating=800,
        mastery_rating=1200,
        versatility_rating=1800,
    )


@pytest.fixture(scope="module")
def brutoh_with_override():
    """Prot Warrior with max_hp_override (matches real Brutoh YAML)."""
    return Character(
        name="BrutohOverride",
        race="human",
        class_spec="protection_warrior",
        talents="kiratank-defensive",
        strength=2182,
        stamina=34176,
        armor_from_gear=5517,
        haste_rating=1950,
        crit_rating=800,
        mastery_rating=1200,
        versatility_rating=1800,
        max_hp_override=751872,
    )


@pytest.fixture(scope="module")
def guardian():
    """Guardian Druid — an Agility-primary spec whose agility feeds real
    armor (Ironfur), unlike Strength which has no live eHP wiring yet for any
    spec. Used to prove primary-stat trinket credit actually reaches eHP."""
    return Character(
        name="TestGuardian",
        race="human",
        class_spec="guardian_druid",
        talents="default-guardian",
        agility=1500,
        stamina=30000,
        armor_from_gear=2500,
    )


# ---------------------------------------------------------------------------
# load_trinkets / caching
# ---------------------------------------------------------------------------


class TestLoadTrinkets:
    def test_returns_list(self, all_trinkets):
        assert isinstance(all_trinkets, list)

    def test_minimum_count(self, all_trinkets):
        assert len(all_trinkets) >= 15

    def test_each_trinket_has_required_keys(self, all_trinkets):
        for t in all_trinkets:
            assert "id" in t, f"Missing 'id' in {t}"
            assert "name" in t, f"Missing 'name' in {t}"
            assert "type" in t, f"Missing 'type' in {t}"
            assert "source" in t, f"Missing 'source' in {t}"

    def test_lru_cache(self, all_trinkets):
        second = load_trinkets()
        assert all_trinkets is second, "lru_cache should return same object"

    def test_known_trinkets_present(self, all_trinkets):
        names = {t["name"] for t in all_trinkets}
        assert "Gaze of the Alnseer" in names
        assert "Rotting Globule" in names
        assert "Solar Core Igniter" in names
        assert "Gloom-Spattered Dreadscale" in names


# ---------------------------------------------------------------------------
# trinkets_for_spec
# ---------------------------------------------------------------------------


class TestTrinketsForSpec:
    def test_universal_trinkets_in_prot_warrior(self, all_trinkets):
        prot = trinkets_for_spec("protection_warrior")
        # Gaze is universal
        gaze_all = next(t for t in all_trinkets if t["name"] == "Gaze of the Alnseer")
        assert gaze_all in prot

    def test_spec_specific_trinket_included(self, all_trinkets):
        # Algeth'ar Puzzle Box is spec-specific to prot_warrior + guardian_druid
        prot = trinkets_for_spec("protection_warrior")
        guard = trinkets_for_spec("guardian_druid")
        brew = trinkets_for_spec("brewmaster_monk")
        names_prot = {t["name"] for t in prot}
        names_guard = {t["name"] for t in guard}
        names_brew = {t["name"] for t in brew}
        assert "Algeth'ar Puzzle Box" in names_prot
        assert "Algeth'ar Puzzle Box" in names_guard
        assert "Algeth'ar Puzzle Box" not in names_brew

    def test_blood_dk_specific(self):
        blood = trinkets_for_spec("blood_death_knight")
        names = {t["name"] for t in blood}
        assert "Light Company Guidon" in names

    def test_different_specs_return_different_sets(self):
        prot = trinkets_for_spec("protection_warrior")
        brew = trinkets_for_spec("brewmaster_monk")
        # Should not be identical since some are spec-specific
        prot_names = {t["name"] for t in prot}
        brew_names = {t["name"] for t in brew}
        assert prot_names != brew_names


# ---------------------------------------------------------------------------
# effective_stat_delta
# ---------------------------------------------------------------------------


class TestEffectiveStatDelta:
    def test_passive_stat_trinket_returns_stats_directly(self, all_trinkets):
        plume = next(t for t in all_trinkets if t["name"] == "Umbral Plume")
        delta = effective_stat_delta(plume)
        assert "agility_or_strength" in delta
        assert "crit_rating" in delta
        assert delta["agility_or_strength"] == pytest.approx(87.0, abs=1)

    def test_on_use_absorb_returns_stamina(self, all_trinkets):
        rg = next(t for t in all_trinkets if t["name"] == "Rotting Globule")
        delta = effective_stat_delta(rg)
        assert "stamina" in delta
        assert delta["stamina"] > 0

    def test_solar_core_igniter_stamina_higher_than_rotting_globule(self, all_trinkets):
        """Solar Core Igniter has a shorter CD (90s vs 120s) so higher uptime → more eq stamina."""
        rg = next(t for t in all_trinkets if t["name"] == "Rotting Globule")
        sci = next(t for t in all_trinkets if t["name"] == "Solar Core Igniter")
        delta_rg = effective_stat_delta(rg)
        delta_sci = effective_stat_delta(sci)
        assert delta_sci["stamina"] > delta_rg["stamina"]

    def test_proc_absorb_returns_stamina(self, all_trinkets):
        jelly = next(t for t in all_trinkets if t["name"] == "Jelly Replicator")
        delta = effective_stat_delta(jelly)
        assert "stamina" in delta
        assert delta["stamina"] > 0

    def test_proc_stat_returns_averaged_stat(self, all_trinkets):
        gaze = next(t for t in all_trinkets if t["name"] == "Gaze of the Alnseer")
        delta = effective_stat_delta(gaze)
        # Should have mastery (passive) and some primary stat proc
        assert "mastery_rating" in delta
        # proc contribution is time-averaged agility_or_strength
        assert "agility_or_strength" in delta
        assert delta["agility_or_strength"] > 0

    def test_passive_stat_crafted(self, all_trinkets):
        stone = next(t for t in all_trinkets if t["name"] == "Magister's Alchemist Stone")
        delta = effective_stat_delta(stone)
        assert "versatility_rating" in delta
        assert delta["versatility_rating"] == pytest.approx(93.0, abs=1)

    def test_damage_trinket_returns_empty(self, all_trinkets):
        """on_use_damage trinkets have no survivability stat value."""
        contract = next(t for t in all_trinkets if t["name"] == "Void Stalker's Contract")
        delta = effective_stat_delta(contract)
        # Should have minor passive primary stat but no stamina
        assert "stamina" not in delta or delta.get("stamina", 0) == 0

    def test_ilvl_scaling(self, all_trinkets):
        """Higher ilvl should yield proportionally larger absorb → stamina conversion."""
        rg = next(t for t in all_trinkets if t["name"] == "Rotting Globule")
        # 250 = Midnight 12.0.5 M+ +2 drop floor (matches Rotting Globule's ilvl_base);
        # 280 ≈ typical Voidcore-upgraded M+ piece for a player at the +10 cap.
        delta_base = effective_stat_delta(rg, character_ilvl=250)
        delta_high = effective_stat_delta(rg, character_ilvl=280)
        # Higher ilvl → scaled absorb value → more effective stamina
        assert delta_high["stamina"] > delta_base["stamina"]

    def test_until_consumed_absorb_not_silently_zero(self, all_trinkets):
        """Gloom-Spattered Dreadscale's shield has no expiration timer
        (``duration_s: 0.0``) — it lasts until its absorb cap is used up, not
        for a fixed window. Before the ``duration_model`` fix, the plain
        ``uptime = duration_s / cooldown_s`` formula read this as uptime=0
        (0/120), silently zeroing its entire on-use value. It must now credit
        real stamina, roughly the full absorb value converted at hp_per_stamina
        (uptime=1.0), not the near-zero a bare `duration_s` ratio would give."""
        dreadscale = next(t for t in all_trinkets if t["name"] == "Gloom-Spattered Dreadscale")
        delta = effective_stat_delta(dreadscale)
        assert "stamina" in delta
        assert delta["stamina"] > 0
        # Sanity floor: the old (bugged) uptime=0 formula gave exactly 0.0, so
        # any positive-but-tiny value would still hint at a lingering
        # duration_s-based miscalculation rather than the intended uptime=1.0.
        assert delta["stamina"] > 1000

    def test_until_consumed_matches_full_value_uptime(self, all_trinkets):
        """The 'until consumed' shape should credit the same stamina as an
        ordinary on_use_absorb effect with duration_s == cooldown_s (i.e.
        uptime=1.0) — proving the special-cased branch reuses the existing
        absorb->stamina conversion rather than inventing a new one."""
        dreadscale = next(t for t in all_trinkets if t["name"] == "Gloom-Spattered Dreadscale")
        effect = dreadscale["effect"]
        equivalent_timed = dict(dreadscale)
        equivalent_timed["effect"] = {k: v for k, v in effect.items() if k != "duration_model"} | {
            "duration_s": effect["cooldown_s"]
        }
        delta_until_consumed = effective_stat_delta(dreadscale)
        delta_full_duration = effective_stat_delta(equivalent_timed)
        assert delta_until_consumed["stamina"] == pytest.approx(delta_full_duration["stamina"])


# ---------------------------------------------------------------------------
# trinket_ehp_contribution
# ---------------------------------------------------------------------------


class TestTrinketEhpContribution:
    def test_returns_dict_with_expected_keys(self, all_trinkets, brutoh):
        t = all_trinkets[0]
        result = trinket_ehp_contribution(t, brutoh)
        assert "delta_ehp_physical" in result
        assert "delta_ehp_magic" in result

    def test_absorb_trinket_positive_ehp(self, all_trinkets, brutoh):
        rg = next(t for t in all_trinkets if t["name"] == "Rotting Globule")
        result = trinket_ehp_contribution(rg, brutoh)
        assert result["delta_ehp_physical"] > 0
        assert result["delta_ehp_magic"] > 0

    def test_absorb_trinket_positive_ehp_with_override(self, all_trinkets, brutoh_with_override):
        """max_hp_override should not block stamina-based eHP calculation."""
        rg = next(t for t in all_trinkets if t["name"] == "Rotting Globule")
        result = trinket_ehp_contribution(rg, brutoh_with_override)
        assert result["delta_ehp_physical"] > 0
        assert result["delta_ehp_magic"] > 0

    def test_versatility_trinket_positive_both_ehp(self, all_trinkets, brutoh):
        stone = next(t for t in all_trinkets if t["name"] == "Magister's Alchemist Stone")
        result = trinket_ehp_contribution(stone, brutoh)
        assert result["delta_ehp_physical"] > 0
        assert result["delta_ehp_magic"] > 0

    def test_physical_ehp_larger_than_magic_for_absorb(self, all_trinkets, brutoh):
        """Absorb (stamina equivalent) should have a larger physical eHP delta
        because armor also multiplies effective physical HP."""
        rg = next(t for t in all_trinkets if t["name"] == "Rotting Globule")
        result = trinket_ehp_contribution(rg, brutoh)
        assert result["delta_ehp_physical"] > result["delta_ehp_magic"]

    def test_solar_core_higher_ehp_than_rotting_globule(self, all_trinkets, brutoh):
        """Solar Core Igniter has shorter CD → higher uptime → higher eHP."""
        rg = next(t for t in all_trinkets if t["name"] == "Rotting Globule")
        sci = next(t for t in all_trinkets if t["name"] == "Solar Core Igniter")
        ehp_rg = trinket_ehp_contribution(rg, brutoh)
        ehp_sci = trinket_ehp_contribution(sci, brutoh)
        assert ehp_sci["delta_ehp_physical"] > ehp_rg["delta_ehp_physical"]


class TestPrimaryStatCredit:
    """The docstring of trinket_ehp_contribution() promises primary-stat
    trinkets 'credit agility/strength' — before the fix, both branches were
    bare `pass`, so every primary-stat trinket silently contributed zero eHP
    regardless of spec."""

    def test_agility_or_strength_trinket_nonzero_for_guardian(self, all_trinkets, guardian):
        """'Heart of Ancient Hunger' is a pure agility_or_strength stick.
        Guardian's agility feeds real armor (Ironfur), so a correctly-wired
        credit must move physical eHP. Pre-fix this was always exactly 0.0
        for every spec."""
        heart = next(t for t in all_trinkets if t["name"] == "Heart of Ancient Hunger")
        result = trinket_ehp_contribution(heart, guardian)
        assert result["delta_ehp_physical"] > 0

    def test_primary_stat_zero_when_it_does_not_match_spec_primary(self, guardian):
        """A trinket whose stat is explicitly 'strength' (not the universal
        'agility_or_strength' placeholder) is dead weight on an Agility spec
        — same as Blizzard's own itemization — and must contribute nothing,
        even though the identical mechanism (agility) DOES move this same
        Guardian's eHP (see the sibling test above)."""
        strength_only_trinket = {
            "id": 999001,
            "name": "Test Strength Stick",
            "type": "passive_stat",
            "ilvl_base": 250,
            "stats": {"strength": 500},
        }
        result = trinket_ehp_contribution(strength_only_trinket, guardian)
        assert result["delta_ehp_physical"] == 0
        assert result["delta_ehp_magic"] == 0

    def test_matching_agility_trinket_nonzero_for_guardian(self, guardian):
        """The mirror of the previous test: a literal 'agility'-keyed
        trinket (as opposed to the universal 'agility_or_strength'
        placeholder) DOES match Guardian's primary and must be credited."""
        agility_only_trinket = {
            "id": 999002,
            "name": "Test Agility Stick",
            "type": "passive_stat",
            "ilvl_base": 250,
            "stats": {"agility": 500},
        }
        result = trinket_ehp_contribution(agility_only_trinket, guardian)
        assert result["delta_ehp_physical"] > 0


# ---------------------------------------------------------------------------
# rank_trinkets_by_ehp
# ---------------------------------------------------------------------------


class TestRankTrinketsByEhp:
    def test_returns_sorted_list(self, brutoh):
        ranked = rank_trinkets_by_ehp(brutoh)
        assert isinstance(ranked, list)
        assert len(ranked) >= 1
        deltas = [d for d, _ in ranked]
        assert deltas == sorted(deltas, reverse=True)

    def test_top_trinket_positive_ehp(self, brutoh):
        ranked = rank_trinkets_by_ehp(brutoh)
        top_delta, _top_trinket = ranked[0]
        assert top_delta >= 0

    def test_magic_ranking_independent(self, brutoh):
        ranked_phys = rank_trinkets_by_ehp(brutoh, physical=True)
        ranked_magic = rank_trinkets_by_ehp(brutoh, physical=False)
        # Both should return full list
        assert len(ranked_phys) == len(ranked_magic)
        # Rankings may differ (versatility impacts magic eHP more equally)
        # Just check they are sorted
        phys_deltas = [d for d, _ in ranked_phys]
        magic_deltas = [d for d, _ in ranked_magic]
        assert phys_deltas == sorted(phys_deltas, reverse=True)
        assert magic_deltas == sorted(magic_deltas, reverse=True)


# ---------------------------------------------------------------------------
# Heart of Wind / Mark of Light — 2026-07-26 VERIFY sweep (Season 2 punch list)
# ---------------------------------------------------------------------------
#
# Both trinkets' ilvl_base=250 figures used to be backscaled PROPORTIONALLY
# (value * 250/render_ilvl) from a render 22-48 ilvls away — an assumption
# (stat proportional to ilvl, zero intercept) that live Wowhead data
# disproves: trinket primary-stat and proc-effect budgets grow
# super-linearly with ilvl, so a distant high-ilvl render both used the
# wrong functional form and borrowed a steeper slope than the true one near
# 250. Re-verified via a tight local bracket (live renders at ilvl 246 and
# 253 — the closest pair the known upgrade-track bonus_id ladder offers
# around the ilvl_base=250 drop floor) interpolated to 250 directly. These
# tests lock in the corrected figures so a future edit can't silently drift
# back toward the old, disproven proportional backscale.


class TestHeartOfWindAndMarkOfLightVerifySweep:
    def test_heart_of_wind_corrected_passive_stat(self, all_trinkets):
        how = next(t for t in all_trinkets if t["name"] == "Heart of Wind")
        assert how["stats"]["agility_or_strength"] == 82

    def test_heart_of_wind_corrected_haste_proc_value(self, all_trinkets):
        how = next(t for t in all_trinkets if t["name"] == "Heart of Wind")
        assert how["effect"]["value"] == 213

    def test_mark_of_light_corrected_passive_stat(self, all_trinkets):
        mol = next(t for t in all_trinkets if t["name"] == "Mark of Light")
        assert mol["stats"]["strength"] == 82

    def test_mark_of_light_corrected_damage_proc_value(self, all_trinkets):
        mol = next(t for t in all_trinkets if t["name"] == "Mark of Light")
        assert mol["effect"]["damage"] == 6884

    def test_effective_stat_delta_at_ilvl_base_uses_corrected_figures(self, all_trinkets):
        """`effective_stat_delta` at the default character_ilvl=250 (== each
        trinket's own ilvl_base) must pass the passive stat through as-is
        (no further ilvl-scaling) and time-average the proc effect off the
        corrected raw value — proving the corrected YAML values (not some
        other cached/derived number) are what a "not yet owned" Vault/Gear
        card actually shows for these two trinkets. The expected uptime is
        computed from the trinket's OWN stored duration_s/proc_ppm rather
        than hardcoded, so this stays meaningful if either changes later
        independently of the raw value fix."""
        how = next(t for t in all_trinkets if t["name"] == "Heart of Wind")
        how_delta = effective_stat_delta(how, character_ilvl=250)
        assert how_delta["agility_or_strength"] == 82
        how_uptime = how["effect"]["duration_s"] / (60.0 / how["effect"]["proc_ppm"])
        assert how_delta["haste_rating"] == pytest.approx(213 * how_uptime, abs=0.1)

        mol = next(t for t in all_trinkets if t["name"] == "Mark of Light")
        mol_delta = effective_stat_delta(mol, character_ilvl=250)
        assert mol_delta["strength"] == 82

"""Gem suggester tests — catalog integrity, ΔeHP scoring, EC-gating, and the
unique-equipped meta constraint.

Marginals are synthetic so each assertion is hand-checkable; the real live
marginals come from the sim path (survivability_weights). Shapes match
``ehp_marginals()`` exactly: ``{stat: {"p": .., "m": ..}}``.
"""

from dataclasses import dataclass

import pytest

from simf.optimizer import gem_suggester
from simf.optimizer.gem_suggester import (
    Socket,
    candidate_gems,
    find_gem_by_id,
    gem_survival_value,
    load_gem_catalog,
    primary_stat_key,
    resolve_gem_stats,
    sockets_from_equipped,
    suggest_gems,
)

# Confirmed-via-Wowhead-xml ids used across the suite.
EVERSONG_INDECIPHERABLE = 240983  # +32 primary
EVERSONG_STOIC = 240971  # +23 primary, +13 armor
QUICK_PERIDOT = 240888  # +17 haste
VERSATILE_PERIDOT = 240894  # +16 haste, +7 vers
MASTERFUL_PERIDOT = 240892  # +16 haste, +7 mastery
VERSATILE_LAPIS = 240912  # +17 vers


def _guardian_ec_marg():
    """Elune's-Chosen Guardian: agility (armor path) dominates, haste has real
    value, vers modest. Strength/crit/mastery zero. Magic side: agility/haste
    are armor-path (phys only), vers both."""
    return {
        "stamina": {"p": 20.0, "m": 16.0},
        "armor_from_gear": {"p": 5.0, "m": 0.0},
        "versatility_rating": {"p": 6.0, "m": 5.0},
        "haste_rating": {"p": 10.0, "m": 0.0},
        "crit_rating": {"p": 0.0, "m": 0.0},
        "mastery_rating": {"p": 0.0, "m": 0.0},
        "strength": {"p": 0.0, "m": 0.0},
        "agility": {"p": 18.0, "m": 0.0},
    }


def _guardian_non_ec_marg():
    """Non-Elune's-Chosen Guardian: haste survival value is exactly 0 (the EC
    gate), agility still valued, vers modest."""
    m = _guardian_ec_marg()
    m["haste_rating"] = {"p": 0.0, "m": 0.0}
    return m


def _warrior_marg():
    """Plate tank: strength carries parry value (phys only), armor + vers
    valued, agility/haste/crit/mastery zero."""
    return {
        "stamina": {"p": 20.0, "m": 16.0},
        "armor_from_gear": {"p": 5.0, "m": 0.0},
        "versatility_rating": {"p": 6.0, "m": 5.0},
        "haste_rating": {"p": 0.0, "m": 0.0},
        "crit_rating": {"p": 0.0, "m": 0.0},
        "mastery_rating": {"p": 0.0, "m": 0.0},
        "strength": {"p": 4.0, "m": 0.0},
        "agility": {"p": 0.0, "m": 0.0},
    }


# --------------------------------------------------------------------------
# Catalog integrity
# --------------------------------------------------------------------------


def test_catalog_loads_known_gems():
    by_id = {int(g["id"]): g for g in load_gem_catalog()}
    assert by_id[EVERSONG_INDECIPHERABLE]["stats"] == {"primary": 32}
    assert by_id[EVERSONG_INDECIPHERABLE]["unique_equipped"] is True
    assert by_id[EVERSONG_STOIC]["stats"] == {"primary": 23, "armor_from_gear": 13}
    assert by_id[VERSATILE_PERIDOT]["stats"] == {"haste_rating": 16, "versatility_rating": 7}
    assert by_id[VERSATILE_LAPIS]["stats"] == {"versatility_rating": 17}


def test_no_stamina_gem_exists():
    """Midnight has no stamina gem — the model depends on this."""
    for g in load_gem_catalog():
        assert "stamina" not in (g.get("stats") or {})


def test_only_eversong_metas_are_unique():
    for g in load_gem_catalog():
        if g.get("unique_equipped"):
            assert "Eversong Diamond" in g["name"]


def test_find_gem_by_id():
    assert find_gem_by_id(EVERSONG_INDECIPHERABLE)["name"] == "Indecipherable Eversong Diamond"
    assert find_gem_by_id(None) is None
    assert find_gem_by_id(999999999) is None


# --------------------------------------------------------------------------
# Primary-stat resolution
# --------------------------------------------------------------------------


def test_primary_stat_key():
    assert primary_stat_key("guardian_druid") == "agility"
    assert primary_stat_key("brewmaster_monk") == "agility"
    assert primary_stat_key("vengeance_demon_hunter") == "agility"
    assert primary_stat_key("protection_warrior") == "strength"
    assert primary_stat_key("protection_paladin") == "strength"
    assert primary_stat_key("blood_death_knight") == "strength"


def test_resolve_gem_stats_maps_primary():
    assert resolve_gem_stats({"primary": 32}, "agility") == {"agility": 32}
    assert resolve_gem_stats({"primary": 32}, "strength") == {"strength": 32}
    # Stoic: primary + armor both surface under their canonical keys.
    assert resolve_gem_stats({"primary": 23, "armor_from_gear": 13}, "agility") == {
        "agility": 23,
        "armor_from_gear": 13,
    }


def test_candidate_gems_resolves_for_spec():
    guardian = {c.item_id: c for c in candidate_gems("guardian_druid")}
    warrior = {c.item_id: c for c in candidate_gems("protection_warrior")}
    assert guardian[EVERSONG_INDECIPHERABLE].stats == {"agility": 32}
    assert warrior[EVERSONG_INDECIPHERABLE].stats == {"strength": 32}


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def test_gem_survival_value_respects_school_mix():
    marg = _warrior_marg()
    # Pure-vers gem in a physical vs magic dungeon: phys uses p=6, magic m=5.
    vers = {"versatility_rating": 17}
    phys = gem_survival_value(vers, marg, [{"school_mix": {"physical": 1.0}}])
    magic = gem_survival_value(vers, marg, [{"school_mix": {"physical": 0.0}}])
    assert phys == pytest.approx(17 * 6.0)
    assert magic == pytest.approx(17 * 5.0)


def test_gem_survival_value_generic_fallback_runs():
    # No dungeons → generic 0.65/0.35; just assert it computes and is positive.
    v = gem_survival_value({"versatility_rating": 17}, _warrior_marg())
    assert v > 0


# --------------------------------------------------------------------------
# EC-gating: haste only has value for an Elune's-Chosen Guardian
# --------------------------------------------------------------------------


def test_haste_gem_valued_only_when_ec():
    haste_gem = {"haste_rating": 17}
    assert gem_survival_value(haste_gem, _guardian_ec_marg()) > 0
    assert gem_survival_value(haste_gem, _guardian_non_ec_marg()) == 0.0


def test_best_nonmeta_is_vers_for_non_ec_guardian():
    """With haste worth 0, the pure-vers Lapis beats every haste-base gem and
    every 16-vers+7-wasted hybrid in the non-meta sockets."""
    sockets = [
        Socket("neck", 0, EVERSONG_INDECIPHERABLE),  # holds the meta → meta socket
        Socket("finger1", 0, VERSATILE_PERIDOT),
    ]
    out = suggest_gems(sockets, _guardian_non_ec_marg(), class_spec="guardian_druid")
    nonmeta = next(s for s in out if not s.is_meta_socket)
    assert nonmeta.best.item_id == VERSATILE_LAPIS


def test_best_nonmeta_is_haste_for_ec_guardian():
    """With haste valued, the best non-meta gem carries haste (not pure vers)."""
    sockets = [
        Socket("neck", 0, EVERSONG_INDECIPHERABLE),  # holds the meta → meta socket
        Socket("finger1", 0, VERSATILE_LAPIS),
    ]
    out = suggest_gems(sockets, _guardian_ec_marg(), class_spec="guardian_druid")
    nonmeta = next(s for s in out if not s.is_meta_socket)
    assert "haste_rating" in nonmeta.best.stats
    assert nonmeta.best.item_id != VERSATILE_LAPIS


# --------------------------------------------------------------------------
# Unique-equipped meta constraint
# --------------------------------------------------------------------------


def test_meta_recommended_in_exactly_one_socket():
    sockets = [
        Socket("neck", 0, VERSATILE_PERIDOT),
        Socket("finger1", 0, VERSATILE_PERIDOT),
        Socket("finger2", 0, VERSATILE_PERIDOT),
    ]
    out = suggest_gems(sockets, _guardian_ec_marg(), class_spec="guardian_druid")
    meta_ids = {c.item_id for c in candidate_gems("guardian_druid") if c.unique_equipped}
    meta_socket_recs = [s for s in out if s.best and s.best.item_id in meta_ids]
    assert len(meta_socket_recs) == 1
    assert sum(1 for s in out if s.is_meta_socket) == 1


def test_existing_meta_socket_kept_no_churn():
    """A socket already holding a meta keeps the meta (we don't shuffle it to
    another socket for zero net eHP)."""
    sockets = [
        Socket("neck", 0, EVERSONG_INDECIPHERABLE),  # already has the meta
        Socket("finger1", 0, VERSATILE_PERIDOT),
        Socket("finger2", 0, VERSATILE_PERIDOT),
    ]
    out = suggest_gems(sockets, _guardian_ec_marg(), class_spec="guardian_druid")
    assert out[0].is_meta_socket
    assert out[0].slot == "neck"


def test_no_socket_ever_recommends_a_downgrade():
    """delta_ehp ≥ 0 invariant: a suggestion is never a strict eHP loss. The
    only way to reach a negative would be a meta stranded in a non-meta socket
    (an invalid double-meta loadout) — guarded by keep-current."""
    sockets = [
        Socket("neck", 0, EVERSONG_INDECIPHERABLE),  # +32 primary
        Socket("finger1", 0, EVERSONG_STOIC),  # +23 primary, +13 armor (a 2nd meta!)
        Socket("finger2", 0, VERSATILE_PERIDOT),
    ]
    for marg, spec in (
        (_warrior_marg(), "protection_warrior"),
        (_guardian_ec_marg(), "guardian_druid"),
        (_guardian_non_ec_marg(), "guardian_druid"),
    ):
        out = suggest_gems(sockets, marg, class_spec=spec)
        assert all(s.delta_ehp >= 0 for s in out), (
            f"{spec}: negative delta in {[(s.slot, s.delta_ehp) for s in out]}"
        )


def test_meta_socket_keeps_the_best_held_meta():
    """With two metas held (invalid but possible to pass in), the higher-VALUE
    meta is kept in place — not just the first-listed socket."""
    # For a warrior, Stoic (str23+armor13) out-values Indecipherable (str32).
    sockets = [
        Socket("neck", 0, EVERSONG_INDECIPHERABLE),  # lower-value meta, listed first
        Socket("finger1", 0, EVERSONG_STOIC),  # higher-value meta
    ]
    out = suggest_gems(sockets, _warrior_marg(), class_spec="protection_warrior")
    meta_socket = next(s for s in out if s.is_meta_socket)
    assert meta_socket.slot == "finger1"  # the Stoic socket, not neck
    assert all(s.delta_ehp >= 0 for s in out)


def test_keep_current_when_already_optimal():
    """A socket already holding the best non-meta gem gets a 0 delta and keeps
    that gem as the recommendation (no churn)."""
    # Non-EC guardian → pure-vers Lapis is the best non-meta. Put it in a
    # non-meta socket (neck holds the meta).
    sockets = [
        Socket("neck", 0, EVERSONG_INDECIPHERABLE),
        Socket("finger1", 0, VERSATILE_LAPIS),
    ]
    out = suggest_gems(sockets, _guardian_non_ec_marg(), class_spec="guardian_druid")
    f1 = next(s for s in out if s.slot == "finger1")
    assert f1.best.item_id == VERSATILE_LAPIS
    assert f1.delta_ehp == 0.0


def test_warrior_picks_defensive_stoic_meta():
    """When primary stat (strength) is weak, the +13-armor Stoic meta out-scores
    the +32-primary Indecipherable for a plate tank."""
    sockets = [Socket("neck", 0, None), Socket("finger1", 0, None)]
    out = suggest_gems(sockets, _warrior_marg(), class_spec="protection_warrior")
    meta_rec = next(s for s in out if s.is_meta_socket)
    assert meta_rec.best.item_id == EVERSONG_STOIC


def test_anonguardian1_eversong_outscores_peridots():
    """Acceptance criterion from the plan: for a Guardian the +agi Eversong
    Diamond out-scores the secondary Peridots on survival."""
    marg = _guardian_ec_marg()
    eversong = gem_survival_value({"agility": 32}, marg)
    versatile_peridot = gem_survival_value({"haste_rating": 16, "versatility_rating": 7}, marg)
    masterful_peridot = gem_survival_value({"haste_rating": 16, "mastery_rating": 7}, marg)
    assert eversong > versatile_peridot
    assert eversong > masterful_peridot

    # And end-to-end on AnonGuardian1's actual three sockets.
    sockets = [
        Socket("neck", 0, EVERSONG_INDECIPHERABLE),
        Socket("finger1", 0, MASTERFUL_PERIDOT),
        Socket("finger2", 0, VERSATILE_PERIDOT),
    ]
    out = suggest_gems(sockets, marg, class_spec="guardian_druid")
    # The neck (currently the meta) stays the meta socket.
    neck = next(s for s in out if s.slot == "neck")
    assert neck.best.item_id == EVERSONG_INDECIPHERABLE
    # The Masterful Peridot ring should be told to drop wasted mastery for a
    # haste/vers gem → positive delta.
    f1 = next(s for s in out if s.slot == "finger1")
    assert f1.delta_ehp > 0


# --------------------------------------------------------------------------
# Current-gem accounting + edge cases
# --------------------------------------------------------------------------


def test_unknown_current_gem_marked_and_zero_valued():
    sockets = [Socket("finger1", 0, 405863)]  # a non-catalog (e.g. TWW) gem id
    out = suggest_gems(sockets, _guardian_ec_marg(), class_spec="guardian_druid")
    assert out[0].current_known is False
    assert out[0].current_value == 0.0
    assert out[0].current_name is None


def test_empty_sockets_returns_empty():
    assert suggest_gems([], _warrior_marg(), class_spec="protection_warrior") == []


@dataclass
class _FakeItem:
    slot: str
    gem_ids: list


def test_sockets_from_equipped_flattens_dataclass_and_dict():
    equipped = {
        "neck": _FakeItem("neck", [EVERSONG_INDECIPHERABLE]),
        "finger1": {"gem_ids": [VERSATILE_PERIDOT, MASTERFUL_PERIDOT]},  # 2 sockets
        "head": _FakeItem("head", []),  # no sockets
        "back": None,  # empty slot
    }
    sockets = sockets_from_equipped(equipped)
    pairs = {(s.slot, s.index, s.gem_id) for s in sockets}
    assert ("neck", 0, EVERSONG_INDECIPHERABLE) in pairs
    assert ("finger1", 0, VERSATILE_PERIDOT) in pairs
    assert ("finger1", 1, MASTERFUL_PERIDOT) in pairs
    assert len(sockets) == 3  # head/back contribute nothing


def test_sockets_from_equipped_skips_wowhead_lookup_without_item_id():
    """Items with no ``item_id`` (like the bare test fixtures above) must never
    even attempt the Wowhead socket-count lookup — a network call there would
    make previously-hermetic tests non-hermetic."""

    def _boom(*a, **kw):
        raise AssertionError("must not call the Wowhead lookup without an item_id")

    import simf.optimizer.gem_suggester as gs

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gs, "fetch_item_socket_count_wowhead", _boom)
        sockets = sockets_from_equipped({"neck": _FakeItem("neck", [EVERSONG_INDECIPHERABLE])})
    assert len(sockets) == 1


# --------------------------------------------------------------------------
# Empty-socket detection via Wowhead's nsockets field
# --------------------------------------------------------------------------


@dataclass
class _FakeGearItem:
    """An ``ItemSpec``-shaped fake carrying item_id/bonus_ids, so the
    socket-count lookup path actually fires (unlike the bare ``_FakeItem``
    fixtures above, which intentionally have no item_id)."""

    slot: str
    gem_ids: list
    item_id: int = 0
    bonus_ids: list | None = None


def test_sockets_from_equipped_pads_empty_socket_from_wowhead_count(monkeypatch):
    """The core bug fix: an item with 1 filled gem out of 2 real sockets (per
    Wowhead's nsockets) must produce a second, empty Socket — previously this
    slot silently rendered nothing at all for the unfilled socket."""
    calls = []

    def _fake_count(item_id, bonus_ids=None):
        calls.append((item_id, bonus_ids))
        return 2

    monkeypatch.setattr(gem_suggester, "fetch_item_socket_count_wowhead", _fake_count)
    equipped = {
        "neck": _FakeGearItem("neck", [EVERSONG_INDECIPHERABLE], item_id=151309, bonus_ids=[13440]),
    }
    sockets = sockets_from_equipped(equipped)
    assert calls == [(151309, [13440])]
    assert len(sockets) == 2
    filled = [s for s in sockets if s.gem_id is not None]
    empty = [s for s in sockets if s.gem_id is None]
    assert filled == [Socket(slot="neck", index=0, gem_id=EVERSONG_INDECIPHERABLE)]
    assert empty == [Socket(slot="neck", index=1, gem_id=None)]


def test_sockets_from_equipped_no_padding_when_count_matches_filled(monkeypatch):
    """Wowhead reporting the same count as already-filled gems must not
    synthesize anything extra (no phantom sockets)."""
    monkeypatch.setattr(gem_suggester, "fetch_item_socket_count_wowhead", lambda *a, **kw: 1)
    equipped = {"neck": _FakeGearItem("neck", [EVERSONG_INDECIPHERABLE], item_id=151309)}
    sockets = sockets_from_equipped(equipped)
    assert sockets == [Socket(slot="neck", index=0, gem_id=EVERSONG_INDECIPHERABLE)]


def test_sockets_from_equipped_none_lookup_result_leaves_behavior_unchanged(monkeypatch):
    """Offline / not-found / any fetch failure returns None from the lookup —
    must degrade gracefully to the pre-fix behavior (filled sockets only), no
    crash, no fabricated socket."""
    monkeypatch.setattr(gem_suggester, "fetch_item_socket_count_wowhead", lambda *a, **kw: None)
    equipped = {"neck": _FakeGearItem("neck", [EVERSONG_INDECIPHERABLE], item_id=151309)}
    sockets = sockets_from_equipped(equipped)
    assert sockets == [Socket(slot="neck", index=0, gem_id=EVERSONG_INDECIPHERABLE)]


def test_sockets_from_equipped_pads_fully_empty_item():
    """An item with zero filled gems but a real socket (per Wowhead) must
    still surface the empty socket — this is the exact bug report: an
    unfilled socket previously rendered no gem line at all."""

    def _fake_count(item_id, bonus_ids=None):
        return 1

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gem_suggester, "fetch_item_socket_count_wowhead", _fake_count)
        equipped = {"finger1": _FakeGearItem("finger1", [], item_id=99999)}
        sockets = sockets_from_equipped(equipped)
    assert sockets == [Socket(slot="finger1", index=0, gem_id=None)]


def test_suggest_gems_recommends_a_gem_for_a_newly_detected_empty_socket():
    """End to end: an empty socket detected via the Wowhead count still gets a
    real survival recommendation, not just a placeholder."""
    sockets = [Socket(slot="finger1", index=0, gem_id=None)]
    out = suggest_gems(sockets, _warrior_marg(), class_spec="protection_warrior")
    assert len(out) == 1
    s = out[0]
    assert s.current_gem_id is None
    assert s.current_known is True  # "known empty," not "unrecognized"
    assert s.current_value == 0.0
    assert s.best is not None
    assert s.delta_ehp > 0  # any real gem beats an empty socket

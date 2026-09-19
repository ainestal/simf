"""Calibration-residual warning: single source of truth.

Round-1 multi-agent review (2026-07-05) caught `_high_residual_warning`
reading `dungeons.yaml`'s abandoned `calibration_gap_pct` field (the
K=2700 self-fit table) while the Calibration-details popover read
`constants.yaml`'s `calibration.per_dungeon` (the post-recalibration,
K-anchored table `_build_run_config` feeds it) — every dungeon disagreed
between the two surfaces, e.g. Algeth'ar read -12.2% on one and -6.0% on
the other. This file guards that class of bug: the warning strip and the
popover must always resolve from the same table, and an unverified
(null-residual) dungeon must never be silently indistinguishable from a
perfectly-calibrated one.
"""

from __future__ import annotations

from simf.ui import load as load_mod
from simf.ui import state as app
from simf.ui.helpers.run_config import RunConfig


def _select(monkeypatch, dungeon_ids):
    monkeypatch.setattr(app, "_ss", lambda: {"selected_dungeon_ids": dungeon_ids})


def _fake_constants(per_dungeon):
    return {"calibration": {"per_dungeon": per_dungeon}, "armor": {"k_constant": 3430}}


# Real ids from the live (post-2026-08-30 promotion) Season 2 catalog — used
# instead of hardcoding synthetic per_dungeon values against a real dungeon id
# whose OWN residual, measured in constants.yaml, is a moving target (subject
# to change the moment real per-dungeon Season 2 calibration work lands).
# Monkeypatching `load_constants` here decouples these tests from that future
# work entirely, which is the more correct fix anyway — the original tests
# were coincidentally relying on constants.yaml and dungeons.yaml agreeing on
# a live dungeon id, exactly the single-source-of-truth coupling this file
# exists to guard against.
_AOF = "altar_of_fangs"  # "Altar of Fangs"
_MROW = "murder_row"  # "Murder Row"


def test_no_stale_calibration_gap_pct_field_remains():
    """dungeons.yaml's calibration_gap_pct is dead — it must not exist in
    the live catalog at all (round-1 fix removed it)."""
    for d in app._dungeon_catalog():
        assert "calibration_gap_pct" not in d, (
            f"{d['id']} still carries a stale calibration_gap_pct field"
        )


def test_warning_matches_the_calibration_popover_source(monkeypatch):
    """The warning strip must flag exactly the dungeons the popover's own
    table (constants.yaml calibration.per_dungeon) marks >10%, not a second,
    stale copy of the numbers."""
    monkeypatch.setattr(app, "load_constants", lambda: _fake_constants({_AOF: 0.15, _MROW: 0.02}))
    msg = app._high_residual_warning()
    catalog_by_id = {d["id"]: d for d in app._dungeon_catalog()}
    assert catalog_by_id[_AOF].get("abbrev", _AOF) in msg, "the >10% residual should surface"
    assert catalog_by_id[_MROW].get("abbrev", _MROW) not in msg, (
        "the <10% residual should NOT surface as a warning"
    )


def test_unverified_dungeons_are_named_not_silently_zeroed(monkeypatch):
    """A `null` residual (no usable calibration replay) must read as
    unverified, never coerce to a clean 0.0% indistinguishable from a
    well-calibrated dungeon."""
    monkeypatch.setattr(app, "load_constants", lambda: _fake_constants({_AOF: None, _MROW: 0.02}))
    msg = app._high_residual_warning()
    assert "unverified" in msg
    catalog_by_id = {d["id"]: d for d in app._dungeon_catalog()}
    assert catalog_by_id[_AOF].get("abbrev", _AOF) in msg


def test_empty_when_selected_dungeon_is_clean_and_verified(monkeypatch):
    """A dungeon with a small, well-measured residual produces no warning."""
    monkeypatch.setattr(app, "load_constants", lambda: _fake_constants({_AOF: 0.04}))
    _select(monkeypatch, [_AOF])  # +4.0%, well under the 10% threshold
    assert app._high_residual_warning() == ""


def test_run_config_popover_resolves_dungeon_ids_to_display_names(monkeypatch):
    """`_build_run_config`'s per-dungeon table must use the same display
    names the dungeon picker uses — not the raw yaml id (`algeth_ar_academy`)
    that used to render next to abbreviations elsewhere in the same popover
    (round-1 copy audit, 2026-07-05)."""
    monkeypatch.setattr(load_mod, "load_constants", lambda: _fake_constants({_AOF: 0.15}))
    cfg = load_mod._build_run_config(1000, 42, ["Altar of Fangs"], "m+_high_key_healer", "abcd")
    assert isinstance(cfg, RunConfig)
    assert cfg.per_dungeon_residuals is not None
    for name in cfg.per_dungeon_residuals:
        assert "_" not in name, f"{name!r} looks like a raw yaml id, not a display name"


def test_run_config_popover_table_scopes_to_the_users_prog_dungeons(monkeypatch):
    """Round-3 review (2026-07-05): the popover's "N dungeons not shown"
    note was added in round 2 with a comment promising "2 of *their* prog
    dungeons" but the table was never actually filtered to the user's
    selection — it always scoped to the whole constants.yaml corpus. A
    user who selects just one dungeon must see only that dungeon's
    residual, not all 8."""
    monkeypatch.setattr(
        load_mod, "load_constants", lambda: _fake_constants({_AOF: 0.15, _MROW: 0.02})
    )
    cfg = load_mod._build_run_config(1000, 42, ["Altar of Fangs"], "m+_high_key_healer", "abcd")
    assert set(cfg.per_dungeon_residuals) == {"Altar of Fangs"}


def test_run_config_popover_table_shows_everything_when_selection_is_empty(monkeypatch):
    """An empty selection (defensive edge case — the live picker always
    falls back to 'all dungeons' when nothing is selected) must not
    filter the table down to nothing."""
    monkeypatch.setattr(
        load_mod, "load_constants", lambda: _fake_constants({_AOF: 0.15, _MROW: 0.02})
    )
    cfg = load_mod._build_run_config(1000, 42, [], "m+_high_key_healer", "abcd")
    assert cfg.per_dungeon_residuals is not None
    assert set(cfg.per_dungeon_residuals) == {"Altar of Fangs", "Murder Row"}


def test_run_config_omits_noise_summary_without_a_character():
    cfg = load_mod._build_run_config(1000, 42, [], "m+_high_key_healer", "abcd", char=None)
    assert cfg.marginal_noise_summary is None


def test_run_config_carries_noise_summary_when_ci_available(monkeypatch):
    """Wiring tripwire: `_build_run_config` must actually consult
    `_marginals_ci_for`/`_marginals_for` and thread the result through — a
    stub character (duck-typed, same trick `test_run_config_strip.py`'s
    `_stub_char` uses) with a monkeypatched CI, not a full sim."""
    from types import SimpleNamespace

    marginals = {"versatility_rating": {"p": 500.0, "m": 90.0}}
    ci = {"versatility_rating": {"p": (458.3, 669.1), "m": None}}
    monkeypatch.setattr(
        load_mod, "load_constants", lambda: _fake_constants({_AOF: 0.15, _MROW: 0.02})
    )
    monkeypatch.setattr(load_mod, "_marginals_ci_for", lambda char: ci)
    monkeypatch.setattr(load_mod, "_marginals_for", lambda char: marginals)
    stub_char = SimpleNamespace(
        class_spec="protection_warrior",
        race="human",
        talents="archon-meta",
        stamina=25000,
        armor_from_gear=100000,
        strength=3000,
        agility=0,
        haste_rating=2000,
        crit_rating=1000,
        mastery_rating=500,
        versatility_rating=1500,
        parry_rating=800,
        shield_armor=12000,
        max_hp_override=None,
        stamina_in_caster_form=False,
        active_buff_spell_ids=frozenset(),
        detected_talent_spell_ids=frozenset(),
        decoded_talents=None,
    )
    cfg = load_mod._build_run_config(1000, 42, [], "m+_high_key_healer", "abcd", char=stub_char)
    assert cfg.marginal_noise_summary == "versatility ±21%"
    assert len(cfg.per_dungeon_residuals) > 1

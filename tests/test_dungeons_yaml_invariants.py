"""Catalog-level invariants for `data/dungeons.yaml`.

Without these, a typo in school_mix silently inflates the magic bucket via
`mag = 1 - phys` in `_score_for_school_mix` — flagged by the engine-math
audit (2026-05-15).
"""

from __future__ import annotations

import yaml

from simf.core.constants import DATA_DIR


def _catalog():
    with open(DATA_DIR / "dungeons.yaml") as f:
        return yaml.safe_load(f)["dungeons"]


def test_catalog_is_nonempty():
    assert _catalog()


def test_each_dungeon_has_required_fields():
    for d in _catalog():
        assert d.get("id"), f"dungeon missing id: {d}"
        assert d.get("abbrev"), f"{d['id']} missing abbrev"
        assert isinstance(d.get("school_mix"), dict), f"{d['id']} missing school_mix"


def test_school_mix_sums_to_one():
    """`_score_for_school_mix` collapses magic to `1 - phys`. If the YAML's
    school fractions don't sum to 1.0, the magic bucket silently absorbs
    or loses the slack — wrong answer with no error."""
    for d in _catalog():
        total = sum(d["school_mix"].values())
        assert abs(total - 1.0) < 0.01, (
            f"{d['id']} school_mix sums to {total:.3f}, expected 1.0 ± 0.01"
        )


def test_dungeon_ids_unique():
    ids = [d["id"] for d in _catalog()]
    assert len(ids) == len(set(ids)), "duplicate dungeon ids"


def test_dungeon_abbrevs_unique():
    """Abbrevs render in the per-dungeon expander column — duplicates would
    confuse the user when two rows show the same `Sky` or `WR` tag."""
    abbrevs = [d["abbrev"] for d in _catalog()]
    assert len(abbrevs) == len(set(abbrevs)), "duplicate dungeon abbrevs"


# ─── Season 2 (12.1.0) catalog — promoted 2026-08-30, S1 archived ─────────────
#
# Historical note: this section used to pin that the unvalidated Season-2
# placeholders lived under `season_2_catalog:` and stayed invisible to the
# live picker (which reads only `dungeons:`) until a real promotion happened.
# That promotion ran for real on 2026-08-30 (see
# scripts/promote_season2_catalog.py + tests/test_promote_season2_catalog.py)
# — `dungeons:` now holds the real, replayed Season 2 catalog, and the old
# Season 1 data survives read-only under `season_1_catalog:`. This section
# now pins the POST-promotion invariants instead.


def _raw():
    with open(DATA_DIR / "dungeons.yaml") as f:
        return yaml.safe_load(f)


_SEASON_2_IDS = {
    "altar_of_fangs",
    "murder_row",
    "den_of_nalorakk",
    "the_blinding_vale",
    "voidscar_arena",
    "ruby_life_pools",
    "temple_of_sethraliss",
    "kings_rest",
}

_SEASON_1_IDS = {
    "windrunner_spire",
    "nexus_point_xenas",
    "algeth_ar_academy",
    "pit_of_saron",
    "skyreach",
    "seat_of_the_triumvirate",
    "maisara_caverns",
    "magisters_terrace",
}


def test_live_loader_serves_season_2_now_that_it_is_promoted():
    """`load_dungeon_catalog()` reads only the `dungeons:` key — post-promotion
    that key holds the real Season 2 catalog, not Season 1's."""
    from simf.core.constants import load_dungeon_catalog

    live_ids = {d["id"] for d in load_dungeon_catalog()}
    assert live_ids == _SEASON_2_IDS, (
        f"live catalog should be the promoted Season 2 pool, got: {live_ids}"
    )


def test_season_2_catalog_key_no_longer_exists():
    """`season_2_catalog:` was folded into `dungeons:` by the promotion —
    the staging key should be gone for good, not sitting around empty."""
    assert "season_2_catalog" not in _raw()


def test_season_1_catalog_is_archived_not_live():
    """The old Season 1 data must survive (historical calibration reference)
    but stay invisible to `load_dungeon_catalog()`, which only reads
    `dungeons:` — mirrors `test_live_loader_serves_season_2_now_that_it_is_promoted`."""
    archived = _raw().get("season_1_catalog", [])
    assert {d["id"] for d in archived} == _SEASON_1_IDS

    from simf.core.constants import load_dungeon_catalog

    live_ids = {d["id"] for d in load_dungeon_catalog()}
    assert not (live_ids & _SEASON_1_IDS), (
        f"archived Season-1 dungeons leaked into the live catalog: {live_ids & _SEASON_1_IDS}"
    )

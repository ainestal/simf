"""Tests for the gear paperdoll's upgrade-track badge (P1b).

The badge turns a bare ilvl into "Myth 6/6 · i289" so the player reads track
+ rank at a glance. The contract is HONESTY: only rank bonus_ids verified
against the export corpus name a track; everything else degrades to ilvl-only,
and a track is NEVER inferred from ilvl alone (ilvl→track is many-to-one).

Coverage:
  1. Label degradation (full → track-only → ilvl-only → empty).
  2. Crafted / unknown / id-less items degrade and never guess a track.
  3. Corpus ground truth — Brutoh's real export classifies as expected.
  4. The render helper emits the tinted badge HTML.
  5. The constants table is internally consistent + the CSS is present.
"""

from __future__ import annotations

from pathlib import Path

from simf.core.constants import load_constants
from simf.io.simc_import import ItemSpec, load_simc_file
from simf.io.upgrade_track import classify_upgrade_track

REPO = Path(__file__).resolve().parents[1]
APP = (REPO / "src" / "simf" / "ui" / "app.py").read_text()


# ── 1 + 2. classifier + label degradation ────────────────────────────────────


def test_full_badge_for_verified_armor_rank() -> None:
    # 12806 = Myth 6/6 / i289 (verified, pervasive in the corpus).
    item = ItemSpec(slot="head", item_id=151333, ilvl=289, bonus_ids=[13440, 6652, 12806])
    badge = classify_upgrade_track(item)
    assert (badge.track, badge.rank, badge.max_rank) == ("Myth", 6, 6)
    assert badge.label() == "Myth 6/6 · i289"


def test_track_only_badge_for_weapon_ceiling_id() -> None:
    # 13654 = Myth weapon/trinket ceiling (298), no clean armor rank → track-only.
    item = ItemSpec(slot="main_hand", item_id=200, ilvl=298, bonus_ids=[13654])
    badge = classify_upgrade_track(item)
    assert badge.track == "Myth"
    assert badge.rank is None
    assert badge.label() == "Myth · i298"


def test_ilvl_only_for_unrecognised_rank_id() -> None:
    # An id NOT in the verified table never names a track — just shows ilvl.
    item = ItemSpec(slot="head", item_id=1, ilvl=285, bonus_ids=[99999])
    badge = classify_upgrade_track(item)
    assert badge.has_track is False
    assert badge.label() == "i285"


def test_crafted_gear_degrades_to_ilvl_only() -> None:
    # Crafted gear uses a different bonus space — must not be mislabelled.
    item = ItemSpec(
        slot="wrist",
        item_id=2,
        ilvl=288,
        bonus_ids=[12806],
        crafting_quality=5,
        crafted_stats=[36, 40],
    )
    badge = classify_upgrade_track(item)
    assert badge.has_track is False
    assert badge.label() == "i288"


def test_empty_when_no_track_and_no_ilvl() -> None:
    assert classify_upgrade_track(ItemSpec(slot="head", item_id=3)).label() == ""
    assert classify_upgrade_track(None).label() == ""


def test_never_infers_track_from_ilvl() -> None:
    # 272 is ambiguous (Hero 5/6 OR Myth 1/6). An item AT 272 but with no
    # recognised rank id must stay ilvl-only — guessing would lie.
    item = ItemSpec(slot="finger1", item_id=4, ilvl=272, bonus_ids=[6652, 13440])
    assert classify_upgrade_track(item).has_track is False


# ── 3. corpus ground truth ────────────────────────────────────────────────────


def test_brutoh_corpus_classifies_as_verified() -> None:
    """The real export is the ground truth the table was mapped from — pin it
    so a table edit that drifts from the corpus breaks loudly.

    ``shoulder``/12798 was CORRECTED 2026-07-04: originally verified as
    "Myth 2/6" by assuming every corpus item climbs to Myth 6/6 — wrong for
    this one. Brutoh's shoulder (like a real AnonGuardian1 ring carrying the same
    id) is actually already maxed on the HERO track (Hero 6/6), confirmed
    independently against Wowhead's own item tooltip
    (``Upgrade Level: Hero 6/6`` for this exact bonus_id combination, not
    Myth). See ``constants.yaml``'s ``upgrade_track_ranks`` comment."""
    imp = load_simc_file(REPO / "examples" / "brutoh.simc")
    expected = {
        "head": "Myth 6/6 · i289",  # 12806
        "neck": "Myth 1/6 · i272",  # 12801
        "shoulder": "Hero 6/6 · i276",  # 12798 — corrected 2026-07-04
    }
    for slot, want in expected.items():
        item = imp.items.get(slot)
        assert item is not None, f"{slot} missing from brutoh.simc"
        assert classify_upgrade_track(item).label() == want, slot


# ── 4. render helper ──────────────────────────────────────────────────────────


def test_track_badge_html_renders_tinted_badge() -> None:
    from simf.ui.app import _track_badge_html

    item = ItemSpec(slot="head", item_id=151333, ilvl=289, bonus_ids=[12806])
    html = _track_badge_html(item, 289)
    assert "Myth 6/6" in html
    assert 'class="track-badge track-myth"' in html
    assert 'class="slot-row-ilvl">i289' in html


def test_track_badge_html_falls_back_to_plain_ilvl() -> None:
    from simf.ui.app import _track_badge_html

    item = ItemSpec(slot="head", item_id=1, ilvl=285, bonus_ids=[99999])
    html = _track_badge_html(item, 285)
    assert "track-badge" not in html
    assert 'class="slot-row-ilvl">i285' in html


# ── 5. constants + CSS integrity ──────────────────────────────────────────────


def test_track_rank_table_is_internally_consistent() -> None:
    table = load_constants()["gear"]["upgrade_track_ranks"]
    assert table, "verified rank table is empty"
    for bid, entry in table.items():
        assert isinstance(bid, int), f"rank bonus_id key {bid!r} should be int"
        assert entry.get("track"), f"{bid} missing track"
        assert entry.get("ilvl"), f"{bid} missing ilvl"
        rank, mx = entry.get("rank"), entry.get("max_rank")
        # rank + max_rank come as a pair, and rank never exceeds max_rank.
        assert (rank is None) == (mx is None), f"{bid} has a half-specified rank"
        if rank is not None:
            assert 1 <= rank <= mx, f"{bid} rank {rank}/{mx} out of range"


def test_track_colors_cover_the_tracks_in_use() -> None:
    consts = load_constants()["gear"]
    colors = consts["upgrade_track_colors"]
    used_tracks = {e["track"].lower() for e in consts["upgrade_track_ranks"].values()}
    assert used_tracks <= set(colors), f"tracks without a tint: {used_tracks - set(colors)}"


def test_track_badge_css_present() -> None:
    assert ".track-badge" in APP
    assert "_render_track_badge_css" in APP
    assert "upgrade_track_colors" in APP

"""Unit tests for ``scripts/promote_season2_catalog.py``.

Two kinds of coverage: (1) synthetic fixtures with a made-up header/comment
structure, proving the text-surgery logic (block-finding, comment
preservation, header refresh) is correct in general, not just lucky against
one specific file; (2) the REAL shipped ``src/simf/data/dungeons.yaml``,
which was actually promoted for real on 2026-08-30 (real Season 2 WCL data
replayed, ``season_2_catalog:`` folded into the live ``dungeons:`` key, the
old Season 1 data archived to ``season_1_catalog:``) — these tests pin that
the promotion took and stuck, rather than (as before promotion) pinning that
the safety gate correctly refused to run early.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "promote_season2_catalog.py"


def _load():
    spec = importlib.util.spec_from_file_location("promote_season2_catalog", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["promote_season2_catalog"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()

_SYNTHETIC_INCOMPLETE = """\
# Some Season 1 M+ dungeon catalog — patch 99.9.9
#
# schema notes here, unrelated to the test.

dungeons:
  - id: old_dungeon_a
    name: "Old Dungeon A"
    school_mix:
      physical: 1.0
    description: >
      A season 1 dungeon with a real multi-line
      description that must survive verbatim.
  - id: old_dungeon_b
    name: "Old Dungeon B"
    school_mix:
      fire: 1.0

# Some commentary about season 2 not being live yet, NOT LIVE, placeholder.

season_2_catalog:
  - id: new_dungeon_a
    name: "New Dungeon A"
    origin: "Test Expansion"
  - id: new_dungeon_b
    name: "New Dungeon B"
    origin: "Test Expansion"
"""

_SYNTHETIC_COMPLETE = """\
# Some Season 1 M+ dungeon catalog — patch 99.9.9
#
# schema notes here, unrelated to the test.

dungeons:
  - id: old_dungeon_a
    name: "Old Dungeon A"
    school_mix:
      physical: 1.0
    description: >
      A season 1 dungeon with a real multi-line
      description that must survive verbatim.

# Some commentary about season 2 not being live yet, NOT LIVE, placeholder.

season_2_catalog:
  - id: new_dungeon_a
    name: "New Dungeon A"
    origin: "Test Expansion"
    recommended_profile: m+_pull_melee
    school_mix:
      physical: 0.8
      fire: 0.2
    description: >
      A brand-new season 2 dungeon with its own
      real description.
"""


def test_check_completeness_reports_every_missing_field():
    gaps = mod.check_completeness(_SYNTHETIC_INCOMPLETE)
    assert set(gaps) == {"new_dungeon_a", "new_dungeon_b"}
    assert any("school_mix" in g for g in gaps["new_dungeon_a"])
    assert any("recommended_profile" in g for g in gaps["new_dungeon_a"])


def test_check_completeness_empty_when_all_entries_are_complete():
    assert mod.check_completeness(_SYNTHETIC_COMPLETE) == {}


def test_check_completeness_flags_an_unknown_profile_name():
    bad = _SYNTHETIC_COMPLETE.replace(
        "recommended_profile: m+_pull_melee", "recommended_profile: not_a_real_profile"
    )
    gaps = mod.check_completeness(bad)
    assert "new_dungeon_a" in gaps
    assert any("doesn't match any file" in g for g in gaps["new_dungeon_a"])


def test_build_promoted_text_swaps_the_two_blocks():
    new_text = mod.build_promoted_text(_SYNTHETIC_COMPLETE)
    parsed = yaml.safe_load(new_text)
    assert "season_2_catalog" not in parsed
    assert [d["id"] for d in parsed["dungeons"]] == ["new_dungeon_a"]
    assert [d["id"] for d in parsed["season_1_catalog"]] == ["old_dungeon_a"]


def test_build_promoted_text_preserves_multiline_descriptions_verbatim():
    """The whole point of text-surgery over parse+redump: comments AND
    block-scalar prose must survive byte-for-byte, not just structurally."""
    new_text = mod.build_promoted_text(_SYNTHETIC_COMPLETE)
    assert "A brand-new season 2 dungeon with its own" in new_text
    assert "A season 1 dungeon with a real multi-line" in new_text


def test_build_promoted_text_drops_the_stale_not_live_commentary():
    new_text = mod.build_promoted_text(_SYNTHETIC_COMPLETE)
    assert "NOT LIVE" not in new_text


def test_build_promoted_text_refreshes_the_header_title_and_note():
    new_text = mod.build_promoted_text(_SYNTHETIC_COMPLETE)
    assert "Some Season 1 M+ dungeon catalog" not in new_text
    assert re.search(r"promoted \d{4}-\d{2}-\d{2}", new_text)
    # The reusable schema-notes line must still be there — only the
    # season-specific title/closing note are meant to change.
    assert "schema notes here, unrelated to the test." in new_text


def test_build_promoted_text_adds_an_archive_header_naming_the_source():
    new_text = mod.build_promoted_text(_SYNTHETIC_COMPLETE)
    assert "Season 1 archive" in new_text
    assert "season_1_catalog:" in new_text


def test_real_shipped_dungeons_yaml_has_been_promoted():
    """The real file was actually promoted 2026-08-30 — updates the test that
    used to pin the pre-promotion "still a bare stub" state (see git history
    for that version). `season_2_catalog:` is gone for good (folded into
    `dungeons:`), and there's nothing left to run `check_completeness`/
    `build_promoted_text` against — `_find_block` raising is the correct,
    permanent post-promotion signature, not a bug. If this test ever starts
    failing because `season_2_catalog:` reappeared (e.g. staged for a future
    Season 3), that's a real signal something regressed the archive step."""
    from simf.core.constants import DATA_DIR

    text = (DATA_DIR / "dungeons.yaml").read_text()
    with pytest.raises(ValueError):
        mod._find_block(text, "season_2_catalog")


def test_real_shipped_dungeons_yaml_dungeons_block_is_the_promoted_season_2_data():
    """Regression pin: the live `dungeons:` key holds the 8 real Season 2
    dungeons with complete school_mix/recommended_profile, and the old
    Season 1 data survived the archive step under `season_1_catalog:`."""
    from simf.core.constants import DATA_DIR

    text = (DATA_DIR / "dungeons.yaml").read_text()
    parsed = yaml.safe_load(text)
    assert "season_2_catalog" not in parsed
    assert "season_1_catalog" in parsed

    live_ids = {d["id"] for d in parsed["dungeons"]}
    assert live_ids == {
        "altar_of_fangs",
        "murder_row",
        "den_of_nalorakk",
        "the_blinding_vale",
        "voidscar_arena",
        "ruby_life_pools",
        "temple_of_sethraliss",
        "kings_rest",
    }
    for d in parsed["dungeons"]:
        assert isinstance(d.get("school_mix"), dict) and d["school_mix"], (
            f"{d['id']} missing real school_mix post-promotion"
        )
        assert d.get("recommended_profile"), f"{d['id']} missing recommended_profile"

    archived_ids = {d["id"] for d in parsed["season_1_catalog"]}
    assert archived_ids == {
        "windrunner_spire",
        "nexus_point_xenas",
        "algeth_ar_academy",
        "pit_of_saron",
        "skyreach",
        "seat_of_the_triumvirate",
        "maisara_caverns",
        "magisters_terrace",
    }
    assert len(parsed["dungeons"]) == 8
    assert len(parsed["season_1_catalog"]) == 8
    assert parsed["dungeons"][0]["id"] == "altar_of_fangs"
    assert parsed["season_1_catalog"][0]["id"] == "windrunner_spire"
    # A real season-1 dungeon's rich description must survive the swap.
    algeth_ar = next(d for d in parsed["season_1_catalog"] if d["id"] == "algeth_ar_academy")
    assert "school_mix" in algeth_ar
    assert "description" in algeth_ar

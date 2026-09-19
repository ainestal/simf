"""Two confirmed Wowhead-tooltip-resolver bugs (2026-07-11 audit):

1. Crafted-item secondary-stat mislabel — Wowhead's XML tooltip endpoint
   ignores `crafted_stats=` URL params entirely and always renders the bonus
   set's own DEFAULT secondary pair, not the crafter's real choice
   (empirically confirmed live against real Brutoh/AnonGuardian3 exports —
   see `item_db._CRAFTED_STAT_ID_MAP`'s docstring). The two numeric
   magnitudes are still correct (crafted items split their budget evenly),
   so the fix is a post-fetch relabel using `ItemSpec.crafted_stats`.

2. Hybrid agi/str primary-stat tooltip labels (`[Agility or Strength or
   Intellect]`, `[Agility or Strength]`) were hardcoded to `strength` —
   silently zeroing the primary-stat contribution of any tri/dual-primary
   item for the three agility tank specs (Guardian / Brewmaster / VDH) on
   the resolver-fallback path.
"""

from __future__ import annotations

from simf.io import item_db
from simf.io.simc_import import ItemSpec


def _fake_xml(*lines: str) -> str:
    # Every line needs its OWN trailing `<br>` (not just a joiner between
    # lines) — the stat-parsing regex requires the value+label to be
    # followed by `<` or end-of-string, and the label character class
    # includes `]`, so a LAST line with no trailing tag would let the
    # regex's non-greedy label match run into the CDATA's closing `]]>`
    # and fail to terminate before the literal `>` (confirmed by a failed
    # first draft of this helper joining with a bare `<br>` between lines).
    body = "".join(f"{line}<br>" for line in lines)
    return f"<?xml version='1.0'?><wowhead><htmlTooltip><![CDATA[{body}]]></htmlTooltip></wowhead>"


class _FakeResp:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def _patch_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(item_db, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(item_db, "_SEED_CACHE_DIR", tmp_path / "seed")


# ─── 1. Crafted-item secondary-stat relabel ───────────────────────────────────


def test_crafted_stats_relabels_wowheads_wrong_default_pair(monkeypatch, tmp_path):
    """Real repro (Brutoh's wrist, item=237834): the export says
    `crafted_stats=32/36` (crit+haste), but Wowhead's own tooltip renders
    the DEFAULT pair for that bonus set — haste (rtg36) + versatility
    (rtg40), both 45. Without the fix the item silently carries versatility
    instead of crit; with the fix the two equal-magnitude values are refiled
    under crit_rating + haste_rating, the crafter's actual choice."""
    _patch_cache(monkeypatch, tmp_path)
    fake_xml = _fake_xml(
        "<!--stat7-->+948 Stamina",
        "<!--rtg36-->45 Haste",
        "<!--rtg40-->45 Versatility",
    )
    monkeypatch.setattr(item_db.requests, "get", lambda *a, **kw: _FakeResp(fake_xml))

    stats = item_db.fetch_item_stats_wowhead(237834, crafted_stats=[32, 36])
    assert stats is not None
    assert stats.get("crit_rating") == 45, f"crafted_stats=32/36 must yield crit — got {stats!r}"
    assert stats.get("haste_rating") == 45
    assert "versatility_rating" not in stats, "Wowhead's wrong default label must not survive"
    assert stats.get("stamina") == 948


def test_crafted_stats_noop_when_wowhead_default_already_matches(monkeypatch, tmp_path):
    """Real repro (a AnonGuardian3 chest, item=244570): `crafted_stats=49/36`
    (mastery+haste) happens to be exactly what Wowhead's default pair
    already renders — the relabel must be a no-op (not accidentally drop or
    duplicate a stat) in this case."""
    _patch_cache(monkeypatch, tmp_path)
    fake_xml = _fake_xml(
        "<!--stat73-->+119 [Agility or Intellect]",
        "<!--rtg36-->81 Haste",
        "<!--rtg49-->81 Mastery",
    )
    monkeypatch.setattr(item_db.requests, "get", lambda *a, **kw: _FakeResp(fake_xml))

    stats = item_db.fetch_item_stats_wowhead(
        244570, crafted_stats=[49, 36], class_spec="guardian_druid"
    )
    assert stats.get("haste_rating") == 81
    assert stats.get("mastery_rating") == 81
    assert stats.get("agility") == 119


def test_crafted_stats_ignored_when_shape_unexpected(monkeypatch, tmp_path):
    """A single-crafted-stat or partial-parse item must not be corrupted —
    the remap only fires on the well-formed 2-id/2-value shape."""
    _patch_cache(monkeypatch, tmp_path)
    fake_xml = _fake_xml("<!--rtg36-->45 Haste")
    monkeypatch.setattr(item_db.requests, "get", lambda *a, **kw: _FakeResp(fake_xml))

    stats = item_db.fetch_item_stats_wowhead(999001, crafted_stats=[32, 36])
    assert stats == {"haste_rating": 45}


def test_crafted_variants_of_same_item_bonus_do_not_collide_in_disk_cache(monkeypatch, tmp_path):
    """Two crafted variants of the SAME base item+bonus (different chosen
    secondary pair) must resolve independently, not share one cache entry —
    the second fetch must not silently serve the first variant's relabeled
    stats."""
    _patch_cache(monkeypatch, tmp_path)
    fake_xml = _fake_xml(
        "<!--rtg32-->42 Critical Strike",
        "<!--rtg36-->42 Haste",
    )
    calls = {"n": 0}

    def _get(*a, **kw):
        calls["n"] += 1
        return _FakeResp(fake_xml)

    monkeypatch.setattr(item_db.requests, "get", _get)

    crit_haste = item_db.fetch_item_stats_wowhead(237831, crafted_stats=[32, 36])
    crit_vers = item_db.fetch_item_stats_wowhead(237831, crafted_stats=[32, 40])

    assert crit_haste.get("crit_rating") == 42
    assert crit_haste.get("haste_rating") == 42
    assert "versatility_rating" not in crit_haste

    assert crit_vers.get("crit_rating") == 42
    assert crit_vers.get("versatility_rating") == 42
    assert "haste_rating" not in crit_vers, (
        "second crafted variant must not reuse the first variant's cached (wrong) labels"
    )
    assert calls["n"] == 2, "distinct crafted_stats must not share one disk-cache entry"

    # Re-fetching either variant now hits its own cache entry — no 3rd network call.
    item_db.fetch_item_stats_wowhead(237831, crafted_stats=[32, 36])
    assert calls["n"] == 2


# ─── 2. Hybrid agi/str primary label, resolved per class_spec ────────────────


def test_hybrid_primary_resolves_to_agility_for_agility_spec(monkeypatch, tmp_path):
    _patch_cache(monkeypatch, tmp_path)
    fake_xml = _fake_xml("<!--stat71-->+101 [Agility or Strength or Intellect]")
    monkeypatch.setattr(item_db.requests, "get", lambda *a, **kw: _FakeResp(fake_xml))

    stats = item_db.fetch_item_stats_wowhead(250256, class_spec="guardian_druid")
    assert stats == {"agility": 101}


def test_hybrid_primary_resolves_to_strength_for_strength_spec(monkeypatch, tmp_path):
    _patch_cache(monkeypatch, tmp_path)
    fake_xml = _fake_xml("<!--stat71-->+101 [Agility or Strength or Intellect]")
    monkeypatch.setattr(item_db.requests, "get", lambda *a, **kw: _FakeResp(fake_xml))

    stats = item_db.fetch_item_stats_wowhead(250256, class_spec="protection_warrior")
    assert stats == {"strength": 101}


def test_hybrid_primary_defaults_to_strength_when_class_spec_omitted(monkeypatch, tmp_path):
    """Un-migrated callers that don't pass class_spec at all keep the old
    (strength-only) behavior — no regression for a caller not yet threaded
    through."""
    _patch_cache(monkeypatch, tmp_path)
    fake_xml = _fake_xml("<!--stat71-->+128 [Agility or Strength]")
    monkeypatch.setattr(item_db.requests, "get", lambda *a, **kw: _FakeResp(fake_xml))

    stats = item_db.fetch_item_stats_wowhead(252420)
    assert stats == {"strength": 128}


def test_hybrid_primary_resolved_correctly_on_cache_hit_too(monkeypatch, tmp_path):
    """The disk cache stores the UNRESOLVED placeholder (spec isn't a caching
    dimension) — a cache HIT must still resolve per the caller's class_spec,
    not silently return whatever the first caller's spec resolved to."""
    _patch_cache(monkeypatch, tmp_path)
    fake_xml = _fake_xml("<!--stat71-->+101 [Agility or Strength or Intellect]")
    calls = {"n": 0}

    def _get(*a, **kw):
        calls["n"] += 1
        return _FakeResp(fake_xml)

    monkeypatch.setattr(item_db.requests, "get", _get)

    warrior_first = item_db.fetch_item_stats_wowhead(250256, class_spec="protection_warrior")
    guardian_second = item_db.fetch_item_stats_wowhead(250256, class_spec="guardian_druid")

    assert warrior_first == {"strength": 101}
    assert guardian_second == {"agility": 101}, (
        "a cache hit must re-resolve per class_spec, not replay the first caller's resolution"
    )
    assert calls["n"] == 1, "both calls share one item+bonus — only one network fetch expected"


# ─── Threaded through fetch_item_stats_for_spec / resolve_equipped_stats ─────


def test_fetch_item_stats_for_spec_threads_crafted_stats_and_class_spec(monkeypatch, tmp_path):
    _patch_cache(monkeypatch, tmp_path)
    fake_xml = _fake_xml(
        "<!--stat71-->+101 [Agility or Strength or Intellect]",
        "<!--rtg32-->42 Critical Strike",
        "<!--rtg36-->42 Haste",
    )
    monkeypatch.setattr(item_db.requests, "get", lambda *a, **kw: _FakeResp(fake_xml))

    spec = ItemSpec(slot="trinket1", item_id=250256, crafted_stats=[32, 40])
    stats = item_db.fetch_item_stats_for_spec(spec, class_spec="guardian_druid")
    assert stats.get("agility") == 101
    assert stats.get("crit_rating") == 42
    assert stats.get("versatility_rating") == 42
    assert "haste_rating" not in stats


def test_resolve_equipped_stats_threads_class_spec_to_every_item(monkeypatch, tmp_path):
    _patch_cache(monkeypatch, tmp_path)
    fake_xml = _fake_xml("<!--stat71-->+64 [Agility or Strength]")
    monkeypatch.setattr(item_db.requests, "get", lambda *a, **kw: _FakeResp(fake_xml))

    items = {"trinket1": ItemSpec(slot="trinket1", item_id=252420)}
    guardian_totals = item_db.resolve_equipped_stats(items, class_spec="guardian_druid")
    assert guardian_totals.get("agility") == 64
    assert "strength" not in guardian_totals


# ─── Real committed seed files resolve per class_spec (2026-07-11) ───────────
#
# The two hybrid agi/str motivating items (Heart of Wind 250256, Solarflare
# Prism 252420) have committed, no-TTL seed entries under
# src/simf/data/item_cache_seed/ — these are read WITHOUT _patch_cache above,
# against the REAL seed directory, because the bug this guards against only
# shows up against the real files: they used to store the OLD resolved-at-parse
# shape ({"strength": 101}), which _resolve_hybrid_primary passes through
# unchanged (no placeholder key present) regardless of class_spec — silently
# defeating the hybrid-primary fix for exactly these two items on a fresh
# checkout / the offline SIMF_PUBLIC box, since seeds are read before any live
# fetch and never expire.


def test_real_seed_heart_of_wind_resolves_per_class_spec(monkeypatch):
    def _no_network(*a, **kw):
        raise AssertionError("a seed hit must not make a live request")

    monkeypatch.setattr(item_db.requests, "get", _no_network)

    guardian = item_db.fetch_item_stats_wowhead(
        250256, bonus_ids=[6652, 12699, 12801, 13440], class_spec="guardian_druid"
    )
    warrior = item_db.fetch_item_stats_wowhead(
        250256, bonus_ids=[6652, 12699, 12801, 13440], class_spec="protection_warrior"
    )
    assert guardian == {"agility": 101}
    assert warrior == {"strength": 101}


def test_real_seed_solarflare_prism_resolves_per_class_spec(monkeypatch):
    def _no_network(*a, **kw):
        raise AssertionError("a seed hit must not make a live request")

    monkeypatch.setattr(item_db.requests, "get", _no_network)

    guardian = item_db.fetch_item_stats_wowhead(
        252420, bonus_ids=[6652, 12699, 13440, 13654], class_spec="guardian_druid"
    )
    warrior = item_db.fetch_item_stats_wowhead(
        252420, bonus_ids=[6652, 12699, 13440, 13654], class_spec="protection_warrior"
    )
    assert guardian == {"agility": 128}
    assert warrior == {"strength": 128}

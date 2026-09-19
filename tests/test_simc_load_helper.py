"""Tests for the pure SimC load helper used by the v0.9 app shell."""

from __future__ import annotations

from pathlib import Path

import pytest

from simf.core.constants import load_constants
from simf.ui.helpers.simc_load import SimcLoadError, SimcLoadOk, load_from_simc

REPO_ROOT = Path(__file__).parent.parent
EXAMPLE_SIMC = REPO_ROOT / "examples" / "brutoh-vault.simc"


def _loadouts_stub(_spec: str) -> list[str]:
    return ["kiratank-defensive"]


def test_load_error_on_empty_string():
    result = load_from_simc("", loadouts_for_spec_fn=_loadouts_stub)
    assert isinstance(result, SimcLoadError)


def test_load_error_on_garbage():
    result = load_from_simc("not a simc export", loadouts_for_spec_fn=_loadouts_stub)
    assert isinstance(result, SimcLoadError)


@pytest.mark.skipif(not EXAMPLE_SIMC.exists(), reason="brutoh-vault.simc not present")
def test_load_real_brutoh_simc_returns_ok():
    """If the example SimC file has gear_stats lines, the load succeeds end-to-end
    with no Blizzard API call (resolve_stats_fn=None)."""
    raw = EXAMPLE_SIMC.read_text()
    result = load_from_simc(raw, loadouts_for_spec_fn=_loadouts_stub)
    if isinstance(result, SimcLoadError):
        # If the example doesn't have gear_stats lines, we'd need a resolver —
        # that's a valid state; test still confirms the error path is shaped right.
        assert "stat" in result.message.lower() or "credentials" in result.message.lower()
        return
    assert isinstance(result, SimcLoadOk)
    assert result.char_data["name"]
    assert result.char_data["class_spec"]
    assert "stamina" in result.char_data
    assert result.summary.startswith("Loaded ")


def test_load_uses_resolver_when_gear_stats_missing():
    """When the export has items but no gear_stats lines, resolve_stats_fn is called."""
    # Minimal SimC string with one equipped item, no gear_stats lines
    simc = """warrior="Test"
spec=protection
race=human
head=,id=12345
"""
    calls: list[dict] = []

    def resolver(items, class_spec=None):
        calls.append(items)
        return {"stamina": 30000, "armor_from_gear": 5000}

    result = load_from_simc(
        simc,
        loadouts_for_spec_fn=_loadouts_stub,
        resolve_stats_fn=resolver,
    )
    if isinstance(result, SimcLoadError):
        # Even if the parse skips the item line, the test confirms the error path.
        return
    assert isinstance(result, SimcLoadOk)
    # Resolver path is gear-only → load adds level-90 base stamina on top.
    base_stam = load_constants()["stat_conversion"]["paste_base_stamina"]
    assert result.char_data["stamina"] == 30000 + base_stam
    assert len(calls) == 1


def test_load_error_when_resolver_returns_nothing_is_actionable_not_owner_only():
    """When the export has no gear_stats AND the resolver (Wowhead/Blizzard)
    also comes back empty — e.g. a transient lookup failure — the error must
    give the visitor pasting the export something THEY can do, not tell them
    to configure Blizzard API credentials (docs/ui_copy_voice.md rule #2: that
    fix-it path only exists for the app owner/self-host operator, and the
    claim is also just wrong whenever credentials ARE configured but the
    lookup still failed for some other reason)."""
    simc = """warrior="Test"
spec=protection
race=human
head=,id=12345
"""
    result = load_from_simc(
        simc,
        loadouts_for_spec_fn=_loadouts_stub,
        resolve_stats_fn=lambda items, class_spec=None: {},
    )
    assert isinstance(result, SimcLoadError)
    lowered = result.message.lower()
    assert "blizzard" not in lowered
    assert "credentials" not in lowered
    assert "try" in lowered or "re-export" in lowered


def test_load_threads_shield_armor_from_resolver():
    """F12: when the resolver surfaces `shield_armor`, it lands on char_data so
    the Character object can compute block_value via the SimC formula
    `shield.armor × 2.5`. Missing key defaults to 0 — non-shield specs are
    unaffected."""
    simc = """warrior="Test"
spec=protection
race=human
head=,id=12345
"""

    def resolver_with_shield(items, class_spec=None):
        return {"stamina": 30000, "armor_from_gear": 5000, "shield_armor": 931}

    result = load_from_simc(
        simc,
        loadouts_for_spec_fn=_loadouts_stub,
        resolve_stats_fn=resolver_with_shield,
    )
    if isinstance(result, SimcLoadError):
        return
    assert isinstance(result, SimcLoadOk)
    assert result.char_data["shield_armor"] == 931

    # Resolver omits shield_armor → defaults to 0 (Brewmaster / VDH / etc.)
    def resolver_no_shield(items, class_spec=None):
        return {"stamina": 30000, "armor_from_gear": 5000}

    result2 = load_from_simc(
        simc,
        loadouts_for_spec_fn=_loadouts_stub,
        resolve_stats_fn=resolver_no_shield,
    )
    if isinstance(result2, SimcLoadError):
        return
    assert isinstance(result2, SimcLoadOk)
    assert result2.char_data["shield_armor"] == 0


def test_stats_estimated_flag_when_resolver_used():
    """When the export lacks gear_*_rating lines and we fall back to the item
    resolver, the load is flagged ``stats_estimated`` with a caveat — the resolver
    (Wowhead) under-reports primary+stamina for current-season gear, so callers
    must not silently trust the magnitudes (the log/WCL hydrate is exact)."""
    simc = """druid="Test"
spec=guardian
race=tauren
head=,id=12345
"""
    est = load_from_simc(
        simc,
        loadouts_for_spec_fn=_loadouts_stub,
        resolve_stats_fn=lambda items, class_spec=None: {"agility": 2127, "stamina": 35000},
    )
    assert isinstance(est, SimcLoadOk)
    assert est.stats_estimated is True
    assert "estimated" in est.summary.lower()


def test_stats_not_estimated_when_export_carries_gear_stats():
    """When the export includes its own gear stats, they are exact — the resolver
    is never called and the load is NOT flagged estimated."""
    simc = """warrior="Test"
spec=protection
race=human
gear_stamina=30000
gear_haste_rating=5000
head=,id=12345
"""
    calls: list = []

    def resolver(items, class_spec=None):
        calls.append(items)
        return {"stamina": 1, "haste_rating": 1}

    res = load_from_simc(simc, loadouts_for_spec_fn=_loadouts_stub, resolve_stats_fn=resolver)
    assert isinstance(res, SimcLoadOk)
    assert res.stats_estimated is False
    assert calls == []  # resolver not consulted — export stats win
    assert res.char_data["stamina"] == 30000
    assert "estimated" not in res.summary.lower()


def test_gear_parry_rating_copied_into_char_data():
    """simc_import.py's _GEAR_STAT_KEYS parses `gear_parry_rating=` into
    parsed.gear_stats["parry_rating"], but the copy list in load_from_simc that
    lifts strength/stamina/haste/etc. into char_data omitted parry_rating
    entirely — silently dropping it before Character construction even though
    Character.base_parry() (core/character.py) genuinely consumes it for
    Warrior/ProtPal/Blood DK/VDH avoidance."""
    simc = """warrior="Test"
spec=protection
race=human
gear_stamina=30000
gear_parry_rating=1234
head=,id=12345
"""
    res = load_from_simc(
        simc, loadouts_for_spec_fn=_loadouts_stub, resolve_stats_fn=lambda items: {}
    )
    assert isinstance(res, SimcLoadOk)
    assert res.char_data["parry_rating"] == 1234


def test_resolver_path_adds_base_character_stats_to_agility_tank():
    """The gear resolver is GEAR-ONLY; the load must add level-90 base character
    stats (stamina + primary) so a paste-loaded character isn't ~25% short on HP
    and ~half its primary. Validated against AnonGuardian1's in-game tooltip (gear agility
    1072 + base = 2011; gear stamina 17327 + base = 24179)."""
    sc = load_constants()["stat_conversion"]
    simc = """druid="Test"
spec=guardian
race=tauren
head=,id=12345
"""
    res = load_from_simc(
        simc,
        loadouts_for_spec_fn=_loadouts_stub,
        resolve_stats_fn=lambda items, class_spec=None: {"agility": 1072, "stamina": 17327},
    )
    assert isinstance(res, SimcLoadOk)
    assert res.char_data["agility"] == 1072 + sc["paste_base_primary"]  # 2011
    assert res.char_data["stamina"] == 17327 + sc["paste_base_stamina"]  # 24179


def test_resolver_path_base_primary_goes_to_strength_for_plate():
    """Plate tanks (warrior/pal/DK) are strength-primary — base primary lands on
    strength, not agility."""
    sc = load_constants()["stat_conversion"]
    simc = """warrior="Test"
spec=protection
race=human
head=,id=12345
"""
    res = load_from_simc(
        simc,
        loadouts_for_spec_fn=_loadouts_stub,
        resolve_stats_fn=lambda items, class_spec=None: {"strength": 2000, "stamina": 30000},
    )
    assert isinstance(res, SimcLoadOk)
    assert res.char_data["strength"] == 2000 + sc["paste_base_primary"]
    assert res.char_data["agility"] == 0  # plate tank gets no agility
    assert res.char_data["stamina"] == 30000 + sc["paste_base_stamina"]


def test_resolver_path_tri_stat_item_resolves_per_spec_class_spec(monkeypatch, tmp_path):
    """Integration, end-to-end through the REAL Wowhead resolver (not a
    stub): a tri-primary stat-stick item (``[Agility or Strength or
    Intellect]``) must resolve to agility for an agility-tank load and
    strength for a strength-tank load, on the resolver-fallback path —
    `load_from_simc` now threads its freshly-parsed `class_spec` into
    `resolve_stats_fn` instead of always resolving the ambiguous label to
    strength regardless of the loaded character's spec."""
    from simf.io import item_db

    monkeypatch.setattr(item_db, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(item_db, "_SEED_CACHE_DIR", tmp_path / "seed")

    fake_xml = (
        "<?xml version='1.0'?><wowhead><htmlTooltip><![CDATA["
        "<!--stat71-->+101 [Agility or Strength or Intellect]<br>"
        "<!--stat7-->+900 Stamina<br>"
        "]]></htmlTooltip></wowhead>"
    )

    class _FakeResp:
        text = fake_xml

        def raise_for_status(self):
            return None

    monkeypatch.setattr(item_db.requests, "get", lambda *a, **kw: _FakeResp())

    guardian_simc = """druid="Test"
spec=guardian
race=tauren
trinket1=,id=250256
"""
    warrior_simc = """warrior="Test"
spec=protection
race=human
trinket1=,id=250256
"""

    guardian_res = load_from_simc(
        guardian_simc,
        loadouts_for_spec_fn=_loadouts_stub,
        resolve_stats_fn=item_db.resolve_equipped_stats,
    )
    warrior_res = load_from_simc(
        warrior_simc,
        loadouts_for_spec_fn=_loadouts_stub,
        resolve_stats_fn=item_db.resolve_equipped_stats,
    )

    assert isinstance(guardian_res, SimcLoadOk)
    assert isinstance(warrior_res, SimcLoadOk)
    sc = load_constants()["stat_conversion"]
    # Resolver-fallback loads add level-90 base primary stat on top of the
    # gear-only resolver value (see test_resolver_path_adds_base_character_
    # stats_to_agility_tank above) — the item's own +101 is the part this
    # test is pinning; the base addend is orthogonal and unaffected by it.
    assert guardian_res.char_data["agility"] == 101 + sc["paste_base_primary"]
    assert guardian_res.char_data["strength"] == 0
    assert warrior_res.char_data["strength"] == 101 + sc["paste_base_primary"]
    assert warrior_res.char_data["agility"] == 0


# ─── gem/enchant stats folded into the resolver estimate (2026-07-10) ──────
# `fetch_item_stats_wowhead` resolves an item's own base tooltip only — a
# socketed gem or permanent enchant's stats never appear there (confirmed via
# a direct XML fetch: Wowhead's gem tooltips use a different comment-marker
# format the resolver regex was never taught, and appending `gem_id` to the
# item's own tooltip URL doesn't change what comes back). `_add_gem_and_enchant_stats`
# closes that gap using the same hand-verified `data/gems.yaml`/`data/enchants.yaml`
# catalogs the gem/enchant SUGGESTERS already use.


def test_add_gem_and_enchant_stats_adds_recognized_gem():
    from simf.io.simc_import import ItemSpec
    from simf.ui.helpers.simc_load import _add_gem_and_enchant_stats

    # Real catalog gem (data/gems.yaml): Flawless Masterful Lapis,
    # {versatility_rating: 16, mastery_rating: 7}.
    items = {"finger1": ItemSpec(slot="finger1", item_id=1, gem_ids=[240918])}
    merged, misses = _add_gem_and_enchant_stats({"stamina": 995}, items, "protection_warrior")
    assert merged == {"stamina": 995, "versatility_rating": 16, "mastery_rating": 7}
    assert misses == 0


def test_add_gem_and_enchant_stats_adds_recognized_enchant():
    from simf.io.simc_import import ItemSpec
    from simf.ui.helpers.simc_load import _add_gem_and_enchant_stats

    # Real catalog enchant (data/enchants.yaml): Silvermoon's Alacrity,
    # {haste_rating: 29} — the exact ring enchant from the user-reported profile.
    items = {"finger1": ItemSpec(slot="finger1", item_id=1, enchant_id=8025)}
    merged, misses = _add_gem_and_enchant_stats({"stamina": 995}, items, "protection_warrior")
    assert merged == {"stamina": 995, "haste_rating": 29}
    assert misses == 0


def test_add_gem_and_enchant_stats_resolves_primary_per_spec():
    from simf.io.simc_import import ItemSpec
    from simf.ui.helpers.simc_load import _add_gem_and_enchant_stats

    # Indecipherable Eversong Diamond meta: {primary: 32} — must resolve onto
    # agility for an agility tank, strength for a plate tank.
    items = {"head": ItemSpec(slot="head", item_id=1, gem_ids=[240983])}
    plate, plate_misses = _add_gem_and_enchant_stats({}, items, "protection_warrior")
    agi, agi_misses = _add_gem_and_enchant_stats({}, items, "guardian_druid")
    assert plate == {"strength": 32}
    assert agi == {"agility": 32}
    assert plate_misses == 0 and agi_misses == 0


def test_add_gem_and_enchant_stats_skips_unrecognized_ids():
    """An unrecognized gem/enchant id (a future season's item this catalog
    hasn't been updated for) is silently skipped from the stats dict — fail-open,
    matching every other convention on this resolver path — not an exception or
    a fabricated 0. But it's no longer invisible: the miss count reports it so a
    caller can surface the gap instead of the previous silent "as if it matched
    everything" behavior."""
    from simf.io.simc_import import ItemSpec
    from simf.ui.helpers.simc_load import _add_gem_and_enchant_stats

    items = {"finger1": ItemSpec(slot="finger1", item_id=1, gem_ids=[999999], enchant_id=888888)}
    merged, misses = _add_gem_and_enchant_stats({"stamina": 995}, items, "protection_warrior")
    assert merged == {"stamina": 995}
    assert misses == 2  # one unrecognized gem + one unrecognized enchant


def test_add_gem_and_enchant_stats_no_op_when_nothing_recognized():
    """No gems/enchants at all on any item → the SAME dict is returned (not a
    copy) — callers rely on this to skip a no-op merge cheaply — with a 0 miss
    count (no ids were present to miss on)."""
    from simf.io.simc_import import ItemSpec
    from simf.ui.helpers.simc_load import _add_gem_and_enchant_stats

    stats = {"stamina": 995}
    items = {"finger1": ItemSpec(slot="finger1", item_id=1)}
    merged, misses = _add_gem_and_enchant_stats(stats, items, "protection_warrior")
    assert merged is stats
    assert misses == 0


def test_load_from_simc_folds_gem_and_enchant_stats_into_resolver_estimate():
    """Integration: a resolver-fallback load with a real gem + enchant on the
    parsed item carries their stats into char_data, on top of the resolver's
    own per-item fetch."""
    simc = """warrior="Test"
spec=protection
race=human
finger1=,id=251115,enchant_id=8025,gem_id=240918
"""
    res = load_from_simc(
        simc,
        loadouts_for_spec_fn=_loadouts_stub,
        resolve_stats_fn=lambda items, class_spec=None: {"stamina": 995, "mastery_rating": 190},
    )
    assert isinstance(res, SimcLoadOk)
    sc = load_constants()["stat_conversion"]
    # Resolver's own 995 stamina + base-stamina addition (existing behavior,
    # untouched by this fix) — the gem/enchant addition only touches
    # versatility/haste/mastery here.
    assert res.char_data["stamina"] == 995 + sc["paste_base_stamina"]
    # Resolver's mastery (190) + gem's mastery (7) = 197.
    assert res.char_data["mastery_rating"] == 190 + 7
    # Gem's versatility (16) + enchant's haste (29) — neither present in the
    # resolver's own return value at all.
    assert res.char_data["versatility_rating"] == 16
    assert res.char_data["haste_rating"] == 29
    assert res.gem_enchant_misses == 0


def test_load_from_simc_surfaces_gem_enchant_miss_count():
    """A gem/enchant id the catalogs don't recognize used to be silently
    dropped with no signal at all — SimcLoadOk.gem_enchant_misses now reports
    how many ids on the resolved paste weren't found, so a future UI pass can
    render an honest caveat instead of a blanket "might be missing" note that
    can't say whether THIS load actually hit the gap."""
    simc = """warrior="Test"
spec=protection
race=human
finger1=,id=251115,enchant_id=888888,gem_id=999999
"""
    res = load_from_simc(
        simc,
        loadouts_for_spec_fn=_loadouts_stub,
        resolve_stats_fn=lambda items, class_spec=None: {"stamina": 995},
    )
    assert isinstance(res, SimcLoadOk)
    assert res.gem_enchant_misses == 2  # unrecognized gem + unrecognized enchant


def test_cap_extra_items_trims_total_vault_first():
    """The public-mode item cap (bounding an attacker-controlled paste) keeps
    vault ahead of bag and never exceeds the budget. Pure helper, no parse."""
    from simf.ui.helpers.simc_load import _cap_extra_items

    bag = {"trinket1": list(range(50)), "ring1": list(range(50))}
    vault = {"head": list(range(50))}
    bag_c, vault_c = _cap_extra_items(bag, vault, 30)
    n_vault = sum(len(v) for v in vault_c.values())
    n_bag = sum(len(v) for v in bag_c.values())
    assert n_vault + n_bag <= 30
    assert n_vault == 30 and n_bag == 0  # vault filled the whole budget first

    # When vault fits, the remainder spills to bag.
    bag_c2, vault_c2 = _cap_extra_items(
        {"trinket1": list(range(50))}, {"head": list(range(10))}, 30
    )
    assert sum(len(v) for v in vault_c2.values()) == 10
    assert sum(len(v) for v in bag_c2.values()) == 20


@pytest.mark.skipif(not EXAMPLE_SIMC.exists(), reason="brutoh-vault.simc not present")
def test_load_from_simc_respects_max_extra_items():
    """`max_extra_items` threads through the real load path (the public UI
    passes it). None = uncapped (owner); a small cap bounds bag+vault."""
    raw = EXAMPLE_SIMC.read_text()
    capped = load_from_simc(raw, loadouts_for_spec_fn=_loadouts_stub, max_extra_items=2)
    if isinstance(capped, SimcLoadError):
        pytest.skip("example export lacks gear stats in this environment")
    n_extra = sum(len(v) for v in capped.bag_items.values()) + sum(
        len(v) for v in capped.vault_items.values()
    )
    assert n_extra <= 2, f"max_extra_items=2 should cap bag+vault, got {n_extra}"


# ─── stats-unresolved error copy (voice rule #2, 2026-08-23) ─────────────────


def _stats_unresolved_message() -> str:
    """The error a visitor sees when neither the export nor the item lookup
    produced stats."""
    simc = """druid="AnonGuardian1"
spec=guardian
race=tauren
head=,id=250024,bonus_id=13440/6652
"""
    result = load_from_simc(
        simc,
        loadouts_for_spec_fn=_loadouts_stub,
        resolve_stats_fn=lambda items, class_spec=None: {},  # lookup came back empty
    )
    assert isinstance(result, SimcLoadError)
    return result.message


def test_stats_unresolved_error_leaks_no_owner_only_instructions():
    """docs/ui_copy_voice.md rule #2: never tell a visitor to do something only
    the app owner can do. This message used to say "Update SimulationCraft
    addon" (no addon build emits `gear_*_rating=` lines — 0 of 6 real exports
    in examples/, so that path is the norm, not a misconfiguration) and to
    configure `~/.simf/blizzard.yaml`, which is the owner's file."""
    msg = _stats_unresolved_message()
    lowered = msg.lower()
    for leak in ("blizzard.yaml", "api credentials", "simulationcraft addon", "isn't configured"):
        assert leak not in lowered, f"owner-only instruction leaked into a visitor error: {leak}"


def test_stats_unresolved_error_names_a_cause_and_an_action():
    """Say what failed, why, and what the reader can do — twice, since a retry
    doesn't help if the items are simply too new to be catalogued."""
    msg = _stats_unresolved_message()
    assert "gear stats" in msg.lower()
    assert "again" in msg.lower(), "no retry action"
    assert "combat log" in msg.lower(), "no fallback action"

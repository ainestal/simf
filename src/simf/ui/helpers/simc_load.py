"""Pure SimC paste → character + gear loader for the v0.9 app.

Separated from the Streamlit layer so it has no `st.*` dependency: the new
app shell calls this, gets back a SimcLoad result, and decides what to put
in session state. Mirrors the behaviour of the old `_load_from_simc` in
app.py without the spinner / session-state writes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from simf.io.resolver_estimated_stats import (
    add_gem_and_enchant_stats as _add_gem_and_enchant_stats,  # noqa: F401
)
from simf.io.resolver_estimated_stats import (
    apply_guardian_caster_form_flag,
    apply_resolver_estimated_corrections,
)


@dataclass(frozen=True)
class SimcLoadOk:
    char_data: dict
    equipped: dict
    bag_items: dict
    vault_items: dict
    summary: str  # human-readable line, e.g. "Loaded Brutoh — 16 equipped, 8 bag, 9 vault."
    # Upgrade economy carried from the export's "Additional Character Info"
    # block — feeds the upgrade-normalized vault comparison. Defaulted so
    # older callers / tests that build SimcLoadOk positionally still work.
    account_ilvl_ceiling: int | None = None
    slot_high_watermarks: list = field(default_factory=list)
    upgrade_currencies: dict = field(default_factory=dict)
    catalyst_currencies: dict = field(default_factory=dict)
    # True when stats were ESTIMATED from the item resolver (Wowhead / Blizzard
    # item lookup) rather than read from the export's own `gear_*_rating=` lines.
    # A resolved paste is GEAR-ONLY; load_from_simc adds level-90 base character
    # stats on top (stat_conversion.paste_base_*) and every RECOGNIZED socketed
    # gem's + permanent enchant's stats (see `_add_gem_and_enchant_stats`, added
    # 2026-07-10 — a gem/enchant on Wowhead's own base-item tooltip fetch was
    # silently dropped entirely, not merely approximated), but an unrecognized
    # gem/enchant and the exact racial HP are still not modeled — so it's an
    # estimate, not exact. The log/WCL hydrate path (COMBATANT_INFO) carries the
    # exact worn totals and is unaffected. Callers surface this as a
    # low-confidence caveat. See docs/validation/simc_paste_stat_resolution_2026_06_27.md.
    stats_estimated: bool = False
    # Count of socketed-gem/permanent-enchant ids present on the resolved paste
    # that weren't found in data/gems.yaml / data/enchants.yaml (season not yet
    # catalogued, or a non-Midnight import) — always 0 on the exact-gear_stats
    # path. Named rather than silently dropped so a future UI pass can render
    # an honest "N gems/enchants not counted" caveat instead of the current
    # blanket "a gem/enchant this app doesn't recognize yet won't be counted"
    # wording, which can't say whether that actually happened on THIS load.
    gem_enchant_misses: int = 0


@dataclass(frozen=True)
class SimcLoadError:
    message: str


def _cap_extra_items(bag: dict, vault: dict, max_total: int) -> tuple[dict, dict]:
    """Trim bag+vault (each a dict of slot → [ItemSpec]) to at most ``max_total``
    items combined, vault kept ahead of bag (it drives the headline vault
    verdict). Pure — used to bound an attacker-controlled paste."""

    def _take(items_by_slot: dict, budget: int) -> tuple[dict, int]:
        out: dict = {}
        used = 0
        for slot, items in items_by_slot.items():
            if used >= budget:
                break
            take = items[: budget - used]
            if take:
                out[slot] = take
                used += len(take)
        return out, used

    vault_capped, used = _take(vault, max_total)
    bag_capped, _ = _take(bag, max(0, max_total - used))
    return bag_capped, vault_capped


def load_from_simc(
    simc_raw: str,
    loadouts_for_spec_fn: Callable[[str], list[str]],
    resolve_stats_fn: Callable[[dict, str], dict[str, int]] | None = None,
    max_extra_items: int | None = None,
) -> SimcLoadOk | SimcLoadError:
    """Parse a /simc string into a character dict + gear breakdown.

    Args:
        simc_raw: raw SimC export string.
        loadouts_for_spec_fn: callable returning known talent loadout names
            for a `class_spec` like "protection_warrior".
        resolve_stats_fn: optional callable taking the parsed items dict and
            the character's `class_spec` (so an agi/str hybrid-primary item
            resolves to the right stat) and returning summed stats (used when
            the export doesn't include `gear_haste_rating=` lines). If None,
            only gear_stats from the export are used; if those are missing,
            returns SimcLoadError.
        max_extra_items: optional cap on the combined bag+vault item count. The
            parser appends every "# slot=" comment with no limit and the vault
            recommender loops once per item, so an attacker-controlled paste
            (public mode) could drive a huge loop — the public UI passes a cap;
            the owner's own loads pass None. Real exports carry ~25 such items.

    Returns:
        SimcLoadOk on success, SimcLoadError on parse failure or missing stats.
    """
    # Imports kept local to avoid forcing yaml/etc. import for callers that
    # only use the helpers package for type-checking.
    from simf.io.simc_import import map_race, parse_simc_string

    try:
        parsed = parse_simc_string(simc_raw)
    except Exception as e:
        return SimcLoadError(f"Parse error: {e}")
    if not parsed.name:
        return SimcLoadError("Couldn't find a character name in that SimC string.")

    bag_items = parsed.bag_items
    vault_items = parsed.vault_items
    if max_extra_items is not None:
        bag_items, vault_items = _cap_extra_items(bag_items, vault_items, max_extra_items)

    class_spec = (
        f"{parsed.spec}_{parsed.class_name}"
        if parsed.spec and parsed.class_name
        else "protection_warrior"
    )
    loadouts = loadouts_for_spec_fn(class_spec)
    char_data: dict = {
        "name": parsed.name,
        "race": map_race(parsed.race),
        "class_spec": class_spec,
        "talents": loadouts[0] if loadouts else class_spec,
    }

    stats: dict[str, int] = dict(parsed.gear_stats)
    # Stats from the export's own `gear_*_rating=` lines are exact. Falling back
    # to the item resolver (Wowhead/Blizzard lookup) is only an ESTIMATE and is
    # unreliable for current-season gear (see SimcLoadOk.stats_estimated).
    stats_estimated = False
    gem_enchant_misses = 0
    if not stats and parsed.items and resolve_stats_fn is not None:
        # class_spec MUST be passed by keyword: item_db.resolve_equipped_stats's
        # own signature is (items, region="eu", class_spec=None) — a caller
        # passing that function directly as resolve_stats_fn (bypassing the
        # ui.state wrapper) would otherwise have class_spec silently land in
        # the `region` slot instead (confirmed by a failing integration test).
        stats = resolve_stats_fn(parsed.items, class_spec=class_spec)
        stats_estimated = bool(stats)

    if not stats:
        # Voice rule #2 (docs/ui_copy_voice.md): never leak owner-only
        # instructions into a visitor's error. The original wording told the
        # reader to configure `~/.simf/blizzard.yaml` — the app owner's file,
        # not theirs, and factually wrong whenever credentials ARE configured
        # but the lookup failed anyway (PR #485's finding) — and to "Update
        # SimulationCraft addon" for `gear_haste_rating=` lines. PR #485 fixed
        # the first and kept a softened form of the second ("re-export from
        # SimulationCraft with those stat lines"); that half is still a dead
        # end, because no addon build emits those lines at all — 0 of 6 real
        # exports in examples/, spanning addon 12.0.5 and 12.1.0 — so the
        # resolver path is the NORMAL path, not a degraded one. Name the real
        # cause and two actions the person in front of the error can take.
        return SimcLoadError(
            "Couldn't read your gear stats. A /simc export lists your items but "
            "not their stats, so simf looks each item up — and that lookup came "
            "back empty (your items may be too new to be catalogued, or the "
            "lookup was briefly busy). Try loading again in a minute. If it "
            'keeps failing, use "Why did I die?" with a combat log or a '
            "Warcraft Logs link instead — those carry your exact stats."
        )

    for f in (
        "strength",
        # agility is the PRIMARY stat for Guardian / Brewmaster / VDH (drives their
        # armor + dodge). resolve_equipped_stats returns it; it was missing from
        # this copy list, so a paste load of any agility tank silently got agility=0.
        "agility",
        "stamina",
        "armor_from_gear",
        "haste_rating",
        "crit_rating",
        "mastery_rating",
        "versatility_rating",
        "shield_armor",
        # gear_parry_rating IS parsed by simc_import.py (_GEAR_STAT_KEYS) but was
        # missing from this copy list, so a paste-loaded avoidance tank
        # (Warrior/ProtPal/Blood DK/VDH) silently ran with parry_rating=0 even
        # though base_parry() in character.py genuinely consumes it.
        "parry_rating",
    ):
        char_data[f] = int(stats.get(f, 0))

    # Gem/enchant credit + level-90 base character stats (a resolved paste is
    # GEAR-ONLY). Without this a resolver-loaded character has ~25% too little
    # HP and ~half its primary stat → wrong eHP/marginals/verdicts. Only on the
    # estimated (resolver) path; exact export gear_stats and the log/WCL
    # hydrate path are left untouched. Shared with the Raider.IO online-lookup
    # path (io/resolver_estimated_stats.py) — both are gear-only resolver
    # estimates and need the identical correction, not a second copy of it.
    if stats_estimated:
        _, gem_enchant_misses = apply_resolver_estimated_corrections(
            char_data, parsed.items, class_spec
        )

    # A SimC export's gear stamina is the form-independent (caster) value, so a
    # Guardian needs Bear Form's +40% HP applied in max_hp(). The log/WCL hydrate
    # paths store in-form stamina and leave this flag False (no double-count).
    # Unconditional on stats_estimated — an exact export's gear_stamina= is
    # ALSO the caster-form value.
    apply_guardian_caster_form_flag(char_data, class_spec)

    n_eq = sum(1 for v in parsed.items.values() if v and v.item_id)
    n_bag = sum(len(v) for v in bag_items.values())
    n_vault = sum(len(v) for v in vault_items.values())
    parts = [f"{n_eq} equipped"]
    if n_bag:
        parts.append(f"{n_bag} bag")
    if n_vault:
        parts.append(f"{n_vault} vault")
    summary = f"Loaded {parsed.name} — {', '.join(parts)}."
    if stats_estimated:
        summary += (
            "  ⚠ Stats estimated from item lookup (base character stats"
            " approximated; a gem/enchant this app doesn't recognize yet won't"
            " be counted). Load a combat log or a Warcraft Logs link for exact"
            " values."
        )

    return SimcLoadOk(
        char_data=char_data,
        equipped=parsed.items,
        bag_items=bag_items,
        vault_items=vault_items,
        summary=summary,
        stats_estimated=stats_estimated,
        gem_enchant_misses=gem_enchant_misses,
        account_ilvl_ceiling=parsed.account_ilvl_ceiling,
        slot_high_watermarks=parsed.slot_high_watermarks,
        upgrade_currencies=parsed.upgrade_currencies,
        catalyst_currencies=parsed.catalyst_currencies,
    )

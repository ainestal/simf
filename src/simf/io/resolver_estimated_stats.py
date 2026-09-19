"""Corrections shared by every GEAR-ONLY, resolver-derived character load.

Two paths build a character's stats by summing per-item Wowhead/Blizzard
lookups rather than reading them straight off a log/WCL COMBATANT_INFO
snapshot: ``ui/helpers/simc_load.py``'s resolver-fallback branch (a `/simc`
paste with no `gear_*_rating=` lines) and ``io/raider_io.py`` (the
Raider.IO online name-lookup, which has no "exact export" alternative at
all — it is ALWAYS a per-item resolve).

Both are missing the same three things a bare item-stat sum omits, and both
used to apply the fix as inline copy-pasted logic (found 2026-07-10/11
auditing the Raider.IO path against the paste-resolver's already-fixed
one — see docs/validation for the paste-side fix history). Centralising
here means a future third resolver-derived source only needs to call these,
not reimplement them.
"""

from __future__ import annotations

# Tank specs whose PRIMARY stat is agility (everyone else is strength). Used
# to route the level-90 base-primary stat onto the right field. Mirrors
# optimizer.gem_suggester._AGI_SPECS / primary_stat_key (kept local to avoid
# forcing an optimizer import just for this constant).
_AGI_PRIMARY_SPECS = frozenset({"brewmaster_monk", "guardian_druid", "vengeance_demon_hunter"})

__all__ = [
    "add_gem_and_enchant_stats",
    "apply_guardian_caster_form_flag",
    "apply_resolver_estimated_corrections",
]


def add_gem_and_enchant_stats(
    stats: dict[str, int], items: dict, class_spec: str
) -> tuple[dict[str, int], int]:
    """Add every equipped item's socketed-gem AND permanent-enchant stats on
    top of a resolver's per-item stat fetch.

    Root cause (found 2026-07-10 chasing a user-reported implausible ΔeHP
    composition line): ``item_db.fetch_item_stats_wowhead`` parses an item's
    own base tooltip only. A gem's Wowhead tooltip uses a DIFFERENT
    comment-marker format (``<!--gem1-->16 <!----> Haste``) than equipment's
    (``<!--stat72-->`` / ``<!--rtg36-->``) — confirmed via a direct XML
    fetch — which the resolver's regex was never taught to parse, so a
    socketed gem's contribution was dropped entirely rather than merely
    mis-parsed.

    Fix: reuse ``data/gems.yaml`` / ``data/enchants.yaml`` — hand-verified,
    offline catalogs ``optimizer/gem_suggester.py`` /
    ``optimizer/enchant_suggester.py`` already use for recommendations —
    instead of teaching the Wowhead-tooltip regex a second, gem-specific
    comment-marker format for a small, closed, already-catalogued item set.
    An unrecognized gem/enchant id (this season's set not yet catalogued, or
    a non-Midnight import) is silently skipped, matching every other
    fail-open convention on this resolver path — this narrows the remaining
    estimate gap, it doesn't close it. Unlike before, an unrecognized id is
    no longer counted as if it silently matched everything: the second
    return value is a count of gem/enchant ids present on the export that
    weren't found in either catalog, so a caller can surface the residual
    gap honestly instead of only being able to say "some gem/enchant stats
    might be missing" with no idea how many.

    Returns:
        (stats-with-gem-and-enchant-contributions-added, miss_count). The
        dict is the SAME object (not a copy) when nothing is recognized —
        callers rely on this to skip a no-op merge cheaply.
    """
    from simf.optimizer.enchant_suggester import find_enchant_by_id
    from simf.optimizer.gem_suggester import find_gem_by_id, primary_stat_key, resolve_gem_stats

    primary = primary_stat_key(class_spec)
    extra: dict[str, int] = {}
    misses = 0

    def _accumulate(raw_stats: dict | None) -> None:
        for k, v in resolve_gem_stats(raw_stats or {}, primary).items():
            extra[k] = extra.get(k, 0) + v

    for item_spec in items.values():
        if item_spec is None:
            continue
        for gem_id in getattr(item_spec, "gem_ids", None) or ():
            gem = find_gem_by_id(gem_id)
            if gem:
                _accumulate(gem.get("stats"))
            elif gem_id:
                misses += 1
        enchant_id = getattr(item_spec, "enchant_id", None)
        enchant = find_enchant_by_id(enchant_id)
        if enchant:
            _accumulate(enchant.get("stats"))
        elif enchant_id:
            misses += 1

    if not extra:
        return stats, misses
    merged = dict(stats)
    for k, v in extra.items():
        merged[k] = merged.get(k, 0) + v
    return merged, misses


def apply_resolver_estimated_corrections(
    char_data: dict, items: dict, class_spec: str
) -> tuple[dict, int]:
    """Fold gem/enchant credit + level-90 base character stats onto a
    gear-only resolver estimate, in place.

    A per-item stat resolve (Wowhead tooltip fetch, or Raider.IO's item list
    run through the same resolver) is GEAR-ONLY: it never includes a
    socketed gem's/permanent enchant's own stats (see
    ``add_gem_and_enchant_stats``), nor the level-90 base stamina/primary
    every real character carries before gear is even considered (~25% too
    little HP, ~half the primary stat, if skipped — validated against a
    real in-game tooltip, see ``docs/validation/simc_paste_stat_resolution``).

    Mutates ``char_data`` in place. Returns ``(char_data, gem_enchant_misses)``
    — the miss count is ``add_gem_and_enchant_stats``'s own honest count of
    gem/enchant ids present on the export but not in ``data/gems.yaml`` /
    ``data/enchants.yaml``; callers that don't care may discard it.
    """
    extra, misses = add_gem_and_enchant_stats({}, items, class_spec)
    for k, v in extra.items():
        char_data[k] = int(char_data.get(k, 0)) + v

    from simf.core.constants import load_constants

    _sc = load_constants()["stat_conversion"]
    char_data["stamina"] = int(char_data.get("stamina", 0)) + int(_sc.get("paste_base_stamina", 0))
    primary = "agility" if class_spec in _AGI_PRIMARY_SPECS else "strength"
    char_data[primary] = int(char_data.get(primary, 0)) + int(_sc.get("paste_base_primary", 0))

    return char_data, misses


def apply_guardian_caster_form_flag(char_data: dict, class_spec: str) -> dict:
    """Set ``stamina_in_caster_form`` for Guardian Druid, in place.

    A gear-only stat sum (whether an export's own exact `gear_*_rating=`
    lines or a resolver estimate) is the form-independent (caster) stamina
    value, so a Guardian needs Bear Form's +40% HP applied explicitly in
    ``Character.max_hp()``. Unconditional on ``stats_estimated`` /
    resolver-vs-exact — BOTH are gear-only aggregates, unlike the log/WCL
    hydrate paths, which capture the already-in-form value and must leave
    this flag False (double-count otherwise).
    """
    if class_spec == "guardian_druid":
        char_data["stamina_in_caster_form"] = True
    return char_data

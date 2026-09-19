"""Hydrate a `Character` (+ equipped gear) straight from a combat log's
`COMBATANT_INFO` row, skipping the SimC paste step.

When Advanced Combat Logging is enabled, every M+ run emits one
`COMBATANT_INFO` line per party member at run start carrying spec, talents,
ratings, and the full 18-slot equipped gear list. This module turns that
into the same `char_data` dict + `equipped` dict shape the existing
SimC-paste pipeline produces (`ui/helpers/simc_load.SimcLoadOk`), so the
Gear and CD-plan surfaces light up without the user pasting anything.

ACL-off logs emit zero COMBATANT_INFO rows. In that case `hydrate_character`
returns `None` and the caller falls back to the SimC paste flow unchanged.

Brutoh's idea #b (2026-05-25): the SimC paste step is the single biggest
piece of friction in the log-load flow. Every M+ player with ACL on
already has the data in the log they uploaded — pasting it again from
SimC is busywork. This module closes that gap.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from simf.core.constants import load_constants
from simf.io.combat_log import (
    CombatantInfoFull,
    detect_active_buffs,
    detect_race,
    iter_combatant_info_full,
)
from simf.io.simc_import import ItemSpec
from simf.io.spec_ids import SPEC_ID_TO_CLASS_SPEC, class_spec_display_name
from simf.io.talent_decoder import decode_combatant_info_entry_ids


@dataclass(frozen=True)
class HydrateResult:
    """What the UI needs to populate session state on a hydrate hit.

    `char_data` shape matches the dict that `simc_load.SimcLoadOk.char_data`
    produces — directly assignable to `_ss()["char_data"]`. `equipped` is the
    slot → ItemSpec map the Gear surface reads from `_ss()["simc_equipped"]`.
    `summary` is a short human-readable line for the load-summary toast.
    `source` is always "combatant_info" — kept as a field so future
    hydration sources (e.g. WCL API) plug in without changing callers.
    """

    char_data: dict
    equipped: dict
    summary: str
    source: str = "combatant_info"


# Race racial armor multipliers — back-out so we store the pre-racial
# `armor_from_gear` value on the Character. Keys match the simf-internal
# race string. Earthen's 10% Titan-Wrought Frame is the only one in
# Midnight 12.0.5 that affects total armor; other races leave it alone.
# Mirrors `constants.yaml::racials.earthen_titan_wrought_frame` (1.10).
# Kept here as a local fallback so the hydrator can resolve without
# triggering a constants reload during streaming; the value is checked
# against constants.yaml in tests.
_RACE_ARMOR_MULT: dict[int, float] = {
    # Earthen Dwarf race_id values from COMBATANT_INFO are not exposed
    # in the prefix fields — we infer race from spec elsewhere. This map
    # is keyed on simf-internal race string instead; see _backout_armor.
}


def _backout_armor(total_armor: int, race: str) -> int:
    """Strip racial armor multipliers from the log's post-racial total armor.

    `COMBATANT_INFO[24]` is the player's *total* armor as the WoW client
    sees it, *including* race passives like Earthen's 10% Titan-Wrought
    Frame. `Character.total_armor()` re-applies the racial multiplier on
    top of `armor_from_gear`, so we have to subtract it before storing.

    Mirrors `Character.total_armor()` in `src/simf/core/character.py:147`.
    Talent-derived armor multipliers (Reinforced Plates, Armor Spec) are
    NOT subtracted here — they're applied separately by `Character.total_armor()`
    via `detected_talent_spell_ids`, so leaving them in the input would
    double-multiply. But: the log's [24] field reports total armor after
    *all* passives including talents. We accept a small (≤6%) over-estimate
    here rather than try to back-out talent-conditional multipliers that
    depend on the talent loadout we just learned — the gear-surface stat-
    weight chain is robust to ±5% gear armor, and the SimC paste path has
    the same issue (`gear_armor=` from /simc is also post-multiplier).
    """
    if race == "earthen":
        return round(total_armor / 1.10)
    return total_armor


def _spec_to_talents_loadout(class_spec: str) -> str:
    """Pick a default talent loadout name for a spec.

    Mirrors the fallback table in `simc_import.simc_to_character_yaml` so
    the hydrated character defaults to the same loadout the SimC paste
    path would pick. The actual *talent IDs* from the log feed
    `detected_talent_spell_ids` separately — this string is for the
    YAML loadout lookup used by `_talent_set()` in
    `core/character.py:361`.
    """
    defaults = {
        "protection_warrior": "brutoh-actual",
        "protection_paladin": "default-paladin",
        "brewmaster_monk": "anonbrewmaster1-brewmaster",
        "guardian_druid": "anonguardian2-guardian",
        "blood_death_knight": "default-dk",
        "vengeance_demon_hunter": "default-dh",
    }
    return defaults.get(class_spec, "")


def _equipped_from_combatant_info(ci: CombatantInfoFull) -> dict[str, ItemSpec]:
    """Map CombatantInfoFull.equipped → {slot: ItemSpec} shape the Gear
    surface expects. Skips empty slots (item_id=0) so the gear surface's
    `n_eq` count matches the SimC-paste behaviour.
    """
    out: dict[str, ItemSpec] = {}
    for item in ci.equipped:
        if not item.item_id:
            continue
        out[item.slot] = ItemSpec(
            slot=item.slot,
            item_id=item.item_id,
            enchant_id=item.enchant_id,
            gem_ids=list(item.gem_ids),
            bonus_ids=list(item.bonus_ids),
            ilvl=item.ilvl,
        )
    return out


def _resolve_target_guid(
    log_path: Path,
    target_name: str,
    *,
    start_byte_offset: int = 0,
    max_bytes: int = 30_000_000,
) -> str | None:
    """Find the GUID for `target_name` by scanning events for the
    `Player-XXXX-YYYY,"Name-Realm-Region"` pattern that every combat-log
    event emits next to either the source or destination player.

    Combat logs use `Player-XXXX-YYYYYYYY` GUIDs in COMBATANT_INFO but
    `Name-Realm-Region` strings as destName / sourceName in events. We need
    a GUID to match the COMBATANT_INFO row to the picked player.

    Bounded to 30 MB to mirror `detect_party_roles` default; a 30-minute run
    window fits in well under that. Returns None if the target never
    appears (corrupt log, wrong name).
    """
    import re as _re

    # Match WoW's real GUID format `Player-<server>-<hex>` but stay loose
    # enough that tests can use ad-hoc identifiers like `Player-1-T`.
    pat = _re.compile(r"(Player-\d+-[0-9A-Za-z]+),\"" + _re.escape(target_name) + r"\"")
    with log_path.open() as f:
        if start_byte_offset > 0:
            f.seek(start_byte_offset)
        bytes_read = 0
        for line in f:
            bytes_read += len(line)
            if bytes_read > max_bytes:
                break
            m = pat.search(line)
            if m:
                return m.group(1)
    return None


def _select_combatant_info(snaps: list[CombatantInfoFull]) -> CombatantInfoFull:
    """Pick which COMBATANT_INFO snapshot to hydrate from, given all of a
    player's snapshots in the run window.

    COMBATANT_INFO reports LIVE state at the emission instant — any temporary
    self-buff active at that moment is baked into the numbers, not just a
    shapeshifter's form. Two known contaminants, both handled by picking an
    extremal snapshot rather than blindly trusting the last one:

    A druid emits a fresh COMBATANT_INFO at every pull carrying its LIVE armor,
    which in Bear Form is the in-form value (Bear Form 5487 ×3.2 + live Ironfur)
    — unstable run-to-run and *already multiplied*, so ``Character.total_armor``'s
    ×3.2 double-counts it (the −22% over-mitigation in
    docs/validation/phase4_guardian_local_logs_2026_06_24.md). For a Guardian we
    therefore source ``total_armor`` from the OUT-OF-FORM (caster) snapshot —
    identified as the minimum-stamina one, since Bear Form's +40% stamina cleanly
    separates caster (low) from in-form (high) — and keep everything else from the
    last snapshot. The engine's ×3.2 then applies to the true caster armor
    (~919 ×3.2 ≈ 2,958 observed Bear-Form base). Guarded so a window with no
    genuine out-of-form snapshot (caster armor not actually lower) is left
    unchanged rather than fabricating a worse value.

    A non-shapeshifter can carry the SAME class of contaminant via a rotational
    *armor* self-buff — Prot Paladin's Shield of the Righteous is a temporary
    +armor window in Midnight (spell 132403/53600, +192% of Strength for 4.5s;
    SimC midnight invalidates the armor cache on Strength for it,
    ``sc_paladin.cpp:4847-4850``), chained into nearly every boss pull, so even
    the LAST snapshot in the window is often buffed. The engine already models
    SotR's own DR separately (100% block window + a flat phys-DR term in
    ``classes/protection_paladin.py``), so hydrating its buffed armor on top
    would double-count it. Confirmed empirically 2026-07-03 against a real
    Paladin log (``examples/bruttah-prot/``): SotR-active correlated 12/12 with
    every inflated snapshot, and the unbuffed floor replicated identically at
    every CHALLENGE_MODE_START. Minimum total_armor across the window is the
    best available proxy for that unbuffed floor — the same trick as the
    Guardian branch above, applied to a different contaminant and without a
    stat that cleanly partitions clean-vs-buffed the way stamina does for Bear
    Form, so we fall back to the raw extremum instead. Likely applies beyond
    Paladin too — VDH's Demon Spikes (armor buff) and Metamorphosis (armor
    multiplier), Blood DK's Bone Shield — flagged in ROADMAP.md rather than
    guessed at here since each needs its own log to confirm the mechanism.

    A window with only one snapshot, or where the last snapshot already IS the
    minimum (no buff was active at that instant — the common case), returns the
    last snapshot unchanged (identity, not a copy) so already-calibrated specs
    without this contamination pattern stay bit-identical.

    NOTE on stamina: we deliberately KEEP the last snapshot's (in-form) stamina
    for Guardian, and do NOT apply Bear Form's +40% HP in ``max_hp()`` on this
    path. The COMBATANT_INFO stamina is already the in-form value, so
    ``stamina × hp_per_stam`` is the correct bear HP without a multiplier. Only
    the SimC-paste path (whose gear stamina is the form-independent CASTER
    value) sets ``Character.stamina_in_caster_form = True`` to opt into the
    ×1.40 — see ``Character.max_hp``. So the bear-form HP fix never
    double-counts on the log (or WCL) path.
    """
    last = snaps[-1]
    if len(snaps) < 2:
        return last
    if SPEC_ID_TO_CLASS_SPEC.get(last.spec_id) == "guardian_druid":
        caster = min(snaps, key=lambda c: c.stamina)
        if caster.total_armor < last.total_armor:
            return replace(last, total_armor=caster.total_armor)
        return last
    cleanest = min(snaps, key=lambda c: c.total_armor)
    if cleanest.total_armor < last.total_armor:
        return replace(last, total_armor=cleanest.total_armor)
    return last


def _spec_gate_buff_ids(class_spec: str | None) -> frozenset[int]:
    """Replay-detectable BUFF spell ids that gate a spec's modeled effects — the
    hero-talent mitigation ledger (e.g. Brewmaster Predictive Training) and the
    Ironfur haste model (Guardian Elune's Chosen, Fury of Elune 202770). Surfacing
    these into ``active_buff_spell_ids`` on the hydrate path lets a log-loaded
    Character credit the SAME effects the replay/calibrate path does — without it
    the live (gear-panel) Guardian never sees haste's value and the Brewmaster PT
    ledger never fires. Empty for any spec without such config → bit-identical.
    """
    if not class_spec:
        return frozenset()
    spec_cfg = load_constants().get("specs", {}).get(class_spec, {})
    ids: set[int] = set()
    for layer in spec_cfg.get("mitigation_ledger", []):
        bid = layer.get("detect_buff_spell_id")
        if bid:
            ids.add(int(bid))
    hm = spec_cfg.get("ironfur_haste_model")
    if hm and hm.get("detect_buff_spell_id"):
        ids.add(int(hm["detect_buff_spell_id"]))
    return frozenset(ids)


def _detect_spec_gate_buffs(
    log_path: Path,
    spec_id: int,
    target_name: str,
    *,
    start_time_s: float | None,
    end_time_s: float | None,
    start_byte_offset: int,
) -> frozenset[int]:
    """Detect which of a spec's gate buffs were active on the tank in the window."""
    wanted = _spec_gate_buff_ids(SPEC_ID_TO_CLASS_SPEC.get(spec_id))
    if not wanted:
        return frozenset()
    try:
        return detect_active_buffs(
            log_path,
            target_name,
            wanted,
            start_time_s=start_time_s,
            end_time_s=end_time_s,
            start_byte_offset=start_byte_offset,
        )
    except Exception:
        return frozenset()


def hydrate_character(
    log_path: Path,
    target_name: str,
    *,
    start_time_s: float | None = None,
    end_time_s: float | None = None,
    start_byte_offset: int = 0,
    region_hint: str = "",
    resolve_stats_fn: Callable[[dict], dict[str, int]] | None = None,
) -> HydrateResult | None:
    """Hydrate a Character + equipped gear from a log's COMBATANT_INFO.

    Returns None when:
      - The log has no COMBATANT_INFO rows (ACL off).
      - We can't resolve `target_name` to a GUID that appears in COMBATANT_INFO.
      - The matching COMBATANT_INFO row has no gear (unequipped — corrupt).

    The caller (UI / CLI) treats None as "fall back to SimC paste".

    `target_name` is the `Name-Realm-Region` string the user picked in the
    party member selector (e.g. `Brutoh-Uldum-EU`). `resolve_stats_fn` is
    an optional last-resort hook that takes the equipped dict and returns
    per-stat values — used only when the COMBATANT_INFO row is missing
    ratings (very rare with ACL on; we ship every COMBATANT_INFO rating
    field by default).
    """
    # Build the GUID → [CombatantInfoFull] map for this run window. A druid
    # emits a FRESH COMBATANT_INFO at every pull carrying its live (often
    # already-in-form) armor, so we keep ALL snapshots per guid and select
    # below — not just the last (see _select_combatant_info).
    infos_by_guid: dict[str, list[CombatantInfoFull]] = {}
    for ci in iter_combatant_info_full(
        log_path,
        start_time_s=start_time_s,
        end_time_s=end_time_s,
        start_byte_offset=start_byte_offset,
    ):
        infos_by_guid.setdefault(ci.guid, []).append(ci)

    if not infos_by_guid:
        # ACL off — no COMBATANT_INFO rows at all. The UI will fall back
        # to its SimC paste prompt.
        return None

    # Resolve target_name → guid via a single bounded file scan that
    # picks up the pattern next to source or destination events.
    guid = _resolve_target_guid(
        log_path,
        target_name,
        start_byte_offset=start_byte_offset,
    )
    if guid is None or guid not in infos_by_guid:
        return None

    ci = _select_combatant_info(infos_by_guid[guid])
    active_buffs = _detect_spec_gate_buffs(
        log_path,
        ci.spec_id,
        target_name,
        start_time_s=start_time_s,
        end_time_s=end_time_s,
        start_byte_offset=start_byte_offset,
    )
    # Infer race from a racial-ability cast in the window (recovers e.g. the
    # Tauren +5% Endurance HP the human default drops). Best-effort → "human"
    # when no racial is seen. Local-log only; the WCL builder has no log to scan.
    try:
        race = detect_race(
            log_path,
            target_name,
            start_time_s=start_time_s,
            end_time_s=end_time_s,
            start_byte_offset=start_byte_offset,
        )
    except Exception:
        race = None
    return build_hydrate_result(
        ci,
        target_name,
        region_hint=region_hint,
        resolve_stats_fn=resolve_stats_fn,
        active_buff_spell_ids=active_buffs,
        race=race or "human",
    )


def build_hydrate_result(
    ci: CombatantInfoFull,
    target_name: str,
    *,
    region_hint: str = "",
    resolve_stats_fn: Callable[[dict], dict[str, int]] | None = None,
    source: str = "combatant_info",
    summary_origin: str = "log",
    summary_suffix: str = " No SimC paste needed.",
    active_buff_spell_ids: frozenset[int] | None = None,
    race: str = "human",
) -> HydrateResult | None:
    """Build a ``HydrateResult`` from a parsed ``CombatantInfoFull``.

    Shared by the local-log front-end (``hydrate_character``) and the
    Warcraft Logs front-end (``io.wcl_combatant_info.character_from_wcl``).
    The WCL ``events(dataType: CombatantInfo)`` payload is the *same* data as
    a local COMBATANT_INFO line, so both converge on this one builder rather
    than drifting into two implementations of "COMBATANT_INFO → Character"
    (a cross-source calibration inconsistency would be the worst failure mode).

    Returns ``None`` for an unmapped spec_id. Kept behaviourally identical to
    the pre-extraction ``hydrate_character`` for the local defaults — the only
    knobs are the human-readable ``summary_origin`` / ``summary_suffix`` and the
    ``source`` tag. NOTE: deliberately does NOT set ``detected_talent_spell_ids``
    — the ids both sources carry are trait-node-ENTRY ids, not the Blizzard
    spell ids ``Character.total_armor()`` matches against, so populating it is
    pointless (the match can never succeed). Historically it was also harmful —
    a zero-match detection attempt silently zeroed warrior armor multipliers —
    but total_armor() now falls back to the YAML loadout when the detected-id
    match applies nothing, so leaving this unset is a no-op, not a dodge.

    DOES set ``decoded_talents`` (2026-07-13) — the SAME trait-node-entry ids
    that made ``detected_talent_spell_ids`` a no-op are exactly what
    ``decode_combatant_info_entry_ids`` needs (it joins on entry id, not spell
    id — see ``talent_tree_data.py``). This is currently Protection-Warrior-
    only (``entry_id_to_modeled_talent`` only has Warrior data); other specs'
    ``ci.talent_spell_ids`` simply match nothing and ``decoded_talents`` stays
    unset, falling back to the YAML loadout guess exactly as before.
    """
    class_spec = SPEC_ID_TO_CLASS_SPEC.get(ci.spec_id)
    if class_spec is None:
        # Unknown spec — likely a brand-new spec_id we haven't mapped.
        # Bail rather than guess; the SimC fallback gets the right spec
        # from the user's paste.
        return None

    # `race` is NOT in COMBATANT_INFO (a packed bit that varies by client build)
    # and the WCL event omits it, so it defaults to "human" (mult 1.0) for the
    # WCL path and any caller that can't supply it. The local-log front-end
    # (`hydrate_character`) infers it from a racial-ability cast and passes it in
    # — recovering e.g. a Tauren's +5% Endurance HP that the human default drops.
    # Earthen is intentionally never inferred: `armor_from_gear` below is the
    # snapshot's POST-racial total, and `_backout_armor` strips the racial mult
    # for whatever race we set, so a non-armor race (tauren/dwarf/…) stays correct
    # for armor while gaining its HP/vers/DR racial; earthen's armor is already
    # right under the human default.
    # (race comes in via the `race` parameter)

    # Parse Name-Realm-Region from target_name if present, so the Why-died
    # surface's pre-fill matches the log's destName format.
    name = target_name
    server = ""
    region = ""
    if "-" in target_name:
        parts = target_name.split("-")
        if len(parts) >= 3:
            name = parts[0]
            server = parts[1]
            region = parts[-1]

    equipped = _equipped_from_combatant_info(ci)

    # Pull stats straight from the log/event — every rating is in COMBATANT_INFO
    # when ACL is on. The `resolve_stats_fn` fallback fires only if a specific
    # rating field is zero AND the gear is non-trivial (a malformed-row net).
    char_data: dict = {
        "name": name,
        "race": race,
        "class_spec": class_spec,
        "talents": _spec_to_talents_loadout(class_spec),
        "strength": ci.strength,
        "agility": ci.agility,
        "stamina": ci.stamina,
        "armor_from_gear": _backout_armor(ci.total_armor, race),
        "haste_rating": ci.haste_rating,
        "crit_rating": ci.crit_rating,
        "mastery_rating": ci.mastery_rating,
        "versatility_rating": ci.versatility_rating,
        "parry_rating": ci.parry_rating,
    }
    if server:
        char_data["server"] = server
    if region:
        char_data["region"] = region.upper()
    elif region_hint:
        char_data["region"] = region_hint.upper()
    # Hero-talent / ledger BUFF gates detected from the log (Guardian Elune's
    # Chosen Fury of Elune → Ironfur haste model; Brewmaster Predictive Training
    # → mitigation ledger). Lets the log-loaded gear panel credit haste/DR the
    # same way the replay path does. None/empty → field stays unset (bit-identical).
    if active_buff_spell_ids:
        char_data["active_buff_spell_ids"] = active_buff_spell_ids

    # Real decoded build from the log's own COMBATANT_INFO talent entry ids —
    # see the docstring above. Empty result (no entry ids matched, e.g. a
    # non-Warrior spec today) leaves the field unset, same as before this
    # existed.
    decoded = decode_combatant_info_entry_ids(ci.talent_spell_ids)
    if decoded.ok and decoded.modeled:
        char_data["decoded_talents"] = decoded.modeled

    # shield_armor — the off-hand shield's armor *in isolation* — drives block
    # value (`character.py`: block_value = shield_armor × 2.5) but is NOT in
    # COMBATANT_INFO's flat fields (field [24] is whole-character armor). Without
    # it a shield tank (Prot Warrior / Paladin) runs with block value ≈ 0 →
    # ~24pp over-pessimistic verdict (see PR #136). Resolve it from the equipped
    # off-hand via the same item lookup the SimC-paste path uses. No-op when no
    # resolver / no off-hand / a non-shield off-hand (fist weapon → no armor).
    if resolve_stats_fn is not None and "off_hand" in equipped:
        try:
            off_hand_stats = resolve_stats_fn({"off_hand": equipped["off_hand"]})
        except Exception:
            off_hand_stats = {}
        if off_hand_stats.get("shield_armor"):
            char_data["shield_armor"] = int(off_hand_stats["shield_armor"])

    # Optional Wowhead-stat fallback: only kicks in if every rating is
    # zero (which would be a malformed COMBATANT_INFO row). Real ACL-on
    # logs always populate ratings.
    all_ratings_zero = (
        ci.haste_rating == 0
        and ci.crit_rating == 0
        and ci.mastery_rating == 0
        and ci.versatility_rating == 0
    )
    if all_ratings_zero and resolve_stats_fn is not None and equipped:
        stats = resolve_stats_fn(equipped)
        for key in (
            "strength",
            "stamina",
            "armor_from_gear",
            "haste_rating",
            "crit_rating",
            "mastery_rating",
            "versatility_rating",
            "shield_armor",
        ):
            if stats.get(key):
                char_data[key] = int(stats[key])

    n_eq = len(equipped)
    avg_ilvl = round(sum(it.ilvl for it in ci.equipped if it.item_id) / max(1, n_eq)) if n_eq else 0
    spec_label = class_spec_display_name(class_spec)
    summary = (
        f"Loaded {name} from {summary_origin} — {spec_label}, "
        f"ilvl {avg_ilvl}, {n_eq} equipped slots.{summary_suffix}"
    )

    return HydrateResult(
        char_data=char_data,
        equipped=equipped,
        summary=summary,
        source=source,
    )


def has_acl_combatant_info(
    log_path: Path,
    *,
    start_time_s: float | None = None,
    end_time_s: float | None = None,
    start_byte_offset: int = 0,
) -> bool:
    """Cheap probe: does this log carry COMBATANT_INFO rows at all?

    Used by the UI to decide whether to *try* hydration before spending
    cycles on it. Returns True after the first matching line — bails
    early so cost is bounded by the byte offset of the first
    COMBATANT_INFO emission (within the first MB of any ACL-on run).
    """
    for _ci in iter_combatant_info_full(
        log_path,
        start_time_s=start_time_s,
        end_time_s=end_time_s,
        start_byte_offset=start_byte_offset,
    ):
        return True
    return False

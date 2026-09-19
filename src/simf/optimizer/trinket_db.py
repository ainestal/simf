"""
trinket_db — Trinket registry helpers for the simf survivability optimizer.

Provides:
  load_trinkets()           — load all trinkets from data/trinkets.yaml (cached)
  trinkets_for_spec()       — filter by class spec (universal + spec-specific)
  effective_stat_delta()    — convert any trinket to a flat stat dict for eHP comparison
  trinket_ehp_contribution()— physical & magic eHP delta vs no-trinket baseline

All effect-to-stat conversions use the hp_per_stamina constant from constants.yaml
so the eHP math stays consistent with the rest of the sim.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from ..core.constants import load_constants
from .gem_suggester import primary_stat_key

if TYPE_CHECKING:
    from ..core.character import Character

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_TRINKETS_FILE = _DATA_DIR / "trinkets.yaml"

# Type aliases
TrinketDict = dict
StatDict = dict[str, float]

# ---------------------------------------------------------------------------
# Trinket types understood by this module
# ---------------------------------------------------------------------------
_PASSIVE_TYPES = {"passive_stat"}
_ON_USE_ABSORB_TYPES = {"on_use_absorb"}
_ON_USE_DR_TYPES = {"on_use_dr"}
_ON_USE_STAT_TYPES = {"on_use_stat"}
_PROC_ABSORB_TYPES = {"proc_absorb"}
_PROC_STAT_TYPES = {"proc_stat"}
_PROC_HEAL_TYPES = {"proc_heal"}
_DAMAGE_TYPES = {"on_use_damage", "on_use_heal"}

# Stat keys that are primary stats (agility_or_strength, strength, agility, intellect)
_PRIMARY_STAT_KEYS = {"agility_or_strength", "strength", "agility", "intellect"}


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_trinkets() -> list[TrinketDict]:
    """Load all trinkets from data/trinkets.yaml. Result is cached after first load."""
    with _TRINKETS_FILE.open() as f:
        data = yaml.safe_load(f)
    return data["trinkets"]


@lru_cache(maxsize=1)
def _trinkets_by_id() -> dict[int, TrinketDict]:
    return {int(t["id"]): t for t in load_trinkets() if t.get("id")}


def find_trinket_by_id(item_id: int) -> TrinketDict | None:
    """O(1) lookup of a trinket by item-id. Returns ``None`` when the id
    isn't in the curated registry (`data/trinkets.yaml`) — caller falls
    back to stats-only and surfaces the ⚠️ warning."""
    if not item_id:
        return None
    return _trinkets_by_id().get(int(item_id))


def trinkets_for_spec(class_spec: str) -> list[TrinketDict]:
    """
    Return trinkets relevant to *class_spec*.

    A trinket is included when:
      - its ``tanks`` field is the string ``"all"`` (universal), OR
      - *class_spec* appears in its ``tanks`` list, OR
      - the ``tanks`` field is absent (treated as universal).
    """
    result = []
    for t in load_trinkets():
        tanks = t.get("tanks", "all")
        if tanks == "all" or not tanks or (isinstance(tanks, list) and class_spec in tanks):
            result.append(t)
    return result


# ---------------------------------------------------------------------------
# Stat normalisation helpers
# ---------------------------------------------------------------------------


def _hp_per_stamina() -> float:
    c = load_constants()
    return float(c["stat_conversion"]["hp_per_stamina"])


def _absorb_to_stamina(absorb_value: float, uptime: float) -> float:
    """
    Convert an absorb shield (flat HP prevented) to effective-stamina equivalent.

    The idea: stamina that would give the same average eHP contribution.
      absorb contributes  absorb_value * uptime  average effective HP blocked.
      1 stamina contributes  hp_per_stamina  additional HP.
    So:
      effective_stamina = (absorb_value * uptime) / hp_per_stamina
    """
    return (absorb_value * uptime) / _hp_per_stamina()


def _dr_to_ehp_multiplier(dr_pct: float) -> float:
    """
    Convert a flat damage-reduction percentage to an eHP multiplier.

    If you take DR% less damage, you survive 1/(1-DR%) times as long:
      eHP_multiplier = 1 / (1 - dr_pct)
    So the eHP gain factor is  (1/(1-dr_pct) - 1).
    We return this fractional increase.
    """
    if dr_pct >= 1.0:
        dr_pct = 0.9999
    return 1.0 / (1.0 - dr_pct) - 1.0


def _passive_stats(trinket: TrinketDict) -> StatDict:
    """Return a copy of the trinket's ``stats`` dict with float values."""
    raw = trinket.get("stats") or {}
    return {k: float(v) for k, v in raw.items() if isinstance(v, (int, float))}


# ---------------------------------------------------------------------------
# effective_stat_delta
# ---------------------------------------------------------------------------


def effective_stat_delta(trinket: TrinketDict, character_ilvl: int = 250) -> StatDict:
    """
    Return the survivability stat contribution of *trinket* as a flat stat dict.

    All active effects are converted to an equivalent stamina value so that
    callers can add the result to a Character's stat dict and compute eHP.

    Conversion rules
    ----------------
    passive_stat
        Return trinket['stats'] as-is (no conversion needed).

    on_use_absorb
        uptime = effect['duration_s'] / effect['cooldown_s']
        effective_stamina += absorb_value * uptime / hp_per_stamina
        (plus any passive stats)
        When ``effect['duration_model'] == "until_consumed"`` (a shield with
        no expiration timer that instead persists until its absorb cap is
        used up, e.g. Gloom-Spattered Dreadscale), uptime is 1.0 instead —
        ``duration_s`` is present only as a documentation placeholder for
        that shape and is not read.

    on_use_dr
        eHP gain fraction = 1/(1-dr_pct) - 1
        effective_stamina += current_max_hp * gain_fraction * (duration/cd) / hp_per_stamina
        We use a nominal max_hp of 500_000 for a level-90 tank; callers with a
        real Character should use trinket_ehp_contribution() instead.

    proc_absorb
        uptime ≈ (absorb_duration / icd)   clamped to [0, 1]
        Falls back to 0.25 if ICD is absent (conservative estimate).

    proc_stat / on_use_stat
        Haste/crit/mastery/versatility procs are returned as their time-averaged
        stat contribution.
        Primary-stat procs (strength/agility) are returned as time-averaged primary stat.

    proc_heal
        heal * proc_chance * (1/icd) * duration expressed as stamina equivalent
        (heal received = HP restored = stamina equivalent)

    on_use_damage / on_use_heal / unknown
        Return empty dict (no survivability stat contribution modelled).

    ilvl scaling
    ------------
    Effect values in the YAML are at ``ilvl_base``.  A simple linear ilvl
    scaling factor is applied:  scale = character_ilvl / trinket['ilvl_base'].
    Passive stats are NOT scaled (they represent the exact stat at ilvl_base and
    callers should use the real item stats if they have them).
    """
    t_type = trinket.get("type", "")
    effect = trinket.get("effect") or {}
    base_ilvl = trinket.get("ilvl_base", character_ilvl)
    ilvl_scale = character_ilvl / base_ilvl if base_ilvl > 0 else 1.0

    result: StatDict = {}

    # --- passive stats (always included regardless of type) ---
    passive = _passive_stats(trinket)
    # Primary-stat keys like "agility_or_strength" are kept as-is; callers should
    # resolve them to the appropriate stat for the spec (agility or strength).
    result.update(passive)

    if t_type in _PASSIVE_TYPES:
        # Nothing more to model.
        return result

    # --- on_use_absorb ---
    if t_type in _ON_USE_ABSORB_TYPES:
        absorb = float(effect.get("value", 0)) * ilvl_scale
        cooldown = float(effect.get("cooldown_s", 120.0))
        if cooldown <= 0:
            cooldown = 120.0
        if effect.get("duration_model") == "until_consumed":
            # No expiration timer — the shield sits at full value until
            # damage burns through it, which sustained M+ incoming damage
            # does well inside one cooldown cycle. duration_s/cooldown would
            # read as 0 (silently worthless) for this shape since there's no
            # duration to ratio against; the shield is delivered in full once
            # per cooldown instead, so uptime=1.0 is the correct analog.
            uptime = 1.0
        else:
            duration = float(effect.get("duration_s", 15.0))
            uptime = min(duration / cooldown, 1.0)
        eq_stam = _absorb_to_stamina(absorb, uptime)
        result["stamina"] = result.get("stamina", 0.0) + eq_stam
        return result

    # --- on_use_dr ---
    if t_type in _ON_USE_DR_TYPES:
        dr_pct = float(effect.get("value", 0.0))
        duration = float(effect.get("duration_s", 8.0))
        cooldown = float(effect.get("cooldown_s", 120.0))
        if cooldown <= 0:
            cooldown = 120.0
        nominal_max_hp = 500_000.0  # conservative level-90 tank HP baseline
        gain_frac = _dr_to_ehp_multiplier(dr_pct)
        uptime = min(duration / cooldown, 1.0)
        eq_stam = nominal_max_hp * gain_frac * uptime / _hp_per_stamina()
        result["stamina"] = result.get("stamina", 0.0) + eq_stam
        return result

    # --- proc_absorb ---
    if t_type in _PROC_ABSORB_TYPES:
        absorb = float(effect.get("value", 0)) * ilvl_scale
        duration = float(effect.get("duration_s", 10.0))
        icd = float(effect.get("icd_s", 0.0))
        # If no ICD, fall back to a conservative ~25% uptime estimate.
        uptime = 0.25 if icd <= 0 else min(duration / icd, 1.0)
        eq_stam = _absorb_to_stamina(absorb, uptime)
        result["stamina"] = result.get("stamina", 0.0) + eq_stam
        # Emergency absorb at low HP contributes less on average (only triggers rarely)
        emergency = float(effect.get("emergency_absorb", 0)) * ilvl_scale
        if emergency > 0:
            # Model emergency as ~10% uptime (triggered at 35% HP — infrequent)
            eq_stam_emerg = _absorb_to_stamina(emergency, 0.10)
            result["stamina"] = result.get("stamina", 0.0) + eq_stam_emerg
        return result

    # --- proc_stat (haste, crit, mastery, versatility, primary stat) ---
    if t_type in _PROC_STAT_TYPES:
        e_type = (effect.get("type") or "").lower()
        stat_key = effect.get("stat_key", "")
        value = float(effect.get("value", 0) or effect.get("value_avg", 0)) * ilvl_scale
        duration = float(effect.get("duration_s", 10.0))
        ppm = float(effect.get("proc_ppm", 2.0))
        icd = float(effect.get("icd_s", 0.0))

        # Time-averaged uptime: ICD-gated procs cap at duration/icd, otherwise ppm-based.
        uptime = min(duration / icd, 1.0) if icd > 0 else min(ppm * duration / 60.0, 1.0)

        avg_value = value * uptime

        # Special handling for primary_stat_proc (stacking)
        if e_type == "primary_stat_proc":
            max_stacks = int(effect.get("max_stacks", 1))
            value_per_stack = float(effect.get("value_per_stack", value)) * ilvl_scale
            # Estimate average stacks as max_stacks * uptime_fraction (rough)
            avg_stacks = max_stacks * uptime
            avg_value = value_per_stack * avg_stacks
            stat_key = effect.get("stat_key", "agility_or_strength")

        if stat_key and avg_value > 0:
            result[stat_key] = result.get(stat_key, 0.0) + avg_value
        return result

    # --- on_use_stat (haste/mastery on-use) ---
    if t_type in _ON_USE_STAT_TYPES:
        stat_key = effect.get("stat_key", "")
        value = float(effect.get("value", 0)) * ilvl_scale
        duration = float(effect.get("duration_s", 15.0))
        cooldown = float(effect.get("cooldown_s", 120.0))
        if cooldown <= 0:
            cooldown = 120.0
        uptime = min(duration / cooldown, 1.0)
        avg_value = value * uptime
        if stat_key and avg_value > 0:
            result[stat_key] = result.get(stat_key, 0.0) + avg_value
        return result

    # --- proc_heal ---
    if t_type in _PROC_HEAL_TYPES:
        heal = float(effect.get("value", 0)) * ilvl_scale
        proc_chance = float(effect.get("proc_chance", 0.10))
        icd = float(effect.get("icd_s", 30.0))
        if icd <= 0:
            icd = 30.0
        # Avg heal per second (simplified — no damage event rate modelled here)
        heal_per_s = heal * proc_chance / icd
        # Convert to stamina equivalent at nominal 500k HP tank
        eq_stam = heal_per_s * 30.0 / _hp_per_stamina()  # 30s window average
        result["stamina"] = result.get("stamina", 0.0) + eq_stam
        return result

    # --- on_use_damage, on_use_heal, unknown — no survivability stats ---
    return result


# ---------------------------------------------------------------------------
# trinket_ehp_contribution
# ---------------------------------------------------------------------------


def trinket_ehp_contribution(trinket: TrinketDict, char: Character) -> dict[str, float]:
    """
    Return {"delta_ehp_physical": float, "delta_ehp_magic": float} for this trinket
    vs no-trinket baseline, using the Character's own eHP math.

    Approach:
      1. Compute effective_stat_delta() for the trinket.
      2. Build a *modified* copy of the character with those stats added.
      3. Compare modified vs base eHP.

    Primary-stat contributions (agility_or_strength) are modelled as strength
    for STR-based specs and agility for AGI-based specs.
    """
    from dataclasses import replace

    delta = effective_stat_delta(trinket, character_ilvl=getattr(char, "ilvl", 250))

    # Mixed-type kwargs: ratings are int, max_hp_override is int | None.
    kwargs: dict[str, int | None] = {}

    for stat_key, value in delta.items():
        v = round(value)
        if v == 0:
            continue

        if stat_key == "stamina":
            kwargs["stamina"] = getattr(char, "stamina", 0) + v
        elif stat_key == "armor_from_gear":
            kwargs["armor_from_gear"] = getattr(char, "armor_from_gear", 0) + v
        elif stat_key == "versatility_rating":
            kwargs["versatility_rating"] = getattr(char, "versatility_rating", 0) + v
        elif stat_key == "haste_rating":
            kwargs["haste_rating"] = getattr(char, "haste_rating", 0) + v
        elif stat_key == "crit_rating":
            kwargs["crit_rating"] = getattr(char, "crit_rating", 0) + v
        elif stat_key == "mastery_rating":
            kwargs["mastery_rating"] = getattr(char, "mastery_rating", 0) + v
        elif stat_key in ("agility_or_strength", "strength", "agility"):
            # "agility_or_strength" is the catalog's universal primary-stat
            # placeholder — always resolves to this spec's real primary. A
            # literal "strength"/"agility" entry names ONE specific stat (see
            # e.g. Mark of Light's passive Strength): it only counts for a
            # spec whose primary actually matches, same as Blizzard's own
            # itemization (a Strength trinket is dead weight on an Agility
            # spec). Whatever eHP effect the resulting stat has — if any —
            # comes entirely from Character's own per-spec formulas (e.g.
            # Guardian's agility->armor Ironfur term) via the replace() below;
            # this branch does not add a new conversion path of its own.
            primary = primary_stat_key(char.class_spec)
            if stat_key == "agility_or_strength" or stat_key == primary:
                kwargs[primary] = getattr(char, primary, 0) + v

    if not kwargs:
        # Trinket has no survivability stats — zero contribution
        return {"delta_ehp_physical": 0.0, "delta_ehp_magic": 0.0}

    # If stamina is being modified and the character uses max_hp_override, the
    # override takes precedence over stamina in max_hp(), so the delta would be
    # invisible.  We handle this by clearing max_hp_override and deriving a
    # stamina value that reproduces the original max HP, then applying the delta
    # on top of that.
    if "stamina" in kwargs and char.max_hp_override is not None:
        # Compute effective base stamina that reproduces char.max_hp() via the
        # regular formula (accounting for race/talent multipliers but not the
        # override).  We approximate by clearing the override and letting the
        # regular formula apply.
        kwargs["max_hp_override"] = None

    try:
        # dataclass.replace can't be type-checked through **kwargs splat — the
        # keys are validated at runtime by the dataclass machinery itself.
        char_modified = replace(char, **kwargs)  # type: ignore[arg-type]
    except Exception:
        return {"delta_ehp_physical": 0.0, "delta_ehp_magic": 0.0}

    # Compute eHP on a normalised baseline (both with override cleared if needed).
    char_base = replace(char, max_hp_override=None) if "max_hp_override" in kwargs else char

    base_phys = char_base.effective_hp_physical()
    base_magic = char_base.effective_hp_magic()
    mod_phys = char_modified.effective_hp_physical()
    mod_magic = char_modified.effective_hp_magic()

    return {
        "delta_ehp_physical": mod_phys - base_phys,
        "delta_ehp_magic": mod_magic - base_magic,
    }


# ---------------------------------------------------------------------------
# Convenience: rank trinkets for a spec by physical eHP contribution
# ---------------------------------------------------------------------------


def rank_trinkets_by_ehp(
    char: Character,
    character_ilvl: int = 250,
    physical: bool = True,
) -> list[tuple[float, TrinketDict]]:
    """
    Return a sorted list of (ehp_delta, trinket) for all trinkets relevant
    to char.class_spec, ranked descending by physical eHP (or magic eHP).

    Useful for quick vault/upgrade comparisons.
    """
    results = []
    for t in trinkets_for_spec(char.class_spec):
        contrib = trinket_ehp_contribution(t, char)
        key = "delta_ehp_physical" if physical else "delta_ehp_magic"
        results.append((contrib[key], t))
    results.sort(key=lambda x: x[0], reverse=True)
    return results

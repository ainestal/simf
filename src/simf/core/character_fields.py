"""Single source of truth for which `Character` fields change the sim's
numeric output.

Two independent call sites each need this exact list — `ui/marginals.py`'s
`_char_marginals_signature` (the cache key for sim-derived stat-weight
marginals) and `ui/helpers/run_config.py`'s `compute_reproduction_hash`
payload (the run-manifest hash) — and both used to hand-maintain their own
copy of it. That drifted twice from the same root cause (a field added to
`Character` updated one list but not the other): PR #275
(`_char_marginals_signature` alone was missing `agility`, the largest
Guardian/Brewmaster/VDH marginal) and a second gap found in the 2026-07-10
audit (`_REPRO_HASH_CHAR_FIELDS` already carried `max_hp_override` and
`detected_talent_spell_ids`; `_char_marginals_signature` carried neither —
`detected_talent_spell_ids` changes Prot Warrior's armor multiplier in
`total_armor()`, so two Prot Warriors differing only in detected talents
could silently share one cached marginals entry). Both call sites now derive
from this ONE tuple so the next field anyone adds to `Character` needs
exactly one edit here, not two kept in sync by hand.

``max_hp_override`` is a genuine asymmetry, not a copy-paste gap: the sim
path (`survivability_weights.compute_survivability_marginals`) strips it via
`dc_replace(character, max_hp_override=None)` before perturbing, so it does
NOT affect a sim-derived marginals result — but `_marginals_for` (the
signature's own caller) falls back to the closed-form `ehp_marginals(char)`
when the sim errors or its anchor fails, and THAT path reads
`char.max_hp()` directly, which honors `max_hp_override`. Because one cache
key can end up storing either a sim-derived or a closed-form value
(`_marginals_for` doesn't record which), the signature must be safe for the
closed-form case too — so it's included unconditionally here.

Deliberately excludes `talent_set_override` — a transient per-run A/B
override (`run_simulation(talent_set_override=...)`) that is never set on
the character object either the marginals cache or the run-config banner
represents (the user's actually-loaded character); it would need its own
audit if a caller ever starts threading an override-carrying character
through these paths.
"""

from __future__ import annotations

# Order mirrors `Character`'s own field declaration order in
# `core/character.py`, purely for readability when diffing the two — the
# tuple itself is unordered from a correctness standpoint since every
# consumer hashes/serializes the full tuple, never a slice by position.
SIM_AFFECTING_CHARACTER_FIELDS: tuple[str, ...] = (
    "class_spec",
    "race",
    "talents",
    "stamina",
    "armor_from_gear",
    "strength",
    "agility",
    "haste_rating",
    "crit_rating",
    "mastery_rating",
    "versatility_rating",
    "parry_rating",
    "shield_armor",
    "max_hp_override",
    "stamina_in_caster_form",
    "active_buff_spell_ids",
    "detected_talent_spell_ids",
    "decoded_talents",
)


def normalize_sim_field(value: object) -> object:
    """Stable, order-independent representation of one `Character` field —
    safe both as an element of a hashable dict-cache-key tuple (the
    marginals cache) and inside a `json.dumps` hash payload (the
    reproduction hash).

    ``active_buff_spell_ids`` / ``detected_talent_spell_ids`` are
    ``frozenset[int] | None``: set/frozenset iteration order isn't
    guaranteed stable across processes, and a bare frozenset isn't
    JSON-serializable at all (``json.dumps(..., default=str)`` would fall
    back to ``str(frozenset(...))``, whose element order carries the exact
    same instability). Sorting into a tuple fixes both problems with one
    transform: a tuple is hashable for the cache-key use, and
    `json.dumps` renders a tuple as a JSON array identically to a list for
    the hash-payload use.
    """
    if isinstance(value, (frozenset, set)):
        return tuple(sorted(value))
    return value


def sim_affecting_signature(char: object) -> tuple:
    """The `(field_value, ...)` tuple for `SIM_AFFECTING_CHARACTER_FIELDS`,
    each value passed through `normalize_sim_field`, in field-list order.

    Takes a duck-typed ``char`` (not annotated `Character` — callers include
    plain `SimpleNamespace` test stubs) rather than importing `Character`
    here, so this module stays a leaf: no runtime dependency beyond stdlib,
    importable from both `core` and `ui` call sites with zero cycle risk.

    Callers append their own extra key material after this tuple (e.g.
    `_char_marginals_signature`'s trailing `constants_version`, or
    `disposition_ledger.py`'s trailing key level + damage multiplier) —
    nothing here assumes it's the complete cache key on its own.
    """
    return tuple(
        normalize_sim_field(getattr(char, field)) for field in SIM_AFFECTING_CHARACTER_FIELDS
    )

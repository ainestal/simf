"""Per-slot enchant suggestion rows for the gear card / slot dialog.

Sibling to ``ui/helpers/gem_panel.py`` — pure (no Streamlit imports)
presentation adapter over ``optimizer/enchant_suggester`` (tested there); this
module filters to a slot, formats labels, and decides when a slot has nothing
useful to recommend (``EnchantRow.modeled is False`` — see the suggester's
module docstring for why several Midnight enchant slots can't be scored yet).

Two distinct truths can make a slot show no confident "best" pick, and each
gets its own caption so "nothing renders" never reads as broken:
  - ``modeled=False`` (``no_enchant_exists`` also False): a real catalog
    entry exists for this slot but every candidate scores ~0 survival value
    (Avoidance/Leech/Speed/procs) — "not modeled".
  - ``no_enchant_exists=True``: back/wrist/neck/waist/hands — confirmed,
    researched finding that no permanent enchant exists for this slot in
    Midnight.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...optimizer.enchant_suggester import (
    EnchantSuggestion,
    enchant_slots_from_equipped,
    enchant_survival_value,
    find_enchant_by_id,
    suggest_enchants,
)
from ...optimizer.gem_suggester import primary_stat_key, resolve_gem_stats
from ...optimizer.per_dungeon import CompositionTerms
from .gear_list import format_item_stats

_ZERO_TERM = {"p": 0.0, "m": 0.0, "blended": 0.0}


def _delta_terms(best_terms: CompositionTerms, current_terms: CompositionTerms) -> CompositionTerms:
    """Per-stat composition of a SWAP (best minus current) — see
    ``gem_panel._delta_terms``'s docstring for why ``best_terms`` alone
    (the recommended enchant's own absolute-value composition) can't be
    rendered as-is against ``delta_ehp`` (a swap delta) whenever the current
    enchant isn't empty. Identical fix, applied to enchants."""
    keys = set(best_terms) | set(current_terms)
    return {
        k: {
            field_name: (
                best_terms.get(k, _ZERO_TERM)[field_name]
                - current_terms.get(k, _ZERO_TERM)[field_name]
            )
            for field_name in ("p", "m", "blended")
        }
        for k in keys
    }


# Shown once below the sheet when any slot's enchant choices aren't priced by
# the survival model — those rows still name what's equipped, just without a
# ΔeHP claim, so this explains why some cards show no recommendation. Does
# NOT cover the two per-row captions below (those already explain themselves
# inline, on the row itself).
NOT_MODELED_NOTE = (
    "Some enchants (Avoidance/Leech/Speed/weapon procs) aren't priced by the "
    "survival model yet — those slots show what's equipped, not a pick."
)

# Per-row caption replacing what used to be silence for back/wrist/neck/
# waist/hands.
NO_ENCHANT_EXISTS_CAPTION = "No enchant exists for this slot in Midnight."

# Below this ΔeHP a swap isn't worth surfacing as an action — matches
# gem_panel's convention and epsilon value.
_OPTIMAL_EPSILON_EHP = 1.0


@dataclass(frozen=True)
class EnchantRow:
    """One slot's display row.

    ``is_optimal`` and ``is_identity_optimal`` answer two DIFFERENT
    questions. ``is_identity_optimal`` is the strict sibling of
    ``GemRow.is_identity_optimal``: True only when the current enchant is
    KNOWN (recognized, or genuinely confirmed empty) AND is genuinely the
    model's best pick (or beats the whole catalog) — independent of
    ``optimal_epsilon``.

    Unlike ``GemRow`` (which lets ``is_optimal`` stay True below the
    swap-worthiness bar even for an empty socket, deferring the
    "genuinely optimal" vs. "below-bar, no verdict" distinction to the
    renderer), this ``is_optimal`` is forced False whenever the current enchant isn't
    identity-optimal for a reason OTHER than "a real, known, non-empty
    enchant sitting a hair below best": namely, an UNRECOGNIZED current
    enchant or a genuinely EMPTY slot. A live 2026-07 validation round found
    exactly the first case rendering a confident "optimal" badge on a real
    character's uncataloged enchant, because the old single-``is_optimal``
    gate only checked whether ``delta_ehp`` sits below the paperdoll's
    (large, baseline-relative) meaningful-upgrade epsilon — true here only
    because an unrecognized/empty current enchant scores as 0, making
    almost any real candidate's value look "close enough." Forcing
    ``is_optimal`` False for both routes them into the existing "actionable
    swap" rendering (naming the real recommendation via ``best_label``)
    instead of a bare "optimal" lie — the same practical outcome PR #276
    shipped for gems via a dedicated third render state, reached here
    through ``is_optimal`` itself since no renderer change shipped
    alongside this fix.

      - ``is_identity_optimal`` True → confident "optimal" badge.
      - ``is_identity_optimal`` False, ``is_optimal`` True → a KNOWN,
        non-empty current enchant that's for real just a hair below the
        model's best (pre-existing "close enough" behaviour, unchanged).
      - ``is_optimal`` False → actionable swap: the current enchant is
        unrecognized, the slot is empty, or the gap clears the
        swap-worthiness bar outright.
    """

    slot: str
    current_label: str  # "Mark of the Worldsoul" / "no enchant" / "unrecognized enchant"
    modeled: bool  # False when this slot's whole catalog pool scores ~0 survival
    is_optimal: bool  # see class docstring — NOT the same test as is_identity_optimal
    best_label: str | None  # recommended enchant name (None when already optimal/unmodeled)
    best_stats_label: str  # e.g. "Vers 29" or "Str 32 · Sta 93"
    delta_ehp: float
    delta_label: str  # "+2,650 eHP" / "not modeled" / "no enchant exists"
    caveats: list[str] = field(default_factory=list)
    # True only when the current enchant is genuinely the model's best pick
    # — see the class docstring for the full is_optimal/is_identity_optimal
    # split. Defaults True so the honesty-caption rows below
    # (``no_enchant_exists``/``not modeled`` — which have no real "best" to
    # compare against) read as the quiet state by construction.
    is_identity_optimal: bool = True
    # Ids for Wowhead-linking the name (see item_html._enchant_link_html).
    # None for an unrecognized/empty current enchant, an unconfirmed-id
    # catalog entry, or when already optimal/unmodeled (no best to link).
    current_enchant_id: int | None = None
    best_enchant_id: int | None = None
    # Wowhead's "on-use" spell id — a DIFFERENT namespace from *_enchant_id
    # above (which is for matching, not linking; see enchants.yaml's header
    # and optimizer/enchant_suggester.EnchantCandidate.wowhead_spell_id).
    # This is the id item_html._enchant_link_html actually uses for the href.
    current_wowhead_spell_id: int | None = None
    best_wowhead_spell_id: int | None = None
    # Only ever True alongside modeled=False — see the module docstring.
    no_enchant_exists: bool = False  # back/wrist/neck/waist/hands: confirmed no enchant in Midnight
    # Per-stat ΔeHP composition of `delta_ehp` (per_dungeon.CompositionTerms)
    # — sums exactly to `delta_ehp`. Empty for the not-modeled/no-enchant-
    # exists/already-optimal states (delta_ehp is 0, nothing to decompose).
    terms: CompositionTerms = field(default_factory=dict)


def _current_label(s: EnchantSuggestion, live_name: str | None) -> str:
    # A live Blizzard-resolved name (armory.py's ItemSpec.enchant_name) is
    # always preferred — it's ground truth, unlike catalog matching, which
    # can only recognize the small set of enchants we've researched an id for.
    if live_name:
        return live_name
    if s.current_enchant_id is None:
        return "no enchant"
    if not s.current_known:
        return "unrecognized enchant"
    return s.current_name or "current enchant"


def _enchant_row_terms(
    s: EnchantSuggestion,
    class_spec: str,
    marginals: dict | None,
    dungeons: list[dict] | None,
) -> CompositionTerms:
    """Composition of `s.delta_ehp` (best minus current) — see
    `gem_panel._row_terms`'s sibling docstring. `marginals=None` (a caller
    that only wants labels/deltas) yields an empty composition."""
    if s.best is None or marginals is None:
        return {}
    cur = find_enchant_by_id(s.current_enchant_id)
    if cur is None:
        current_terms: CompositionTerms = {}
    else:
        primary = primary_stat_key(class_spec)
        current_stats = resolve_gem_stats(cur.get("stats") or {}, primary)
        _, current_terms = enchant_survival_value(
            current_stats, marginals, dungeons, return_terms=True
        )
    return _delta_terms(s.best_terms, current_terms)


def _row_from_suggestion(
    s: EnchantSuggestion,
    live_name: str | None,
    optimal_epsilon: float,
    class_spec: str = "",
    marginals: dict | None = None,
    dungeons: list[dict] | None = None,
) -> EnchantRow:
    current_label = _current_label(s, live_name)
    if s.no_enchant_exists:
        # Neither a real recommendation nor a "not modeled" catalog gap — a
        # structural truth about the slot itself. Handled before the generic
        # `not s.modeled` branch below since this flag implies modeled=False
        # too, and it's the more specific, more useful thing to say.
        return EnchantRow(
            slot=s.slot,
            current_label=current_label,
            modeled=False,
            is_optimal=True,
            best_label=None,
            best_stats_label="",
            delta_ehp=0.0,
            delta_label="no enchant exists",
            current_enchant_id=s.current_enchant_id,
            best_enchant_id=None,
            no_enchant_exists=True,
        )
    if not s.modeled:
        # Nothing in this slot's pool clears the "modeled" bar — don't name a
        # "best" pick, since the sort order among all-~0 candidates is an
        # arbitrary tiebreak, not a real recommendation (see suggester docstring).
        return EnchantRow(
            slot=s.slot,
            current_label=current_label,
            modeled=False,
            is_optimal=True,
            best_label=None,
            best_stats_label="",
            delta_ehp=0.0,
            delta_label="not modeled",
            current_enchant_id=s.current_enchant_id,
            best_enchant_id=None,
            current_wowhead_spell_id=s.current_wowhead_spell_id,
            best_wowhead_spell_id=None,
        )
    assert s.best is not None  # noqa: S101 — modeled=True implies a real best candidate exists
    # `enchant_id is not None and ... ==` — NOT a bare `==`: an empty slot
    # (current_enchant_id=None) and an unconfirmed-id catalog entry
    # (best.enchant_id=None) would otherwise coincidentally compare equal and
    # wrongly read as "already holding the best pick." Also gated on
    # `current_known`: an id that merely LOOKS equal but was resolved from an
    # unrecognized enchant isn't a real identity match either (defensive —
    # `current_enchant_id`/`current_known` already move together in
    # `suggest_enchants`, but the explicit check makes the invariant local).
    is_identity_optimal = (
        s.current_known
        and s.best.enchant_id is not None
        and s.best.enchant_id == s.current_enchant_id
    )
    # The epsilon-based "close enough, don't bother swapping" grace period
    # must ONLY apply when the current enchant is a real, known, non-empty
    # pick that's merely a hair short of the model's best. Two states must
    # NEVER get that grace, because reporting "optimal" for them would be a
    # fabrication, not a rounding approximation:
    #   - `current_known=False` (unrecognized real-world enchant): we don't
    #     actually know what's socketed, so `delta_ehp` was computed against
    #     an assumed value of 0 — it says nothing about whether the REAL
    #     current enchant is close to best.
    #   - `current_enchant_id=None` (genuinely empty slot): there's nothing
    #     socketed, so "optimal" would mean "correctly leaving this slot
    #     unenchanted," which is never true once `modeled=True` implies a
    #     real, positive recommendation exists.
    # A live 2026-07 validation round found the first case rendering a
    # confident "optimal" badge on a real character's uncataloged enchant —
    # this is the same class of bug PR #276 fixed for an empty gem socket.
    optimal_grace = (
        s.current_known and s.current_enchant_id is not None and s.delta_ehp < optimal_epsilon
    )
    is_optimal = is_identity_optimal or optimal_grace
    return EnchantRow(
        slot=s.slot,
        current_label=current_label,
        modeled=True,
        is_optimal=is_optimal,
        is_identity_optimal=is_identity_optimal,
        # Named whenever current ISN'T genuinely the model's best — even when
        # the gap is below the swap bar — so a "kept" row can still say what
        # the real best candidate is instead of silently agreeing with
        # "optimal" (mirrors GemRow's `best_label`).
        best_label=None if is_identity_optimal else s.best.name,
        best_stats_label=format_item_stats(s.best.stats),
        delta_ehp=s.delta_ehp,
        delta_label=f"+{s.delta_ehp:,.0f} eHP",
        current_enchant_id=s.current_enchant_id,
        best_enchant_id=None if is_identity_optimal else s.best.enchant_id,
        current_wowhead_spell_id=s.current_wowhead_spell_id,
        best_wowhead_spell_id=None if is_identity_optimal else s.best.wowhead_spell_id,
        terms=_enchant_row_terms(s, class_spec, marginals, dungeons),
    )


def _live_enchant_names(equipped: dict) -> dict[str, str]:
    """slot -> Blizzard-resolved enchant display name, for items that carry
    one (online-lookup imports only; ``.simc``-paste/log-hydrate never set
    ``enchant_name``, so this is empty for those import paths)."""
    out: dict[str, str] = {}
    for slot, item in equipped.items():
        if item is None:
            continue
        name = getattr(item, "enchant_name", None)
        if name is None and isinstance(item, dict):
            name = item.get("enchant_name")
        if name:
            out[slot] = name
    return out


def build_enchant_rows_all(
    equipped: dict,
    marginals: dict,
    class_spec: str,
    dungeons: list[dict] | None = None,
    *,
    optimal_epsilon: float = _OPTIMAL_EPSILON_EHP,
    slot: str | None = None,
) -> list[EnchantRow]:
    """Enchant display rows across all of the character's enchant-relevant
    slots (both truly enchantable slots and the honest-caption slots —
    back/wrist/neck/waist/hands, see ``enchant_slots_from_equipped``),
    optionally filtered to ``slot`` for the per-slot dialog. Empty when the
    character has none of those equipped (or, filtered, when ``slot`` is a
    structural non-slot for this spec — a trinket, or a shield off_hand)."""
    enchant_slots = enchant_slots_from_equipped(equipped, class_spec)
    if slot is not None and not any(sl.slot == slot for sl in enchant_slots):
        return []
    suggestions = suggest_enchants(
        enchant_slots, marginals, class_spec=class_spec, dungeons=dungeons
    )
    live_names = _live_enchant_names(equipped)
    return [
        _row_from_suggestion(
            s, live_names.get(s.slot), optimal_epsilon, class_spec, marginals, dungeons
        )
        for s in suggestions
        if slot is None or s.slot == slot
    ]


def build_enchant_rows(
    slot: str,
    equipped: dict,
    marginals: dict,
    class_spec: str,
    dungeons: list[dict] | None = None,
    *,
    optimal_epsilon: float = _OPTIMAL_EPSILON_EHP,
) -> list[EnchantRow]:
    """Display rows for ``slot`` only (the slot dialog) — 0 or 1 rows, since
    every item has at most one enchant."""
    return build_enchant_rows_all(
        equipped, marginals, class_spec, dungeons, optimal_epsilon=optimal_epsilon, slot=slot
    )


def build_enchant_rows_by_slot(
    equipped: dict,
    marginals: dict,
    class_spec: str,
    dungeons: list[dict] | None = None,
    *,
    optimal_epsilon: float = _OPTIMAL_EPSILON_EHP,
) -> dict[str, EnchantRow]:
    """``build_enchant_rows_all``'s rows keyed by slot, for the paperdoll
    card's enchant line. Unlike gems (multiple sockets per item are
    possible), an item has at most one enchant, so this is a flat dict, not
    dict-of-lists. back/wrist/neck/waist/hands get a row with an honest
    caption (see module docstring); structural non-slots for this spec
    (a trinket, a shield off_hand) are absent — the card omits the enchant
    line entirely for those.

    ``optimal_epsilon`` — see ``build_gem_rows_by_slot``'s docstring: the
    paperdoll caller passes the same baseline-relative meaningful-upgrade
    threshold the item-swap gate uses, so an enchant card can't recommend a
    swap the tool's own error bar can't back up."""
    rows = build_enchant_rows_all(
        equipped, marginals, class_spec, dungeons, optimal_epsilon=optimal_epsilon
    )
    return {r.slot: r for r in rows}


def enchant_card_notes(rows_by_slot: dict[str, EnchantRow]) -> list[str]:
    """Honesty caption shown once below the card paperdoll when any slot's
    enchant choices aren't priced by the survival model. Scoped to the
    genuine "cataloged but scores ~0" case only — back/wrist/neck/waist/hands
    already explain themselves inline on their own row, so they don't also
    trigger this aggregate note."""
    if any(not r.modeled and not r.no_enchant_exists for r in rows_by_slot.values()):
        return [NOT_MODELED_NOTE]
    return []


__all__ = [
    "NOT_MODELED_NOTE",
    "NO_ENCHANT_EXISTS_CAPTION",
    "EnchantRow",
    "build_enchant_rows",
    "build_enchant_rows_all",
    "build_enchant_rows_by_slot",
    "enchant_card_notes",
    "find_enchant_by_id",
]

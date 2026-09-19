"""Per-socket gem suggestion rows for the slot dialog.

Pure (no Streamlit imports) so the display logic is unit-testable without
booting the app — the slot dialog (`ui/app.py:_slot_dialog`) just renders the
``GemRow`` list this returns. The survival scoring lives in
``optimizer/gem_suggester`` (tested there); this module is the presentation
adapter: it filters the character-wide suggestions to one slot and formats the
current/recommended labels, the ΔeHP, and the honest caveats.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...optimizer.gem_suggester import (
    GemSuggestion,
    find_gem_by_id,
    gem_survival_value,
    primary_stat_key,
    resolve_gem_stats,
    sockets_from_equipped,
    suggest_gems,
)
from ...optimizer.per_dungeon import CompositionTerms
from .gear_list import format_item_stats

_ZERO_TERM = {"p": 0.0, "m": 0.0, "blended": 0.0}


def _delta_terms(best_terms: CompositionTerms, current_terms: CompositionTerms) -> CompositionTerms:
    """Per-stat composition of a SWAP (best minus current), from each side's
    own absolute-value composition.

    ``gem_survival_value``/``enchant_survival_value``'s ``terms`` decompose
    one item's OWN absolute survival value (Σ == that item's scalar) — not a
    delta from whatever's currently socketed. ``GemRow.delta_ehp`` (like
    every other ΔeHP this feature renders) is a swap delta
    (``best_value - current_value``), so naively rendering ``best_terms`` on
    its own would only sum to ``delta_ehp`` in the one case where the current
    socket is empty (``current_value == 0``) — a real swap between two
    non-empty gems would show a composition that doesn't add up to the
    number next to it, exactly the bug this whole feature exists to prevent.

    The fix needs no change to ``gem_suggester``'s core value computation:
    the per-stat "blended" contribution is linear in the stat's own value
    (``blended = phys*p_contrib + mag*m_contrib``, and each `*_contrib` is
    `marginal * stat_value`), so `best_terms[stat] - current_terms[stat]`
    equals the composition of `(best.stats - current.stats)` directly —
    the exact arithmetic `_score_for_school_mix` would produce if called on
    that delta. Sums to `best_value - current_value` by construction.
    """
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


# Dropping the Eversong Diamond surprises warriors who treat it as BiS — frame
# it as a throughput pick, not a bug (Brutoh 2026-06-24). Shared between the
# slot-dialog's per-row rendering (widgets.py) and the card paperdoll's
# once-per-sheet honesty caption (gem_card_notes) so the wording has one home.
META_DROP_NOTE = (
    "Dropping the Eversong Diamond isn't a bug — its primary stat is a "
    "throughput pick, not your best survival gem."
)

# Below this ΔeHP a swap isn't worth surfacing as an action — the current gem
# is "already the best survival gem" for that socket. Gems are small (tens to a
# few thousand eHP), so the floor is low; it mainly catches the keep-current
# case (delta exactly 0) and sub-eHP rounding.
_OPTIMAL_EPSILON_EHP = 1.0


@dataclass(frozen=True)
class GemRow:
    """One socket's display row.

    ``is_optimal`` and ``is_identity_optimal`` answer two DIFFERENT
    questions and a 2026-07-05 gem-trust review (novice/engaged/elite tank +
    ui-craft-critic + calibration-scientist, all independently) found the UI
    collapsing them into one green "optimal" badge reads as a lie: an empty,
    ungemmed socket and a socket sitting 0.3% under the swap-worthiness bar
    both rendered "optimal" — indistinguishable from a socket that's
    genuinely the model's best pick. Callers MUST branch on
    ``is_identity_optimal`` first:

      - ``is_identity_optimal`` True  → genuinely scored and won. Confident
        "optimal" badge.
      - ``is_identity_optimal`` False, ``is_optimal`` True → a real
        candidate beats the current gem (``best_label``/``delta_ehp`` name
        it), but the gap is below the swap-worthiness bar. Render as a
        neutral "{current} → {best} {delta}" state with NO verdict word —
        never "optimal," and (2026-07-30 user direction) never "kept"
        either: show the reader the actual numbers and let them decide,
        don't decide for them.
      - ``is_optimal`` False → actionable swap.

    The epsilon-based below-bar grace (``is_optimal`` True while
    ``is_identity_optimal`` is False) is only valid when the current gem's
    value is actually known — ``s.current_known`` (an empty socket counts as
    known-empty; see ``gem_suggester.suggest_gems``). An UNRECOGNIZED
    currently-socketed gem scores ``current_value=0`` by construction, so its
    ``delta_ehp`` says nothing about whether the real (unidentified) gem is
    close to best — granting it the grace produced a live, confusing
    "unrecognized gem → X, below swap bar"-shaped line (a real gap stated
    with false confidence about a gem the model never actually compared).
    ``_row_from_suggestion`` gates the epsilon clause on ``s.current_known``
    for exactly this reason — this is the gem-side twin of the
    ``current_known`` gate ``enchant_panel.py`` already applies (commit
    19023d3). A genuinely known-empty socket does NOT get the caller's
    baseline-relative grace either (2026-08-07 fix): that bar is ~0.5% of
    the character's total eHP, routinely larger than any single gem's own
    value, so it rendered every empty socket "kept, below swap bar" forever.
    An empty socket instead uses a near-zero absolute floor — there's no
    opportunity cost to filling it, so there's no tradeoff for a
    meaningful-upgrade bar to suppress.
    """

    slot: str
    index: int
    current_label: str  # "Indecipherable Eversong Diamond" / "empty socket" / "unrecognized gem"
    is_optimal: bool  # current gem clears the meaningful-upgrade bar OR is the model's actual best
    best_label: str | None  # the model's best candidate name (None only when current IS that best)
    best_stats_label: str  # e.g. "Agi 32" or "Hst 16 · Vers 7"
    delta_ehp: float
    delta_label: str  # "+2,650 eHP"
    current_is_meta: bool = False  # current gem is a Unique meta (primary/throughput pick)
    # True only when the model's best candidate for this socket IS the
    # currently-socketed gem — independent of `optimal_epsilon`. A meta/
    # throughput gem sitting in a survival slot is a qualitative mismatch
    # ("wrong kind of gem," not "a slightly-too-small number") that stays
    # true even when its computed ΔeHP is too small to headline as its own
    # recommendation — so the meta-drop honesty note keys off THIS, not
    # `is_optimal` (2026-07-05: the note went silent on a real character
    # once the epsilon gate was raised to a meaningful-upgrade threshold,
    # since a gem's own delta rarely clears an item-scale bar).
    is_identity_optimal: bool = True
    caveats: list[str] = field(default_factory=list)
    # Item ids for Wowhead-linking the name (rich hover tooltip via the page's
    # existing Wowhead Power script — see item_html._gem_link_html). None for
    # an unrecognized/empty current gem, or when current IS the model's best
    # (identity-optimal — no separate "best" item to link).
    current_gem_id: int | None = None
    best_gem_id: int | None = None
    # The next-best candidate distinct from both the current gem and
    # `best_label` — answers "why did the runner-up lose" (2026-07-05 review:
    # `GemSuggestion.ranked` was already computed and discarded at render).
    # "" when the pool has no third distinct candidate.
    runner_up_label: str = ""
    # Per-stat ΔeHP composition of `delta_ehp` (per_dungeon.CompositionTerms)
    # — sums exactly to `delta_ehp` (see `_delta_terms`). Empty when
    # `delta_ehp` is 0 (identity-optimal — nothing to decompose).
    terms: CompositionTerms = field(default_factory=dict)


def _current_label(s: GemSuggestion) -> str:
    if s.current_gem_id is None:
        return "empty socket"
    if not s.current_known:
        return "unrecognized gem"
    return s.current_name or "current gem"


def _runner_up_label(s: GemSuggestion) -> str:
    """The best candidate distinct from both the current gem and `s.best` —
    the answer to "why did the next option lose" (2026-07-05 review). Walks
    `s.ranked` (already sorted best-to-worst, computed once per pool by
    `suggest_gems`) past whichever entries are the current/recommended gem.

    Reports the SAME unit as `delta_ehp` — a delta against the current gem,
    not `s.ranked`'s raw absolute score. A live-app check caught this: the
    absolute score (e.g. 7,069) can read larger than the recommendation's own
    delta (e.g. "+6,653 eHP"), implying the runner-up beats the pick, when
    6,653 is itself already a delta and 7,069 is not — the two numbers
    weren't comparable."""
    exclude = {s.current_gem_id, s.best.item_id if s.best else None}
    for cand, val in s.ranked:
        if cand.item_id not in exclude:
            stats = format_item_stats(cand.stats)
            suffix = f" ({stats})" if stats else ""
            delta = val - s.current_value
            sign = "+" if delta >= 0 else ""
            return f"{cand.name}{suffix} · {sign}{delta:,.0f} eHP"
    return ""


def _row_terms(
    s: GemSuggestion,
    class_spec: str,
    marginals: dict | None,
    dungeons: list[dict] | None,
) -> CompositionTerms:
    """Composition of `s.delta_ehp` (best minus current), NOT `s.best_terms`
    on its own — see `_delta_terms`'s docstring for why the two differ
    whenever the current socket isn't empty. `marginals=None` (a caller that
    only wants labels/deltas, not composition — e.g. a `GemSuggestion` built
    by hand in a test) yields an empty composition rather than raising."""
    if s.best is None or marginals is None:
        return {}
    cur = find_gem_by_id(s.current_gem_id)
    if cur is None:
        current_terms: CompositionTerms = {}
    else:
        primary = primary_stat_key(class_spec)
        current_stats = resolve_gem_stats(cur.get("stats") or {}, primary)
        _, current_terms = gem_survival_value(current_stats, marginals, dungeons, return_terms=True)
    return _delta_terms(s.best_terms, current_terms)


def _row_from_suggestion(
    s: GemSuggestion,
    class_spec: str,
    optimal_epsilon: float,
    marginals: dict | None = None,
    dungeons: list[dict] | None = None,
) -> GemRow:
    is_identity_optimal = s.best.item_id == s.current_gem_id
    # A genuinely empty socket (current_gem_id is None) has no opportunity
    # cost to fill — there's no tradeoff for a baseline-relative epsilon to
    # suppress, unlike a real item-vs-item swap. The caller's `optimal_epsilon`
    # is ~0.5% of the character's TOTAL eHP (hundreds to thousands), routinely
    # larger than any single gem's own value (tens to a few thousand eHP) —
    # under that bar every empty socket on a well-geared character rendered
    # "kept, below swap bar" forever, even though socketing it is free value.
    # Use the module's own near-zero absolute floor for this case instead;
    # a filled socket keeps the caller's baseline-relative bar unchanged.
    epsilon = _OPTIMAL_EPSILON_EHP if s.current_gem_id is None else optimal_epsilon
    is_optimal = is_identity_optimal or (s.current_known and s.delta_ehp < epsilon)
    cur = find_gem_by_id(s.current_gem_id)
    current_is_meta = bool(cur and cur.get("unique_equipped"))
    caveats: list[str] = []
    if not is_identity_optimal:
        if s.best.unique_equipped:
            caveats.append("Unique — only one Eversong Diamond can be socketed.")
        if class_spec == "guardian_druid" and s.best.stats.get("haste_rating"):
            caveats.append("Haste counts toward survival only for Elune's Chosen.")
    return GemRow(
        slot=s.slot,
        index=s.index,
        current_label=_current_label(s),
        is_optimal=is_optimal,
        # Named whenever current ISN'T genuinely the model's best — even when
        # the gap is below the swap bar, so a "kept" row can still say what
        # the real best candidate is instead of silently agreeing with "optimal".
        best_label=None if is_identity_optimal else s.best.name,
        best_stats_label=format_item_stats(s.best.stats),
        delta_ehp=s.delta_ehp,
        delta_label=f"+{s.delta_ehp:,.0f} eHP",
        current_is_meta=current_is_meta,
        is_identity_optimal=is_identity_optimal,
        caveats=caveats,
        current_gem_id=s.current_gem_id,
        best_gem_id=None if is_identity_optimal else s.best.item_id,
        runner_up_label=_runner_up_label(s),
        terms=_row_terms(s, class_spec, marginals, dungeons),
    )


def build_gem_rows_all(
    equipped: dict,
    marginals: dict,
    class_spec: str,
    dungeons: list[dict] | None = None,
    *,
    optimal_epsilon: float = _OPTIMAL_EPSILON_EHP,
    slot: str | None = None,
) -> list[GemRow]:
    """Gem display rows across all of the character's sockets.

    Scores every socket together (so the Unique-Equipped Eversong Diamond meta
    is reserved for exactly one socket character-wide), then returns the rows —
    optionally filtered to ``slot`` for the per-slot dialog. Empty when there
    are no (matching) sockets, so the caller renders nothing.
    """
    all_sockets = sockets_from_equipped(equipped)
    if slot is not None and not any(sk.slot == slot for sk in all_sockets):
        return []
    suggestions = suggest_gems(all_sockets, marginals, class_spec=class_spec, dungeons=dungeons)
    return [
        _row_from_suggestion(s, class_spec, optimal_epsilon, marginals, dungeons)
        for s in suggestions
        if s.best is not None and (slot is None or s.slot == slot)
    ]


def build_gem_rows(
    slot: str,
    equipped: dict,
    marginals: dict,
    class_spec: str,
    dungeons: list[dict] | None = None,
    *,
    optimal_epsilon: float = _OPTIMAL_EPSILON_EHP,
) -> list[GemRow]:
    """Display rows for the gem sockets on ``equipped[slot]`` (the slot dialog)."""
    return build_gem_rows_all(
        equipped, marginals, class_spec, dungeons, optimal_epsilon=optimal_epsilon, slot=slot
    )


def gem_section_notes(class_spec: str, marginals: dict) -> list[str]:
    """Spec/build-level caveats shown ABOVE the gem rows (not per-socket).

    For a Guardian (the gem suggester's target spec), two stats need explicit
    steering because a silently-zero marginal reads as "the model doesn't
    understand bears," not as a known limitation:

    1. Nature's Guardian mastery → healing/absorb-received DOES have survival
       value since 2026-06-26 (docs/validation/phase4_guardian_mastery_hp),
       via the sim-perturbation path every gear/gem surface uses — but it's
       build-dependent (the healing-profile assumption), so a build where it
       genuinely scores ~0 still needs the honest callout. Gate on the LIVE
       marginal, exactly like the haste check below — a 2026-07-05 gem-trust
       review found this note firing unconditionally, unconditionally
       contradicting a mastery gem the same screen was labeling "optimal"
       (the caveat was written 2026-06-25, one day before mastery shipped,
       and never updated).
    2. Haste only has survival value for an Elune's-Chosen Guardian. For Druid
       of the Claw, haste gems silently score 0 and never surface — the silence
       reads as a gap, so steer explicitly to versatility.
    """
    notes: list[str] = []
    if class_spec == "guardian_druid":
        mastery_active = float((marginals.get("mastery_rating") or {}).get("p", 0.0)) > 0.0
        if not mastery_active:
            notes.append(
                "⚠️ Guardian mastery isn't scoring survival value for this build — "
                "mastery gems may be undervalued here."
            )
        ec_active = float((marginals.get("haste_rating") or {}).get("p", 0.0)) > 0.0
        if not ec_active:
            notes.append(
                "On Druid of the Claw, haste does nothing for your survival — "
                "versatility is your secondary."
            )
    return notes


def build_gem_rows_by_slot(
    equipped: dict,
    marginals: dict,
    class_spec: str,
    dungeons: list[dict] | None = None,
    *,
    optimal_epsilon: float = _OPTIMAL_EPSILON_EHP,
) -> dict[str, list[GemRow]]:
    """``build_gem_rows_all``'s rows grouped by slot, for the paperdoll card's
    gem line(s). One entry per socket (almost always exactly one per slot —
    multi-socket items are rare, and get one stacked line per socket rather
    than a lossy name-less aggregate) so the card can name the actual gem
    instead of just a number. Slots with no filled sockets are absent (the
    card omits the gem line entirely, same as an item with no gem_ids).

    ``optimal_epsilon`` defaults to the module's near-zero constant for
    backward-compatible callers (tests, the slot dialog's per-slot view where
    a tiny gem gain is still worth surfacing on click) — the paperdoll caller
    passes the same baseline-relative meaningful-upgrade threshold the
    item-swap gate uses, so a gem card can't recommend a swap the tool's own
    error bar can't back up."""
    rows = build_gem_rows_all(
        equipped, marginals, class_spec, dungeons, optimal_epsilon=optimal_epsilon
    )
    by_slot: dict[str, list[GemRow]] = {}
    for r in rows:
        by_slot.setdefault(r.slot, []).append(r)
    return by_slot


def gem_card_notes(
    class_spec: str, marginals: dict, rows_by_slot: dict[str, list[GemRow]]
) -> list[str]:
    """Honesty captions shown ONCE below the card paperdoll, instead of
    repeating per-card: the spec/build-level notes (``gem_section_notes``),
    the Eversong meta-drop callout when any card recommends dropping a
    currently-equipped Unique meta, and every distinct per-recommendation
    caveat (Unique-meta / Elune's-Chosen-only) across all sockets."""
    notes = gem_section_notes(class_spec, marginals)
    rows = [r for group in rows_by_slot.values() for r in group]
    if any(r.current_is_meta and not r.is_identity_optimal for r in rows):
        notes.append(META_DROP_NOTE)
    for r in rows:
        for c in r.caveats:
            if c not in notes:
                notes.append(c)
    return notes

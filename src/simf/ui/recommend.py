"""simf UI — per-slot survivability recommendations + paperdoll grid (L3).

The survivability/DPS slider, the per-slot best-candidate sweep
(``_per_slot_picks``), the recommendation-summary headline, and the dark
16-slot card paperdoll. No ``st.*`` at module scope.

Lives at ``src/simf/ui/`` (same depth as app.py); imports only from the
L0/L1/L2 layers (``format_html``/``item_html``/``marginals``/``models``/
``state``) + ``optimizer``/``io``. NEVER imports from ``app`` (strict L3 DAG).
"""

from __future__ import annotations

import streamlit as st

from simf.core.character import Character
from simf.core.constants import load_constants
from simf.io.dungeon_loot import items_for_slot, load_dungeon_loot
from simf.optimizer.alternatives import alternatives_for_slot, equivalent_slots
from simf.optimizer.per_dungeon import trinket_swap_per_dungeon
from simf.optimizer.tier_sets import SetStatus
from simf.optimizer.trinket_db import find_trinket_by_id
from simf.optimizer.vault_ranking import compute_delta_dps
from simf.ui.format_html import _composite_score, _surv_axis_label
from simf.ui.helpers.enchant_panel import EnchantRow
from simf.ui.helpers.gear_list import CANONICAL_SLOT_ORDER, SLOT_LABELS, display_name
from simf.ui.helpers.gem_panel import GemRow
from simf.ui.item_html import _slot_card_html
from simf.ui.marginals import _marginals_for
from simf.ui.models import (
    _card_for,
    _dedupe_paired_picks,
    _flag_set_breaks,
    _is_meaningful_swap,
    _SlotPick,
)
from simf.ui.state import (
    _apply_trial_all,
    _equipped,
    _format_ehp_delta,
    _is_read_only,
    _is_two_handed_item,
    _meaningful_upgrade_ehp_threshold,
    _ss,
    _stats_for_item,
    _surv_weight,
)
from simf.ui.widgets import _render_per_dungeon_row


def _per_slot_picks(char: Character, dungeons: list[dict], surv_pct: float) -> dict[str, _SlotPick]:
    """For every slot, pick the candidate (equipped + bag + vault + M+ loot) with
    the highest composite score under the survivability/DPS slider. Returns one
    pick per slot, marking which slots recommend a swap.

    M+ loot from the user's prog-filtered dungeons feeds in via the same
    ``extra_sources`` channel the slot dialog uses, so the paperdoll
    button's enabled state stays consistent with what the dialog actually
    surfaces. A slot whose only candidates are M+ chase targets no longer
    looks like "no alternatives" on the paperdoll.
    """
    marginals = _marginals_for(char)
    dps_weights = load_constants().get("dps_stat_weights", {})
    # Meaningful-upgrade gate: a slot is only flagged as a swap on the default
    # paperdoll when the best candidate's ΔeHP clears this fraction of baseline
    # eHP (else it reads a quiet "Best you own"). Stops marginal vers re-shuffles
    # lighting up nearly every slot. The full ranked sweep (ranked_by_slot) is
    # kept ungated for the Browse control + upgrade panel. 0 → gate disabled.
    ehp_swap_threshold = _meaningful_upgrade_ehp_threshold(char)
    bag = _ss().get("simc_bag_items") or {}
    vault = _ss().get("simc_vault_items") or {}
    equipped = _equipped()
    loot_table = load_dungeon_loot()
    id_to_abbrev = {d["id"]: d.get("abbrev") or d["id"] for d in dungeons}
    dungeon_ids = [d["id"] for d in dungeons]
    picks: dict[str, _SlotPick] = {}
    # Retain the full ranked candidate list + the baseline per slot so the
    # paired-slot dedup pass (below) can re-pick a displaced slot's next-best
    # distinct candidate without rescoring.
    ranked_by_slot: dict[str, list[_SlotPick]] = {}
    baseline_by_slot: dict[str, _SlotPick] = {}
    for slot in CANONICAL_SLOT_ORDER:
        current = equipped.get(slot)
        is_trinket = slot in ("trinket1", "trinket2")
        # Whether the item this slot would give up is itself registry-known
        # (real proc/use value) — used below to hold an unknown candidate
        # from winning the recommendation on stats alone (see
        # `_SlotPick.eligible_for_recommendation`).
        equipped_known_trinket = (
            is_trinket
            and current is not None
            and find_trinket_by_id(getattr(current, "item_id", 0)) is not None
        )
        loot_pairs = items_for_slot(equivalent_slots(slot), dungeon_ids, loot=loot_table)
        extra_sources = [(f"m+ {id_to_abbrev.get(d_id, d_id)}", item) for d_id, item in loot_pairs]
        alts = alternatives_for_slot(
            slot=slot,
            equipped=equipped,
            bag=bag,
            vault=vault,
            marginals=marginals,
            dungeons=dungeons,
            item_stats_fn=_stats_for_item,
            extra_sources=extra_sources,
            exclude_two_handed=char.uses_shield,
            item_is_two_handed_fn=_is_two_handed_item,
        )
        n_alts = len(alts)
        # Baseline: equipped wins by default (delta = 0 vs itself). The
        # ``n_alternatives`` count lets the renderer distinguish "no
        # alternatives in bag/vault" from "alternatives exist but equipped is
        # the best of the bunch" — user-flagged 2026-05-15.
        baseline = _SlotPick(
            slot=slot,
            item=current,
            is_swap=False,
            delta_ehp=0.0,
            delta_dps=0.0,
            composite=0.0,
            has_warning=is_trinket,
            n_alternatives=n_alts,
        )
        baseline_by_slot[slot] = baseline
        equipped_stats = _stats_for_item(current) if current else None
        candidates: list[_SlotPick] = []
        for a in alts:
            new_stats = _stats_for_item(a.item)
            d_dps = compute_delta_dps(new_stats, equipped_stats, dps_weights)
            # For trinket slots, try the curated registry first — gives
            # proc/use-aware ΔeHP for known trinkets and drops the
            # ⚠️ warning when both sides are modeled. Stats-only with
            # ⚠️ is the fallback when the candidate is unknown.
            trinket_warning = is_trinket
            # ΔeHP is "modeled" (safe to show as a number) for every
            # non-trinket slot, and for a trinket only when the proc/use-aware
            # registry produced the number. An unknown candidate trinket
            # (`trinket_swap_per_dungeon` returns None) falls back to passive-
            # stat ΔeHP — false precision for an item whose survivability value
            # lives in its proc/use effect. `both_known=False` is NOT this case:
            # the candidate is still proc-modeled, only the equipped side is
            # unknown, so the number is real (keep it, keep the ⚠️).
            ehp_modeled = not is_trinket
            d_ehp = a.avg_delta_ehp
            # Stat-marginal composition by default (every non-trinket slot,
            # and a trinket whose registry lookup below doesn't pan out) —
            # replaced with an empty dict + `via_registry=True` the moment
            # the trinket-effect registry actually supplies the number,
            # since that ΔeHP isn't a stat dot product at all (see
            # `_SlotPick.via_trinket_registry`).
            terms = a.terms
            per_dungeon = a.per_dungeon
            via_registry = False
            eligible = True
            if is_trinket:
                registry = trinket_swap_per_dungeon(current, a.item, char, dungeons)
                if registry is not None:
                    reg_scores, both_known = registry
                    d_ehp = (
                        sum(s.delta_ehp for s in reg_scores) / len(reg_scores)
                        if reg_scores
                        else 0.0
                    )
                    trinket_warning = not both_known
                    ehp_modeled = True
                    terms = {}
                    # Registry-scored per-dungeon breakdown replaces the
                    # generic passive-stat one `alternatives_for_slot` computed
                    # — same override symmetry as `d_ehp`/`terms` above, so the
                    # consolidated per-dungeon section shows numbers consistent
                    # with the card's own displayed ΔeHP, not a different model.
                    per_dungeon = reg_scores
                    via_registry = True
                elif equipped_known_trinket:
                    # Candidate unknown, equipped known — see
                    # `_SlotPick.eligible_for_recommendation`. Still ranked
                    # and shown in Browse/the upgrade panel below; just held
                    # from winning the "swap now" pick.
                    eligible = False
            comp = _composite_score(d_ehp, d_dps, surv_pct)
            candidates.append(
                _SlotPick(
                    slot=slot,
                    item=a.item,
                    is_swap=True,
                    delta_ehp=d_ehp,
                    delta_dps=d_dps,
                    composite=comp,
                    has_warning=trinket_warning,
                    n_alternatives=n_alts,
                    ehp_modeled=ehp_modeled,
                    source=a.source,
                    terms=terms,
                    per_dungeon=per_dungeon,
                    via_trinket_registry=via_registry,
                    eligible_for_recommendation=eligible,
                )
            )
        # Best-first. Stable sort keeps the avg-ΔeHP order alternatives_for_slot
        # returned among composite ties, matching the prior "first strictly-
        # greater wins" loop. A candidate only beats the baseline on a strict
        # composite gain (equipped keeps ties), exactly as before.
        candidates.sort(key=lambda p: -p.composite)
        ranked_by_slot[slot] = candidates
        # A swap is flagged only when the best candidate both wins on the
        # composite (slider-weighted) AND its survival gain clears the
        # meaningful-upgrade bar. Sub-bar candidates stay in ranked_by_slot
        # (Browse / upgrade panel show the full sweep) but don't mark the slot.
        # `eligible_for_recommendation=False` candidates are skipped for the
        # "swap now" pick (still visible in ranked_by_slot) — see that field's
        # docstring in models.py.
        best = next((c for c in candidates if c.eligible_for_recommendation), None)
        picks[slot] = best if _is_meaningful_swap(best, baseline, ehp_swap_threshold) else baseline
    _dedupe_paired_picks(picks, ranked_by_slot, baseline_by_slot)
    _flag_set_breaks(picks, equipped, char.class_spec)
    return picks


def _render_surv_slider() -> None:
    """Survivability ↔ DPS dial. Default biased toward survivability — these
    are tanks, the brief is 'keep me alive.' 100% = pure ΔeHP, 0% = pure ΔDPS.

    Streamlit's `format=` only controls the visible thumb label — the
    native `aria-valuenow` still announces the bare number (e.g. "70"),
    not "70% survivability". So put the unit in the *label* so SR users
    hear "Prioritize survivability, 70" when focusing the slider."""
    st.slider(
        "Prioritize survivability",
        min_value=0,
        max_value=100,
        value=_surv_weight(),
        step=5,
        key="surv_weight",
        format="%d%% survivability",
        # NOT %-formatted by Streamlit — `help=` is rendered as plain
        # markdown, unlike the slider's own `format=` param above. A literal
        # "%%" was rendering to the user as "100%%... 0%%... 70%%" (round-1
        # copy audit, 2026-07-05).
        help=(
            "100% picks the swap with the highest ΔeHP across your prog dungeons. "
            "0% picks the swap with the highest ΔDPS. Default 70% — tanks live or "
            "die on survivability first, throughput second."
        ),
    )
    # Bare tier name, no "Bias tier:" label prefix (mobile round 2,
    # 2026-07-26) — under a slider already labeled "Prioritize
    # survivability", a bold tier name reads self-evidently as the current
    # setting. The tier itself must stay (it's the whole point of this
    # caption: Brutoh's 2026-05-16 review asked for a plain-English feel
    # instead of a raw percentage — see `_surv_axis_label`'s docstring),
    # only the redundant label prefix goes.
    st.caption(f"**{_surv_axis_label(_surv_weight())}**")


def _render_recommendation_summary(picks: dict[str, _SlotPick]) -> None:
    # This tab and the Vault tab answer different questions from the same
    # gear — Vault scores only this week's 3 offers; this re-optimizes
    # everything already owned. Named explicitly so the two verdicts never
    # read as contradicting each other (reviewer round, 2026-07-04/05) —
    # see vault_panel.py's matching subtitle for the mirror framing.
    # "gems, enchants, rings, bag & vault pieces" used to omit the 4th
    # candidate source `_per_slot_picks` actually scores — M+ dungeon-loot
    # drops the player doesn't own yet — so a drop-sourced swap card read as
    # an owned-item swap with no textual hint otherwise (round-1 review,
    # 2026-07-05; each such card now also carries its own "M+ drop · not yet
    # owned" line — see item_html.py). Tightened 2026-07-26 (mobile round
    # 2, ui-craft-critic + copy-microcopy-editor review, then measured
    # in-place against the live element to confirm a real wrapped-line
    # drop rather than eyeballing it: 126 chars wrapped to 3 lines at
    # 390px, 96 chars wraps to 2) — same facts, "not yet owned" replaces
    # the old trailing "and loot table" as a more explicit not-yet-owned
    # signal.
    st.caption(
        "Re-optimizes owned gems, enchants, rings, bag, vault — plus "
        "prog-dungeon M+ drops not yet owned."
    )
    swaps = [p for p in picks.values() if p.is_swap]
    if not swaps:
        # Differentiate "alts exist, equipped wins" from "nothing to compare
        # against at all" — both end up here but the user's mental model is
        # different (the second wants to know they should paste a SimC with
        # bag contents, not that simf is endorsing their gear).
        total_alts = sum(p.n_alternatives for p in picks.values())
        if total_alts == 0:
            st.caption(
                "Nothing in your bag or vault to compare against. Paste a SimC export "
                "that includes bag contents — or wait for vault Tuesday — to see swap "
                "suggestions here."
            )
        else:
            pct = (
                float(
                    (load_constants().get("survivability_recommender") or {}).get(
                        "meaningful_upgrade_ehp_pct", 0.0
                    )
                )
                * 100
            )
            msg = "Your gear is optimized for survival — no swap is a meaningful upgrade"
            if pct > 0:
                msg += f" (nothing gains more than {pct:.1f}% eHP at this bias)"
            msg += ". Browse a slot or open the upgrade list to see every marginal sidegrade."
            st.caption(msg)
        return
    # Only sum ΔeHP simf can actually back up. Unmodeled proc/use trinkets
    # carry passive-stat ΔeHP (false precision) — counting them would inflate
    # the headline with the very numbers the per-slot chips deliberately
    # suppress. Flag the excluded count instead of silently dropping it.
    modeled = [p for p in swaps if p.ehp_modeled]
    unmodeled = [p for p in swaps if not p.ehp_modeled]
    total_ehp = sum(p.delta_ehp for p in modeled)
    total_dps = sum(p.delta_dps for p in modeled)
    n = len(swaps)
    headline = (
        f"{n} slot{'s' if n != 1 else ''} recommend a swap · total {_format_ehp_delta(total_ehp)}"
    )
    # Match the per-row chips: only stamp ΔDPS when it's a real number. A
    # "· ΔDPS ≈0" on a survivability tool is noise + fake precision.
    if abs(total_dps) >= 0.1:
        headline += f" · ΔDPS {'+' if total_dps >= 0 else ''}{total_dps:,.2f}"
    if unmodeled:
        k = len(unmodeled)
        headline += f" · {k} proc trinket{'s' if k != 1 else ''} need a real sim (not counted)"
    cols = st.columns([3, 1])
    with cols[0]:
        # Rendered as a styled <p>, not plain markdown bold (live-UI review,
        # 2026-07-18) — this IS the answer to "what should I swap," the most
        # decision-relevant number on the whole Gear tab, but a bare bold
        # paragraph renders at body-text size, quieter than the section
        # headers around it. `.gear-swap-headline` (css block in app.py)
        # gives it real visual weight without touching the primary
        # key-level-verdict <h2>'s size.
        st.markdown(f'<p class="gear-swap-headline">{headline}</p>', unsafe_allow_html=True)
    with cols[1]:
        if _is_read_only():
            from simf.ui.helpers.aria_button import aria_disabled_button

            aria_disabled_button(
                f"Trial all {n}",
                help="Read-only share — trial swaps disabled.",
                key="trial_all_recommendations",
            )
        else:
            st.button(
                f"Trial all {n}",
                key="trial_all_recommendations",
                width="stretch",
                help="Apply every recommended swap as a trial and re-run the verdict.",
                on_click=_apply_trial_all,
                args=(picks,),
            )

    # Tier-set caveat: any recommended swap that drops an active 2pc/4pc. The
    # ΔeHP doesn't model the set bonus, so the gold "+X eHP" headline can read
    # as a clean win when it's actually a net survival loss. Name it.
    set_breakers = [p for p in swaps if p.breaks_set]
    if set_breakers:
        names = ", ".join(sorted({str(p.breaks_set) for p in set_breakers}))
        k = len(set_breakers)
        st.caption(
            f"⚠ {k} of these break a tier-set bonus ({names}) not counted in the ΔeHP — "
            "keep the set unless the upgrade is large."
        )


# WoW paperdoll order — match the in-game character sheet so the user can
# pattern-match each slot in one glance. The left rail includes the cosmetic
# shirt + tabard slots (rendered as empty placeholder cards) so both rails are
# 8 tall and the panel has no orphan dead space — exactly the in-game / ui.png
# layout. shirt/tabard aren't in CANONICAL_SLOT_ORDER (no stats, no
# alternatives), so the grid synthesises empty rows for them via _card_for.
_PAPERDOLL_LEFT: list[str] = [
    "head",
    "neck",
    "shoulder",
    "back",
    "chest",
    "shirt",
    "tabard",
    "wrist",
]
_PAPERDOLL_RIGHT: list[str] = [
    "hands",
    "waist",
    "legs",
    "feet",
    "finger1",
    "finger2",
    "trinket1",
    "trinket2",
]
_PAPERDOLL_WEAPONS: list[str] = ["main_hand", "off_hand"]


def _render_paperdoll_grid(
    rows_by_slot: dict,
    picks: dict[str, _SlotPick],
    *,
    gem_rows_by_slot: dict[str, list[GemRow]] | None = None,
    enchant_rows_by_slot: dict[str, EnchantRow] | None = None,
    class_spec: str = "",
    marginals: dict | None = None,
    tier_status_by_item_id: dict[int, SetStatus] | None = None,
    eyebrow: str = "",
    # "Best in slot" was the sheet's title even when several of its own cards
    # were gold-edged swap recommendations — i.e. explicitly NOT best in
    # slot (ui-craft-critic review, round-1, 2026-07-05). The per-card
    # fallback text for a non-swap slot was "Best in slot" too at the time;
    # that per-card copy was itself renamed "Best you own" on 2026-07-28
    # (same "BiS means globally-best-in-game to this audience" concern,
    # applied to the card scope this header rename had deliberately left
    # alone) — see `item_html.py::_slot_card_html`.
    title: str = "Best available per slot",
) -> None:
    """Render the full 16-slot gear sheet as a dark card paperdoll — the
    universal WoW / Raidbots / QE Live character window (examples/screenshots/
    ui.png; user ask 2026-06-20 "follow raidbots or blizzard, don't reinvent").

    The whole panel — header lockup, two mirrored card columns, and the centred
    weapons row — is emitted as ONE ``st.markdown`` block scoped to the
    ``.gear-sheet-dark`` wrapper. Using a single block (not ``st.columns``) is
    load-bearing: Streamlit columns inject gap gutters that would split the
    dark slate into separate rectangles with cream seams between them. Display
    only, no per-slot widgets (the reference tools have no buttons on the
    sheet; browse / trial-swap lives off it). ``eyebrow`` is the spec name
    ("Protection Warrior"); the cards keep top-to-bottom slot order within each
    visual column via two ``.gear-col`` flex stacks inside the CSS grid.
    ``gem_rows_by_slot`` (absent → every card's gem line omitted, e.g. the
    stats-unresolved fallback render) is keyed by slot from
    ``build_gem_rows_by_slot``. ``enchant_rows_by_slot``, similarly, is keyed
    by slot from ``build_enchant_rows_by_slot`` (absent → every card's
    enchant line omitted). ``class_spec``/``marginals`` drive the swap
    card's per-stat composition + mechanism tags (empty/``None`` → no
    composition rendered, e.g. the stats-unresolved fallback render, where
    every pick is the non-swap baseline anyway). ``tier_status_by_item_id``
    (``optimizer.tier_sets.tier_status_by_item_id``) adds a per-card "N/5 Set
    Name" tag to every tier-set piece, so a card doesn't rely on the header
    strip's aggregate badge alone to say which pieces count (2026-07-28)."""

    def _card(slot: str, *, mirror: bool = False) -> str:
        row, pick = _card_for(slot, rows_by_slot, picks)
        return _slot_card_html(
            row,
            pick,
            mirror=mirror,
            gem_rows=(gem_rows_by_slot or {}).get(slot),
            enchant_row=(enchant_rows_by_slot or {}).get(slot),
            class_spec=class_spec,
            marginals=marginals,
            tier_status_by_item_id=tier_status_by_item_id,
        )

    left = "".join(_card(s) for s in _PAPERDOLL_LEFT)
    right = "".join(_card(s, mirror=True) for s in _PAPERDOLL_RIGHT)
    mh = _card("main_hand")
    oh = _card("off_hand", mirror=True)
    head = ""
    if eyebrow or title:
        head = (
            '<div class="gear-sheet-head">'
            + (f'<span class="gs-eyebrow">{eyebrow}</span>' if eyebrow else "")
            + (f'<span class="gs-title">{title}</span>' if title else "")
            + "</div>"
        )
    st.markdown(
        '<div class="gear-sheet-dark">'
        f"{head}"
        '<div class="gear-sheet-grid">'
        f'<div class="gear-col">{left}</div>'
        f'<div class="gear-col">{right}</div>'
        "</div>"
        f'<div class="gear-sheet-weapons">{mh}{oh}</div>'
        "</div>",
        unsafe_allow_html=True,
    )


def _render_gear_per_dungeon_breakdown(picks: dict[str, _SlotPick]) -> None:
    """Consolidated per-dungeon eHP breakdown for every slot with an
    actionable swap — closes a real, long-standing Vault/Gear parity gap
    (named 2026-07-05, closed 2026-07-27): Vault's per-offer cards and the
    slot-browse dialog both show a per-dungeon breakdown
    (``_render_per_dungeon_row``), but the Gear tab's own paperdoll cards
    never did.

    Rendered as its OWN section below the (unmodified) paperdoll rather
    than a per-card expander — checked with the user first, since the
    other real option (breaking the paperdoll's single-combined-HTML-block
    architecture to host a native per-card expander) carries real visual-
    regression risk `_render_paperdoll_grid`'s own docstring explains
    (``st.columns`` injects gap gutters that split the seamless dark
    slate). One ``st.expander`` per swap slot, NOT nested inside an outer
    expander — Streamlit doesn't support nesting expanders at all, so the
    grouping here is a plain section caption, not a collapsible wrapper.
    """
    swap_slots = [(slot, pick) for slot, pick in picks.items() if pick.is_swap and pick.per_dungeon]
    if not swap_slots:
        return
    st.caption("Per-dungeon breakdown for your swaps")
    for slot, pick in swap_slots:
        label = SLOT_LABELS.get(slot, slot)
        item_name = display_name(pick.item) if pick.item is not None else label
        with st.expander(f"{label}: {item_name}"):
            for s in pick.per_dungeon:
                _render_per_dungeon_row(s)

"""The gear-surface composer (L4).

Assembles the Gear tab body from the L3 render panels: the survivability
slider + recommendation summary + paperdoll grid + off-sheet slot-browse
control + gem suggestions + upgrade panel. Split out of app.py (2026-07)
so the entry module stays thin; imports only lower layers (state / widgets
/ marginals / recommend / upgrade_panel / slot_dialog) — never app. Named
``gear_surface`` to avoid confusion with the pre-existing data helper
``simf.ui.helpers.gear_list``.
"""

from __future__ import annotations

import streamlit as st

from simf.core.character import Character
from simf.io.spec_ids import class_spec_display_name
from simf.optimizer.tier_sets import tier_status_by_item_id
from simf.ui.helpers.enchant_panel import build_enchant_rows_by_slot, enchant_card_notes
from simf.ui.helpers.gear_list import CANONICAL_SLOT_ORDER, SLOT_LABELS, build_slot_rows
from simf.ui.helpers.gem_panel import build_gem_rows_by_slot, gem_card_notes
from simf.ui.helpers.stat_price_sheet import render_stat_price_sheet
from simf.ui.marginals import _marginals_for, _surv_marginals_fallback_warning
from simf.ui.models import _SlotPick
from simf.ui.recommend import (
    _per_slot_picks,
    _render_gear_per_dungeon_breakdown,
    _render_paperdoll_grid,
    _render_recommendation_summary,
    _render_surv_slider,
)
from simf.ui.slot_dialog import _slot_dialog
from simf.ui.state import (
    _equipped,
    _gear_stats_estimated,
    _is_read_only,
    _meaningful_upgrade_ehp_threshold,
    _stats_unresolved,
    _surv_weight,
)
from simf.ui.upgrade_panel import _render_upgrade_panel
from simf.ui.widgets import _render_unresolved_stats_banner


def _render_slot_browse_control(picks: dict[str, _SlotPick]) -> None:
    """Off-sheet access to a slot's alternatives + trial-swap.

    The card paperdoll itself is display-only (Raidbots / in-game keep the
    sheet button-free), so the per-slot dialog moves here: a slot picker +
    Browse button that opens the existing ``_slot_dialog``. Slots with a
    recommended swap are marked "↑" in the picker so the upgrades are easy to
    find. Hidden on a read-only share (trial-swaps are disabled there)."""
    if _is_read_only():
        return

    def _fmt(slot: str) -> str:
        if not slot:
            return "Pick a slot to browse alternatives or try a swap…"
        tag = " ↑" if (picks.get(slot) and picks[slot].is_swap) else ""
        return f"{SLOT_LABELS.get(slot, slot)}{tag}"

    cols = st.columns([3, 1], vertical_alignment="bottom")
    with cols[0]:
        sel = st.selectbox(
            "Browse alternatives / try a swap",
            options=["", *CANONICAL_SLOT_ORDER],
            format_func=_fmt,
            key="_slot_browse_select",
            help="Opens that slot's bag / vault / M+ alternatives so you can compare and trial a swap.",
        )
    with cols[1]:
        # Only render the (real) button once a slot is chosen — keeps the sheet
        # area button-free until the user opts in, and avoids a disabled button
        # in the a11y tree. on_click matches the prior per-slot dialog trigger.
        if sel:
            st.button(
                "Browse →",
                key="_slot_browse_btn",
                width="stretch",
                on_click=_slot_dialog,
                args=(sel,),
            )


def _render_gear_list(char: Character, dungeons: list[dict]) -> None:
    # Gear stats didn't resolve (online name-lookup on the public instance,
    # item DB offline → stamina=0 → near-zero eHP). Showing recommendations /
    # ΔeHP% off that produces nonsense like "+195% eHP" and a vers-only swap
    # sweep. Be honest instead: render the gear DISPLAY (item ids/ilvls/gems/
    # enchants came through fine) but suppress every survivability NUMBER.
    if _stats_unresolved(char, _equipped()):
        _render_unresolved_stats_banner()
        rows_by_slot = {r.slot: r for r in build_slot_rows(equipped=_equipped())}
        # Empty picks → the paperdoll renders every slot as a quiet display
        # card (no swap markers, no ΔeHP) via _card_for's baseline fallback.
        # Set membership is a structural fact (item ids), not a survival
        # number, so it's still shown even though the stats didn't resolve.
        _render_paperdoll_grid(
            rows_by_slot,
            {},
            tier_status_by_item_id=tier_status_by_item_id(_equipped(), char.class_spec),
            eyebrow=class_spec_display_name(char.class_spec),
        )
        return

    # This character's OWN stats (not just the marginals below) came from a
    # per-item Wowhead lookup estimate, not an exact export/log — every ΔeHP
    # on this tab inherits that extra uncertainty. The load-time toast saying
    # so is long gone by the time a reader is looking at a swap card; this
    # persistent caption is the fix (found 2026-07-10, chasing an implausible
    # composition-line number the user flagged on a real ring swap — the
    # sim-derived marginal for a stat-under-resolved character can differ
    # from a fresh recompute by several times, even flip a swap's sign).
    if _gear_stats_estimated():
        st.caption(
            "⚠ Your stats were estimated from item lookup, not read exactly from "
            "your export — every ΔeHP on this tab carries extra uncertainty on top "
            "of the usual model error, and can occasionally be off by a large "
            "margin, not just a few percent. For exact numbers, load a combat log "
            "or a Warcraft Logs link — those carry your worn stats directly."
        )

    # Collapsed by default — "how the engine prices your stats" — so the
    # sheet stays skimmable for a first-time visitor but a skeptical reader
    # can verify any swap's ΔeHP against the exact per-stat table it was
    # computed from. Placed above the swap chrome (not inside any
    # card-rendering loop) so it reads as reference material for the whole
    # tab, not a note on one particular card.
    render_stat_price_sheet(char)
    _render_surv_slider()
    surv_pct = float(_surv_weight())
    picks = _per_slot_picks(char, dungeons, surv_pct)
    _render_recommendation_summary(picks)
    # If the sim-derived stat weights fell back to the closed-form (which gives
    # haste/crit/mastery zero survival value), say so — otherwise the swaps
    # silently over-value versatility. Phase 2.7 surfaced this loudly on purpose.
    if _surv_marginals_fallback_warning(char):
        st.caption(
            "⚠ Stat weights are using a simplified estimate for this character — "
            "haste and mastery survival value may be under-counted in these swaps."
        )
    rows_by_slot = {r.slot: r for r in build_slot_rows(equipped=_equipped())}

    # Hoisted once so the card paperdoll's gem line and its honesty captions
    # below score off the identical marginals (session-cached, so this isn't
    # a second sim run — see _marginals_for).
    marginals = _marginals_for(char)
    # Same baseline-relative gate the item-swap paperdoll uses (was a
    # near-zero hardcoded epsilon here — a usability review caught gem/
    # enchant cards recommending swaps on deltas the tool's own stated
    # model error couldn't back up; see the helper's docstring).
    gem_enchant_epsilon = _meaningful_upgrade_ehp_threshold(char)
    gem_rows_by_slot = build_gem_rows_by_slot(
        _equipped(), marginals, char.class_spec, dungeons, optimal_epsilon=gem_enchant_epsilon
    )
    enchant_rows_by_slot = build_enchant_rows_by_slot(
        _equipped(), marginals, char.class_spec, dungeons, optimal_epsilon=gem_enchant_epsilon
    )
    tier_status = tier_status_by_item_id(_equipped(), char.class_spec)

    # Full 16-slot card paperdoll, always (the universal WoW / Raidbots /
    # Blizzard layout; user ask 2026-06-20 "follow raidbots or blizzard"). The
    # sheet is display only — quality-coloured cards on a dark character-sheet
    # panel, swap slots picked out by a gold edge + an inline "↑ +X eHP", and
    # socketed slots get a Wowhead-linked "💎 {gem name}" line (named + hoverable
    # for stats, not just a number — user ask 2026-07-01) plus a "✨ {enchant
    # name}" line on every enchantable slot. No buttons on the sheet; the
    # browse / trial-swap interaction lives in the control below + the
    # upgrade panel. The headline above carries the swap count; the panel
    # header carries the spec name (the in-game sheet title).
    _render_paperdoll_grid(
        rows_by_slot,
        picks,
        gem_rows_by_slot=gem_rows_by_slot,
        enchant_rows_by_slot=enchant_rows_by_slot,
        class_spec=char.class_spec,
        marginals=marginals,
        tier_status_by_item_id=tier_status,
        eyebrow=class_spec_display_name(char.class_spec),
    )
    _render_gear_per_dungeon_breakdown(picks)
    _render_slot_browse_control(picks)

    # The old consolidated "💎 Gems" text block is gone (user ask 2026-07-01 —
    # redundant now that each card names its own gem + hover-tooltips its
    # stats). What can't live on a card survives as a caption here: Guardian's
    # mastery/haste build-level notes, the Eversong meta-drop honesty note,
    # any per-recommendation caveat (Unique-meta / Elune's-Chosen-only), and
    # the enchant "not modeled" note when a head/shoulder/weapon enchant has
    # nothing the survival model can score yet.
    for note in gem_card_notes(char.class_spec, marginals, gem_rows_by_slot):
        st.caption(note)
    for note in enchant_card_notes(enchant_rows_by_slot):
        st.caption(note)

    st.markdown("---")
    _render_upgrade_panel(char, dungeons)

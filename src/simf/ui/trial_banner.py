"""simf UI — trial-swap banner + its pre-trial ΔeHP helper (L3).

The "Trial active · N slots" status banner (with per-slot Reset chips) and the
``_trial_delta_vs_baseline`` helper that scores a trialed item against the
parsed-from-SimC baseline gear. No ``st.*`` at module scope.

Lives at ``src/simf/ui/`` (same depth as app.py); imports only from the
L0/L1/L2 layers (``marginals``/``state``) + ``optimizer``. NEVER imports from
``app`` (strict L3 DAG).
"""

from __future__ import annotations

import html

import streamlit as st

from simf.core.character import Character
from simf.optimizer.per_dungeon import score_item_across_dungeons, trinket_swap_per_dungeon
from simf.ui.helpers.gear_list import SLOT_LABELS, display_name
from simf.ui.helpers.trial_swap import TrialState
from simf.ui.marginals import _marginals_for
from simf.ui.state import (
    _baseline_equipped,
    _format_ehp_delta,
    _is_read_only,
    _reset_trial_swaps,
    _revert_trial_slot,
    _stats_for_item,
    _trial_state,
)


def _trial_delta_vs_baseline(
    slot: str, item: object, marginals: dict, dungeons: list[dict], char: Character
) -> float:
    """Pre-trial ΔeHP — what this trial swap is worth vs the parsed-from-SimC
    gear (NOT vs the current trial set, which would tautologically be 0).

    Trinket slots route through the same on-use/proc-aware `trinket_db`
    registry the Gear-tab recommendation card uses (falling back to
    stats-only when the candidate isn't catalogued, same as the card does).
    Without this, a proc trinket the card recommends on its real proc value
    could show the opposite SIGN here — this is the exact swap the card just
    suggested, not an independently-browsed alternative, so the two numbers
    must agree (F-005, 2026-07-19)."""
    baseline_item = _baseline_equipped().get(slot)
    if slot in ("trinket1", "trinket2"):
        registry = trinket_swap_per_dungeon(baseline_item, item, char, dungeons)
        if registry is not None:
            reg_scores, _both_known = registry
            return sum(s.delta_ehp for s in reg_scores) / len(reg_scores) if reg_scores else 0.0
    new_stats = _stats_for_item(item)
    baseline_stats = _stats_for_item(baseline_item) if baseline_item else None
    scores = score_item_across_dungeons(
        new_stats=new_stats,
        equipped_stats=baseline_stats,
        marginals=marginals,
        dungeons=dungeons,
    )
    return sum(s.delta_ehp for s in scores) / len(scores) if scores else 0.0


def _partial_trial_summary(
    slot: str, trial: TrialState, marginals: dict, dungeons: list[dict], char: Character
) -> tuple[str, float]:
    """Caption + ΔeHP for a slot trialing an enchant and/or gem override with
    no full-item swap (those go through ``_trial_delta_vs_baseline`` instead,
    which diffs whole-item stats and is blind to a gem/enchant-only change —
    ``fetch_item_stats_for_spec`` keys purely on item_id/bonus_ids/crafted_
    stats, never gem_ids/enchant_id).

    Re-derives the row(s) against the pre-trial BASELINE, not ``_equipped()``
    (which already reflects this slot's own trial and would score it
    identity-optimal — 0 delta) and not other slots' trials (which don't
    change this slot's own recommendation). Matches by id rather than
    recomputing a value, so the number is exactly what the user saw on the
    "Trial this gem/enchant" button they clicked.
    """
    from simf.ui.helpers.enchant_panel import build_enchant_rows
    from simf.ui.helpers.gem_panel import build_gem_rows_all

    baseline = _baseline_equipped()
    parts: list[str] = []
    total = 0.0
    trialed_gem_ids = trial.gem_overrides.get(slot) or []
    if trialed_gem_ids:
        for row in build_gem_rows_all(baseline, marginals, char.class_spec, dungeons, slot=slot):
            if (
                row.best_gem_id is not None
                and row.index < len(trialed_gem_ids)
                and trialed_gem_ids[row.index] == row.best_gem_id
            ):
                parts.append(f"💎 {row.best_label}")
                total += row.delta_ehp
    trialed_enchant_id = trial.enchant_overrides.get(slot)
    if trialed_enchant_id is not None:
        for erow in build_enchant_rows(slot, baseline, marginals, char.class_spec, dungeons):
            if erow.best_enchant_id == trialed_enchant_id:
                parts.append(f"✨ {erow.best_label}")
                total += erow.delta_ehp
    return " · ".join(parts) or "gem/enchant trial", total


def _render_trial_banner(char: Character, dungeons: list[dict]) -> None:
    trial = _trial_state()
    if not trial.is_active:
        return

    marginals = _marginals_for(char)
    # `trial.swaps` alone misses enchant/gem-only trials — every affected
    # slot needs a row so the banner's slot count (and `st.columns` arg)
    # never goes to 0 while a trial is genuinely active.
    rows: list[tuple[str, str, float]] = []
    for slot in sorted(trial.affected_slots):
        if slot in trial.swaps:
            item = trial.swaps[slot]
            rows.append(
                (
                    slot,
                    html.escape(display_name(item), quote=False),
                    _trial_delta_vs_baseline(slot, item, marginals, dungeons, char),
                )
            )
        else:
            desc, delta = _partial_trial_summary(slot, trial, marginals, dungeons, char)
            rows.append((slot, desc, delta))

    summary_plain = " · ".join(
        f"{SLOT_LABELS.get(slot, slot)} → {desc} "
        f"({'+' if delta >= 0 else ''}{delta:,.0f} eHP vs equipped)"
        for slot, desc, delta in rows
    )

    n = len(rows)
    # The banner is the SR-announced status (role=status, aria-live=polite).
    # `aria-live` reacts to text-content mutations, NOT to aria-label changes
    # — so the dynamic summary has to live inside the element (in a
    # visually-hidden span). Without this, swapping slots produces no SR
    # announcement at all. Per-slot Reset chips render below it; each
    # chip's accessible name carries the slot label so screen-reader users
    # navigating by button list aren't left with context-free "Reset"s.
    #
    # "Trial active · N slot(s)" matches simf's specificity rule: anonymous
    # banners ("Trial active") undersell scope when the trial spans multiple
    # swaps. The count is rendered as a visible secondary chip so sighted
    # users see how many swaps are pending without reading the SR-only summary.
    slot_chip = f"{n} slot" + ("" if n == 1 else "s")
    st.markdown(
        f'<div class="trial-banner" role="status" aria-live="polite">'
        f"<strong>Trial active</strong>"
        f'<span class="trial-banner__count"> · {slot_chip}</span>'
        f'<span class="visually-hidden">: {summary_plain}</span>'
        f"</div>",
        unsafe_allow_html=True,
    )

    # Reset-all only when there are 2+ to make it worth its own button.
    col_count = n + (1 if n > 1 else 0)
    cols = st.columns(col_count)
    read_only = _is_read_only()
    for idx, (slot, desc, delta) in enumerate(rows):
        label = SLOT_LABELS.get(slot, slot)
        with cols[idx]:
            st.caption(f"{label} → **{desc}** · {_format_ehp_delta(delta)} vs equipped")
            if read_only:
                from simf.ui.helpers.aria_button import aria_disabled_button

                aria_disabled_button(
                    f"Reset {label}",
                    help="Read-only share — trial swaps disabled.",
                    key=f"trial_reset_{slot}",
                )
            elif st.button(
                f"Reset {label}",
                key=f"trial_reset_{slot}",
                width="stretch",
                help=f"Revert the {label} swap; the rest of the trial stays.",
            ):
                _revert_trial_slot(slot)
    if n > 1:
        with cols[-1]:
            st.caption(" ")  # vertical alignment with chip captions above
            if read_only:
                from simf.ui.helpers.aria_button import aria_disabled_button

                aria_disabled_button(
                    "Reset all",
                    help="Read-only share — trial swaps disabled.",
                    key="trial_reset",
                )
            elif st.button(
                "Reset all",
                key="trial_reset",
                width="stretch",
                help="Revert every trial swap and restore the gear from your SimC export.",
            ):
                _reset_trial_swaps()

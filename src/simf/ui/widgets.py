"""simf UI — small shared widgets (L2).

Self-contained render helpers reused across gear surfaces: the per-dungeon
ΔeHP row, the gem-suggestion section, the unresolved-stats honesty banner,
and the "Compare at item level" controls (shared radio + single-slot
variant). No ``st.*`` at module scope.

Lives at ``src/simf/ui/`` (same depth as app.py); imports only from the
L0/L1 foundation layer (``format_html``/``state``) and helpers. NEVER
imports from ``app`` (strict L2 DAG).
"""

from __future__ import annotations

import streamlit as st

from simf.ui.format_html import _school_mix_caption, _wowhead_dungeon_url
from simf.ui.helpers.gear_list import SLOT_LABELS
from simf.ui.helpers.stat_composition import composition_html
from simf.ui.helpers.upgrade_compare import (
    COMPARE_BASE_MODES,
    COMPARE_CUSTOM,
    resolve_target_ilvl,
)
from simf.ui.state import _apply_trial_enchant, _apply_trial_gem, _equipped, _is_read_only

_ZERO_TERM = {"p": 0.0, "m": 0.0, "blended": 0.0}


def _sum_terms(rows_terms) -> dict:
    """Sum (not average) several rows' composition terms — for the
    collapsed "re-gem N sockets" summary line, whose displayed ΔeHP is
    itself a sum across rows, not one row's own delta."""
    keys: set[str] = set()
    for t in rows_terms:
        keys.update(t)
    return {
        k: {
            field: sum(t.get(k, _ZERO_TERM)[field] for t in rows_terms)
            for field in ("p", "m", "blended")
        }
        for k in keys
    }


def _render_per_dungeon_row(s) -> None:
    sign = "+" if s.delta_ehp >= 0 else ""
    label = s.name or s.abbrev
    url = _wowhead_dungeon_url(label)
    mix = _school_mix_caption(getattr(s, "school_mix", {}) or {})
    line = f"[{label}]({url}) · {sign}{s.delta_ehp:,.0f} eHP"
    st.markdown(line, help=f"Damage mix: {mix}" if mix else None)


def _compare_mode_radio(key: str, *, include_custom: bool) -> str:
    """Shared "Compare at item level" radio — one control + mental model for
    both the Vault tab and the slot dialog. Returns the selected mode label.

    The Vault grid spans slots (different equipped ilvls + ceilings), so it
    offers the three semantic modes only; the single-slot dialog appends a
    free-form Custom slider.
    """
    options = list(COMPARE_BASE_MODES)
    if include_custom:
        options.append(COMPARE_CUSTOM)
    return st.radio(
        "Compare at item level",
        options=options,
        index=0,  # Match my gear — the apples-to-apples default
        horizontal=True,
        key=key,
        help=(
            "Vault and M+ pieces drop below your upgraded gear, so **as dropped** "
            "they all read as downgrades. **Match my gear** rescales every option to "
            "your equipped item level and **Max upgrade** to its highest reachable "
            "level — so you compare the stat spread, not the item-level gap."
        ),
    )


def _render_compare_control(slot: str, bounds, ilvl_per_level: int) -> int | None:
    """Single-slot "compare at item level" control for the slot dialog. Returns
    the target ilvl to normalize both sides to (None = as-dropped). Falls back
    to as-dropped silently when there's no ilvl to normalize against."""
    if not bounds.default_target:
        return None
    mode = _compare_mode_radio(f"_cmp_mode_{slot}", include_custom=True)
    custom = None
    if mode == COMPARE_CUSTOM and bounds.max_target > bounds.min_target:
        custom = int(
            st.slider(
                "Target item level",
                min_value=int(bounds.min_target),
                max_value=int(bounds.max_target),
                value=int(bounds.default_target),
                step=1,
                key=f"_cmp_ilvl_{slot}",
                help="Rescale your gear and every option to this item level.",
            )
        )
    target = resolve_target_ilvl(
        mode,
        equipped_ilvl=int(bounds.default_target),
        ceiling=int(bounds.max_target),
        custom_value=custom,
    )
    return int(target) if target else None


def _render_gem_suggestions(
    char, marginals: dict, dungeons: list[dict], *, slot: str | None = None
) -> None:
    """Best-survival-gem hint per socket.

    ``slot=None`` renders the consolidated Gear-tab "Gems" section across every
    socketed slot; ``slot="neck"`` (etc.) renders just that slot inside its
    dialog. No-op when there are no (matching) sockets. Scores candidate gems by
    survival ΔeHP against the character's sim-derived marginals (so haste only
    counts for an Elune's-Chosen Guardian, agility only for the agi tanks) and
    respects the Unique-Equipped Eversong Diamond meta character-wide.
    """
    from simf.ui.helpers.gem_panel import META_DROP_NOTE, build_gem_rows_all, gem_section_notes

    rows = build_gem_rows_all(_equipped(), marginals, char.class_spec, dungeons, slot=slot)
    if not rows:
        return

    if slot is None:
        st.markdown("#### 💎 Gems")
        st.caption(
            "Best **survival** gem per socket — scored on eHP only, so it ignores "
            "throughput/DPS. Re-gem in-game to apply."
        )
    else:
        st.markdown("**Gems** · best survival gem per socket (eHP only)")

    # Spec/build-level honesty notes (Guardian: mastery-not-modeled + the
    # Druid-of-the-Claw haste steering) — shown once, above the rows.
    for note in gem_section_notes(char.class_spec, marginals):
        st.caption(note)

    show_slot_label = slot is None
    actionable = [r for r in rows if not r.is_optimal and r.best_label]
    optimal = [r for r in rows if r.is_optimal]

    def _ehp_pct(total: float) -> str:
        # Rough "how much does this matter" context (Brutoh 2026-06-24): the eHP
        # gain as a fraction of the character's physical effective HP.
        try:
            base = char.effective_hp_physical()
        except Exception:
            base = 0.0
        return f" · ~{total / base * 100:.1f}% of your eHP" if base > 0 else ""

    def _meta_note(dropped: list) -> None:
        if any(r.current_is_meta for r in dropped):
            st.caption(META_DROP_NOTE)

    # Collapse the common all-same-recommendation case into one summary line so
    # four identical rows don't read as a stuck loop (Brutoh 2026-06-24).
    if len(actionable) >= 2 and len({r.best_label for r in actionable}) == 1:
        total = sum(r.delta_ehp for r in actionable)
        slots = ", ".join(SLOT_LABELS.get(r.slot, r.slot) for r in actionable)
        st.markdown(
            f"Re-gem {len(actionable)} sockets ({slots}) → **{actionable[0].best_label}**"
            f"  ·  **+{total:,.0f} eHP total**{_ehp_pct(total)}"
        )
        if actionable[0].best_stats_label:
            st.caption(actionable[0].best_stats_label)
        # Composition of the SUMMED total across every collapsed row (not one
        # row's own delta) — Σ over rows preserves the terms-sum-to-total
        # invariant just like a single row's terms do.
        summed_terms = _sum_terms([r.terms for r in actionable])
        comp = composition_html(summed_terms, char.class_spec, marginals)
        if comp:
            st.markdown(comp, unsafe_allow_html=True)
        _meta_note(actionable)
        for caveat in dict.fromkeys(c for r in actionable for c in r.caveats):
            st.caption(caveat)
    else:
        for r in actionable:
            prefix = f"{SLOT_LABELS.get(r.slot, r.slot)}: " if show_slot_label else ""
            st.markdown(f"{prefix}{r.current_label} → **{r.best_label}**  ·  **{r.delta_label}**")
            if r.best_stats_label:
                st.caption(r.best_stats_label)
            comp = composition_html(r.terms, char.class_spec, marginals)
            if comp:
                st.markdown(comp, unsafe_allow_html=True)
            _meta_note([r])
            for caveat in r.caveats:
                st.caption(caveat)
            # Runner-up only in the focused single-slot dialog (`slot` set) —
            # answers "why did the next-best option lose" without cluttering
            # the consolidated Gear-tab summary (2026-07-05 review).
            if slot is not None and r.runner_up_label:
                st.caption(f"Next best alternative: {r.runner_up_label}")
            _render_gem_trial_button(r, slot)

    for r in optimal:
        prefix = f"{SLOT_LABELS.get(r.slot, r.slot)}: " if show_slot_label else ""
        if r.is_identity_optimal:
            st.markdown(f"{prefix}🟢 {r.current_label} — already the best survival gem here.")
        else:
            # A real gap exists but sits below the swap bar — no verdict
            # word (2026-07-05 gem-trust review already killed the flat
            # "optimal" lie here; 2026-07-30 user direction killed "kept"
            # too: show the same numbers the actionable branch shows and
            # let the reader decide whether it's worth it).
            st.markdown(f"{prefix}{r.current_label} → **{r.best_label}**  ·  **{r.delta_label}**")
            if r.best_stats_label:
                st.caption(r.best_stats_label)
            comp = composition_html(r.terms, char.class_spec, marginals)
            if comp:
                st.markdown(comp, unsafe_allow_html=True)
            _meta_note([r])
            for caveat in r.caveats:
                st.caption(caveat)
            if slot is not None and r.runner_up_label:
                st.caption(f"Next best alternative: {r.runner_up_label}")
            _render_gem_trial_button(r, slot)


def _render_gem_trial_button(r, slot: str | None) -> None:
    """The gem-trial button — only in the focused single-slot dialog (mirrors
    the runner-up caption's scoping) and only for a real, non-identity-optimal
    candidate (callers already filter to rows with `best_label` set).
    Hidden on a read-only share, same as every other trial control.

    Called inline (not `on_click=`), matching `_apply_trial_swap`'s own
    calling convention — the mutator ends in `st.rerun()`, which is a no-op
    warning if invoked as an `on_click` callback (Streamlit already reruns
    after those) instead of forcing the immediate rerun this needs."""
    if slot is None or r.best_gem_id is None or _is_read_only():
        return
    if st.button(f"Trial this gem · {r.best_label}", key=f"_trial_gem_btn_{r.slot}_{r.index}"):
        _apply_trial_gem(r.slot, r.index, r.best_gem_id)


def _render_enchant_suggestions(char, marginals: dict, dungeons: list[dict], *, slot: str) -> None:
    """Best-survival-enchant hint for one slot, shown in its dialog.

    Same data source as the paperdoll card's "✨" line, scoped to the slot the
    user opened. Unlike gems, an item has at most one enchant — no multi-row
    collapse-into-summary logic needed, just the single row (or nothing, for
    a structural non-slot for this spec — a trinket, a shield off_hand; see
    ``enchant_suggester.enchant_slots_from_equipped``). back/wrist/neck and
    waist/hands still render, with an honest caption instead of a pick.
    """
    from simf.ui.helpers.enchant_panel import (
        NO_ENCHANT_EXISTS_CAPTION,
        build_enchant_rows,
    )

    rows = build_enchant_rows(slot, _equipped(), marginals, char.class_spec, dungeons)
    if not rows:
        return
    r = rows[0]

    st.markdown("**Enchant** · best survival option for this slot (eHP only)")
    if r.no_enchant_exists:
        st.markdown(NO_ENCHANT_EXISTS_CAPTION)
        return
    if not r.modeled:
        st.markdown(f"{r.current_label} — Avoidance/Leech/Speed/proc value isn't modeled here.")
        return
    if r.is_identity_optimal:
        st.markdown(f"🟢 {r.current_label} — already the best survival enchant here.")
        return
    # `r.is_optimal` True but not identity-optimal (the "optimal_grace" case,
    # a real gap below the swap bar) renders IDENTICALLY to an actionable
    # swap below — no "kept" verdict, just the current/best/delta numbers
    # (2026-07-30 user direction: let the reader decide, don't decide for
    # them). There's nothing left to special-case once the wording matches.
    st.markdown(f"{r.current_label} → **{r.best_label}**  ·  **{r.delta_label}**")
    if r.best_stats_label:
        st.caption(r.best_stats_label)
    comp = composition_html(r.terms, char.class_spec, marginals)
    if comp:
        st.markdown(comp, unsafe_allow_html=True)
    _render_enchant_trial_button(r, slot)


def _render_enchant_trial_button(r, slot: str) -> None:
    """The enchant-trial button. Only reached from the single-slot dialog
    (`_render_enchant_suggestions` always has a real `slot`, unlike the gem
    section's optional consolidated view) and only for a real,
    non-identity-optimal candidate. Hidden on a read-only share, same as
    every other trial control.

    Called inline, not `on_click=` — see `_render_gem_trial_button`'s
    docstring for why."""
    if r.best_enchant_id is None or _is_read_only():
        return
    if st.button(f"Trial this enchant · {r.best_label}", key=f"_trial_enchant_btn_{slot}"):
        _apply_trial_enchant(slot, r.best_enchant_id)


def _render_unresolved_stats_banner(*, concise: bool = False) -> None:
    """Honest 'we couldn't read your gear's stats' message — shown instead of
    fabricated survivability numbers when `_stats_unresolved` is True."""
    if concise:
        st.info(
            "Survivability stats aren't available for this online lookup — paste a "
            "**`/simc`** export (Change character) for the verdict."
        )
        return
    st.warning(
        "**Couldn't read your gear's stats.** This public site can't reach the item "
        "database to look up each piece, so survivability numbers — eHP, upgrade "
        "suggestions, and the key-level verdict — aren't available for an online name "
        "lookup here. Your gear is still shown below. For accurate numbers, load via a "
        "**SimulationCraft export**: in-game type **`/simc`**, copy the text, then "
        "**Change character → paste**."
    )

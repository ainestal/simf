"""simf UI — per-slot alternatives dialog (L3).

The ``@st.dialog`` modal that lists a slot's bag / vault / M+ alternatives,
their scaled ΔeHP / ΔDPS, tier-set break/complete warnings, the per-piece
upgrade curve, and the Pareto scatter. No ``st.*`` at module scope.

Lives at ``src/simf/ui/`` (same depth as app.py); imports only from the
L0/L1/L2 layers (``format_html``/``item_html``/``marginals``/``state``) +
``optimizer``/``io``/``helpers``. NEVER imports from ``app`` (strict L3 DAG).
"""

from __future__ import annotations

import streamlit as st

from simf.core.character import Character
from simf.core.constants import load_constants
from simf.io.dungeon_loot import items_for_slot, load_dungeon_loot
from simf.optimizer.alternatives import alternatives_for_slot, equivalent_slots
from simf.optimizer.item_upgrade import ehp_curve
from simf.optimizer.tier_sets import swap_breaks_threshold, swap_completes_threshold
from simf.ui.format_html import _trinket_scoring_caveat
from simf.ui.helpers.gear_list import SLOT_LABELS, display_name, format_item_stats
from simf.ui.helpers.stat_composition import composition_html
from simf.ui.helpers.upgrade_compare import (
    category_ceiling,
    compare_bounds,
    crest_cost_label,
    curve_ilvl_points,
)
from simf.ui.item_html import _humanize_source_label, _item_link_html
from simf.ui.marginals import _marginals_for
from simf.ui.state import (
    _apply_trial_swap,
    _equipped,
    _format_ehp_delta,
    _is_read_only,
    _is_two_handed_item,
    _selected_dungeons,
    _ss,
    _stats_for_item,
)
from simf.ui.widgets import (
    _render_compare_control,
    _render_enchant_suggestions,
    _render_gem_suggestions,
    _render_per_dungeon_row,
)


@st.dialog("Slot details")
def _slot_dialog(slot: str) -> None:
    # Slot is a parameter (not session state) so the dialog has no
    # sticky-open state that survives a rerun.
    cd = _ss().get("char_data") or {}
    try:
        char = Character.from_dict(cd)
    except Exception:
        st.error("No character loaded.")
        return

    marginals = _marginals_for(char)
    dungeons = _selected_dungeons()
    current = _equipped().get(slot)
    label = SLOT_LABELS.get(slot, slot)

    st.markdown(f"### {label}")
    is_trinket = slot in ("trinket1", "trinket2")
    if is_trinket:
        st.warning(_trinket_scoring_caveat())
    if current:
        ilvl = getattr(current, "ilvl", None)
        ilvl_str = f" · ilvl {ilvl}" if ilvl else ""
        st.markdown(
            f"Currently equipped: {_item_link_html(current)}{ilvl_str}",
            unsafe_allow_html=True,
        )
        # The equipped item shows at its TRUE ilvl — never rescaled (it's the
        # baseline; scaling it would invent stats). Inline static stats + the
        # focusable Wowhead-linked name above carry WCAG 2.1.1, so the
        # redundant item-details popover is gone (it also showed the same
        # numbers, reading as duplication).
        stats_line = format_item_stats(_stats_for_item(current))
        if stats_line:
            st.caption(stats_line)
    else:
        st.caption("(slot is empty)")

    # Per-socket gem suggestion for this slot's sockets (renders only when the
    # equipped piece has gems). Same data as the Gear-tab "Gems" summary, scoped
    # to the slot the user opened.
    _render_gem_suggestions(char, marginals, dungeons, slot=slot)
    # Same idea for the slot's enchant (renders only when the slot can carry
    # a permanent enchant in Midnight 12.0.5).
    _render_enchant_suggestions(char, marginals, dungeons, slot=slot)

    # Bring M+ dungeon loot in as an additional candidate source — the
    # user's prog-filter selection drives which dungeons contribute. Tag
    # each loot row with the dungeon abbrev ("m+ AA") so the alt-row
    # source line reads naturally alongside "bag" / "vault". The slot
    # dialog and the alternative ranker treat loot items the same as
    # bag/vault otherwise: same ΔeHP scorer, same Try button, same
    # trial-swap pipeline. Items the user already has equipped are
    # deduped by alternatives_for_slot.
    loot_table = load_dungeon_loot()
    id_to_abbrev = {d["id"]: d.get("abbrev") or d["id"] for d in dungeons}
    loot_pairs = items_for_slot(
        equivalent_slots(slot),
        [d["id"] for d in dungeons],
        loot=loot_table,
    )
    extra_sources = [(f"m+ {id_to_abbrev.get(d_id, d_id)}", item) for d_id, item in loot_pairs]

    bag_items = _ss().get("simc_bag_items") or {}
    vault_items = _ss().get("simc_vault_items") or {}

    # ── "Compare at item level" control ──────────────────────────────────────
    # The Tuesday-vault problem: a maxed player's vault picks all sit below
    # their upgraded equipped ilvl, so as-dropped every option reads as a
    # downgrade and the comparison teaches nothing. Default to rescaling both
    # sides to the equipped piece's ilvl so the player compares itemization, not
    # the ilvl gap. Reachable ceiling = per-category season cap (constants),
    # further clamped by the player's own account watermark from their export.
    gear_cfg = load_constants().get("gear", {})
    ilvl_per_level = int(gear_cfg.get("ilvl_per_upgrade_level", 3)) or 3
    equipped_ilvl = getattr(current, "ilvl", None) if current else None
    cand_ilvls = [
        int(getattr(it, "ilvl", 0) or 0)
        for src in (bag_items, vault_items)
        for s in equivalent_slots(slot)
        for it in src.get(s, [])
        if getattr(it, "ilvl", 0)
    ]
    cand_ilvls += [
        int(getattr(it, "ilvl", 0) or 0) for _lbl, it in loot_pairs if getattr(it, "ilvl", 0)
    ]
    slot_ceiling = category_ceiling(
        slot,
        gear_cfg.get("ilvl_ceiling_by_category"),
        account_ceiling=_ss().get("simc_account_ilvl_ceiling"),
    )
    bounds = compare_bounds(equipped_ilvl, cand_ilvls, ceiling_ilvl=slot_ceiling)
    target_ilvl = _render_compare_control(slot, bounds, ilvl_per_level)

    alts = alternatives_for_slot(
        slot=slot,
        equipped=_equipped(),
        bag=bag_items,
        vault=vault_items,
        marginals=marginals,
        dungeons=dungeons,
        item_stats_fn=_stats_for_item,
        extra_sources=extra_sources,
        dps_weights=load_constants().get("dps_stat_weights", {}),
        target_ilvl=target_ilvl,
        exclude_two_handed=char.uses_shield,
        item_is_two_handed_fn=_is_two_handed_item,
    )
    if not alts:
        st.info(
            "Nothing in your bag, vault, or your prog-filtered M+ dungeons to swap into this slot."
        )
        return

    # Bucket alternatives into upgrades vs strict-worse before rendering.
    # The dialog used to render a strict-worse swap (e.g. "Try Night Ender's
    # Tusks · ΔeHP -14,448") as a full-width primary CTA — the same
    # affordance as a real upgrade. That gave Brutoh a too-clickable
    # negative on a ✓ best-in-slot Helm (ui-critic #8, 2026-05-16).
    # Positives keep the primary CTA; negatives drop under a "Worse — for
    # reference" header and render as secondary (Streamlit's outlined
    # variant, the ghost-button affordance the brief asked for).
    positives = [a for a in alts if a.avg_delta_ehp >= 0]
    negatives = [a for a in alts if a.avg_delta_ehp < 0]

    # Per-row reminder of which equipped piece a swap takes out. The dialog
    # header names the slot and "Currently equipped" shows it once up top, but
    # Brutoh (2026-05-31) asked for it on every option: "it says what the item
    # is, but it doesn't say which one to be replaced." For paired ring/trinket
    # slots this also disambiguates *which* of the two the candidate displaces
    # — the one whose dialog you opened.
    if current is not None:
        _cur_ilvl = getattr(current, "ilvl", None)
        _cur_ilvl_str = f" · i{_cur_ilvl}" if _cur_ilvl else ""
        replaces_caption = f"↳ replaces your {label}: {display_name(current)}{_cur_ilvl_str}"
    else:
        replaces_caption = f"↳ fills your empty {label}"

    def _render_alt_row(idx: int, a, *, is_worse: bool) -> None:
        name = display_name(a.item)
        base_ilvl = getattr(a.item, "ilvl", None)
        shown = a.shown_at_ilvl
        # "Normalized-up": the candidate was rescaled above its drop ilvl to
        # match the comparison target. Show both ilvls so the player knows the
        # number assumes an upgrade investment, not the item as it'd drop.
        upgraded_view = bool(shown and base_ilvl and shown > base_ilvl)
        if upgraded_view:
            ilvl_str = f" · ilvl {shown} (up from {base_ilvl})"
        elif shown and base_ilvl:
            ilvl_str = f" · ilvl {shown}"
        elif base_ilvl:
            ilvl_str = f" · ilvl {base_ilvl}"
        else:
            ilvl_str = ""
        with st.container():
            cols = st.columns([3, 1])
            with cols[0]:
                st.markdown(
                    f"{_item_link_html(a.item)}{ilvl_str}  ·  _{_humanize_source_label(a.source)}_",
                    unsafe_allow_html=True,
                )
                st.caption(replaces_caption)
                # Show the SCALED stats (a.new_stats is normalized to
                # shown_at_ilvl when a target is set, else as-dropped) so the
                # stat line matches the ilvl label + the ΔeHP. Inline static
                # text + the focusable Wowhead-linked name carry WCAG 2.1.1;
                # the old item-details popover is removed (it re-fetched
                # UNSCALED stats — a second, contradictory number on the row).
                stats_line = format_item_stats(a.new_stats)
                if stats_line:
                    st.caption(stats_line)
                # Composition — same shared renderer every gem/enchant/gear-
                # swap card uses (`stat_composition.py`), so this row's ΔeHP
                # is explained the same way as everywhere else it appears.
                # No trinket-registry branch needed here: unlike the
                # cross-slot ranker / Vault tab, this dialog's alternatives
                # never route through the trinket_db registry (see
                # `optimizer/alternatives.py`) — `a.terms` is always a real
                # stat-marginal composition, even for a trinket row (the
                # `_trinket_scoring_caveat()` warning above already covers
                # the "may not reflect the special effect" caveat).
                comp_html = composition_html(a.terms, char.class_spec, marginals)
                if comp_html:
                    st.markdown(comp_html, unsafe_allow_html=True)
            with cols[1]:
                # ΔDPS uses the same ±0.1 rounding rule as the vault grid
                # — anything smaller is false precision against survival's
                # hundreds-of-eHP scale.
                if abs(a.delta_dps) < 0.1:
                    dps_text = "≈0"
                else:
                    dps_sign = "+" if a.delta_dps >= 0 else ""
                    dps_text = f"{dps_sign}{a.delta_dps:,.2f}"
                st.markdown(f"**{_format_ehp_delta(a.avg_delta_ehp)}**  ·  **ΔDPS {dps_text}**")
            # Tier-set warning: if applying this swap drops the user
            # below an active 2pc/4pc threshold, surface it inline
            # before they click Try. Brutoh's "next single thing" ask.
            char_spec = (_ss().get("char_data") or {}).get("class_spec", "")
            broken = (
                swap_breaks_threshold(_equipped(), char_spec, slot, a.item) if char_spec else None
            )
            if broken is not None:
                st.caption(
                    f"⚠️ Breaks {broken.active_threshold}pc **{broken.set.name}** "
                    f"({broken.pieces}/5 → {broken.pieces - 1}/5)."
                )
            # Inverse of the break warning: a vault tier piece that *completes*
            # a 2pc/4pc is undersold by the stats-only ΔeHP — the set bonus
            # dwarfs the secondary delta. Surface the gain so normalization
            # doesn't make the player reject a real upgrade.
            gained = (
                swap_completes_threshold(_equipped(), char_spec, slot, a.item)
                if char_spec
                else None
            )
            if gained is not None:
                st.caption(
                    f"✓ Completes {gained.active_threshold}pc **{gained.set.name}** "
                    f"({gained.pieces}/5) — set bonus isn't counted in the ΔeHP above."
                )
            # Upgrade-normalized rows: show the (approximate) crest cost and the
            # honest caveat that reaching this ilvl depends on the piece's own
            # upgrade track — simf can't read the candidate's track ceiling.
            if upgraded_view:
                cost = crest_cost_label(base_ilvl, shown, ilvl_per_level)
                st.caption(
                    f"⤴ {cost} · assumes this piece's track reaches {shown} — verify in-game."
                )
            # Positives → primary CTA. Strict-worse alts → secondary
            # (outlined "ghost") so the affordance carries the warning, plus
            # a reworded label/help (mirrors the vault cell's is_downgrade
            # treatment) so a net-loss row never reads like a recommendation.
            btn_type = "secondary" if is_worse else "primary"
            btn_label = f"Trial anyway · {name}" if is_worse else f"Try {name}"
            btn_help = (
                "This is a net loss vs your equipped gear — trial it anyway to see the "
                "per-dungeon breakdown."
                if is_worse
                else f"Trial-swap {name} into your {label} and re-run the verdict."
            )
            if _is_read_only():
                from simf.ui.helpers.aria_button import aria_disabled_button

                aria_disabled_button(
                    btn_label,
                    help="Read-only share — trial swaps disabled.",
                    key=f"try_alt_{slot}_{idx}",
                )
            elif st.button(
                btn_label,
                key=f"try_alt_{slot}_{idx}",
                width="stretch",
                type=btn_type,
                help=btn_help,
            ):
                _apply_trial_swap(slot, a.item)
            with st.expander("Per-dungeon breakdown"):
                for s in a.per_dungeon:
                    _render_per_dungeon_row(s)

    if positives:
        st.markdown("**Alternatives** (best ΔeHP first)")
        for idx, a in enumerate(positives):
            _render_alt_row(idx, a, is_worse=False)

    if negatives:
        # Strict-worse bucket sits below upgrades. Header sets the
        # expectation before the eye reads each row's ΔeHP. Indexing
        # continues from `len(positives)` so widget keys stay unique.
        st.markdown("**Worse — for reference:**")
        for offset, a in enumerate(negatives):
            _render_alt_row(len(positives) + offset, a, is_worse=True)

    # ── Per-piece upgrade curves ─────────────────────────────────────────────
    # "What's the impact of crests on this piece?" — ΔeHP vs the currently
    # equipped piece across the reachable ilvl range, one line per top
    # candidate. The zero-crossing is where the candidate overtakes current
    # gear. Default-closed; only built when there's a ceiling to climb toward.
    if slot_ceiling and current is not None:
        equipped_stats_raw = _stats_for_item(current)
        curve_candidates = (positives or negatives)[:4]
        curves: list[tuple[str, list]] = []
        for a in curve_candidates:
            base = getattr(a.item, "ilvl", None)
            cstats = _stats_for_item(a.item)
            if not base or not cstats:
                continue
            pts = curve_ilvl_points(int(base), max(int(slot_ceiling), int(base)), ilvl_per_level)
            if len(pts) < 2:
                continue
            cpoints = ehp_curve(
                cstats,
                int(base),
                pts,
                marginals,
                dungeons,
                baseline_stats=equipped_stats_raw,
                ceiling_ilvl=slot_ceiling,
            )
            if cpoints:
                curves.append((display_name(a.item), cpoints))
        if curves:
            with st.expander("Upgrade curve — ΔeHP vs your gear as you spend crests"):
                from simf.ui.helpers.upgrade_curve_chart import render_upgrade_curves

                note = (
                    f"Each line is one option's survivability vs your current {label} as it's "
                    f"upgraded toward ilvl {slot_ceiling}. Crossing the dashed line means it "
                    f"overtakes what you wear now. Crest cost is approximate "
                    f"(≈{ilvl_per_level} ilvl per upgrade level)."
                )
                render_upgrade_curves(curves, crest_note=note)

    # Phase 6.4 — Pareto scatter over the same alternatives list.
    # Default-closed expander; opening shows ΔDPS vs ΔeHP with the
    # Pareto frontier highlighted. Helps power users spot the swaps
    # that dominate on both axes without scanning rows.
    from simf.ui.helpers.pareto_scatter import render_pareto_scatter

    render_pareto_scatter(alts, slot_label=SLOT_LABELS.get(slot, slot))

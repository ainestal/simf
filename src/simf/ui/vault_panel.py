"""simf UI — Great Vault verdict + 3-up grid (L3).

The Tuesday-vault surface: the verdict card (best pick / no-upgrade / dead /
unresolved-stats headlines), the per-pick cell, the 3-up grid, and the panel
that ranks the week's offers and wires it all together. No ``st.*`` at module
scope.

Lives at ``src/simf/ui/`` (same depth as app.py); imports only from the
L0/L1/L2 layers (``format_html``/``item_html``/``marginals``/``state``/
``widgets``) + ``optimizer``/``helpers``. NEVER imports from ``app`` (strict
L3 DAG).
"""

from __future__ import annotations

import html

import streamlit as st

from simf.core.character import Character
from simf.core.constants import load_constants
from simf.optimizer.vault_ranking import (
    dead_count_sentence,
    headline_for_winner,
    rank_vault_items,
)
from simf.ui.format_html import _filter_qualifier
from simf.ui.helpers.ehp_gloss import ehp_gloss_core_text, ehp_gloss_exclusion_text
from simf.ui.helpers.gear_list import SLOT_LABELS, display_name, format_item_stats
from simf.ui.helpers.stat_composition import composition_html, trinket_registry_html
from simf.ui.helpers.upgrade_compare import category_ceiling, resolve_target_ilvl
from simf.ui.item_html import _item_link_html
from simf.ui.marginals import _marginals_for
from simf.ui.state import (
    _apply_trial_swap,
    _baseline_equipped,
    _dungeon_catalog,
    _equipped,
    _format_ehp_delta,
    _high_residual_warning,
    _is_read_only,
    _ss,
    _stats_for_item,
    _stats_unresolved,
    _trial_state,
    _vault,
)
from simf.ui.widgets import (
    _compare_mode_radio,
    _render_per_dungeon_row,
    _render_unresolved_stats_banner,
)

# This tab and the Gear tab answer genuinely different questions from the
# same gear — Vault scores only THIS WEEK's 3 offers (rescaled to your
# equipped ilvl); Gear re-optimizes everything you already own (gems,
# enchants, rings, bag & vault pieces). A reviewer round (2026-07-04/05)
# found that without saying so, the two tabs read as contradicting each
# other even after the underlying math was reconciled — "no vault upgrade"
# next to "+128,056 eHP available" one tab over looks like a bug, not two
# honest, differently-scoped answers. Name the scope explicitly instead of
# unifying the two numbers into one (that would hide real information).
_SCOPE_SUBTITLE = "Scores this week's 3 offers, rescaled to your gear."
# "eHP" is the load-bearing term in every verdict sentence below (and on the
# Gear tab) but was never defined in plain English anywhere a first-time
# visitor would see it before reading it (round-1 copy audit, 2026-07-05).
# Vault is the tab that loads first whenever a vault exists (see app.py's
# tab-ordering comment), making its scope caption the single earliest,
# lowest-risk place to gloss it once rather than touching all ~140 call sites.
# Calls the shared canonical definition (`ehp_gloss.ehp_gloss_core_text`),
# passed THIS character's own `class_spec` (2026-07-09) — the earlier plain
# `EHP_GLOSS` constant hardcoded Protection Warrior's own always-on passive
# ("Defensive Stance") and excluded cooldowns ("Shield Block/Shield Wall")
# for every spec that read this caption, which is exactly the drift risk
# `ehp_gloss.py`'s own docstring flagged as an open follow-up.
#
# Only the CORE definition renders eagerly (2026-07-26, mobile round 2) —
# the "what it leaves out" caveat moves into the popover below. Measured
# live at 390px: the old single concatenated caption cost 179px of pure
# chrome before ANY vault-offer content, on the one tab that loads first
# for a visitor with a vault. A ui-craft-critic + copy-microcopy-editor
# review round explicitly rejected collapsing the CORE definition itself
# (that's the exact "first-time visitor never sees it" problem the
# 2026-07-05 fix solved) but found the exclusion caveat is second-read
# "why is my number lower than expected" reassurance, not something a
# first read needs — the same click-to-disclose treatment this app
# already gives "Calibration details".
_GEAR_TAB_BRIDGE = (
    "The Gear tab re-optimizes everything you already own — gems, enchants, "
    "rings, bag & vault pieces — and may still find something worth doing."
)


def _render_vault_panel(char: Character, dungeons: list[dict]) -> None:
    vault = _vault()
    if not vault:
        st.info(
            "No weekly vault choices in this export. If your vault is unlocked, "
            "re-run `/simc` in WoW this week and paste the new export — the addon "
            "prints a **Weekly Reward Choices** block when you have picks waiting."
        )
        return

    # Stats didn't resolve (online name-lookup on the public instance) → every
    # vault ΔeHP would be computed off a near-zero eHP pool. Same honesty gate
    # as the gear sheet: say so instead of ranking vault picks on garbage.
    if _stats_unresolved(char, _equipped()):
        _render_unresolved_stats_banner(concise=True)
        return

    marginals = _marginals_for(char)
    constants = load_constants()
    dps_weights = constants.get("dps_stat_weights", {})
    gear_cfg = constants.get("gear", {})
    full_catalog = _dungeon_catalog()
    selected_ids = [d["id"] for d in dungeons]
    equipped = _equipped()

    # "Compare at item level" — same control + default ("Match my gear") as the
    # slot dialog, so the two surfaces share one mental model. A maxed player's
    # vault picks all sit below their equipped ilvl, so AS-DROPPED every choice
    # reads as a downgrade ("No vault upgrade"). Match-my-gear / Max-upgrade
    # rescale each pick to its slot's equipped ilvl (or reachable ceiling) so the
    # verdict, the ΔeHP, and the shown stats all reflect itemization, not the
    # item-level gap. The grid spans slots, so per-slot targets via a closure.
    mode = _compare_mode_radio("_vault_cmp_mode", include_custom=False)
    ceilings_cfg = gear_cfg.get("ilvl_ceiling_by_category")
    account_ceiling = _ss().get("simc_account_ilvl_ceiling")

    def _ceiling_for_slot(slot: str) -> int | None:
        return category_ceiling(slot, ceilings_cfg, account_ceiling=account_ceiling)

    def _target_for_slot(slot: str) -> int | None:
        eq = equipped.get(slot)
        return resolve_target_ilvl(
            mode,
            equipped_ilvl=getattr(eq, "ilvl", None) if eq else None,
            ceiling=_ceiling_for_slot(slot),
        )

    rows = rank_vault_items(
        vault_items=vault,
        equipped=equipped,
        marginals=marginals,
        dungeons=full_catalog,
        item_stats_fn=_stats_for_item,
        dps_weights=dps_weights,
        selected_dungeon_ids=selected_ids,
        target_ilvl_fn=_target_for_slot,
        # Ownership (dead-pick / ceiling-upgrade detection) reads the
        # parsed-from-SimC baseline, NOT the trial overlay — trialing a
        # vault offer must not make the offer itself look "owned" and flip
        # its own badges mid-trial.
        bag=_ss().get("simc_bag_items") or {},
        owned_equipped=_baseline_equipped(),
        ceiling_fn=_ceiling_for_slot,
        char=char,
    )
    if not rows:
        st.warning("Vault items detected but simf couldn't resolve their stats yet.")
        return

    st.caption(f"{_SCOPE_SUBTITLE} {ehp_gloss_core_text(char.class_spec)}")
    with st.popover("ⓘ What eHP leaves out", key="vault-ehp-exclusion-popover"):
        st.caption(ehp_gloss_exclusion_text(char.class_spec))
    # Dead picks (owned at the offer's reachable ceiling) never win the
    # headline — the best live row does. None = every offer is dead.
    winner = next((r for r in rows if not r.dead), None)
    trial_active = _trial_state().is_active
    _render_verdict_card(
        winner,
        trial_active=trial_active,
        dead_note=dead_count_sentence(rows),
    )
    warn = _high_residual_warning()
    if warn:
        st.caption(warn)
    # The ★ "Recommended pick" mark must never contradict the headline above
    # it. `winner` is the best NON-DEAD row even when every offer is a net
    # loss (picked purely to name the least-bad option in the dead-count
    # sentence) — in that case the verdict headline reads "No vault upgrade
    # this week," so starring `winner` anyway told the user two opposite
    # things in the same glance (ui-craft-critic review, 2026-07-04). Only
    # star it when the verdict card actually calls it a good pick: a real
    # gain, or the trial-holds case where the headline says so explicitly.
    star_winner = (
        winner if winner is not None and (winner.avg_delta_ehp > 0 or trial_active) else None
    )
    _render_vault_grid(rows, winner=star_winner, class_spec=char.class_spec, marginals=marginals)


def _render_verdict_card(winner, trial_active: bool, dead_note: str = "") -> None:
    # Verdict is the surface's primary outcome — render it as a semantic
    # <section> with role="region" + aria-labelledby so SR users landing
    # on the page via landmark navigation can jump straight to the verdict
    # and hear the headline as the region's accessible name. The <h2>
    # already sits one level under the page <h1> "simf".
    if winner is None:
        # Every offer this week duplicates an item the player already owns
        # at its reachable ceiling — nothing is worth taking. Same headline
        # as the no-positive week; the dead-count sentence says why. This is
        # GOOD news (your gear already wins) — `verdict-neutral`, not
        # `verdict-warn`'s bronze bar, which reads as a problem (live-UI
        # review, 2026-07-18: painting reassurance as a warning erodes trust
        # on a tool whose whole point is being honest about what's actionable).
        st.markdown(
            f'<section class="verdict-neutral" role="region" '
            f'aria-labelledby="verdict-head">'
            f'<h2 id="verdict-head">No vault upgrade this week.</h2>'
            f"<p>{dead_note} The offers below stay visible for transparency. "
            f"{_GEAR_TAB_BRIDGE}</p>"
            f"</section>",
            unsafe_allow_html=True,
        )
        return
    stats_resolved = bool(winner.new_stats)
    if not stats_resolved:
        # Was: "Update SimulationCraft to a version that emits
        # gear_haste_rating= lines, or add Blizzard API credentials" — a
        # dev fix-it path, not something a visitor pasting a `/simc` export
        # can act on (Blizzard creds are an owner/self-host setting), and a
        # third, differently-worded sibling of this same "couldn't read
        # stats" family existed alongside widgets.py's version (round-1
        # copy audit, 2026-07-05). Mirror that sibling's actionable text.
        # (That third sibling was `simc_load.py`'s SimcLoadError — the audit
        # apparently missed it; it still had the owner-only wording until
        # 2026-08-22.)
        st.markdown(
            '<section class="verdict-warn" role="region" '
            'aria-labelledby="verdict-head">'
            "<h2 id=\"verdict-head\">simf can't read this item's stats yet.</h2>"
            "<p>Vault rows below show what was parsed. Try pasting a fresh "
            "<code>/simc</code> export — in-game type <code>/simc</code>, copy the "
            "result, then <strong>Change character → paste</strong>.</p>"
            "</section>",
            unsafe_allow_html=True,
        )
        return
    qualifier = _filter_qualifier(winner)
    weakest = getattr(winner, "weakest_line", "") or ""
    weakest_html = f' <span class="verdict-weakest">{weakest}</span>' if weakest else ""
    coverage = getattr(winner, "coverage_line", "") or ""
    coverage_html = f'<p class="verdict-coverage">{coverage}</p>' if coverage else ""
    dead_html = f" {dead_note}" if dead_note else ""
    if winner.avg_delta_ehp > 0:
        st.markdown(
            f'<section class="verdict-best" role="region" '
            f'aria-labelledby="verdict-head">'
            f'<h2 id="verdict-head">{headline_for_winner(winner)}</h2>'
            f"<p>{winner.verdict_sentence}{weakest_html}{qualifier}{dead_html}</p>"
            f"{coverage_html}"
            f"</section>",
            unsafe_allow_html=True,
        )
        return
    if trial_active:
        st.markdown(
            f'<section class="verdict-best" role="region" '
            f'aria-labelledby="verdict-head">'
            f'<h2 id="verdict-head">Your trial holds — '
            f"{html.escape(display_name(winner.item), quote=False)} is the strongest vault pick.</h2>"
            f"<p>No remaining vault choice beats the current trial set. "
            f"Reset the trial in the banner above to compare against your "
            f"original gear.{qualifier}{dead_html}</p>"
            f"</section>",
            unsafe_allow_html=True,
        )
        return
    st.markdown(
        # verdict-neutral, not verdict-warn — "your gear already wins" is
        # good news, not a warning (live-UI review, 2026-07-18).
        f'<section class="verdict-neutral" role="region" '
        f'aria-labelledby="verdict-head">'
        f'<h2 id="verdict-head">No vault upgrade this week.</h2>'
        f"<p>Your equipped gear beats every vault choice on average ΔeHP. "
        f"The per-dungeon breakdowns below may still flag a swap that wins "
        f"a specific key.{qualifier}{dead_html} {_GEAR_TAB_BRIDGE}</p>"
        f"</section>",
        unsafe_allow_html=True,
    )


def _render_vault_cell(
    r, is_winner: bool, key_suffix: str, class_spec: str = "", marginals: dict | None = None
) -> None:
    slot_label = SLOT_LABELS.get(r.slot, r.slot)
    base_ilvl = getattr(r.item, "ilvl", None)
    shown = r.shown_at_ilvl
    # When the row is upgrade-normalized above its drop ilvl, name the target
    # AND the drop level so the player knows the stats + ΔeHP below assume an
    # upgrade investment, not the piece as it dropped.
    upgraded_view = bool(shown and base_ilvl and shown > base_ilvl)
    if upgraded_view:
        ilvl_str = f" · ilvl {shown} (up from {base_ilvl})"
    elif shown and base_ilvl:
        ilvl_str = f" · ilvl {shown}"
    elif base_ilvl:
        ilvl_str = f" · ilvl {base_ilvl}"
    else:
        ilvl_str = ""
    # The ★ is a purely-visual cue — without an SR-only label it reads as
    # "black star" (or is dropped entirely) and the "this is the winner"
    # semantic is lost (WCAG 1.3.1 / 1.4.1: color/glyph alone isn't enough).
    # Pair the glyph (aria-hidden) with hidden text that names the meaning.
    winner_mark = (
        ' <span class="visually-hidden">Recommended pick.</span><span aria-hidden="true"> ★</span>'
        if is_winner
        else ""
    )
    dead = bool(getattr(r, "dead", False))
    cell_class = "vault-cell"
    if is_winner:
        cell_class += " vault-cell-winner"
    if dead:
        cell_class += " vault-cell-dead"
    st.markdown(
        f'<div class="{cell_class}">'
        f'<div class="vault-cell-slot">{slot_label}{winner_mark}</div>'
        f"<div>{_item_link_html(r.item)}{ilvl_str}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    # Track-aware ownership badges. Dead: the player owns this item_id at
    # (or above) everything this offer's track can reach — taking it buys
    # nothing. Ceiling upgrade: owned below the offer's reachable ceiling —
    # we can't read the owned copy's own track cap without a bonus-id rank
    # table, so the callout states only what's known.
    if dead:
        st.caption(
            f"You own this at i{r.owned_max_ilvl} — already at this offer's ceiling. Dead pick."
        )
    elif getattr(r, "ceiling_upgrade", False):
        st.caption(
            f"Ceiling upgrade: you own this at i{r.owned_max_ilvl} — "
            f"this offer reaches i{r.offer_ceiling}."
        )
    # Paired ring/trinket offers: name which equipped piece the most
    # favorable swap takes out — same voice as the slot dialog (PR #131).
    # Suppressed on dead rows: telling the player what a dead pick would
    # replace is a mixed message (it shouldn't be picked at all).
    replaces_slot = getattr(r, "replaces_slot", None)
    if replaces_slot and not getattr(r, "dead", False):
        rep_label = SLOT_LABELS.get(replaces_slot, replaces_slot)
        incumbent = getattr(r, "replaces_item", None)
        if incumbent is not None:
            rep_ilvl = getattr(incumbent, "ilvl", None)
            rep_ilvl_str = f" · i{rep_ilvl}" if rep_ilvl else ""
            st.caption(f"↳ replaces your {rep_label}: {display_name(incumbent)}{rep_ilvl_str}")
        else:
            st.caption(f"↳ fills your empty {rep_label}")
    # Stats are shown inline (always-visible static text) and the item name
    # above is a keyboard-focusable Wowhead link — together those carry the
    # WCAG 2.1.1 reach that the old item-details popover provided. The popover
    # was removed here: its unscaled stat line would contradict this scaled
    # caption (r.new_stats is normalized to `shown`), and a11y is unaffected.
    stats_line = format_item_stats(r.new_stats)
    if stats_line:
        st.caption(stats_line)
    if upgraded_view:
        st.caption(
            f"↗ stats estimated at ilvl {shown} (up from {base_ilvl}) — "
            "assumes this piece's track reaches it; verify in-game."
        )
    # Only stamp ΔDPS when it's a real number. A "· ΔDPS ≈0" on a
    # survivability tool is noise + fake precision (mirrors recommend.py's
    # per-row/headline chips, which apply the same rule).
    card_md = f"**{_format_ehp_delta(r.avg_delta_ehp)}**"
    if abs(r.delta_dps) >= 0.1:
        dps_sign = "+" if r.delta_dps >= 0 else ""
        card_md += f"  ·  **ΔDPS {dps_sign}{r.delta_dps:,.2f}**"
    st.markdown(card_md)
    # Composition — the same shared renderer every gem/enchant/gear-swap card
    # uses (see `stat_composition.py`), so this number is explained exactly
    # the same way everywhere it appears. Registry-valued trinket rows (see
    # `VaultRow.via_trinket_registry`) get the honest "no stat breakdown"
    # caption instead — that ΔeHP isn't a stat-marginal dot product at all.
    if getattr(r, "via_trinket_registry", False):
        st.markdown(trinket_registry_html(), unsafe_allow_html=True)
    else:
        comp_html = composition_html(getattr(r, "terms", {}) or {}, class_spec, marginals or {})
        if comp_html:
            st.markdown(comp_html, unsafe_allow_html=True)
    if r.selected_dungeon_count and r.selected_dungeon_count < r.total_dungeon_count:
        st.caption(
            f"vs all {r.total_dungeon_count} dungeons: {_format_ehp_delta(r.all_avg_delta_ehp)}"
        )
    if getattr(r, "trinket_warning", False):
        st.caption("⚠️ Comparison may not fully reflect this trinket's special effect.")
    with st.expander("Per-dungeon breakdown"):
        for s in r.per_dungeon:
            _render_per_dungeon_row(s)
    # The trial swap targets the slot the row's comparison replaced — for a
    # paired offer that's the most-favorable incumbent's slot, so the Try
    # button and the "↳ replaces your …" caption never contradict.
    swap_slot = replaces_slot or r.slot
    swap_label = SLOT_LABELS.get(swap_slot, swap_slot)
    trial_item = _trial_state().swaps.get(swap_slot)
    is_trialed = trial_item is not None and getattr(trial_item, "item_id", None) == getattr(
        r.item, "item_id", 0
    )
    read_only = _is_read_only()
    # A net-loss row (dead, or simply negative ΔeHP like a genuinely-new item
    # that still doesn't beat what's equipped) must not read "Try X" as if
    # it were a recommendation — a novice reviewer flagged exactly this
    # confusion: a CTA that says "try it" directly under a card that just
    # said it's worse (2026-07-04 review). Reword to name the trade-off
    # instead of suppressing the button outright — power users still want
    # to trial it and see the per-dungeon breakdown.
    is_downgrade = r.avg_delta_ehp <= 0
    btn_label = (
        f"Trial anyway · {display_name(r.item)}" if is_downgrade else f"Try {display_name(r.item)}"
    )
    if is_trialed:
        # WCAG 2.1.1: was st.button(disabled=True) which strips the button
        # from the a11y tree entirely. aria_disabled_button keeps it in
        # tab order with aria-disabled="true" + aria-describedby on help.
        from simf.ui.helpers.aria_button import aria_disabled_button

        aria_disabled_button(
            f"Equipped (trial) · {display_name(r.item)}",
            help=f"Already in your trial loadout. Reset the {swap_label} trial in the banner above.",
            key=f"vault_try_{key_suffix}",
        )
    elif read_only:
        from simf.ui.helpers.aria_button import aria_disabled_button

        aria_disabled_button(
            btn_label,
            help="Read-only share — trial swaps disabled.",
            key=f"vault_try_{key_suffix}",
        )
    elif st.button(
        btn_label,
        key=f"vault_try_{key_suffix}",
        width="stretch",
        help=(
            "This is a net loss vs your equipped gear — trial it anyway to see the "
            "per-dungeon breakdown."
            if is_downgrade
            else f"Trial-swap {display_name(r.item)} into your {swap_label}."
        ),
    ):
        _apply_trial_swap(swap_slot, r.item)


def _render_vault_grid(
    rows: list, winner, class_spec: str = "", marginals: dict | None = None
) -> None:
    """3-up grid; wraps to additional rows when the vault has >3 picks."""
    width = 3
    for i in range(0, len(rows), width):
        chunk = rows[i : i + width]
        cols = st.columns(width)
        for j, r in enumerate(chunk):
            with cols[j]:
                _render_vault_cell(
                    r,
                    is_winner=r is winner,
                    key_suffix=f"{i + j}",
                    class_spec=class_spec,
                    marginals=marginals,
                )

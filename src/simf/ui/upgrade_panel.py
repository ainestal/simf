"""simf UI — "Apply an upgrade · which slot wins?" panel (L3).

The crest/Voidcore slot-ranker: scales every equipped piece by a uniform
``ilvl_delta`` and ranks slots by survivability gain, plus its per-row
renderer. No ``st.*`` at module scope.

Lives at ``src/simf/ui/`` (same depth as app.py); imports only from the
L0/L1/L2 layers (``format_html``/``item_html``/``marginals``/``state``) +
``optimizer``. NEVER imports from ``app`` (strict L3 DAG).
"""

from __future__ import annotations

import html

import streamlit as st

from simf.core.character import Character
from simf.core.constants import load_constants
from simf.io.upgrade_track import classify_upgrade_track, is_crafted
from simf.optimizer.item_upgrade import UpgradeRow, compute_upgrade_impact
from simf.ui.format_html import _base_secondary_pool
from simf.ui.helpers.gear_list import SLOT_LABELS, display_name
from simf.ui.helpers.stat_composition import composition_html
from simf.ui.helpers.upgrade_compare import category_ceiling, category_for_slot
from simf.ui.item_html import _icon_link_html_q, _item_link_html
from simf.ui.marginals import _marginals_for
from simf.ui.state import _equipped, _format_ehp_delta, _ss, _stats_for_item


def _can_project_crest_upgrade(slot: str, item: object) -> bool:
    """Whether we can honestly project a crest-driven ilvl ceiling for this
    equipped piece at all.

    Two real cases the flat/per-category ceiling still got wrong (both
    user-flagged 2026-07-04, both real gear on the same character):

    - Crafted gear (``is_crafted``) uses its own quality-rank system, not
      the loot upgrade tracks at all — a crest ceiling can't be projected
      for it, honestly, at any category.
    - Armor-category items (rings/necks/cloaks/plate-and-cloth pieces —
      see ``category_for_slot``) whose track ISN'T CONFIRMED MYTH
      specifically (``classify_upgrade_track(...).track == "Myth"``, not
      just "any recognised track"). Requiring "any track" wasn't enough —
      it was ALSO fooled by a genuinely wrong ``constants.yaml`` label
      (fixed the same day): a ring's rank ``bonus_id`` was confidently
      classified as "Myth 2/6" when it's actually "Hero 6/6" (already
      maxed on ITS OWN track — zero crest headroom left on this exact
      piece), verified against Wowhead's own item tooltip. 276 is
      genuinely ambiguous between the two — see ``io/upgrade_track.py``'s
      "ilvl → track is many-to-one" warning — and the armor category
      ceiling (289) is specifically Myth's own max, not a generic "any
      track" ceiling, so only a CONFIRMED Myth-track item can honestly use
      it. Weapon/trinket items are NOT held to this stricter bar: only
      their top rung has a registered bonus_id today, so requiring a
      confirmed track here would wrongly exclude every not-yet-maxed
      weapon/trinket — a real regression with no matching ambiguity risk
      to justify it.
    """
    if is_crafted(item):
        return False
    if category_for_slot(slot) != "armor":
        return True
    return classify_upgrade_track(item).track == "Myth"


def _all_slots_at_ceiling(item_ilvls: dict[str, int], slot_ceilings: dict[str, int | None]) -> bool:
    """True when every slot with a known ilvl is already at (or past) its
    OWN reachable ceiling — armor and weapon/trinket categories cap at
    different ilvls in Midnight 12.0.5 (see ``category_ceiling``), so this
    can never be a single flat-number comparison."""
    positive = {slot: ilvl for slot, ilvl in item_ilvls.items() if ilvl > 0}
    if not positive:
        return False
    return all(
        slot_ceilings.get(slot) is not None and ilvl >= slot_ceilings[slot]
        for slot, ilvl in positive.items()
    )


def _render_upgrade_panel(char: Character, dungeons: list[dict]) -> None:
    """Apply-an-upgrade ranker — answers "which slot should my Voidcore go on?"

    Reads the equipped items, scales each by a uniform ``ilvl_delta``, and
    runs the cached survivability marginals over the resulting stat
    deltas. Identical math path to the vault scorer in
    ``per_dungeon.score_item_across_dungeons`` — no Monte Carlo, no
    network. Renders the top ranked slots inline; the rest behind a
    disclosure so the page doesn't stretch on characters with a 16-slot
    paperdoll where 4 of them dominate.
    """
    equipped = _equipped()
    if not equipped:
        return

    item_stats: dict[str, dict[str, int]] = {}
    item_ilvls: dict[str, int] = {}
    item_names: dict[str, str | None] = {}
    n_excluded_unprojectable = 0
    for slot, spec in equipped.items():
        stats = _stats_for_item(spec)
        if not stats:
            continue
        ilvl = getattr(spec, "ilvl", None) or (spec.get("ilvl") if isinstance(spec, dict) else None)
        if not ilvl:
            continue
        if not _can_project_crest_upgrade(slot, spec):
            n_excluded_unprojectable += 1
            continue
        item_stats[slot] = stats
        item_ilvls[slot] = int(ilvl)
        item_names[slot] = display_name(spec)

    if not item_stats:
        if n_excluded_unprojectable:
            st.caption(
                "No slot has a projectable crest upgrade — the rest is crafted gear or "
                "sits on an unconfirmed upgrade track (can't tell an already-maxed lower "
                "track from real headroom without guessing)."
            )
        return

    marginals = _marginals_for(char)
    constants = load_constants()
    dps_weights = constants.get("dps_stat_weights", {})
    ceilings_cfg = constants.get("gear", {}).get("ilvl_ceiling_by_category")
    account_ceiling = _ss().get("simc_account_ilvl_ceiling")
    # Per-slot, NOT a single flat number — armor's crest track maxes out well
    # below weapon/trinket's Voidcore-reachable ceiling (289 vs. 298 in
    # Midnight 12.0.5). A flat cap let the ranker suggest "upgrading" an
    # armor piece already at its own max, which is impossible in-game on
    # every axis (the crest system has nothing left to sell it, and Voidcore
    # doesn't apply to that slot at all) — user-flagged 2026-07-04.
    slot_ceilings = {
        slot: category_ceiling(slot, ceilings_cfg, account_ceiling=account_ceiling)
        for slot in item_ilvls
    }

    # Precompute at the session's current (or default) delta BEFORE any
    # heading/widget renders, so a delta that can't produce a single row
    # doesn't still show a live "ilvl gain" stepper above a dead end (real
    # report: every card above already shows a known ilvl, so blaming a
    # missing `/simc` export was false by construction). Every slot already
    # sitting at its own reachable ceiling is delta-independent —
    # compute_upgrade_impact excludes an at-ceiling slot no matter what
    # ilvl_delta is passed — so collapsing straight to one sentence there
    # can't trap the player. A rounding-driven empty result (every stat
    # scales to an unchanged integer at a small delta) IS delta-dependent,
    # so that case falls through and keeps the stepper visible instead of
    # stranding the player on a delta that can never produce a row.
    ilvl_delta = int(_ss().get("_upgrade_ilvl_delta", 6))
    all_at_ceiling = _all_slots_at_ceiling(item_ilvls, slot_ceilings)
    rows = compute_upgrade_impact(
        equipped=equipped,
        item_stats=item_stats,
        item_ilvls=item_ilvls,
        item_names=item_names,
        marginals=marginals,
        dungeons=dungeons,
        dps_stat_weights=dps_weights,
        base_secondary_pool=_base_secondary_pool(char),
        ilvl_delta=ilvl_delta,
        slot_ceilings=slot_ceilings,
    )
    if not rows and all_at_ceiling:
        st.caption(
            "Every equipped slot is already at its reachable ceiling this season "
            "— armor caps where its crest track maxes out, and any weapon/trinket "
            "slot that could still take a Voidcore is capped too. Nothing to "
            "upgrade here."
        )
        return

    # Eyebrow label — names this a distinct, subordinate utility (a ranker
    # over the SAME equipped set, not a second paperdoll) so it doesn't read
    # as competing with the primary gear sheet above it. Light-page styling:
    # the number_input right below is a real Streamlit widget, so this
    # section can't be moved into the dark `.gear-sheet-dark` scope wholesale
    # (that treatment is display-HTML-only — see the panel CSS comment in
    # app.py). Only the pure-display rows below go dark.
    st.markdown(
        '<div class="upgrade-panel-eyebrow">Cross-slot ranker</div>', unsafe_allow_html=True
    )
    st.markdown("### Apply an upgrade · which slot wins?")
    st.caption(
        "Pick how many ilvls your upgrade currency adds. simf scales each "
        "piece up and ranks them by survivability gain."
    )

    ilvl_delta = int(
        st.number_input(
            "ilvl gain",
            min_value=1,
            max_value=13,
            value=ilvl_delta,
            step=1,
            key="_upgrade_ilvl_delta_input",
            help=(
                "Gilded Crest = +6 ilvls, any slot. Aspect / Wyrm Crest = +3, "
                "any slot. Voidcore = +6, but only main-hand/off-hand weapons "
                "and trinkets can take one — no other slot is eligible."
            ),
        )
    )
    _ss()["_upgrade_ilvl_delta"] = ilvl_delta

    # Re-score at whatever the widget actually returned — matters when it
    # differs from the pre-render session value above (a live stepper edit).
    rows = compute_upgrade_impact(
        equipped=equipped,
        item_stats=item_stats,
        item_ilvls=item_ilvls,
        item_names=item_names,
        marginals=marginals,
        dungeons=dungeons,
        dps_stat_weights=dps_weights,
        base_secondary_pool=_base_secondary_pool(char),
        ilvl_delta=ilvl_delta,
        slot_ceilings=slot_ceilings,
    )
    if not rows:
        # Not the at-ceiling case (that returns above, before this chrome
        # renders at all) — every slot had stats + a known ilvl and was
        # crest-projectable, but this delta produced no ranked slot.
        st.caption(
            f"simf couldn't rank any slot for this upgrade size — nothing here "
            f"has projectable headroom at +{ilvl_delta} ilvls."
        )
        return

    has_trinket_in_top = any(r.is_trinket for r in rows[:3])

    # Show only the top slots — the panel answers "which slot wins my next
    # upgrade?", and the winner is always near the top. The full per-slot list
    # is redundant with the paperdoll above (which already shows every slot),
    # so the old "Show all N slots" expander was just noise (user-flagged).
    top_n = min(5, len(rows))
    # One `st.markdown` call for the whole dark box (header + every row) —
    # same reason `_render_paperdoll_grid` does this: Streamlit stacks
    # separate st.markdown calls as sibling elements with its own gap between
    # them, so N separate calls would show cream seams between "rows" instead
    # of one continuous dark strip. `.gear-sheet-dark` is reused (not a new
    # scope) so the rows inherit the same audited `--gs-*` tokens the
    # paperdoll cards use — verified against the lighter `--gs-panel` bg too,
    # see tests/test_gear_dark_contrast.py.
    rows_html = "".join(
        _upgrade_row_html(r, equipped, char.class_spec, marginals) for r in rows[:top_n]
    )
    st.markdown(
        f'<div class="gear-sheet-dark upgrade-panel-dark"><div class="upgrade-rows">{rows_html}</div></div>',
        unsafe_allow_html=True,
    )

    if has_trinket_in_top:
        st.caption(
            "⚠️ Trinkets near the top — stat scaling captures only their passive stats, "
            "not their on-use / proc effects. Cross-check with the trinket registry before deciding."
        )
    if n_excluded_unprojectable:
        n = n_excluded_unprojectable
        st.caption(
            f"{n} equipped slot{'s' if n != 1 else ''} not shown here — crafted gear or an "
            "unconfirmed upgrade track (can't tell an already-maxed lower track from real "
            "crest headroom without guessing)."
        )


def _upgrade_row_html(
    r: UpgradeRow, equipped: dict, class_spec: str = "", marginals: dict | None = None
) -> str:
    """One slot's row inside the upgrade panel — icon + name + ilvl arrow +
    deltas. Pure string builder (like ``_slot_card_html``) so the panel can
    join every row into one HTML blob and emit it in a single ``st.markdown``
    call — required for the dark-panel background to read as one continuous
    strip rather than N separately-gapped boxes.

    ``class_spec``/``marginals`` drive the per-stat composition line appended
    under the slot name (``r.terms`` sums exactly to ``r.delta_ehp``) — it
    lives in the flexible middle column (``.slot-row-text``), not next to the
    fixed-width ΔeHP/ΔDPS pair, so a long composition line wraps instead of
    squeezing the numeric columns."""
    slot_label = SLOT_LABELS.get(r.slot, r.slot)
    item = equipped.get(r.slot)
    # Icon shares the Wowhead anchor with the text link so the tooltip
    # also appears when the player hovers the image. "large" source (56px)
    # for the enlarged 52px display size — see _slot_card_html's icon.
    icon_html = (
        _icon_link_html_q(item, "slot-row-icon", size="large")
        if item is not None
        else '<div class="slot-row-icon"></div>'
    )
    name_html = (
        _item_link_html(item, with_icon=False)
        if item is not None
        else html.escape(r.item_name or slot_label, quote=False)
    )
    ehp_text = _format_ehp_delta(r.delta_ehp)
    dps_pct = r.delta_dps_pct * 100.0
    dps_text = "≈0%" if abs(dps_pct) < 0.01 else f"{dps_pct:+.2f}%"
    # is_capped tells the player the upgrade was clamped to THIS SLOT's own
    # reachable ceiling (armor tops out at its crest track's max; weapons/
    # trinkets can go further via Voidcore, but still cap at 298). Surfacing
    # this inline keeps the player from picking a slot where the gain is partial.
    cap_badge = (
        f' <span class="upgrade-cap-badge" title="Clamped to this slot\'s reachable ceiling ({r.upgraded_ilvl}).">'
        f"capped</span>"
        if r.is_capped
        else ""
    )
    comp = composition_html(r.terms, class_spec, marginals or {})
    return (
        f'<div class="slot-row">'
        f"{icon_html}"
        f'<div class="slot-row-text">'
        f'<div class="slot-row-eyebrow">{slot_label}</div>'
        f'<span class="slot-row-name">{name_html}</span>'
        f"{comp}"
        f"</div>"
        f'<span class="upgrade-ilvl">{r.current_ilvl} → {r.upgraded_ilvl}{cap_badge}</span>'
        f'<span class="upgrade-deltas"><strong>{ehp_text}</strong> · '
        f'<span class="upgrade-dps">ΔDPS {dps_text}</span></span>'
        f"</div>"
    )

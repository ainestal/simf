"""simf UI — character-load + run-config + share-URL flows (L2).

Every way a character enters a session — online name lookup (Raider.IO /
Blizzard), ``/simc`` paste, demo build — plus the run-config trust strip,
the tier-set header badge, the post-load toast, and the cold-share URL
applier. Each loader ends in ``st.rerun()`` on success → the gear surface,
and arms ``_flag_scroll_to_top`` first so the browser resets to the top of
the page on the render that follows — ``main()`` calls
``_scroll_to_top_if_requested()`` first thing, ahead of the header, to
consume it (2026-07-09 — a full landing↔gear-surface swap left a visitor's
prior scroll offset in place, so the new surface first-painted mid-page
with no wordmark/tabs/confirmation visible).
No ``st.*`` at module scope.

Lives at ``src/simf/ui/`` (same depth as app.py); imports only from the
L0/L1 foundation layer (``state``), helpers/core, and — since 2026-07-08 —
its L2 sibling ``ui.marginals`` for the run-config popover's noise summary
(the same cross-sibling pattern ``gear_surface.py``/``vault_panel.py``/etc.
already use to reach ``marginals.py``). NEVER imports from ``app`` (strict
L2 DAG). The ``Path(__file__)`` repo-root walk in ``_load_demo_character``
relies on that same depth.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import streamlit as st
import yaml

from simf.core.character import Character
from simf.core.constants import DATA_DIR, load_constants
from simf.io.realms import realm_names, slug_for
from simf.io.spec_ids import class_spec_display_name
from simf.optimizer.tier_sets import equipped_set_status
from simf.ui.helpers.calibration_badge import CalibrationBadge, compute_calibration_badge
from simf.ui.helpers.run_config import (
    CALIBRATION_CORPUS_SPEC,
    RunConfig,
    compute_reproduction_hash,
    format_log_surface_no_character_summary,
    format_run_config_details,
)
from simf.ui.helpers.share_url import parse_share_params
from simf.ui.helpers.simc_load import SimcLoadError, SimcLoadOk, load_from_simc
from simf.ui.helpers.usage_tracking import record_event
from simf.ui.marginals import (
    _marginals_ci_for,
    _marginals_for,
    marginal_noise_basis_label,
    summarize_marginal_noise,
)
from simf.ui.state import (
    _announce_status,
    _dungeon_catalog,
    _equipped,
    _has_character,
    _is_public_mode,
    _item_db_date,
    _loadouts_for_spec,
    _reset_rio_realm,
    _resolve_equipped_stats,
    _simf_sha,
    _ss,
)

# ─── scroll-to-top on a full page-content swap ─────────────────────────────
# A demo/online/`/simc` load (or the reverse — "Change character") swaps the
# ENTIRE body under the visitor without moving their browser scroll offset.
# A visitor who'd scrolled partway down the landing page lands mid-scroll on
# the new surface with no wordmark, tabs, or "Change character" button
# visible — reads as "the click did nothing / broke something" (novice_tank
# review, 2026-07-09). `_flag_scroll_to_top` arms a one-shot session flag
# right before each loader's `st.rerun()`; `_scroll_to_top_if_requested`
# (called at the very top of `main()`'s render, ahead of the header) consumes
# it and resets the browser to the top of the page.
_SCROLL_TO_TOP_JS = """
<script>
(function() {
  var doc = window.parent ? window.parent.document : document;
  var target = doc.querySelector('[data-testid="stMain"]') || doc.scrollingElement || doc.body;
  if (target) {
    if (typeof target.scrollTo === 'function') {
      target.scrollTo(0, 0);
    } else {
      target.scrollTop = 0;
    }
  }
  // Belt-and-suspenders: stMain isn't always what's carrying the visible
  // scroll on every Streamlit build, so reset the parent window too.
  var win = window.parent || window;
  if (typeof win.scrollTo === 'function') {
    win.scrollTo(0, 0);
  }
})();
</script>
"""


def _flag_scroll_to_top() -> None:
    """Arm the one-shot flag `_scroll_to_top_if_requested` consumes on the
    render that follows a loader's `st.rerun()`. Call this immediately
    before every `st.rerun()` that swaps the landing form for the gear
    surface (or vice versa — "Change character")."""
    _ss()["_scroll_to_top_on_next_render"] = True


def _scroll_to_top_if_requested() -> None:
    """Consume the flag `_flag_scroll_to_top` set and reset the browser's
    scroll position to the top of the page. Must run at the very top of
    `main()`'s render, ahead of the header/tabs, so a load's first paint is
    always the wordmark — never wherever the visitor happened to be
    scrolled on the previous page. `.pop(..., False)` both reads and clears
    the flag in one step, so a later widget-only rerun (no new load) never
    re-triggers the scroll.

    Renders exactly ONE element on EVERY run — an `st.empty()` placeholder
    that the scroll script replaces on flagged runs — never zero-or-one.
    This is load-bearing, not tidiness: the original version emitted the
    `st.html` only on flagged runs, so the main flow held one MORE element
    on a load-transition run than on every run after it. Streamlit
    reconciles elements purely by tree position, which means that
    off-by-one shifted every element on the page up one slot on the first
    rerun after a load — and when that rerun blocked mid-script (the
    key-level verdict's 8-30s Monte Carlo sweep), the previous run's
    not-yet-overwritten last element sat on screen as a stale duplicate of
    the whole verdict panel for the entire sweep. That was the real root
    cause of the live-reported duplicated-panel bug
    (examples/screenshots/double.png) — reproduced deterministically on
    localhost 2026-07-19 with the natural flow (load → open expander →
    Compute, no intermediate rerun) and gone with this stable slot.

    The empty-then-fill shape (rather than always calling `st.html` with an
    inert body) is deliberate: `st.html` raises on an empty body, a
    style-only body gets rerouted to the event container (a DIFFERENT tree
    position — drift again), and flipping the slot's element TYPE
    (empty → html) forces a fresh mount in the browser on each transition
    run, re-executing the script; updating an existing html element's body
    in place doesn't reliably do that."""
    slot = st.empty()
    if not _ss().pop("_scroll_to_top_on_next_render", False):
        return
    slot.html(_SCROLL_TO_TOP_JS, unsafe_allow_javascript=True)


def _format_elapsed(seconds: float) -> str:
    """Coarse "N ago" bucketing for the gear-staleness caption — simple
    thresholds, no external deps. Mirrors the shape of
    ``run_config._format_commit_age`` but is deliberately its own copy:
    this one describes "time since an online gear fetch," a different
    fact from that helper's "time since a git commit," even though both
    render as minutes/hours/days."""
    seconds = max(seconds, 0.0)
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = int(seconds // 3600)
    if hours < 24:
        return f"{hours}h ago"
    days = int(seconds // 86400)
    return f"{days}d ago"


def _build_run_config(
    iterations: int,
    seed: int,
    dungeon_ids: list[str],
    healer_profile: str,
    talent_hash: str,
    char: Character | None = None,
) -> RunConfig:
    c = load_constants()
    cal = c.get("calibration") or {}
    per_d = cal.get("per_dungeon") or {}
    # The popover previously showed raw yaml ids here ("algeth_ar_academy")
    # while the "Prog dungeons" line two rows down showed the same dungeons
    # by display name — three different name forms on one screen (round-1
    # multi-agent review, 2026-07-05). Resolve to the catalog's display name
    # everywhere in this popover.
    names_by_id = {d["id"]: d.get("name", d["id"]) for d in _dungeon_catalog()}
    # `dungeon_ids` (despite the name) holds the caller's DISPLAY-name
    # selection (see app.py's `dungeon_labels`) — scope the popover's
    # measured table + its "N dungeons not shown" note to those, not the
    # whole constants.yaml corpus. Round 2 added the "not shown" note with
    # a comment promising "2 of *their* prog dungeons" but never actually
    # filtered — a comment-says-X-code-does-Y gap of the same family this
    # cycle keeps catching (round-3 review, 2026-07-05). A no-op when the
    # user hasn't narrowed their selection (the default is "all dungeons"),
    # so this only changes behavior once someone actually deselects one.
    selected_names = set(dungeon_ids)
    # `k not in names_by_id` means this constants.yaml residual belongs to a
    # dungeon no longer in the LIVE catalog (e.g. an archived prior-season id
    # after `scripts/promote_season2_catalog.py` cuts the pool over) — drop
    # it rather than falling back to leaking the raw yaml id, which would
    # reopen the exact "raw id shown next to display names" bug this
    # popover's own history (round-1 review, 2026-07-05) already fixed once.
    per_d_named = {
        names_by_id[k]: v
        for k, v in per_d.items()
        if k in names_by_id and (not selected_names or names_by_id[k] in selected_names)
    }
    worst_dungeon, worst_pct = None, None
    if per_d_named:
        worst_dungeon, worst_pct = max(per_d_named.items(), key=lambda kv: abs(kv[1] or 0.0))
    cfg = RunConfig(
        k_active=int(cal.get("k_active", 0)) or int(c.get("armor", {}).get("k_constant", 0)),
        global_rmse=cal.get("global_rmse"),
        log_count=int(cal.get("log_count", 0)),
        worst_residual_dungeon=worst_dungeon,
        worst_residual_pct=worst_pct,
        iterations=iterations,
        seed=seed,
        constants_version=c.get("last_verified_patch", "v0.7"),
        simf_sha=_simf_sha(),
        item_db_date=_item_db_date(),
        policy_version=c.get("policy_version", "v3"),
        dungeons=dungeon_ids,
        healer_profile=healer_profile,
        talent_hash=talent_hash,
        per_dungeon_residuals=per_d_named or None,
    )
    # `_marginals_ci_for` resolves from the session cache OR (its 2026-07-08
    # fix) a disk-cache fallback — the latter is what makes this render on
    # the FIRST pass after a load for a prewarmed character instead of
    # needing an unrelated rerun to catch up. Calling `_marginals_for` here
    # is free: it's the same per-character session cache every gear-surface
    # call already reads, never triggers a second compute.
    noise_summary = None
    noise_basis = None
    if char is not None:
        ci = _marginals_ci_for(char)
        if ci is not None:
            noise_summary = summarize_marginal_noise(_marginals_for(char), ci)
            if noise_summary is not None:
                noise_basis = marginal_noise_basis_label()
    return replace(
        cfg,
        repro_hash=compute_reproduction_hash(cfg, char),
        marginal_noise_summary=noise_summary,
        marginal_noise_basis=noise_basis,
    )


def _compose_run_config_strip_html(summary: str, badge_html: str) -> str:
    """Join the trust summary with the (optional) tier-set badge for the strip.

    A plain space — NOT a ' · ' middot — separates the two. The model-error
    trust statement and the gear tier-set badge are different categories, and
    the middot made them read as one CSV-style statement (F-003, review round
    R1 2026-07-17, ui-craft-critic: "±6.8% model error ·" welded to "4pc Night
    Ender's Vesture"). `.tier-badge`'s 12px left margin + bordered pill already
    give sighted users clear separation, so the middot was pure redundancy that
    caused the misread.

    The space is load-bearing, not cosmetic: it preserves the copy-paste /
    screen-reader text boundary the 2026-07-05 copy audit added. The two inline
    elements share no whitespace char in the HTML source otherwise, so without
    a real separator extracted text ran the summary and badge together as
    "…error4pc…". A space fixes that just as the middot did, without implying
    the two are one statement.
    """
    if not badge_html:
        return summary
    return f"{summary} {badge_html}"


def _render_change_character_button() -> None:
    """Subtle 'Change character' action — rare enough not to earn a primary
    affordance, but it needs to exist somewhere visible (was the only useful
    button in the now-removed sidebar). Clears the loaded character + its
    cached gear so a fresh `/simc` paste starts clean."""
    if st.button(
        "Change character",
        width="stretch",
        help="Clear the loaded character and paste a different `/simc` export.",
        key="change_character_inline",
    ):
        for k in (
            "char_data",
            "simc_equipped",
            "simc_bag_items",
            "simc_vault_items",
            "_gear_fetched_at",
            "_gear_source",
            "_gear_stats_estimated",
            "_gear_from_log",
        ):
            _ss().pop(k, None)
        _flag_scroll_to_top()
        st.rerun()


def _calibration_chip_label(
    badge: CalibrationBadge, global_rmse: float | None, is_corpus_spec: bool
) -> str:
    """The one-glance calibration chip text: confidence + (when honest) the number.

    F-001/F-002 (review round R2, 2026-07-17) — both tank personas read the
    ✓-pill and the floating "±X% model error" as two things they had to
    mentally staple together (novice) with no confidence attached to the
    number and no number attached to the confidence (the project's own
    Number→Confidence voice-formula, torn across two widgets). This collapses
    them into a single statement that IS the popover's click target.

    The ±X% number rides in the chip ONLY for the corpus spec (Prot Warrior).
    `global_rmse` is Warrior's 16-log number with no per-spec variant yet (see
    `CALIBRATION_CORPUS_SPEC`), so welding it onto another `calibrated`-tier
    spec — Guardian, whose real RMSE is ~1.7× Warrior's — would be a false
    per-spec precision claim. For any non-corpus spec the chip shows the tier
    word + its plain-English trailing clause instead ("✓ Calibrated — matches
    real combat logs closely"), which is honest without over-claiming a number
    that isn't that spec's. The trailing clause is dropped for the corpus spec
    because the number ("±6.8% off real logs") is the more precise gloss.
    """
    if badge.tier == "calibrated" and is_corpus_spec and global_rmse is not None:
        return f"{badge.icon} {badge.label} · ±{global_rmse * 100:.1f}% off real logs"
    if badge.trailing_clause:
        return f"{badge.icon} {badge.label} — {badge.trailing_clause}"
    return f"{badge.icon} {badge.label}"


def _render_run_config_strip(cfg: RunConfig) -> None:
    # ONE calibration chip (F-001/F-002, review round R2, 2026-07-17): the
    # confidence tier + (for the corpus spec) the ±X% number read as a single
    # statement, and that chip is itself the click target for the full
    # Calibration-details popover. This replaces three scattered signals — a
    # floating "±X% model error" strip, a separate "Model calibration: ✓
    # Calibrated" pill row (was in app.py, now deleted), and a standalone "ⓘ
    # Calibration details" button — that both tank personas said they had to
    # reconcile themselves.
    spec = (_ss().get("char_data") or {}).get("class_spec", "")
    badge = compute_calibration_badge(spec, load_constants()) if spec else None
    # The ±X% error is Prot Warrior's corpus (CALIBRATION_CORPUS_SPEC), with no
    # per-spec variant yet — so it's authoritative ONLY for that spec. For any
    # other loaded spec it's a reference figure, never this build's own error
    # (advisor catch, R2). This flag gates both the chip number and the
    # popover's framing.
    is_corpus_spec = spec == CALIBRATION_CORPUS_SPEC
    # `badge is None` (no character loaded) is a THIRD, separate state — the
    # no-character log surface, where nothing on screen is sim-derived at all
    # (see `format_log_surface_no_character_summary`'s docstring). It used to
    # fold into `is_corpus_spec=True` above; dual-reviewed 2026-07-26 and
    # found to be a real honesty gap, not a defensible reuse.
    details = format_run_config_details(
        cfg, is_corpus_spec=is_corpus_spec, log_surface_no_character=badge is None
    )
    # `view` folds into the popover's own key below (F-008, 2026-07-20): a
    # loaded character keeps the same class_spec across a Gear<->Why-did-I-die
    # view switch, so both views render this SAME popover at the same script
    # position with the SAME tier — Streamlit's client-side widgets persist
    # open/closed state across reruns by key identity (1.56+), so the popover
    # stayed open right through the switch, its stale Gear-view content
    # rendered on top of the unrelated new view underneath (live-reproduced:
    # opening it on Gear then switching views left it open, blocking clicks on
    # the log-upload surface below it). Folding `view` into the key makes a
    # view switch a genuinely NEW widget from Streamlit's side, so it mounts
    # fresh (closed) instead of carrying over the old one's open state.
    view = _ss().get("view", "gear")
    # Keyed container so the mobile CSS (`.st-key-run-config-row`) can pull the
    # trailing columns onto a shared row at narrow widths, matching the
    # header's nav-row treatment (round-1 review, 2026-07-05 — see
    # `_render_header`'s matching container for the fuller rationale).
    char_loaded = _has_character()
    with st.container(key="run-config-row"):
        if badge is not None:
            # A character is loaded → we have a per-spec confidence tier to
            # merge the number into.
            chip_label = _calibration_chip_label(badge, cfg.global_rmse, is_corpus_spec)
            cols = st.columns([4, 2, 1]) if char_loaded else st.columns([4, 2])
            # Per-tier keyed container so the popover trigger button can be
            # tinted to the tier's accent (steel/neutral/bronze) in css.py —
            # a descendant selector (`.st-key-… button`), which sidesteps the
            # nested-wrapper display:contents gotcha a child selector hits.
            # Streamlit renders the popover `label` as both the visible text
            # AND the accessible name; the chip label is already a descriptive
            # statement, so no bare-glyph WCAG 4.1.2 issue. `width="content"`
            # shrink-wraps it to a pill (not a full-width bar); Streamlit adds
            # its own expand chevron.
            with (
                cols[0],
                st.container(key=f"calibration-chip-{badge.tier}"),
                st.popover(
                    chip_label,
                    help="See the logs, dungeons, and settings behind this verdict.",
                    width="content",
                    key=f"calibration-popover-{view}",
                ),
            ):
                st.markdown(details)
            # Tier-set badge (4pc / 2pc) rides in its own column — the chip
            # absorbed the model-error text it used to sit beside (F-003
            # unwelded the two; keep them apart). Its own column also means no
            # summary+badge join, so `_compose_run_config_strip_html` is only
            # exercised on the no-spec branch below.
            badge_html = _tier_sets_badge_html()
            if badge_html:
                with cols[1]:
                    st.markdown(
                        f'<div class="run-config-strip">{badge_html}</div>',
                        unsafe_allow_html=True,
                    )
            if char_loaded:
                with cols[-1]:
                    _render_change_character_button()
        else:
            # No spec loaded (e.g. the log surface with no character): there's
            # no per-spec confidence tier to merge a number into, AND nothing
            # on this page is sim-derived (see
            # `format_log_surface_no_character_summary`'s docstring) — the
            # old plain model-error strip here was a real honesty gap
            # (dual-reviewed 2026-07-26), not a defensible reuse of the
            # Warrior corpus number.
            summary = format_log_surface_no_character_summary()
            cols = st.columns([4, 2])
            with cols[0]:
                st.markdown(
                    f'<div class="run-config-strip" role="status" '
                    f'title="This page reconstructs your own combat log — the '
                    f'numbers are your recorded events, not a model prediction." '
                    f'aria-label="Run configuration: {summary}">'
                    f"{_compose_run_config_strip_html(summary, '')}</div>",
                    unsafe_allow_html=True,
                )
            with (
                cols[1],
                st.popover(
                    "ⓘ Calibration details",
                    help="See the logs, dungeons, and settings behind this verdict.",
                    width="stretch",
                    key=f"calibration-popover-nospec-{view}",
                ),
            ):
                st.markdown(details)
    # Staleness disclosure for an online-lookup-sourced gear set — cleared
    # by any non-online load (see _do_simc_load / _load_demo_character) so
    # a later /simc paste or demo load never keeps showing a stale "fetched
    # via raiderio" caption. Unconditional (not public-mode-gated): the
    # owner benefits from knowing their own gear snapshot's age too.
    fetched_at = _ss().get("_gear_fetched_at")
    if fetched_at:
        st.caption(
            f"Gear fetched via {_ss().get('_gear_source', 'online lookup')} "
            f"{_format_elapsed(time.time() - fetched_at)} — re-lookup your "
            "character to refresh."
        )
    # Provenance for a log-hydrated character (set by the Why-did-I-die
    # surface's COMBATANT_INFO auto-load, cleared on any non-log load —
    # see _gear_from_log's set/clear sites) — otherwise the Gear tab gives
    # no hint why item names read as bare ids and bags/vault are empty.
    gear_from_log = _ss().get("_gear_from_log")
    if gear_from_log:
        st.caption(
            f"Gear read from your combat log ({gear_from_log}) — item names "
            "show as ids and your bag/vault are empty. Use **Change "
            "character** to load a `/simc` export or an armory lookup instead."
        )


# ─── SimC paste flow ──────────────────────────────────────────────────────────

# Batch E (novice on-ramp, 2026-07-06 Active Triage Queue) — plain-English
# "feel" + a fresh complexity axis per spec. This is general tank-design
# reputation that's held steady across WoW expansions (Brewmaster's
# Stagger/Purify loop and Guardian's passive simplicity are long-standing,
# widely-agreed community reads), NOT a Midnight-12.0.5-specific balance
# claim — every spec here can clear keys. Deliberately distinct from
# `calibration_tier` below, which is pulled live from constants.yaml (not
# hardcoded here) so it can never go stale the way a hand-copied number
# would — see `gem_optimal_label_trust_fix` in project memory for the
# class of bug a duplicated-instead-of-shared number produces.
_SPEC_FEEL: dict[str, tuple[str, str]] = {
    "protection_warrior": (
        "Active and punchy — block/parry reflexes, Shield Block timing, "
        "Ignore Pain as a damage buffer you top up constantly.",
        "Medium",
    ),
    "protection_paladin": (
        "Rhythmic Holy Power economy — Shield of the Righteous on cooldown, "
        "Word of Glory as a heal-or-bank choice, strong panic buttons.",
        "Medium-High",
    ),
    "blood_death_knight": (
        "Self-healing through Death Strike — bank Runic Power, then heal big "
        "right after a big hit. Bone Shield stacks to keep an eye on.",
        "Medium",
    ),
    "vengeance_demon_hunter": (
        "Fast and aggressive — damage taken fuels your defensives (Demon "
        "Spikes, Fiery Brand), very mobile, several short cooldowns to juggle.",
        "Medium-High",
    ),
    "brewmaster_monk": (
        "A defensive puzzle — Stagger converts a chunk of every hit into a "
        "damage-over-time bar you actively Purify. Widely considered the "
        "most hands-on tank kit.",
        "High",
    ),
    "guardian_druid": (
        "Simple and sturdy — a big HP pool and Ironfur uptime carry you, "
        "few buttons to track mid-fight. Widely considered the most "
        "beginner-friendly tank.",
        "Low",
    ),
}


def _render_spec_comparison() -> None:
    """Batch E — plain-English spec comparison for a visitor who hasn't
    picked a tank spec yet. Collapsed by default so it doesn't compete
    with the primary load flow for anyone who already has a character."""
    with st.expander("🤔 Haven't picked a tank spec yet? Compare all 6"):
        st.caption(
            "General tank-design reputation across expansions, not a "
            "Midnight-specific balance claim — every spec here can clear "
            'keys. "Model confidence" is how well simf\'s own math is '
            "validated for that spec today, not how good the spec is."
        )
        specs_cfg = load_constants().get("specs", {})
        lines = []
        for class_spec, (feel, complexity) in _SPEC_FEEL.items():
            tier = specs_cfg.get(class_spec, {}).get("calibration_tier", "placeholder")
            lines.append(
                f"**{class_spec_display_name(class_spec)}** — {feel} "
                f"*(complexity: {complexity} · model confidence: {tier})*"
            )
        st.markdown("\n\n".join(lines))


def _trigger_rio_fetch() -> None:
    """`on_change` for the name field — lets pressing Enter there search
    without a click, without the fragility of `st.form` (see the comment
    above the name/realm/region row for why a real form isn't used here).
    Sets a one-shot flag; the render function below reads+clears it via
    `.pop(..., False)` in the same expression as the button's own click
    check, so either one sets `fetch_clicked`.
    """
    _ss()["_rio_fetch_triggered"] = True


def _render_simc_paste() -> None:
    # This <h2> sits one level under the page <h1> "simf".
    st.markdown("## Check your gear and vault picks")
    # Nothing on the cold landing (H1 "simf" + this H2) ever spelled out what
    # simf IS for a first-time visitor — SEO audit 2026-08-01. This is the
    # first sentence a crawler or cold visitor sees; it's also just a fair
    # one-line pitch, not solely a keyword play. "Raidbots doesn't do this
    # for tanks" is the strongest validated search intent from that audit
    # (Raidbots dropped tank survivability metrics in 2017 — see README).
    st.caption(
        "Free Mythic+ tank survivability simulator for World of Warcraft — "
        "the Raidbots alternative for tanks. See if your gear survives a "
        "key level, check your vault picks, or find out why you died."
    )
    # Batch E item 1 — a single up-front reassurance sentence ahead of BOTH
    # load paths. The name-lookup path below genuinely needs none of addon/
    # login/paste; only the vault/bags path (further down) needs `/simc`.
    st.caption(
        "No addons, no combat logs, no `/simc` paste required to get "
        "started — just your character name. (The `/simc` export further "
        "down unlocks your Great Vault and bags specifically.)"
    )
    _render_spec_comparison()
    # Batch E item 3 — the "Why did I die?" surface (top nav) needs no
    # character at all; name it explicitly so a visitor who only wants a
    # death recap doesn't feel forced through this load form first.
    st.caption(
        "Just want to know why you died last pull? Click **Why did I "
        "die?** above — no character load needed."
    )

    # Single top-of-form loading surface. Every load path (online lookup,
    # /simc paste, demo) renders its spinner + errors INTO this container, so
    # the loading state always lands in one predictable spot at the top of the
    # form — never wedged mid-layout beside whichever button was clicked. The
    # container is laid out first but written to last, after the whole form
    # renders (Streamlit's render-here-later pattern).
    status_slot = st.container()

    # Name lookup is the lowest-friction start, so it leads in BOTH modes. On
    # the public instance it is forced to the zero-auth Raider.IO source and
    # rate-limited (see _do_raider_io_load) — the owner's Blizzard creds are
    # never spent and the region-interpolated armory host is never reached, so
    # there's nothing to hide. The caption is the only mode-dependent bit:
    # public must not advertise the owner-only Blizzard Armory path.
    public = _is_public_mode()
    fetch_clicked = False

    # ── Primary path: look up by name (no addon, no paste, no login) ──
    st.markdown("### Find your character by name")
    if public:
        st.caption(
            'Best for "can my gear survive a +X key?" — pulls your '
            "**currently equipped** gear from Raider.IO (no addon, no login). "
            "This is your *current* gear, which may differ from an older logged "
            "fight. Great Vault and bags need a `/simc` paste (below)."
        )
    else:
        st.caption(
            'Best for "can my gear survive a +X key?" — pulls your '
            "**currently equipped** gear online (Blizzard Armory when configured "
            "for exact stats, otherwise Raider.IO). This is your *current* gear, "
            "which may differ from an older logged fight."
        )
    # Region → realm → name, matching the order every WoW armory/lookup site
    # (Raider.IO, WCL, Blizzard Armory) uses. Region renders BEFORE realm so
    # the realm dropdown can scope its options to the chosen region; changing
    # region clears the realm (a realm from the old region isn't in the new
    # region's options — a Streamlit selectbox raises if its session value
    # isn't an option). See _reset_rio_realm.
    #
    # Deliberately NOT an st.form (which would give "Enter submits" for free):
    # forms rely on Streamlit's internal current-form tracking, and this
    # codebase's tests routinely do a bare `from simf.ui import app` (7 sites)
    # to read module-level constants — since app.py calls main() at module
    # scope, that import runs the whole script once OUTSIDE any real
    # ScriptRunContext ("missing ScriptRunContext" warning). A form entered in
    # that bare pass never gets its "active form" state torn down properly,
    # permanently corrupting later st.button() calls for the rest of that
    # pytest worker — confirmed by reproduction, not a hypothetical. The
    # on_change flag below gets the same "press Enter in the name field to
    # search" UX without touching that machinery.
    rio_cols = st.columns([1, 2, 2, 2])
    with rio_cols[0]:
        rio_region = st.selectbox(
            "Region", ["eu", "us", "kr", "tw"], key="_rio_region", on_change=_reset_rio_realm
        )
    with rio_cols[1]:
        # Searchable dropdown beats free-typing a realm name. `accept_new_options`
        # keeps a free-text escape hatch for a realm missing from the bundled
        # list (new/renamed) — but it must NOT be what handles region-switch
        # (it would silently treat a stale realm as a custom one and 404);
        # _reset_rio_realm does that.
        rio_realm_name = st.selectbox(
            "Realm",
            options=realm_names(rio_region),
            index=None,
            placeholder="Type to search…",
            accept_new_options=True,
            key="_rio_realm",
        )
        # Pass the real Blizzard slug (naive name-slugification breaks
        # special-char realms); fall back to the raw input for a custom realm.
        rio_realm = slug_for(rio_region, rio_realm_name) or (rio_realm_name or "")
    with rio_cols[2]:
        # on_change fires on Enter AND on blur, but only when the committed
        # value actually changed — so it can't misfire on an unrelated click
        # elsewhere with the name field untouched, only on a genuine edit.
        rio_name = st.text_input("Character name", key="_rio_name", on_change=_trigger_rio_fetch)
    with rio_cols[3]:
        st.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
        fetch_clicked = st.button(
            "Look up my character", type="primary", width="stretch"
        ) or _ss().pop("_rio_fetch_triggered", False)

    st.divider()

    # ── Secondary path: full /simc export (the only source of vault + bags) ──
    # Heading + caption are job-led ("Check your vault picks") so the easy
    # name-lookup path never reads as the lesser one — the /simc export is the
    # tool you reach for the Tuesday vault verdict, not a wall (user test,
    # 2026-06-09). KEEP the heading + placeholder verbatim — they tested well.
    st.markdown("### Check your vault picks (and bags)")
    st.caption(
        "Only a `/simc` export carries your **Great Vault and bags**. Type "
        "`/simc` in WoW chat — one-time SimulationCraft addon — and paste the "
        "result here."
    )
    raw = st.text_area(
        "SimC export",
        height=180,
        max_chars=_PUBLIC_MAX_SIMC_CHARS if _is_public_mode() else None,
        key="_simc_textarea",
        placeholder='warrior="YourName"\nspec=protection\n…',
        label_visibility="collapsed",
    )
    # NOTE: do NOT disable on `not raw` — Streamlit doesn't push pasted text
    # into widget state until the textarea blurs (mouse-out / Tab), so a fresh
    # paste leaves Load greyed-out and the user wrongly clicks another button.
    # Validate at click time instead (see _do_simc_load).
    load_clicked = st.button("Load from /simc", width="stretch")

    # ── Fallback: a ready-made sample build (used once, if ever) ──────────────
    # Demoted below both real paths and never type="primary" — the escape hatch
    # for a visitor with nothing of their own to load, not a peer of the two
    # real start paths (user feedback, 2026-06-14).
    st.divider()
    st.caption("New here? Load a sample build to look around.")
    demo_clicked = st.button(
        "Load sample build",
        help="A sample Protection Warrior with gear, bags, and vault items — "
        "for looking around without your own export.",
    )

    # ── Dispatch ──────────────────────────────────────────────────────────────
    # Run the chosen load AFTER the whole form is laid out, inside the top
    # status_slot, so the spinner/errors render at the top — not beside the
    # button. Each loader ends in st.rerun() on success → the gear surface.
    if fetch_clicked:
        with status_slot:
            _do_raider_io_load(rio_name, rio_realm, rio_region)
    elif demo_clicked:
        with status_slot:
            _load_demo_character("brutoh")
    elif load_clicked:
        with status_slot:
            _do_simc_load(raw)


def _load_demo_character(name: str) -> None:
    # Demo YAMLs carry pre-calibrated stats — bypasses the Blizzard-API
    # stats-resolution step that the live SimC paste path uses.
    yaml_path = DATA_DIR / "characters" / f"{name}.yaml"
    if not yaml_path.exists():
        _announce_status(f"Couldn't find the demo character `{name}`.")
        return
    with open(yaml_path) as f:
        d = yaml.safe_load(f)

    char_data = {k: v for k, v in d.items() if k in Character.__dataclass_fields__}
    # Remember which demo this is so the diff card can build a cold-share URL
    # (?demo=<slug>) — an imported /simc gear set has no slug and can't be
    # encoded in a URL.
    _ss()["_loaded_demo_slug"] = name

    # Load items from the bundled SimC file (if any) for vault/bag UX. Resolve
    # under the packaged data dir FIRST so the demo's gear ships in the wheel /
    # public container (no repo root or examples/ there); fall back to the repo
    # root for any dev-tree path (e.g. a legacy examples/ reference).
    simc_path = d.get("simc_path")
    if simc_path:
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        full = next(
            (c for c in (DATA_DIR / simc_path, repo_root / simc_path) if c.exists()),
            DATA_DIR / simc_path,
        )
        if full.exists():
            from simf.io.simc_import import parse_simc_string

            try:
                parsed = parse_simc_string(full.read_text())
                _ss()["simc_equipped"] = parsed.items
                _ss()["simc_bag_items"] = parsed.bag_items
                _ss()["simc_vault_items"] = parsed.vault_items
                _ss()["simc_account_ilvl_ceiling"] = parsed.account_ilvl_ceiling
                _ss()["simc_upgrade_currencies"] = parsed.upgrade_currencies
                _ss()["simc_catalyst_currencies"] = parsed.catalyst_currencies
                # Carry server/region from the SimC so the Why-died target
                # field can pre-fill `Name-Server-Region` (ROADMAP 3.11).
                if parsed.server:
                    char_data["server"] = parsed.server
                if parsed.region:
                    char_data["region"] = parsed.region
            except Exception as exc:
                _announce_status(f"Couldn't parse the demo SimC export: {exc}", level="warning")
                _ss()["simc_equipped"] = {}
                _ss()["simc_bag_items"] = {}
                _ss()["simc_vault_items"] = {}
    else:
        _ss()["simc_equipped"] = {}
        _ss()["simc_bag_items"] = {}
        _ss()["simc_vault_items"] = {}

    _ss()["char_data"] = char_data
    record_event("character_loaded", load_method="demo")
    # A demo load isn't an online gear fetch — drop any staleness metadata
    # a prior Raider.IO/Blizzard lookup left behind so the run-config strip
    # doesn't keep captioning this demo's gear as "fetched via raiderio".
    _ss().pop("_gear_fetched_at", None)
    _ss().pop("_gear_source", None)
    _ss().pop("_gear_from_log", None)
    n_eq = sum(1 for v in (_ss().get("simc_equipped") or {}).values() if getattr(v, "item_id", 0))
    n_bag = sum(len(v) for v in (_ss().get("simc_bag_items") or {}).values())
    n_vault = sum(len(v) for v in (_ss().get("simc_vault_items") or {}).values())
    summary_bits = [f"{n_eq} equipped"]
    if n_bag:
        summary_bits.append(f"{n_bag} bag")
    if n_vault:
        summary_bits.append(f"{n_vault} vault")
    _ss()["_load_summary"] = f"Loaded {char_data['name']} demo — {', '.join(summary_bits)}."
    # Demo YAMLs carry curated, exact stats (see this function's own opening
    # comment) — never estimated, even if a prior load in this session was.
    _ss()["_gear_stats_estimated"] = False
    _flag_scroll_to_top()
    st.rerun()


# Public-mode cap on bag+vault items accepted from one /simc paste. Far above
# any real export (~25) but bounds a 25 MB attacker paste so it can't drive a
# huge vault-ranking loop. Infra limit, not a sim constant (cf. item_db caps).
_PUBLIC_MAX_EXTRA_ITEMS = 100

# Public-mode cap on the raw /simc TEXTAREA itself (`max_chars`, enforced by
# Streamlit's frontend before a paste can even reach `_do_simc_load`) — a
# narrower fix than `_PUBLIC_MAX_EXTRA_ITEMS` above, which only trims the
# ALREADY-PARSED bag+vault list; a giant paste still paid the full
# parse_simc_string cost first (2026-08-10 ship-readiness audit). Real
# exports (equipped + bags + vault) top out around 8 KB in examples/ — 50 KB
# is a ~6x margin over the largest real sample seen, generous for a real
# character while bounding the worst case a public-mode paste can cost to
# parse. Same cgroup-safety rationale as _PUBLIC_MAX_EXTRA_ITEMS: the public
# deploy's MemoryMax kills the WHOLE service on breach, not just one request.
_PUBLIC_MAX_SIMC_CHARS = 50_000


def _do_simc_load(raw: str) -> None:
    if not (raw or "").strip():
        _announce_status("Paste your `/simc` export into the box above before loading.")
        return
    with st.spinner("Reading your export and resolving gear stats…"):
        result = load_from_simc(
            raw,
            loadouts_for_spec_fn=_loadouts_for_spec,
            resolve_stats_fn=_resolve_equipped_stats,
            # A public visitor's paste is attacker-controlled — cap the bag+vault
            # item count so it can't drive a giant vault-ranking loop. Generous
            # vs any real export (~25 such items); the owner's loads are uncapped.
            max_extra_items=_PUBLIC_MAX_EXTRA_ITEMS if _is_public_mode() else None,
        )
    if isinstance(result, SimcLoadError):
        _announce_status(result.message)
        return
    assert isinstance(result, SimcLoadOk)  # noqa: S101 — internal invariant, not user input
    _ss()["char_data"] = result.char_data
    record_event("character_loaded", load_method="simc")
    _ss()["simc_equipped"] = result.equipped
    _ss()["simc_bag_items"] = result.bag_items
    _ss()["simc_vault_items"] = result.vault_items
    _ss()["simc_account_ilvl_ceiling"] = result.account_ilvl_ceiling
    _ss()["simc_upgrade_currencies"] = result.upgrade_currencies
    _ss()["simc_catalyst_currencies"] = result.catalyst_currencies
    _ss()["_load_summary"] = result.summary
    # Persist alongside the ephemeral toast (result.summary's own caveat
    # sentence) so a caption can keep warning on every render after the toast
    # has faded — see _gear_stats_estimated's docstring for why the one-time
    # toast wasn't enough.
    _ss()["_gear_stats_estimated"] = result.stats_estimated
    # A /simc paste isn't an online gear fetch — drop any staleness metadata
    # a prior Raider.IO/Blizzard lookup left behind (see _do_raider_io_load)
    # so this fresher, more-complete gear set never shows a stale caption.
    _ss().pop("_gear_fetched_at", None)
    _ss().pop("_gear_source", None)
    _ss().pop("_gear_from_log", None)
    # This is a real character's paste, not the bundled demo — drop any
    # `_loaded_demo_slug` a PRIOR demo load in this same browser session left
    # behind. Left stale, it would wrongly gate the key-verdict disk cache
    # (`ui/verdict.py`) into writing THIS real, unique gear set to disk —
    # exactly the server-side-persistence privacy leak that flag exists to
    # prevent (see `core.key_verdict_cache`'s module docstring).
    _ss().pop("_loaded_demo_slug", None)
    _flag_scroll_to_top()
    st.rerun()


def _do_raider_io_load(name: str, realm: str, region: str) -> None:
    if not (name or "").strip() or not (realm or "").strip():
        _announce_status("Enter both a character name and realm to look up.")
        return

    # Public instance: gate the lookup behind a rolling-window + per-session
    # cooldown (ADR 0001 pattern, mirroring the WCL surface). A Raider.IO fetch
    # is one cheap unauth GET, so the guard is lighter than the WCL one and uses
    # its OWN window + a distinct session key. The owner (single user) skips it.
    if _is_public_mode():
        from simf.core import wcl_budget

        remaining = wcl_budget.session_cooldown_remaining(
            _ss().get("_gear_lookup_last_ts"),
            wcl_budget.DEFAULT_GEAR_SESSION_COOLDOWN_S,
        )
        if remaining > 0:
            _announce_status(
                "You just looked up a character — try again in "
                f"{wcl_budget.format_retry(remaining)}.",
                level="warning",
            )
            return
        allowed, retry = wcl_budget.gear_lookup_guard().check()
        if not allowed:
            _announce_status(
                "simf is handling a lot of lookups right now — try again in "
                f"{wcl_budget.format_retry(retry)}.",
                level="warning",
            )
            return

    # Force the zero-auth Raider.IO source in public mode. "auto" would call the
    # Blizzard Armory FIRST when configured (which it is on the public Pi —
    # owner creds back item_db + WCL), spending the owner's OAuth budget and
    # exercising armory's region-interpolated request host. "raiderio" returns
    # before that branch, so neither is ever reached publicly. Owner mode keeps
    # "auto" (Blizzard-first, exact stats — the 2026-06-09 behaviour).
    source = "raiderio" if _is_public_mode() else "auto"
    from simf.io.gear_import import fetch_online_gear

    with st.spinner(f"Looking up {name}'s gear…"):
        result, used = fetch_online_gear(name.strip(), realm.strip(), region, source)
    if result is None:
        _announce_status(
            f"Couldn't fetch {name}-{realm} ({region}). Check the spelling — "
            "or the character may not be a supported tank spec, or not crawled yet."
        )
        return
    _ss()["char_data"] = result.char_data
    record_event("character_loaded", load_method=f"online_{used}")
    _ss()["simc_equipped"] = result.equipped
    # Online sources expose equipped gear only — clear any vault/bag/currency
    # state from a prior load so those surfaces don't show stale items here.
    _ss()["simc_bag_items"] = {}
    _ss()["simc_vault_items"] = {}
    _ss()["simc_account_ilvl_ceiling"] = None
    _ss()["simc_upgrade_currencies"] = {}
    _ss()["simc_catalyst_currencies"] = {}
    _ss()["_load_summary"] = result.summary
    # Blizzard's `/statistics` endpoint returns exact aggregate character
    # stats — not a resolver estimate. Raider.IO has no such endpoint: its
    # gear list is always run through the SAME per-item Wowhead resolver the
    # /simc-paste fallback uses (io/resolver_estimated_stats.py backfills the
    # same gem/enchant/base-stat corrections onto it), so it carries the
    # identical estimate-grade caveat and must set the flag True, matching
    # the paste-resolver's own honest labeling — previously hardcoded False
    # here regardless of which source actually answered, silently hiding the
    # "stats estimated" caption from every Raider.IO visitor (this path's own
    # separate caveat, current-gear-can-differ-from-fight-gear, is unrelated).
    _ss()["_gear_stats_estimated"] = used == "raiderio"
    # Real, looked-up gear — not the bundled demo. Same staleness-flag
    # cleanup as `_do_simc_load` above: a prior demo load in this session
    # must not leave `_loaded_demo_slug` set, or the key-verdict disk cache
    # would wrongly treat this unique gear set as demo-safe to persist.
    _ss().pop("_loaded_demo_slug", None)
    if _is_public_mode():
        _ss()["_gear_lookup_last_ts"] = time.monotonic()
    # Wall-clock fetch time + source, for the staleness caption in
    # _render_run_config_strip. Distinct from `_gear_lookup_last_ts` above
    # (monotonic, public-mode-only, internal rate-limit bookkeeping) — this
    # is display-only and renders for the owner too.
    _ss()["_gear_fetched_at"] = time.time()
    _ss()["_gear_source"] = used
    _flag_scroll_to_top()
    st.rerun()


def _surface_load_summary_toast() -> None:
    """Pop the post-load summary as a transient toast.

    Replaces the sidebar's persistent ``st.success`` banner that surfaced
    after a ``/simc`` paste or demo load. Toast is shown once and disappears,
    which matches the "I confirmed your load worked" intent without adding
    permanent chrome.
    """
    summary = _ss().pop("_load_summary", None)
    if summary:
        st.toast(summary, icon="✅")


def _tier_sets_badge_html() -> str:
    """Inline tier-set badge HTML for the header strip. Empty string when
    no character is loaded or the spec has no equipped tier pieces.

    Renders compact ('4pc Night Ender's Vesture' or '2/5 Night Ender's
    Vesture'). The bonus description rides on the badge's ``title``
    attribute so it surfaces on hover (and to screen readers via the
    accessible-name fallback) — the old sidebar-verbose rendering went
    away with the sidebar itself.
    """
    cd = _ss().get("char_data")
    if not cd:
        return ""
    statuses = equipped_set_status(_equipped(), cd["class_spec"])
    if not statuses:
        return ""
    parts: list[str] = []
    for s in statuses:
        # Browsers don't render embedded markup inside title="...";
        # escape quotes so a bonus description with quotes can't
        # break out of the attribute.
        bonus_attr = ""
        if s.active_bonus:
            safe_bonus = s.active_bonus.replace('"', "&quot;")
            bonus_attr = f' title="{safe_bonus}"'
        if s.active_threshold >= 2:
            parts.append(
                f'<span class="tier-badge"{bonus_attr}>'
                f"<strong>{s.active_threshold}pc</strong> "
                f"{s.set.name}</span>"
            )
        elif s.pieces > 0:
            # Below 2pc but partial set — show as muted "1/5 …" so
            # mid-acquisition characters still see their progress.
            parts.append(
                f'<span class="tier-badge tier-badge-muted"{bonus_attr}>'
                f"<strong>{s.pieces}/5</strong> {s.set.name}</span>"
            )
    return "".join(parts)


def _apply_share_url() -> None:
    # Single-fire via a sentinel — without it, later widget reruns would
    # re-apply `?view=gear` and defeat the user's nav clicks.
    if _ss().get("_share_url_applied"):
        return
    _ss()["_share_url_applied"] = True
    directive = parse_share_params(dict(st.query_params))
    if directive.is_empty():
        return
    # One-arrow instrumentation: count PUBLIC cold-share loads (privacy-safe,
    # no PII) so we can tell whether the shared link converts a stranger. Public
    # instance only — the owner's own loads shouldn't pollute the funnel signal.
    if _is_public_mode():
        from simf.core import share_hits

        share_hits.record(
            {
                "demo": directive.load_demo,
                "view": directive.view,
            }
        )
    if directive.advanced:
        _ss()["advanced_mode"] = True
    if directive.view is not None:
        _ss()["view"] = directive.view
    if directive.read_only:
        _ss()["read_only"] = True
    if directive.load_demo and not _has_character():
        _load_demo_character(directive.load_demo)  # calls st.rerun()

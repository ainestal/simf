"""simf UI — key-level verdict + skill-ladder + world-ceiling caption (L3).

The "Am I tankable enough for +X?" sweep panel, the Phase 2.10 skill-adjusted
ladder, and the real-world completion-ceiling honesty caption. No ``st.*`` at
module scope.

Lives at ``src/simf/ui/`` (same depth as app.py); imports only from the
L0/L1/L2 layers (``format_html``/``state``/``widgets``) + ``core``/``data``.
NEVER imports from ``app`` (strict L3 DAG).
"""

from __future__ import annotations

import streamlit as st

from simf.core.character import Character
from simf.core.metrics import death_rate_stderr_pp
from simf.ui.format_html import (
    _SPEC_PLURAL_LABEL,
    _pick_ladder_key,
    _skill_assumption_caption_for_spec,
    _verdict_claimed_key,
)
from simf.ui.helpers.disposition_ledger import render_disposition_ledger
from simf.ui.helpers.hp_trace_chart import render_hp_trace_chart
from simf.ui.helpers.tail_risk_chart import render_tail_risk_panel
from simf.ui.helpers.usage_tracking import record_event
from simf.ui.marginals import _char_marginals_signature
from simf.ui.state import _equipped, _is_read_only, _ss, _stats_unresolved
from simf.ui.widgets import _render_unresolved_stats_banner

_KEY_VERDICT_ITERATIONS = 200  # per-level iter count for the sweep
_SKILL_LADDER_ITERATIONS = 200  # per-tier iter count for the Phase 2.10 ladder

# The sweep's own compute parameters — named here (rather than left as
# inline literals in `_render_key_level_verdict_panel_body`) so they can
# feed BOTH the `compute_key_level_verdict` call AND `_key_verdict_signature`
# below without the two drifting apart. A future change to any of these is
# then a disk-cache miss (self-invalidating) rather than a silently
# mismatched hit — the same class of bug PR #275/#316 caused for the
# marginals cache before its signature had a single source of truth.
_KEY_VERDICT_DAMAGE_PROFILE = "m+_boss_tankbuster"
_KEY_VERDICT_HEALING_PROFILE = "m+_high_key_healer"
_KEY_VERDICT_AFFIX = "fortified"

# Rendered twice by the two-phase compute (the click run paints it as
# instant feedback; the sweep run re-renders it at the same position) —
# one constant so the two copies can't drift apart. Shares its duration
# with the pre-click caption below via `_SWEEP_SECONDS_LABEL`, so the
# panel can't promise two different sweep durations again — a 2026-07-05
# novice review timed a real click at 34s on this Pi, so "about 20-30
# seconds" is the figure that's true; the old in-flight caption's
# "8-12 seconds" was the optimistic, unratified one.
_SWEEP_SECONDS_LABEL = "about 20-30 seconds"
_SWEEP_PROGRESS_CAPTION = f"Sweeping +2 through +24… {_SWEEP_SECONDS_LABEL}."


# Per-spec defensive-CD names for the "assumes proper defensive use"
# caption beneath the key-level verdict headline. The Warrior list names
# the active-mitigation (Shield Block) + the magic-bucket short CD (Demo
# Shout) + the absorb (Ignore Pain) + the DR-stack bottoms (Shield Wall
# / Last Stand) — the press cadence the policy simulates and the player
# can actually skip. Other specs follow the same shape:
# active-mit-or-equivalent + the long DR CDs from LONG_CD_BUTTONS. Kept
# inline here rather than reusing LONG_CD_BUTTONS because that table
# only covers long-cooldown DR — the verdict's "press uptime" assumption
# also covers active mitigation, which lives in policy code, not in a
# central per-spec map.
def _render_world_ceiling_caption(verdict, class_spec: str) -> None:
    """Real-world completion-ceiling honesty caption.

    Surfaces beneath the verdict headline (above the modelling-scope
    caveat) when the player's *claimed* key level — what the headline
    is telling them they can do — is at or above the broadly-completed
    threshold for their spec.

    Stays silent when:
      * the spec isn't in the YAML table (defensive — adding a new
        tank shouldn't crash the surface),
      * the claimed key is below `caveat_below` (the floor at which
        the ceiling becomes relevant — below +14, the world ceiling
        isn't the conversation),
      * the claimed key is below `broad_completion_key` (the player
        isn't being told to push into the rarefied tail yet).

    Brutoh user-feedback origin (2026-05-27); see the YAML at
    `src/simf/data/world_ceilings.yaml` for source notes.
    """
    from simf.data.world_ceilings import world_ceiling_for

    ceiling = world_ceiling_for(class_spec)
    if ceiling is None:
        return
    claimed = _verdict_claimed_key(verdict)
    if claimed is None:
        return
    if claimed < ceiling.caveat_below:
        return
    if claimed < ceiling.broad_completion_key:
        return
    # Spec-friendly headline noun. The class_spec strings collapse
    # nicely on "_" — `protection_warrior` → "Protection Warrior" →
    # short form "Prot Warriors." Falls back to the raw spec when the
    # title-case form isn't useful.
    spec_label = _SPEC_PLURAL_LABEL.get(class_spec, class_spec.replace("_", " ").title())

    # Prefer the live distinct-player count from WCL over the hand-wavy
    # "a few hundred" descriptor when available. Capped counts read as
    # a floor ("at least 2,000+") because the WCL endpoint truncates
    # the row set; uncapped counts read as the exact integer ("at least
    # 1,253"). The "at least" framing is honest about the
    # single-encounter scope (count is a lower bound for the
    # cross-encounter union — see wcl_rankings.py docstring).
    if ceiling.broad_count is not None and ceiling.broad_count > 0:
        if ceiling.broad_count_capped:
            count_phrase = f"At least {ceiling.broad_count:,}+ {spec_label}"
        else:
            count_phrase = f"At least {ceiling.broad_count:,} {spec_label}"
        threshold_key = ceiling.broad_completion_key
        # When the count comes from the cross-encounter pre-warm cache
        # (CLI `simf rankings-refresh`), annotate so the user knows the
        # higher-precision union is in play. Single-encounter counts get
        # no suffix — they're a floor, framed already as "at least".
        scope_suffix = " (cross-encounter)" if ceiling.is_cross_encounter else ""
        st.caption(
            f"{count_phrase} have timed +{threshold_key} or higher this "
            f"season{scope_suffix} (world max: +{ceiling.world_max_key}). "
            "Survivability is one of several gates — group DPS, mechanic "
            "avoidance, and the timer all matter too."
        )
    else:
        st.caption(
            f"Only {ceiling.population_descriptor} {spec_label} have timed "
            f"+{claimed} this season (world max: +{ceiling.world_max_key}). "
            "Survivability is one of several gates — group DPS, mechanic "
            "avoidance, and the timer all matter too."
        )


def _render_healer_ask_caption(verdict) -> bool:
    """'What this build asks of your healer' — surfaces ``mean_dtps`` at
    the SAME key level the headline just claimed (``_verdict_claimed_key``).
    The headline above is 100% about damage-stream survivability and
    never mentions the healer; this is the first place a reader sees
    what the build asks of a healer.

    Uses ``mean_dtps``, NOT ``mean_hrps`` — despite "HRPS" (Healing
    Required Per Second) being the more literal name for this callout.
    This was originally built around ``mean_hrps``, plus a comparison
    against the token-bucket healer-throughput reference rate from
    ``m+_high_key_healer.yaml`` (PR #288). Both were dropped after an
    empirical check — not just reading docstrings — found ``mean_hrps``
    unfit for this surface, independently confirmed by
    calibration-scientist review (2026-07-07):

    * ``mean_hrps`` (``core/normalized_score.py: compute_hrps``) nets
      out not just the tank's self-heals but ALSO the sim's own
      modeled healer output — ``IterationResult.healing_total``
      (``core/runner.py``) sums the ENTIRE ``heal_timeline``, which
      includes the engine's ``baseline_hps`` per-tick heal and
      reactive-burst heal alongside real self-heals, true since the
      engine's first commit. That makes it the shortfall beyond simf's
      own idealized modeled healer, not a real "what a healer must
      supply" figure.
    * Independently reproduced on Brutoh: ``mean_hrps`` runs ~12x
      SMALLER than the real external-heal-received rate, AND is
      INVERSELY correlated with key level across the real sweep range
      (the modeled baseline healer scales with DTPS, so the residual
      *shrinks* as keys get harder) — a reader scanning the per-key
      bullet rows below would see "safer keys ask more of my healer,"
      backwards from reality.
    * It also collided with this same PR's tail-risk panel, which
      states a peak-incoming-HPS number on a sound gross/pre-heal basis
      (``core/runner.py``'s window-sum ÷ window length) landing an
      order of magnitude higher — two "healer HPS" numbers on one trust
      surface with different bases violates this project's own
      ratified rule (2026-07-05 review cycle: derived claims on a trust
      surface must share their basis with every other number there, or
      don't make the claim).

    ``mean_dtps`` doesn't have these problems: raw post-mitigation
    damage taken per second, BEFORE any self-sustain is credited (this
    file's generic Tank Score caption below glosses it the same way) —
    sound, honestly scoped, already computed, and it correctly
    INCREASES with key level. It still isn't literally "the HPS a
    healer must supply" (real self-sustain reduces the real ask below
    this number), so the copy below frames it as damage to help
    absorb/out-heal, not as a literal required-HPS claim.

    FIXED 2026-07-08 (item (a) below — was "KNOWN, NOT FIXED HERE"):
    (a) the pre-existing generic HRPS captions elsewhere in the
    codebase (this file's Tank Score caption below, and
    ``log_cd_plan.py``'s HRPS column tooltip) used to describe
    ``mean_hrps`` as "the healing your healer needs after your
    self-sustain" — the same overclaim diagnosed above, just without a
    comparison attached. Both now describe HRPS as an internal
    healing-throughput composite rather than a healer-facing HPS
    figure, and the per-key ladder row below no longer prints a raw
    ``mean_hrps`` number at all. No calibration/numeric impact,
    description only.

    STILL KNOWN, NOT FIXED HERE — flagged for a future ticket:
    (b) ``normalized_score.py``'s ``BASELINE_HRPS = 60_000``
    constant is stale (see the durable note left there) — Tank Score's
    30%-weighted HRPS component is saturated near ~0.95 across the
    whole real key range today.

    Returns whether the caption actually rendered. Stays silent
    (returns ``False``) when there's no key to anchor on: no claimed
    key, or no matching sweep point (defensive; shouldn't happen in
    practice).
    """
    claimed = _verdict_claimed_key(verdict)
    if claimed is None:
        return False
    point = next((p for p in verdict.points if p.key_level == claimed), None)
    if point is None:
        return False
    st.caption(
        f"**What this build asks of your healer at +{claimed}:** your "
        f"healer has to help absorb or out-heal roughly "
        f"**{point.mean_dtps:,.0f} DTPS** of incoming damage — the raw "
        "post-mitigation damage you take, before any of your own "
        "self-sustain is credited."
    )
    return True


def _key_verdict_signature(char: Character) -> tuple:
    """Disk-cache signature for the key-level verdict sweep
    (`core.key_verdict_cache`).

    Do NOT invent a new signature here — reuses `_char_marginals_signature`
    (`ui.marginals`), the same `Character` sim-affecting-field-plus-
    `constants_version` base the marginals disk cache and
    `disposition_ledger.py`'s ledger cache both key on, exactly the
    "established, tested, drift-proofed way this codebase invalidates a
    sim-derived cache when a Character field or constants.yaml changes"
    `core.character_fields` describes. Trailing elements are this sweep's
    own compute parameters (iterations / affix / profile names) — appended
    the same way `disposition_ledger.py`'s `_ledger_char_key` appends its
    own key level + damage multiplier: `sim_affecting_signature`'s
    docstring explicitly sanctions callers appending "their own extra key
    material after this tuple." Without them, a future change to any of
    those constants would be a silently-mismatched cache hit rather than a
    self-invalidating miss — the same class of bug PR #275/#316 caused for
    the marginals cache before it had one shared base.
    """
    return (
        *_char_marginals_signature(char),
        _KEY_VERDICT_ITERATIONS,
        _KEY_VERDICT_AFFIX,
        _KEY_VERDICT_DAMAGE_PROFILE,
        _KEY_VERDICT_HEALING_PROFILE,
    )


# Spec → caption-friendly plural noun. Lookup table beats a string
# transform because "Death Knights" / "Demon Hunters" are two words
# and "Brewmaster Monks" reads better than "Brewmaster Monk Monks."
def _render_key_level_verdict_panel(char: Character) -> None:
    """Phase 2.1 — 'Am I tankable enough for +X?' verdict.

    Off by default because each sweep runs ~10 sims (~3-5s on Pi-class
    hardware). User clicks 'Compute key-level verdict' to trigger.
    Result caches on session_state keyed by character hash; opening the
    expander on subsequent renders is instant.

    Rendered inside `st.fragment` — load-bearing, not an optimization.
    This panel is the only place in the app where a script run blocks for
    8-30s mid-render (the sweep, plus the skill ladder's ~5s right after).
    On a FULL-page rerun, Streamlit reconciles elements purely by tree
    position, so if anything above this panel renders a different number
    of elements than the previous run did (a one-shot element, a
    conditional caption, a banner toggling), every element below shifts
    position — and the previous run's not-yet-overwritten node for this
    panel sits on screen as a stale, dimmed DUPLICATE of the whole panel
    until the run finishes writing past it. With the compute inline, that
    window was the entire sweep: the live-reported duplicated-panel bug
    (examples/screenshots/double.png, 2026-07-19), reproduced
    deterministically on localhost with the natural flow — load character
    → open expander → click Compute with no other interaction in between
    (the load-transition run carried the one-shot scroll-to-top `st.html`;
    the compute click was the first rerun after it). Six earlier fix
    attempts missed it because every automated test flow had at least one
    extra rerun between load and compute, which consumed the position
    shift invisibly in milliseconds.

    The fragment removes the whole failure class for this panel: clicking
    "Compute verdict" now reruns ONLY this fragment (Streamlit wraps its
    output in one stable container at one stable page position), so the
    long-blocking run never participates in full-page positional
    reconciliation at all — no matter what conditional elements exist
    above, today or in the future. The specific drift source that
    triggered the live bug is ALSO fixed at its root (see
    `load._scroll_to_top_if_requested`'s stable slot); belt and braces.
    """

    @st.fragment
    def _fragment_body() -> None:
        _render_key_level_verdict_panel_body(char)

    _fragment_body()


def _render_key_level_verdict_panel_body(char: Character) -> None:
    """The panel body — see `_render_key_level_verdict_panel` (the
    `st.fragment` wrapper) for why the two are split."""
    from simf.core.key_level_verdict import compute_key_level_verdict
    from simf.core.profiles import load_damage_profile, load_healing_profile

    cache_key = "_key_verdict_cache"
    char_key = (
        char.class_spec,
        round(char.max_hp(), 0),
        round(char.total_armor(), 0),
        round(char.versatility_pct(), 4),
    )
    cached = _ss().get(cache_key)
    has_fresh = bool(cached and cached.get("char_key") == char_key)
    # Second phase of the two-phase compute (see the `if trigger:` block
    # below): armed by the click run, consumed by this run. Peeked here
    # (not popped — the compute block pops it) because the control-row
    # caption also needs to know a compute is in flight.
    pending = bool(_ss().get("_key_verdict_compute_pending"))

    # Disk cache — survives a browser refresh, so a repeat cold load of the
    # SAME signature (in practice: the bundled demo character, or a cold
    # `?demo=<slug>` share-link to it — see `core.key_verdict_cache`'s
    # module docstring for why writes below are scoped to demo loads only)
    # skips the ~20-30s sweep entirely, no click needed. `sig` is computed
    # unconditionally (cheap — no I/O) so it's available both here and at
    # the store() call in the `pending` compute block below. Read is
    # unconditional too: a hit can only ever occur for a signature a demo
    # load previously wrote, so serving it to any visitor is safe. Checked
    # BEFORE the button/caption block below so a disk hit renders
    # "Re-compute verdict" / "Saved from your last run" on the very first
    # render — exactly like a session-cache hit would, just one browser
    # session earlier than the session cache could ever manage on its own.
    sig = _key_verdict_signature(char)
    if not has_fresh and not pending:
        from simf.core import key_verdict_cache

        disk_verdict = key_verdict_cache.load(sig)
        if disk_verdict is not None:
            _ss()[cache_key] = {
                "char_key": char_key,
                "iterations": _KEY_VERDICT_ITERATIONS,
                "verdict": disk_verdict,
            }
            cached = _ss()[cache_key]
            has_fresh = True

    # `expanded=False`, static — NOT recomputed from `has_fresh` or a
    # `just_computed` session-state flag. Streamlit 1.56+ (this app runs
    # 1.57.0) tracks an expander's open/closed state natively client-side:
    # confirmed empirically with a minimal standalone probe app that once a
    # user manually opens the expander, it stays open across reruns
    # indefinitely even when the server sends a literal `expanded=False`
    # every render. The button lives inside the expander, so a user can
    # only ever click Compute after opening it — no server-side force-open
    # is needed, and an older recompute-every-render workaround
    # (`expanded=has_fresh or just_computed`) was removed as redundant.
    #
    # History, for anyone tempted to re-add that recompute while chasing a
    # ghost: this panel's real, live-reported duplicated-panel bug
    # (examples/screenshots/double.png, 2026-07-19) was NOT caused by the
    # `expanded=` prop at all — nor, on its own, by streamlit#14404 (a
    # real upstream st.spinner bug, also fixed here by dropping the
    # spinners). It was positional element-tree drift: a one-shot
    # scroll-to-top `st.html` on the load-transition run shifted this
    # panel's tree position on the very next rerun, and the inline 8-30s
    # sweep kept the previous run's panel node on screen as a stale
    # duplicate for the whole compute. Reproduced deterministically on
    # localhost, then fixed at the root (`load._scroll_to_top_if_requested`
    # now renders a stable slot every run) and hardened here (this panel
    # renders inside `st.fragment` — see `_render_key_level_verdict_panel`'s
    # docstring for the full story).
    with st.expander("🔑 Key-level verdict — am I tankable enough?", expanded=False):
        # Stats didn't resolve → the sweep would report a near-zero-eHP tank
        # dying at +2. Don't compute a verdict off garbage; say why.
        if _stats_unresolved(char, _equipped()):
            _render_unresolved_stats_banner(concise=True)
            return
        st.caption(
            "Sweeps the sim from Mythic 0 (+2) to title-push territory (+24). "
            "Reports the highest key you clear comfortably (death rate under 5%) "
            "and the highest you can still push (under 25%)."
        )
        cols = st.columns([2, 5])
        with cols[0]:
            if _is_read_only():
                from simf.ui.helpers.aria_button import aria_disabled_button

                aria_disabled_button(
                    "Compute verdict",
                    help="Read-only share — verdict computation disabled.",
                    key="compute_verdict_read_only",
                )
                trigger = False
            else:
                label = "Re-compute verdict" if has_fresh else "Compute verdict"
                trigger = st.button(label, width="stretch")
        with cols[1]:
            if trigger or pending:
                # A compute fires on this run (trigger) or is about to run
                # (pending). Neither "Takes about 20-30 seconds" (the
                # has_fresh/else fallthrough) nor a "done" claim belongs
                # here — the compute caption below already covers "in
                # progress," and the results appearing further down already
                # cover "done." `st.empty()` (not a bare no-op) matters: a
                # block that simply calls nothing this run still holds the
                # PREVIOUS run's leftover child until Streamlit's
                # end-of-script prune — an explicit empty element flushes
                # immediately instead, clearing any stale text the instant
                # this render reaches it.
                st.empty()
            elif has_fresh:
                st.caption(
                    f"Saved from your last run — {cached['iterations']} iterations per "
                    "key. Re-compute after a gear or trial change."
                )
            else:
                # A bare button with no time estimate reads the same as any
                # instant UI action — novice_tank round-1 review (2026-07-05)
                # timed a real click at 34s with no warning first, which
                # feels like the app hung. Matches the CD-plan optimizer's
                # own documented ballpark (log_cd_plan.py).
                st.caption(
                    f"Takes {_SWEEP_SECONDS_LABEL} — it sweeps every key level from +2 to +24."
                )

        if trigger:
            # Two-phase compute — feedback first, sweep second. Live user
            # report after the fragment fix (2026-07-19): "when I click
            # nothing happens, it seems it's stuck, and then suddenly the
            # results appear." Cause: the progress caption was enqueued and
            # then the SAME script run immediately dove into the 8-30s
            # GIL-bound Monte Carlo sweep — on a busy Pi the websocket
            # flush can starve behind the compute, so the caption might not
            # paint until the sweep finishes; and inside a fragment, the
            # rest of the page no longer dims either, leaving literally
            # zero visible change until results land. So the click run now
            # only paints the caption and finishes (fast — the script
            # yields, messages flush, the browser is GUARANTEED to paint
            # the feedback), then reruns just this fragment; the follow-up
            # run (the `pending` branch below) does the actual sweep.
            _ss()["_key_verdict_compute_pending"] = True
            st.caption(_SWEEP_PROGRESS_CAPTION)
            # scope="fragment" is only LEGAL during a fragment rerun —
            # Streamlit raises if it's passed during a full script run
            # (`execution_control._new_fragment_id_queue`), and a full run
            # is exactly what AppTest does when a test clicks this button.
            # In a real browser session the click always arrives as a
            # fragment rerun (the button lives inside this fragment), so
            # the fast path is fragment-scoped; the app-scoped fallback is
            # structurally safe too — full-page runs are drift-stable now
            # (see `load._scroll_to_top_if_requested`) — it just re-renders
            # more than necessary. The gate mirrors Streamlit's own
            # legality condition (`ctx.fragment_ids_this_run` non-empty).
            from streamlit.runtime.scriptrunner import get_script_run_ctx

            _ctx = get_script_run_ctx()
            in_fragment_rerun = bool(_ctx and _ctx.fragment_ids_this_run)
            st.rerun(scope="fragment" if in_fragment_rerun else "app")

        if pending:
            # Pop BEFORE computing, not after — if this run gets aborted
            # mid-sweep (a full-page rerun from an unrelated widget), a
            # still-armed flag would re-trigger the sweep on every later
            # render of this panel. Popped first, an aborted sweep just
            # returns the panel to its resting state; the user clicks again.
            _ss().pop("_key_verdict_compute_pending", None)
            # m+_boss_tankbuster stress-tests spike survival, which is
            # what the death-rate threshold actually measures. Pure
            # auto-attack profiles produce 0% death at every key level
            # for well-geared tanks, which collapses the verdict to a
            # useless "always comfortable." The tankbuster profile is
            # the conservative answer the user asked for.
            damage_profile = load_damage_profile(_KEY_VERDICT_DAMAGE_PROFILE)
            healing_profile = load_healing_profile(_KEY_VERDICT_HEALING_PROFILE)
            # Plain caption, deliberately NOT `st.spinner()` — see the
            # docstring above (streamlit/streamlit#14404). Rendered into its
            # own `st.empty()` slot (not a bare `st.caption()`) so it can be
            # cleared once the sweep returns — left un-cleared, this caption
            # sat on screen above the finished headline reading as if the
            # panel were still computing (live report,
            # examples/screenshots/elite-05-stale-sweeping-caption.png).
            progress_slot = st.empty()
            progress_slot.caption(_SWEEP_PROGRESS_CAPTION)
            verdict = compute_key_level_verdict(
                character=char,
                damage_profile=damage_profile,
                healing_profile=healing_profile,
                iterations=_KEY_VERDICT_ITERATIONS,
                affix=_KEY_VERDICT_AFFIX,
            )
            record_event(
                "verdict_computed",
                spec=char.class_spec,
                comfortable_max=str(verdict.comfortable_max),
                prog_ceiling=str(verdict.prog_ceiling),
            )
            progress_slot.empty()
            _ss()[cache_key] = {
                "char_key": char_key,
                "iterations": _KEY_VERDICT_ITERATIONS,
                "verdict": verdict,
            }
            has_fresh = True
            cached = _ss()[cache_key]
            # Persist to disk ONLY for the bundled demo character / a cold
            # share-link to it (`_ss()["_loaded_demo_slug"]` — set by
            # `ui/load.py`'s `_load_demo_character`, never by a real
            # SimC/Raider.IO/Armory load). Deliberately narrow, not an
            # oversight: a stranger's unique gear signature has near-zero
            # cache-hit value (nobody else will ever share it) and writing
            # it server-side would reintroduce the exact "server-side
            # persistence outliving your session" privacy carve-out already
            # flagged for the WCL cache. See `core.key_verdict_cache`'s
            # module docstring for the full rationale.
            if _ss().get("_loaded_demo_slug"):
                from simf.core import key_verdict_cache

                key_verdict_cache.store(sig, verdict)

        if has_fresh and cached:
            verdict = cached["verdict"]
            st.markdown(f"### {verdict.headline()}")
            if verdict.detail():
                st.caption(verdict.detail())
            # "What this build asks of your healer" (Batch C, 2026-07-07) —
            # anchored at the same key the headline just claimed, using
            # mean_dtps (NOT mean_hrps — see `_render_healer_ask_caption`'s
            # docstring for why HRPS, and a comparison against the
            # token-bucket healer-budget reference rate, were both built
            # and then deliberately dropped after an empirical check +
            # independent calibration-scientist review found mean_hrps
            # unfit for this surface). Sits above the world-ceiling
            # caption so the healer framing reads right after the
            # headline it's about.
            _render_healer_ask_caption(verdict)
            # World-ceiling caption — Brutoh user-feedback 2026-05-27:
            # "the highest keys in the world with a prot warrior are
            # currently +20, the highest in a spanish server in EU is
            # +19. So our calibration of 'can I survive this' is a
            # little off, according to the number of people that
            # completed keys over 16, it's not so easy to do." The
            # damage-stream math is fine; what was missing was a real-
            # world frame for how rare keys at the ladder's top
            # actually are. Sits ABOVE the modelling-scope caption
            # below (broad context first, scope caveat second).
            _render_world_ceiling_caption(verdict, char.class_spec)
            # Player-skill caveat (Ideas Backlog 2026-05-19, shipped via
            # the autonomous loop). The sweep assumes you press your
            # defensive cooldowns on cooldown — active mitigation (Shield
            # Block / Death Strike / etc.) and the DR-stack chain. A
            # player who forgets those presses will see a higher real-
            # world death rate than the verdict reports. The Prot-Warrior
            # skill ladder below *quantifies* this gap with measured
            # uptimes; for the other five tank specs (which don't have a
            # ladder yet) this caption is the only place the assumption
            # is stated. Distinct copy from the "avoidable-mechanic
            # damage isn't modeled" caveat further down — modelling
            # scope (what the engine simulates) vs player execution
            # (whether you actually pressed the button).
            st.caption(_skill_assumption_caption_for_spec(char.class_spec))
            # Per-key-level breakdown as a markdown bullet list (one
            # row per key). Plain `\n`.join collapsed into one paragraph
            # in the rendered HTML; bullets give each row its own line.
            lines = []
            band_dot = {
                "comfortable": "🟢",
                "progression": "🟡",
                "danger": "🔴",
            }
            # Noise floor (Top-5 #3, 2026-07-06 retrospective) — death_rate is
            # a fraction estimated from a finite iteration count, same
            # "respect the noise floor" treatment the gem/enchant gate and
            # the CD-plan verdict card already apply (shared formula, see
            # core.metrics.death_rate_stderr_pp). Gated on the healer-cap
            # fix landing first — an uncapped reactive-heal faucet made this
            # number's PRECISION beside the point when its ACCURACY was
            # already in question.
            _iters = cached.get("iterations", _KEY_VERDICT_ITERATIONS)

            def _death_rate_noise_display(p: float, n: int) -> tuple[float, bool]:
                """(noise pp, is_upper_bound) for one key's death-rate row.

                The plug-in binomial SE degenerates to exactly 0 at p=0 or
                p=1 — "±0.0pp" would read as false certainty at exactly the
                reading most comfortable-key rows show. Rule-of-three gives
                a defensible one-sided upper bound at that edge instead
                (validator finding, 2026-07-07 healer-cap review)."""
                if n > 0 and p in (0.0, 1.0):
                    return 300.0 / n, True
                return death_rate_stderr_pp(p, n), False

            # Brutoh feedback 2026-05-22: showing every key in the sweep
            # (+2 → +24) invited "the sim thinks I can push +24" when the
            # truth is "the model ran out of comfortable keys at +17."
            # `displayable_points()` trims the chain past the first cliff
            # so we don't dump fictional pushability past the resolution
            # of the model.
            shown_points = verdict.displayable_points()
            omitted = len(verdict.points) - len(shown_points)
            for p in shown_points:
                dot = band_dot.get(p.band, "·")
                # Tank Score surfaced per row — Brutoh user-feedback
                # 2026-05-21: "didn't we compute a coefficient of
                # survivability that takes into account self healing,
                # absorbs and other stuff?" Score is in [0, 1.0], so we
                # render two decimal places — finer than the ±0.1
                # rounding noise floor and matches the precision used
                # in the underlying normalization.
                #
                # HRPS was dropped from this row (2026-07-08, the ticket
                # `_render_healer_ask_caption`'s docstring named): it ran
                # ~20x too small AND fell as key level rose (~3,505 at +2
                # to ~2,361 at +19) while DTPS correctly rose 30k→49k,
                # because `compute_hrps` nets out the sim's OWN modeled
                # healer output, not just self-heals — see
                # `core/normalized_score.py: compute_hrps`'s docstring.
                score_pct = p.normalized_tank_score * 100
                noise_pp, is_bound = _death_rate_noise_display(p.death_rate, _iters)
                noise_str = f"≤{noise_pp:.1f}pp" if is_bound else f"±{noise_pp:.1f}pp"
                lines.append(
                    f"- {dot} **+{p.key_level}** · "
                    f"death **{p.death_rate * 100:.1f}%** ({noise_str}) · "
                    f"DTPS {p.mean_dtps:,.0f} · "
                    f"Tank Score **{score_pct:.0f}/100** · {p.band}"
                )
            if omitted > 0:
                last_shown = shown_points[-1].key_level
                top_swept = verdict.points[-1].key_level
                lines.append(
                    f"- ⋯ **+{last_shown + 1}** to **+{top_swept}** hidden — "
                    "past the model's resolution."
                )
            st.markdown("\n".join(lines))
            st.caption(
                f"**±pp next to death%** is the sweep's own statistical noise "
                f"floor at {_iters} iterations per key — two death-rate "
                "readings within that range aren't meaningfully different. "
                "**≤pp** on a 0.0% row means zero deaths were observed, not "
                "zero risk — it's a one-sided upper bound (rule of three), "
                "not a symmetric error bar."
            )
            st.caption(
                "**What this measures.** The sweep streams tank-buster + "
                "periodic damage at your character and counts deaths. It "
                "**does not** model mechanic damage (swirlies, dispatches, "
                "Voidstorm), group wipes, or timer pressure — the things "
                "that actually fail high keys. Read the chain as "
                "*damage-stream survivability*, not *can I finish the run*."
            )
            # DTPS was never glossed, and "without reading ETMI" dropped a
            # second undefined acronym right next to the first — a reader
            # who doesn't already know ETMI (Theck-Meloree Index, the
            # deeper burst-risk metric the Talent A/B panel surfaces
            # directly) gets no reassurance from a sentence whose whole
            # point is "you don't need to know this" (novice_tank + copy
            # audit, round-1 review, 2026-07-05).
            #
            # Batch C, 2026-07-07: the headline-anchored healer-ask
            # callout above is about mean_dtps now, NOT mean_hrps (see
            # `_render_healer_ask_caption`'s docstring) — it no longer
            # defines HRPS.
            #
            # 2026-07-08: this caption used to describe HRPS as a
            # literal healer-facing "healing per second" ask net of
            # self-sustain — the exact overclaim
            # `_render_healer_ask_caption`'s docstring flagged as
            # "KNOWN, NOT FIXED HERE" item (a). `compute_hrps`
            # (`core/normalized_score.py`) nets out not just self-heals
            # but ALSO the sim's own modeled healer output, so the
            # resulting number runs ~20x too small and, on the real
            # sweep range, FALLS as key level rises while DTPS correctly
            # rises — backwards from what a healer-facing figure should
            # do. HRPS still feeds the Tank Score weighting (untouched
            # here — this is a surfacing fix only), so it's named as an
            # internal composite term, not a literal HPS ask.
            st.caption(
                "**Tank Score** weights death rate (40%), an internal "
                "healing-throughput composite (30%), and DTPS (30%) — a "
                "single number that bakes in self-heals + absorbs so you "
                "can compare specs, builds, and gear without digging into "
                "deeper burst-risk metrics (ETMI) yourself. **DTPS** is "
                "the raw damage taken per second, before that self-sustain "
                "is credited."
            )

            # Tail-risk surfacing (2026-07-06 retrospective, Top-5 #2).
            # Same key-selection logic as the skill ladder below — the
            # first key where death starts being non-trivial is the most
            # informative one to show burst risk at, not the top of the
            # sweep (which is fictional pushability for most tanks).
            tail_risk_key = _pick_ladder_key(verdict)
            if tail_risk_key is not None:
                tail_risk_point = next(
                    (p for p in verdict.points if p.key_level == tail_risk_key), None
                )
                if tail_risk_point is not None:
                    render_tail_risk_panel(tail_risk_point, tail_risk_key)
                    # Same sample iteration as the bars above — HP over
                    # time (2026-07-28) — one representative curve, not a
                    # percentile-band fan chart across every iteration.
                    render_hp_trace_chart(tail_risk_point, tail_risk_key)
                    # "Where your survivability comes from" disposition
                    # ledger (2026-07-09) — property of the whole simulated
                    # run, not a per-swap delta, so it renders once here
                    # rather than per-key-row. Scaled to the SAME key's
                    # damage_multiplier as the tail-risk panel above so the
                    # two read as one consistent story about +{tail_risk_key}.
                    render_disposition_ledger(
                        char, tail_risk_key, tail_risk_point.damage_multiplier
                    )

            # Phase 2.10 — Skill-Adjusted Verdict ladder. Sits beneath the
            # per-key breakdown so the eye reads the headline (where you
            # are) → the cliff (where the model breaks) → the ladder
            # (how your play changes the answer). Prot Warrior only in
            # v1; other specs ignore skill_modifier in their policy
            # modules (engine fidelity gap, not a ladder bug).
            _render_skill_ladder_panel(char, verdict)


def _render_skill_ladder_panel(char: Character, verdict) -> None:
    """Render the Phase 2.10 ladder at the most informative key level.

    Only Prot Warrior in v1; surfacing the ladder for non-Warrior
    specs would render four identical rows (their policies ignore
    the modifier), which is misleading. We say so explicitly —
    silent absence reads as "broken tool".
    """
    if char.class_spec != "protection_warrior":
        st.caption(
            "Skill ladder is **Prot Warrior–only** today — your spec's "
            "policy doesn't yet respond to the skill modifier, so the "
            "ladder would show four identical rows. Support arrives with "
            "the spec's calibration work."
        )
        return

    push_key = _pick_ladder_key(verdict)
    if push_key is None:
        return

    from simf.core.profiles import load_damage_profile, load_healing_profile
    from simf.core.skill_ladder import compute_skill_ladder

    cache_key = "_skill_ladder_cache"
    char_key = (
        char.class_spec,
        round(char.max_hp(), 0),
        round(char.total_armor(), 0),
        round(char.versatility_pct(), 4),
        push_key,
    )
    cached = _ss().get(cache_key)
    has_fresh = bool(cached and cached.get("char_key") == char_key)

    if not has_fresh:
        damage_profile = load_damage_profile("m+_boss_tankbuster")
        healing_profile = load_healing_profile("m+_high_key_healer")
        # Plain caption, deliberately NOT `st.spinner()` — this call sits in
        # the exact same vulnerable position `_render_key_level_verdict_panel`'s
        # own spinner did (widgets already rendered above it, in the same
        # script run) that streamlit/streamlit#14404 names as the trigger for
        # a real, live-confirmed stuck-stale-DOM bug over a real network
        # connection. See that panel's docstring for the full root-cause story.
        # Rendered into its own `st.empty()` slot so it can be cleared once
        # the ladder returns — same treatment as the sweep caption above;
        # left un-cleared it read as still-computing above the finished
        # ladder rows.
        progress_slot = st.empty()
        progress_slot.caption(f"Computing skill ladder at +{push_key}… ~5 seconds.")
        ladder = compute_skill_ladder(
            character=char,
            damage_profile=damage_profile,
            healing_profile=healing_profile,
            key_level=push_key,
            iterations=_SKILL_LADDER_ITERATIONS,
        )
        progress_slot.empty()
        _ss()[cache_key] = {"char_key": char_key, "ladder": ladder}
        cached = _ss()[cache_key]

    ladder = cached["ladder"]
    if not ladder.points:
        return

    # Phase 2.10b — read the inferred-from-log tier if present. Keyed on
    # `log_name` so a stale tier from a previous log doesn't bleed through
    # after the user picks a different log on the Why-died surface.
    inferred = _ss().get("inferred_skill_tier")
    inferred_tier_id = inferred.get("tier_id") if isinstance(inferred, dict) else None
    inferred_uptime = inferred.get("sb_uptime_pct") if isinstance(inferred, dict) else None
    inferred_log = inferred.get("log_name") if isinstance(inferred, dict) else None
    inferred_casts = inferred.get("cast_count") if isinstance(inferred, dict) else None
    inferred_ds_rate = inferred.get("ds_press_rate") if isinstance(inferred, dict) else None
    inferred_ds_casts = inferred.get("ds_cast_count") if isinstance(inferred, dict) else None
    inferred_sb_tier = inferred.get("sb_tier_id") if isinstance(inferred, dict) else None
    inferred_ds_tier = inferred.get("ds_tier_id") if isinstance(inferred, dict) else None
    # PR #2 (2026-05-21): ceiling + bottleneck attribution. The ceiling
    # describes the *build floor* at perfect play, not the player's
    # actual rage usage — surface that distinction in the caption copy.
    inferred_ceiling = inferred.get("sb_ceiling_pct") if isinstance(inferred, dict) else None
    inferred_rage_floor = (
        inferred.get("sb_ceiling_rage_starved_pct") if isinstance(inferred, dict) else None
    )
    inferred_charge_floor = (
        inferred.get("sb_ceiling_charge_limited_pct") if isinstance(inferred, dict) else None
    )
    inferred_missed_pct = (
        inferred.get("sb_missed_pressable_pct") if isinstance(inferred, dict) else None
    )

    # How much does skill matter at this key? Spread = top - bottom
    # death rate. Reported alongside the heading so the player knows
    # whether 1pp differences are real or noise. Brutoh feedback
    # (2026-05-20): "1pp deltas read as 'skill doesn't matter'."
    top_death = ladder.points[0].death_rate
    bottom_death = ladder.points[-1].death_rate
    spread_pp = (bottom_death - top_death) * 100

    st.markdown("---")
    st.markdown(f"#### How your play changes the answer at +{push_key}")
    if spread_pp < 1.0:
        st.caption(
            f"At +{push_key} your skill bucket moves the death rate by less "
            "than 1 percentage point from defensive-CD uptime — gear is "
            "your bottleneck at this key, not buttons. Avoidable-mechanic "
            "damage isn't modeled."
        )
    else:
        # Caption split into two sentences per brutoh feedback 2026-05-22:
        # the ladder only models defensive-CD press uptime. Avoidable
        # mechanic damage (~50% of +18 tank deaths per external estimate)
        # isn't modeled — be honest about scope, don't bake in a fudge.
        st.caption(
            f"At +{push_key}, your skill bucket is worth **{spread_pp:.0f} "
            "percentage points** of death rate from defensive-CD uptime. "
            "Real spread is wider at the bottom."
        )
    if inferred_tier_id and inferred_uptime is not None:
        # Tell the player which signals drove the inference + the raw
        # numbers. Both signals are surfaced even when one is the floor,
        # so the player can see which lever to pull next. The combined
        # tier is min(sb_tier, ds_tier) per YAML order.
        log_short = inferred_log.rsplit("/", 1)[-1] if inferred_log else "your most recent log"
        sb_phrase = f"**{inferred_casts}** SB casts ({inferred_uptime:.0%} uptime)"
        if inferred_ds_rate is not None and inferred_ds_casts is not None:
            ds_phrase = (
                f"**{inferred_ds_casts}** Demo Shout casts "
                f"({inferred_ds_rate:.0%} of ideal cadence)"
            )
            # If one signal dragged the combined tier down, name it —
            # the player should know which habit to work on.
            floor_hint = ""
            if inferred_sb_tier and inferred_ds_tier and inferred_sb_tier != inferred_ds_tier:
                if inferred_ds_tier == inferred_tier_id:
                    floor_hint = " Demo Shout cadence is the lower of the two."
                elif inferred_sb_tier == inferred_tier_id:
                    floor_hint = " Shield Block uptime is the lower of the two."
            st.caption(
                f"Based on {sb_phrase} and {ds_phrase} in `{log_short}`.{floor_hint} "
                "Upload a different log on the **Why did I die?** tab to re-bucket."
            )
        else:
            st.caption(
                f"Based on {sb_phrase} in `{log_short}`. "
                "Upload a different log on the **Why did I die?** tab to re-bucket."
            )

        # PR #2 — build floor + missed-pressable gap. Only render when
        # ladder ceiling data was available at inference time. Wording
        # is careful: the rage/charge fractions describe the *build* at
        # perfect play, not the player's actual rage usage (which would
        # require log-side rage replay — Phase 3.4).
        if inferred_ceiling and inferred_ceiling > 0:
            ceiling_pct = float(inferred_ceiling)
            rage_floor_pct = float(inferred_rage_floor or 0.0)
            charge_floor_pct = float(inferred_charge_floor or 0.0)
            missed_pct = float(inferred_missed_pct or 0.0)
            # Threshold at 0.01 (1pp) — anything smaller is noise on a
            # ~200-iteration ladder sim and the "missed-pressable" line
            # reads as misleading precision.
            if missed_pct >= 0.01:
                st.caption(
                    f"Your build's SB ceiling is **{ceiling_pct:.0%}** "
                    f"(at perfect play, rage gaps cost **{rage_floor_pct:.0%}** + "
                    f"charge cooldowns cost **{charge_floor_pct:.0%}**). "
                    f"You're **{missed_pct * 100:.0f}pp below ceiling** — pressable "
                    "but missed."
                )
            else:
                st.caption(
                    f"You're at or above your build's SB ceiling of "
                    f"**{ceiling_pct:.0%}** (rage gaps cost "
                    f"**{rage_floor_pct:.0%}**, charges **{charge_floor_pct:.0%}** "
                    "at perfect play)."
                )
    else:
        st.caption(
            "The verdict above assumes optimal play. Here's how it shifts as "
            "your button discipline changes. Upload a log on the **Why did "
            "I die?** tab to see where you sit on the ladder today."
        )

    # Phase 2.10c — anchored coaching callout from the inferred log.
    # When we have a top SB gap, surface it under the matched tier so
    # the generic focus string ("press Shield Block on cooldown") is
    # paired with a specific moment from the player's actual run.
    top_gaps = inferred.get("top_gaps") if isinstance(inferred, dict) else None
    top_gap = top_gaps[0] if top_gaps else None

    # Render each tier as a row: label + survive % + focus hint. Tier
    # the user's log matches gets a `← you` marker on the label so the
    # eye lands on it before reading the focus string. Markdown bullet
    # list keeps the same visual rhythm as the per-key breakdown above.
    # Phase 2.10g (elite_tank ask, 2026-05-22) — surface the modifier
    # (press rate) and SB uptime per row so the gate is visible. "Right
    # now the tiers are opaque flavor labels — show me the gate, let me
    # argue with it." Rendered inline as a · -separated trio next to
    # the survive number.
    lines = []
    for p in ladder.points:
        survive_pct = (1.0 - p.death_rate) * 100
        press_pct = p.modifier * 100
        uptime_pct = p.mean_sb_uptime * 100
        marker = " &nbsp;← **you**" if p.tier_id == inferred_tier_id else ""
        row = (
            f"- **{p.label}**{marker} — survive **{survive_pct:.0f}%** "
            f"· presses **{press_pct:.0f}%** "
            f"· SB uptime **{uptime_pct:.0f}%**  \n"
            f"  *{p.focus}*"
        )
        if p.tier_id == inferred_tier_id and top_gap:
            row += (
                f"  \n  &nbsp;&nbsp;**On your last run:** "
                f"{top_gap['duration_s']:.0f}s uncovered at "
                f"**{top_gap['time_phrase']}** — "
                f"{top_gap['damage_during_gap']:,.0f} physical damage taken."
            )
        lines.append(row)
    st.markdown("\n".join(lines))

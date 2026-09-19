"""simf UI — the Cooldown Planner surface (Phase 6.1).

``render_cd_plan_panel`` replays the picked run with a brute-force search of
long-CD placements and surfaces the best plan + delta vs a reactive baseline,
plus the press/spike/candidate formatters and the @st.cache_data optimizer
wrapper. The prescriptive counterpart to log_segment_risk's diagnosis.
Extracted from ``log_view.py`` (PR 2/3).
"""

from __future__ import annotations

from dataclasses import replace as dc_replace

import pandas as pd
import streamlit as st

from simf.core.character import Character
from simf.core.metrics import death_rate_stderr_pp as _death_rate_stderr_pp
from simf.core.profiles import load_healing_profile
from simf.io.log_replay import load_replay
from simf.optimizer.cooldown_planner_optimizer import optimize_cooldown_plan
from simf.ui.log_data import _resolve_log_path

# ─── CD-plan optimizer wrapper ────────────────────────────────────────────────
#
# Phase 6.1 UI surface. Mirrors `simf cd-plan` (see cli.py:cd_plan) so the
# UI and CLI agree on what's "best." Cached by a content key built from the
# character snapshot + log/run + search budget so trial swaps on the Gear
# tab invalidate appropriately.


def _character_cache_key(char_dict: dict) -> tuple:
    """Hashable subset of `_ss()['char_data']` that affects the optimizer.

    The optimizer reads Character via Character.from_dict, so we hash the
    fields that change mitigation/HP math. We deliberately skip name / race
    cosmetics — they don't move the survivability math."""
    return (
        char_dict.get("class_spec", ""),
        char_dict.get("talents", ""),
        int(char_dict.get("stamina", 0)),
        int(char_dict.get("armor_from_gear", 0)),
        int(char_dict.get("haste_rating", 0)),
        int(char_dict.get("crit_rating", 0)),
        int(char_dict.get("mastery_rating", 0)),
        int(char_dict.get("versatility_rating", 0)),
        char_dict.get("max_hp_override"),
    )


@st.cache_data(show_spinner=False)
def _cached_cd_plan(
    _char_dict: dict,
    log_name: str,
    target: str,
    run_index: int,
    healer_profile: str,
    search_iterations: int,
    top_n_spikes: int,
    max_candidates: int,
    char_key: tuple,
):
    """Run the optimizer and return its OptimizerResult.

    ``char_key`` is the hashable cache discriminator; ``_char_dict`` (the
    leading-underscore disables hashing of the dict) is the real payload."""
    char = Character.from_dict(_char_dict)
    replay = load_replay(_resolve_log_path(log_name), target, run_index)
    heal = load_healing_profile(healer_profile)
    # Mirror the CLI baseline calibration (cli.py: cd_plan).
    # Without this the heuristic policy fires CDs against a baseline_hps
    # that doesn't match the log's actual healing throughput, and the
    # death-rate ranking will diverge from `simf cd-plan` for the same input.
    heal = dc_replace(heal, baseline_hps_abs=replay.actual_dealt / replay.duration_s * 1.1)
    return optimize_cooldown_plan(
        character=char,
        damage_profile=None,
        healing_profile=heal,
        events_override=replay.events,
        duration_override=replay.duration_s,
        search_iterations=search_iterations,
        top_n_spikes=top_n_spikes,
        max_candidates=max_candidates,
    )


# ─── CD-plan renderer ─────────────────────────────────────────────────────────


def _segment_for_time(t: float, segments) -> object | None:
    """Return the RunSegment containing absolute time ``t``, or None.

    Uses half-open `[start, end)` to avoid double-matching when two
    segments touch (the end of one boss is the start of the trash
    before the next). Returns None when ``t`` falls in pre-pull /
    post-run padding outside every segment.
    """
    if not segments:
        return None
    for seg in segments:
        if seg.start_time_s <= t < seg.end_time_s:
            return seg
    return None


def _segment_position_phrase(t: float, segments) -> str | None:
    """Return a player-readable phrase locating ``t`` inside its segment.

    Three buckets — `early in`, `midway through`, `late in` — chosen so
    they compose cleanly with both boss names (``early in Hadrox``) and
    trash labels (``early in the trash before Gemellus``). The trash
    label is lower-cased and articled so a sentence reads as English
    rather than as a segment-label dump.

    Returns None when ``t`` is outside every segment (caller falls back
    to absolute ``at M:SS`` framing). The user-facing rationale: a
    timestamp tells the player nothing actionable — they're not
    watching a clock during a key — but "early in Hadrox" is a
    relatable pull moment.
    """
    seg = _segment_for_time(t, segments)
    if seg is None:
        return None
    dur = max(seg.duration_s(), 1e-9)
    frac = (t - seg.start_time_s) / dur
    if frac < 0.33:
        position = "early in"
    elif frac < 0.66:
        position = "midway through"
    else:
        position = "late in"
    if seg.kind == "boss":
        where = seg.label
    else:
        # "Trash before Gemellus" → "the trash before Gemellus"
        where = "the " + seg.label[0].lower() + seg.label[1:] if seg.label else "the trash"
    return f"{position} {where}"


def _fmt_anchor_when(t: float, segments) -> str:
    """Render an anchor time as a positional phrase if segments allow,
    otherwise as `at M:SS`."""
    phrase = _segment_position_phrase(t, segments)
    if phrase:
        return phrase
    mm, ss = divmod(int(t), 60)
    return f"at {mm}:{ss:02d}"


def _fmt_press_row(
    press,
    segments=None,
    *,
    anchor_damage_by_time: dict[float, float] | None = None,
    anchor_tolerance_s: float = 0.5,
) -> str:
    """Render one press in the press timeline.

    When ``anchor_damage_by_time`` is supplied, presses whose
    ``time_s`` matches an anchor (within ``anchor_tolerance_s``) get a
    "covers a <X>M spike" suffix; chained on-cooldown presses (which
    have no anchored spike) get a "CD refresh" suffix instead.

    Falls back to the bare ``— <when>`` form when the caller doesn't
    pass anchor metadata (CLI path, baselines, tests that pre-date
    Phase 6.1's annotation work).
    """
    when = _fmt_anchor_when(press.time_s, segments)
    label = press.ability.replace("_", " ").title()
    rationale = _press_rationale(
        press.time_s,
        anchor_damage_by_time,
        tolerance_s=anchor_tolerance_s,
    )
    if rationale:
        return f"  - **{label}** — {when} · {rationale}"
    return f"  - **{label}** — {when}"


def _press_rationale(
    t: float,
    anchor_damage_by_time: dict[float, float] | None,
    *,
    tolerance_s: float,
) -> str:
    """Build the rationale clause for one press.

    Returns "covers a <X>M-damage spike" for presses anchored to a
    detected spike, "CD refresh" for chained on-cooldown presses that
    don't match any anchor, and "" when the caller passes no anchor
    map at all (legacy behaviour preserved)."""
    if anchor_damage_by_time is None:
        return ""
    if not anchor_damage_by_time:
        return "CD refresh"
    best_anchor_t: float | None = None
    best_delta: float = tolerance_s
    for anchor_t in anchor_damage_by_time:
        delta = abs(anchor_t - t)
        if delta <= best_delta:
            best_anchor_t = anchor_t
            best_delta = delta
    if best_anchor_t is None:
        return "CD refresh"
    dmg = anchor_damage_by_time[best_anchor_t]
    return f"covers a {_fmt_spike_damage(dmg)} spike"


def _fmt_spike_damage(dmg: float) -> str:
    """Format a spike's window-damage sum as the human-readable
    magnitude annotation reads ("1.2M-damage", "850k-damage")."""
    if dmg >= 1_000_000:
        return f"{dmg / 1_000_000:.1f}M-damage"
    if dmg >= 1_000:
        return f"{dmg / 1_000:.0f}k-damage"
    return f"{dmg:.0f}-damage"


def _fmt_candidate_label(candidate, segments) -> str:
    """Build a candidate label using segment context when available.

    Falls back to the optimizer's pre-built ``label`` (absolute
    timestamps) for baselines (`no_plan` / `naive`) which have no
    `press_anchors`, and for CLI callers that pass `segments=None`.
    """
    if not candidate.press_anchors or not segments:
        return candidate.label
    parts: list[str] = []
    for anchor_t, abilities in candidate.press_anchors:
        ability_phrase = " + ".join(abilities)
        when = _fmt_anchor_when(anchor_t, segments)
        parts.append(f"{ability_phrase} {when}")
    return " · ".join(parts)


# Threshold above which the heuristic policy's absolute death-rate
# is considered unphysical — typical M+ deaths sit at 0-30%, so a
# no-plan baseline above 50% means we're in the "pessimistic regime"
# (heuristic CD usage is systematically too cautious) and the absolute
# percent is more misleading than informative. The ranking between
# candidates still holds; we surface the delta and tag the regime.
_PESSIMISTIC_REGIME_THRESHOLD = 0.50


def _cd_prescription_card(
    headline: str,
    detail: str = "",
    *,
    warn: bool = False,
    regime_tag: str = "",
) -> None:
    """Smaller-than-verdict prescription card used by the CD-plan panel.

    The death verdict above already owns the page's single h2; this card
    renders an h3 with a 2px accent bar so it reads as a derivative
    answer, not a competing one. v0.9 redesign principle, ui-critic
    2026-05-17 #1."""
    cls = "cd-prescription cd-prescription-warn" if warn else "cd-prescription"
    tag = f' <span class="cd-regime-tag">{regime_tag}</span>' if regime_tag else ""
    detail_html = f"<p>{detail}</p>" if detail else ""
    st.markdown(
        f'<div class="{cls}"><h3>{headline}{tag}</h3>{detail_html}</div>',
        unsafe_allow_html=True,
    )


def _cd_plan_caption_for_spec(class_spec: str) -> str:
    """Build the panel caption with the player's actual long-CD names.

    Reads ``LONG_CD_BUTTONS[class_spec]`` so a Guardian sees "Incarnation
    and Survival Instincts", a Blood DK sees "Vampiric Blood and
    Icebound Fortitude", a Prot Warrior sees "Shield Wall and Last
    Stand" — not the hardcoded Warrior+sampling-of-other-specs string
    that pre-dated the AnonGuardian3 import.

    Falls back to a spec-agnostic phrasing when ``class_spec`` is empty
    (no character loaded) or unknown to the planner (the panel's other
    branch handles the "spec not modelled yet" CTA itself).
    """
    from simf.core.cooldown_planner import LONG_CD_BUTTONS

    buttons = LONG_CD_BUTTONS.get(class_spec, [])
    if not buttons:
        cd_clause = "your long cooldowns"
    else:
        names = [ability.replace("_", " ").title() for ability, *_ in buttons]
        if len(names) == 1:
            cd_clause = f"your long cooldown — {names[0]}"
        elif len(names) == 2:
            cd_clause = f"your long cooldowns — {names[0]} and {names[1]}"
        else:
            cd_clause = "your long cooldowns — " + ", ".join(names[:-1]) + f", and {names[-1]}"
    return (
        f"simf searches placements for {cd_clause} against this run. Trust the "
        "**ranking** between plans, not the absolute death rates: the reactive baseline "
        "simf compares against plays CDs more cautiously than a real tank, so the "
        "absolute numbers skew high."
    )


def render_cd_plan_panel(
    log_name: str,
    target: str,
    run_index: int,
    char_dict: dict | None,
    healer_profile: str,
    uncalibrated_warning: str,
    segments=None,
) -> None:
    """Phase 6.1 UI surface — Plan your cooldowns.

    Replays the picked run with a brute-force search of long-CD placements
    and surfaces the best plan + delta vs no-plan baseline. Sits below the
    per-segment risk view (descriptive "what killed you") so the prescriptive
    answer ("when to press buttons") follows the diagnosis.

    Gated behind an explicit button — the optimizer is 20-30s on Pi-class
    hardware even at the trimmed search budget below.

    ``segments`` is the run's segment list (already computed by the caller
    for the per-segment risk panel above). When provided, press anchors
    render as `early in Hadrox` instead of `at 4:21`. NOT a cache key —
    segments are derived from the same `(log_name, run_index)` tuple that
    already keys ``_cached_cd_plan``, so passing them through doesn't
    fragment the cache.
    """
    st.markdown("### Plan your cooldowns")

    if not char_dict:
        st.caption(_cd_plan_caption_for_spec(""))
        st.info(
            "Load your character first — paste your `/simc` export on the **Gear** "
            "tab. simf needs your armor, HP, and stats to know what each cooldown is worth."
        )
        return

    # The FULL caveat paragraph already rendered once, page-wide, above the
    # Gear/Log tab switch (app.py's `_uncalibrated_spec_warning()` banner) —
    # repeating it verbatim here read as a rendering bug, not a deliberate
    # caveat, and repetition erodes trust in the caveat itself (round-1
    # multi-agent review, 2026-07-05). Still surface a short local pointer:
    # this panel can be scrolled to well after the banner above is
    # off-screen.
    #
    # `_uncalibrated_spec_warning()` returns a non-empty string for TWO
    # different cases, deliberately (test_uncalibrated_spec_warning.py): a
    # genuinely uncalibrated spec, AND a calibrated:true spec that still
    # carries a per-spec modeling caveat (e.g. Guardian). A round-1 fix
    # gated this caption on "any non-empty string" but hardcoded text
    # claiming the spec was not calibrated — false for the calibrated case,
    # and directly contradicting the "✓ Guardian is calibrated" banner a
    # few inches above it on the same page (round-2 review, 2026-07-05: a
    # fresh instance of the exact two-surfaces-disagree bug this whole
    # cycle keeps hunting). The pointer below asserts nothing about
    # calibration status either way — accurate for both cases.
    if uncalibrated_warning:
        st.caption("ℹ️ See this spec's calibration note above this tab before trusting the plan.")

    spec = char_dict.get("class_spec", "")
    from simf.core.cooldown_planner import LONG_CD_BUTTONS

    if spec not in LONG_CD_BUTTONS:
        st.caption(_cd_plan_caption_for_spec(spec))
        st.info(
            f"CD planning isn't available for **{spec.replace('_', ' ').title()}** yet — "
            "simf hasn't modeled this spec's emergency buttons. It lights up once they land."
        )
        return

    st.caption(_cd_plan_caption_for_spec(spec))

    # Search settings deliberately deferred to AFTER the result — first
    # visit reads as "caption → button → verdict" so the page doesn't
    # leak engine knobs ahead of the primary CTA. ui-critic 2026-05-17 #3.
    # The slider values live in session state so they survive reruns.
    sliders_key = f"cd_plan_sliders::{log_name}::{target}::{run_index}"
    defaults = st.session_state.setdefault(
        sliders_key,
        {"iter": 50, "spikes": 3, "candidates": 8},
    )
    search_iterations = defaults["iter"]
    top_n_spikes = defaults["spikes"]
    max_candidates = defaults["candidates"]

    btn_key = f"cd_plan_run_{log_name}_{target}_{run_index}"

    # Read-only guard: a `?ro=1` cold-share viewer shouldn't accidentally
    # kick off the 20-30s optimizer search and mutate session state for
    # what's supposed to be a passive review — AND, just as importantly,
    # neither should an ordinary SIMF_PUBLIC visitor who reached this panel
    # without a `?ro=1` link at all (a fresh "Why did I die?" WCL lookup,
    # say). The previous check here (`st.session_state.get("read_only",
    # False)`) only ever catches the FIRST case: `read_only` is set in
    # session state exclusively by a `?ro=1` share-URL directive
    # (`load._apply_share_url`), never by `SIMF_PUBLIC` itself — so on the
    # live public deploy, any visitor who didn't arrive via such a link
    # could press "Find best plan" and run up to `max_candidates` unguarded
    # Monte Carlo searches, the one heavy compute path on this whole surface
    # with no rate limit and no single-flight lock anywhere above it.
    # `_is_read_only()` (this function's actual source of truth, imported
    # from `simf.ui.state` — safe, no cycle: `state.py` doesn't import this
    # module or anything that does) ORs in `_is_public_mode()` too, closing
    # that gap the same way `verdict.py`'s key-level-verdict panel already
    # does for its own, much heavier sweep.
    from simf.ui.state import _is_read_only

    if _is_read_only():
        from simf.ui.helpers.aria_button import aria_disabled_button

        aria_disabled_button(
            "Find best plan",
            help="Read-only share — plan search disabled.",
            key=btn_key,
        )
        st.caption("Read-only share — plan search disabled.")
        return

    if not st.button("Find best plan", type="primary", key=btn_key):
        st.caption(
            f"Press **Find best plan** to evaluate up to {max_candidates} candidate "
            f"placements (roughly {max_candidates * 5} seconds at {search_iterations} "
            "iterations each)."
        )
        return

    char_key = _character_cache_key(char_dict)
    with st.spinner("Searching cooldown placements…"):
        result = _cached_cd_plan(
            char_dict,
            log_name,
            target,
            run_index,
            healer_profile,
            search_iterations,
            top_n_spikes,
            max_candidates,
            char_key,
        )

    best = result.best
    no_plan = result.baseline_no_plan

    delta_dr_pp = (best.death_rate - no_plan.death_rate) * 100
    delta_p99 = best.p99_5s_window - no_plan.p99_5s_window
    noise_floor_pp = _death_rate_stderr_pp(no_plan.death_rate, search_iterations)
    pessimistic = no_plan.death_rate >= _PESSIMISTIC_REGIME_THRESHOLD

    # ── Verdict copy: player-subject, not algorithm-subject ─────────────
    # ui-critic 2026-05-17 #4 — "Heuristic CD usage is already best" is
    # engine-speak. Rewrites name the player's action, name the result.

    if not best.plan.presses:
        # The brute-force search couldn't beat the reactive heuristic.
        # In a pessimistic regime, framing this as "keep playing reactively"
        # is misleading — the absolute number is the problem, not the
        # plan-vs-no-plan question. Frame around the search outcome.
        if pessimistic:
            headline = "Search couldn't find a plan that beats reactive play here."
            detail = (
                "The reactive baseline sits at a very high death rate on this run, "
                "so the plan-vs-no-plan delta isn't a useful signal. Lean on the "
                "**Where you died** panel above to find what to fix instead."
            )
            regime_tag = "high-uncertainty run"
        else:
            headline = "You're already pressing your CDs as well as a scheduled plan would."
            detail = (
                f"Reactive play held the line at {no_plan.death_rate * 100:.0f}% "
                f"death rate (±{noise_floor_pp:.1f}pp noise). "
                "No fixed schedule beat that — keep playing reactively."
            )
            regime_tag = ""
        _cd_prescription_card(headline, detail, warn=False, regime_tag=regime_tag)
    else:
        # A scheduled plan beat the reactive baseline. Always frame the
        # *delta* as primary — the absolute % is a secondary detail that
        # we drop entirely in the pessimistic regime.
        signal = abs(delta_dr_pp) > noise_floor_pp
        if delta_dr_pp <= -1.0:
            verb = "drops"
        elif delta_dr_pp < 0:
            verb = "saves"
        else:
            verb = None

        if verb is None:
            headline = "No plan beat reactive play on this run."
            detail = (
                f"The best candidate matched the reactive baseline within "
                f"±{noise_floor_pp:.1f}pp of noise — keep playing reactively."
            )
            warn = True
            regime_tag = "high-uncertainty run" if pessimistic else ""
            # When the search did NOT beat the reactive baseline, showing
            # the "best" plan's press timeline contradicts the verdict
            # ("keep playing reactively" alongside 10 scheduled presses
            # reads as engine-confusion). Suppress it; the candidates
            # expander still has the underlying data for power users.
            _suppress_press_timeline = True
        else:
            _suppress_press_timeline = False
            if pessimistic:
                # Suppress the absolute percentages; lead with the delta.
                headline = f"Best plan {verb} death rate {abs(delta_dr_pp):.1f}pp vs reactive play."
                regime_tag = "high-uncertainty run"
            else:
                headline = (
                    f"Best plan {verb} death rate {abs(delta_dr_pp):.1f}pp "
                    f"({no_plan.death_rate * 100:.1f}% → {best.death_rate * 100:.1f}%)."
                )
                regime_tag = ""
            noise_note = (
                f" — below the ±{noise_floor_pp:.1f}pp noise floor; bump iterations to read it as signal"
                if not signal
                else f" (±{noise_floor_pp:.1f}pp noise)"
            )
            detail = (
                f"p99 5s spike {delta_p99:+,.0f}{noise_note}. "
                "These numbers are relative rankings — lean on the press timeline below."
            )
            warn = not signal
        _cd_prescription_card(headline, detail, warn=warn, regime_tag=regime_tag)

        if best.plan.presses and not _suppress_press_timeline:
            st.markdown("**Press timeline**")
            anchor_damage_by_time = dict(best.spike_damage_by_anchor)
            lines = [
                _fmt_press_row(
                    p,
                    segments,
                    anchor_damage_by_time=anchor_damage_by_time,
                )
                for p in best.plan.presses
            ]
            st.markdown("\n".join(lines))

    if pessimistic:
        # Healer-cap review finding (2026-07-07): on long, high-DTPS-residual
        # log replays, a pre-existing mitigation-model DTPS over-prediction
        # can drain the capped healer's measured throughput and read as
        # near-certain death even on a real, timed clear — previously masked
        # because the reactive healer was an uncapped, infinite safety net.
        # This is the known "structural physical-mit gap" (CONTRIBUTING.md roadmap
        # item 5), not something this cap change fixes or should be read as
        # confirming. Named, not silently shipped — see
        # docs/validation/phase4_healer_throughput_cap_2026_07_06.md.
        st.caption(
            "⚠️ **A very high death rate here can reflect either genuine "
            "danger or a known modeling gap.** On long fights, the sim's own "
            "damage prediction sometimes runs hotter than what this log "
            "actually shows — combined with a realistically finite (not "
            "infinite) healer, that gap alone can read as near-certain death "
            "on a run you actually cleared. Cross-check against **Where you "
            "died** above before trusting the absolute number; the relative "
            "plan-vs-reactive comparison above is more reliable than the "
            "raw percentage."
        )

    candidate_word = "plan" if len(result.candidates) == 1 else "plans"
    with st.expander(
        f"All {len(result.candidates)} candidate {candidate_word} — ranked", expanded=False
    ):
        st.caption(
            "Sorted by death rate (lower is better), tie-broken by p99 5s window. "
            "The `no_plan` and `naive (CD-on-CD)` rows are baselines simf always "
            "includes as sanity checks. Press timings read as a position within "
            "the pull (`early in Hadrox`) when log segments are known — the absolute "
            "timestamp depends on how your run played out and isn't actionable mid-key."
        )
        rows = []
        for c in result.candidates:
            rows.append(
                {
                    "Plan": _fmt_candidate_label(c, segments),
                    "Death rate": c.death_rate,
                    "p99 5s": c.p99_5s_window,
                    "Mean DTPS": c.mean_dtps,
                    "HRPS": c.mean_hrps,
                }
            )
        df = pd.DataFrame(rows)
        st.dataframe(
            df.style.format(
                {
                    "Death rate": "{:.1%}",
                    "p99 5s": "{:,.0f}",
                    "Mean DTPS": "{:,.0f}",
                    "HRPS": "{:,.0f}",
                }
            ),
            width="stretch",
            hide_index=True,
            column_config={
                # COPY-ACCURACY FIX (2026-07-08, this file's sibling of
                # `ui/verdict.py: _render_healer_ask_caption`'s docstring
                # item (a)): this tooltip used to read "net damage after
                # self-sustain per second the healer must supply," the
                # INTENDED semantic of mean_hrps, not the actual one —
                # `compute_hrps` also nets out the sim's own modeled
                # healer output, not just self-sustain, so the absolute
                # number runs far too small and isn't a literal
                # healer-facing HPS figure. Still valid for RELATIVE
                # ranking between candidates in this same table (the
                # "lower-HRPS plan is easier" comparison holds even
                # though the absolute number understates the real ask)
                # — see `core/normalized_score.py: compute_hrps`'s
                # docstring for the full writeup. Column kept (relative
                # ranking is sound); tooltip reworded to an honest
                # internal-composite gloss instead of a healer-facing ask.
                "HRPS": st.column_config.NumberColumn(
                    "HRPS",
                    help=(
                        "Healing Required Per Second — an internal "
                        "healing-throughput composite (nets out the sim's "
                        "own modeled healer output too, not just your "
                        "self-sustain, so treat the absolute number as "
                        "for ranking, not a literal healer-facing HPS "
                        "figure). Lower is easier to heal. When two plans "
                        "tie on death rate, the lower-HRPS plan is the "
                        "easier real-world ask."
                    ),
                ),
            },
        )

    # Settings expander moves BELOW the result so the first-visit
    # reading order is caption → button → verdict → details. Renamed
    # from "Search settings" to "Tune search depth" — the user only
    # opens this if they want more precision, which is a depth question.
    with st.expander("Tune search depth (advanced)", expanded=False):
        st.caption(
            f"This run used {search_iterations} iterations × {max_candidates} "
            f"candidates × {top_n_spikes} spike anchors. "
            f"Noise floor at this budget: ±{noise_floor_pp:.1f}pp."
        )
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            new_iter = st.slider(
                "Iterations per candidate",
                min_value=20,
                max_value=300,
                value=search_iterations,
                step=10,
                help=(
                    "More iterations means a tighter confidence band. "
                    "50 iter ≈ ±7pp noise at p=0.5; 200 iter ≈ ±3.5pp. "
                    "Raise this if you need to read sub-1pp deltas as signal."
                ),
                key=f"{sliders_key}_iter",
            )
        with col_b:
            new_spikes = st.slider(
                "Spike anchors",
                min_value=2,
                max_value=8,
                value=top_n_spikes,
                help="How many of the run's biggest damage spikes simf considers as cooldown anchors.",
                key=f"{sliders_key}_spikes",
            )
        with col_c:
            new_candidates = st.slider(
                "Max candidate plans",
                min_value=4,
                max_value=24,
                value=max_candidates,
                help="Upper bound on how many distinct plans simf evaluates.",
                key=f"{sliders_key}_candidates",
            )
        if (
            new_iter != search_iterations
            or new_spikes != top_n_spikes
            or new_candidates != max_candidates
        ):
            st.session_state[sliders_key] = {
                "iter": new_iter,
                "spikes": new_spikes,
                "candidates": new_candidates,
            }
            st.caption("Press **Find best plan** again to re-run with the new settings.")

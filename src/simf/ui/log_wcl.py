"""simf UI — the paste-a-Warcraft-Logs-URL flow.

``_render_wcl_url_flow`` validates the URL, fetches the report/fight/players via
the WCL v2 API, applies the public rate-limit guards (ADR 0001), and hands the
analyzed bundle to ``render_log_analysis``. Extracted from ``log_view.py`` (PR 3/3).
"""

from __future__ import annotations

import time

import streamlit as st

from simf.ui.helpers.usage_tracking import record_event
from simf.ui.log_analysis import render_log_analysis
from simf.ui.log_formatters import _OTHER_SENTINEL, _format_wcl_fight, _format_wcl_player


def _wcl_help_banner() -> None:
    """Render the credentials-missing help banner.

    The user can configure credentials at any time; the banner is the only
    surface in simf that exposes the steps. Keep this on the WCL tab body
    (not behind an expander) — a `not configured` state with no actionable
    text is the worst UX. The credential setup is free (register a client
    at https://www.warcraftlogs.com/api/clients/) so the cost is friction,
    not money.
    """
    st.info(
        "**Warcraft Logs API credentials not configured.**\n\n"
        "To analyze WCL URLs without uploading a local log, register a free "
        "API client at https://www.warcraftlogs.com/api/clients/ and either:\n\n"
        "1. Set environment variables `WCL_CLIENT_ID` and `WCL_CLIENT_SECRET` "
        "before launching simf, **or**\n"
        "2. Create `~/.simf/wcl_config.yaml` with two lines:\n"
        "   ```\n"
        "   client_id: <your-client-id>\n"
        "   client_secret: <your-client-secret>\n"
        "   ```\n\n"
        "Local log upload (the other tab) still works without WCL credentials."
    )


def _pick_wcl_target(
    players: list,
    report_code: str,
    fight_id: int,
    *,
    prefill: str = "",
    source_actor_id: int | None = None,
) -> str | None:
    """WCL twin of `_pick_target_name`.

    Renders a tank-first selectbox of detected players when ``players`` has
    anything, with an "Other (type a name)…" escape hatch for the unusual
    case where playerDetails came back empty for the picked actor.

    ``source_actor_id`` is the WCL actor ID from a ``source=N`` URL fragment;
    when it matches a player it takes precedence over ``prefill``, since the
    URL is the user's most recent explicit selection.
    """
    bare_prefill = prefill.split("-")[0] if prefill else ""

    if not players:
        # No playerDetails — fall back to the free-text input (legacy
        # behavior). This branch still triggers on very old reports or
        # trash-only pulls where WCL has no role data.
        target = st.text_input(
            "Character to analyze",
            value=bare_prefill,
            key=f"v9_wcl_target::{report_code}::{fight_id}",
            help="Your character name as it appears in the WCL report.",
        ).strip()
        if not target:
            st.info("Enter the character name to analyze (e.g. `Brutoh`).")
            return None
        return target

    options = [p.name for p in players] + [_OTHER_SENTINEL]
    # source=N from the URL wins over the SimC prefill — it's the player
    # the user *just* clicked before copying the share link.
    source_match: str | None = None
    if source_actor_id is not None:
        for p in players:
            if p.actor_id == source_actor_id:
                source_match = p.name
                break
    if source_match and source_match in options:
        default_idx = options.index(source_match)
    elif bare_prefill in options:
        default_idx = options.index(bare_prefill)
    else:
        default_idx = 0
    label_for = {p.name: _format_wcl_player(p) for p in players}
    label_for[_OTHER_SENTINEL] = _OTHER_SENTINEL

    pick = st.selectbox(
        "Character to analyze",
        options,
        index=default_idx,
        format_func=lambda v: label_for.get(v, v),
        key=f"v9_wcl_target_pick::{report_code}::{fight_id}",
        help=(
            "Auto-detected from the report. 🛡 Tank · 💚 Healer · ⚔ DPS. "
            "Pick **Other** to type a name simf didn't auto-detect."
        ),
    )
    if pick != _OTHER_SENTINEL:
        return pick

    typed = st.text_input(
        "Character name",
        value=bare_prefill,
        key=f"v9_wcl_target_typed::{report_code}::{fight_id}",
        help="Your character name as it appears in the WCL report.",
    ).strip()
    if not typed:
        st.info("Type a character name above, or pick a detected name from the dropdown.")
        return None
    detected_names = {p.name for p in players}
    if typed not in detected_names:
        st.warning(
            f"`{typed}` wasn't auto-detected in this fight. simf will still try the "
            "analysis — if it returns zero events, check the spelling against the "
            f"detected names ({', '.join(list(detected_names)[:3])}…)."
        )
    return typed


# Public analysis cap: refuse fights longer than this (minutes) so one very
# long log can't drain the shared WCL points budget in a single fetch. A normal
# M+ run finishes well under this; raid-length / depleted-key logs get bounced
# to a local run (ADR 0001).
_PUBLIC_MAX_FIGHT_MINUTES = 45


def _public_wcl_analyze_gate(fight, token) -> str | None:
    """Pre-flight checks for a *public* WCL analyze. Returns a user-facing
    message to show (and abort), or None to proceed.

    Order is cheapest-first: fight-length cap and per-session cooldown (both
    free, local), then the global rolling-window, then the WCL points
    pre-flight (one cheap query). The points check degrades *open* — a query
    failure never hard-blocks the flow (ADR 0001)."""
    from simf.core import wcl_budget
    from simf.io.wcl_api import fetch_rate_limit

    minutes = max(0.0, (fight.end_time_ms - fight.start_time_ms) / 60000.0)
    if minutes > _PUBLIC_MAX_FIGHT_MINUTES:
        return (
            f"This fight is {minutes:.0f} minutes long — public analysis is capped at "
            f"{_PUBLIC_MAX_FIGHT_MINUTES} minutes to protect the shared Warcraft Logs "
            "budget. Run simf locally for full-length fights."
        )

    remaining = wcl_budget.session_cooldown_remaining(st.session_state.get("_wcl_last_analyze_ts"))
    if remaining > 0:
        return f"You just ran an analysis — try again in {wcl_budget.format_retry(remaining)}."

    allowed, retry = wcl_budget.public_guard().check()
    if not allowed:
        return (
            "simf is handling a lot of Warcraft Logs requests right now — "
            f"try again in {wcl_budget.format_retry(retry)}."
        )

    try:
        rl = fetch_rate_limit(token)
        if not rl.has_headroom(wcl_budget.DEFAULT_RESERVE_FRACTION):
            return (
                "Warcraft Logs is busy right now — try again in "
                f"{wcl_budget.format_retry(rl.points_reset_in)}."
            )
    except Exception:  # noqa: S110 — best-effort fallback
        pass

    return None


def _run_wcl_analysis(analyze_fn, report, fight, target, token, target_actor_id):
    """Run ``analyze_wcl_fight``, surface user-facing errors, return the
    bundle (or None on failure). Extracted so the public single-flight path
    and the owner path share one error surface."""
    try:
        with st.spinner("Fetching damage events and analyzing…"):
            bundle = analyze_fn(report, fight, target, token=token, target_actor_id=target_actor_id)
        record_event("wcl_flow", wcl_status="success")
        return bundle
    except ValueError as exc:
        # actor-not-found and friends — surface the message verbatim, it's
        # written for end users.
        st.error(str(exc))
        record_event("wcl_flow", wcl_status="failed_value_error")
        return None
    except Exception as exc:
        st.error(
            "Couldn't reach Warcraft Logs for that fight. The report may be "
            "private, still uploading, or the API may be busy — try again in "
            "a minute, or use the **Local log** tab."
        )
        st.caption(f"Technical detail: {exc}")
        record_event("wcl_flow", wcl_status="failed_other")
        return None


def _wcl_coverage_for_render(session_state, cache_key: str, builder):
    """Build the WCL defensive-coverage report AT MOST ONCE per session.

    ``builder`` is a 0-arg callable returning a ``CoverageReport`` or None (and
    which may raise — a failure is recorded as None). The outcome (report OR
    None) is memoised in ``session_state`` under ``cache_key`` so subsequent
    Streamlit reruns reuse it instead of re-calling ``builder``.

    Why this exists: ``render_log_analysis`` re-runs on every Streamlit
    interaction once a fight has been analyzed, and the underlying Buffs fetch
    (``fetch_buff_windows``) is a network call against the shared owner key. Its
    disk cache only persists on SUCCESS — a failed fetch (rate limit / 429 /
    network) would otherwise re-fire the same GraphQL query on every rerun,
    ungated, exactly when the WCL budget is already exhausted. Memoising the
    outcome (incl. the failure) here bounds it to one attempt per session.
    """
    if cache_key in session_state:
        return session_state[cache_key]
    try:
        result = builder()
    except Exception:
        result = None
    session_state[cache_key] = result
    return result


def _resolve_wcl_target_info(
    players: list, target: str, fallback_class_spec: str | None
) -> tuple[int | None, str | None]:
    """The picked WCL player's own detected spec + actor ID, not the loaded
    Gear-tab character's. Before this fix, defensive-coverage coaching (and
    the rest of the analysis — bleed detection, the death verdict) always
    graded the LOADED CHARACTER's spec, even when analyzing a different
    player's fight entirely (e.g. a raid leader checking a Death Knight
    friend's pull while their own Warrior stays loaded on the Gear tab) —
    silently wrong, no error (round-2 review, 2026-07-05).

    Falls back to ``fallback_class_spec`` (the loaded character's spec)
    only when the picked player's own spec is unknown — an unrecognized
    WCL role icon, or a typed "Other" name with no matching player.
    """
    for p in players:
        if p.name == target:
            return p.actor_id, (p.class_spec or fallback_class_spec)
    return None, fallback_class_spec


def _render_wcl_url_flow(
    character_name: str = "",
    class_spec: str | None = None,
    public: bool = False,
) -> None:
    """Paste-a-WCL-URL flow.

    Three stages:

      1. URL input + validation. Bad URL → clean error, no stack trace.
      2. If credentials missing → help banner, halt.
      3. Fetch report → fight picker → on selection, fetch + analyze →
         render the same Why-died surface the local-log flow produces.

    ``public`` (ADR 0001): the public instance runs this on the shared owner
    client-credentials key, so report-fetch and analyze are gated by the
    rate-limit guard (per-session cooldown + global rolling-window +
    single-flight + WCL points pre-flight + a fight-length cap). Owner mode
    (``public=False``) is unguarded — bit-identical to the prior behaviour.

    Hydrate-from-WCL is intentionally NOT attempted today. WCL's GraphQL
    schema does expose ``playerDetails`` with gear / talents, but the
    parse surface is meaningfully different from ``COMBATANT_INFO``
    (slot numbering, talent encoding). Per the brief: hydrate is a
    nice-to-have; surface the caveat and run with the user's existing
    SimC-pasted character (when available) or a minimal default.
    """
    # Lazy imports so a totally-credential-less environment doesn't even
    # try to import requests on every render.
    from simf.core import wcl_budget
    from simf.io.wcl_api import (
        _get_token,
        fetch_player_details,
        fetch_report,
        is_configured,
        url_to_code_and_fight,
        url_to_source_id,
    )
    from simf.io.wcl_bridge import analyze_wcl_fight, build_wcl_coverage_report

    st.caption(
        "Paste a Warcraft Logs report URL — simf fetches the fight via the v2 API, "
        "no local log needed. Example: `https://www.warcraftlogs.com/reports/abc123#fight=4`."
    )

    url = st.text_input(
        "Warcraft Logs URL",
        value=st.session_state.get("v9_wcl_url", ""),
        key="v9_wcl_url",
        placeholder="https://www.warcraftlogs.com/reports/abc123XYZ#fight=4",
    )

    if not url.strip():
        if not is_configured():
            _wcl_help_banner()
        else:
            st.caption(
                "Paste a URL above to load fights. Credentials look configured — "
                "you should be able to fetch reports as soon as you submit a URL."
            )
        return

    # Validate the URL before we touch the network.
    try:
        report_code, default_fight_id = url_to_code_and_fight(url)
    except ValueError:
        st.error(
            "Couldn't parse that URL. Warcraft Logs URLs look like "
            "`https://www.warcraftlogs.com/reports/abc123XYZ` (optionally with "
            "`#fight=N` at the end to deep-link a fight)."
        )
        return

    if not is_configured():
        _wcl_help_banner()
        return

    # Cache the fetched report by code so repeating the URL doesn't re-hit
    # the API on every Streamlit rerun. Session-state-scoped — multiple
    # tabs / multiple users on the same Streamlit instance each get their
    # own cache.
    report_cache_key = f"v9_wcl_report::{report_code}"
    cached_report = st.session_state.get(report_cache_key)
    if cached_report is None:
        # Public-mode guard (ADR 0001): bound report-fetch flooding (a bot
        # pasting many report URLs) on the shared owner key. The heavier
        # analyze step has its own fuller gate below.
        if public:
            allowed, retry = wcl_budget.public_guard().check()
            if not allowed:
                st.info(
                    "simf is handling a lot of Warcraft Logs requests right now — "
                    f"try again in {wcl_budget.format_retry(retry)}."
                )
                return
        try:
            with st.spinner(f"Fetching report {report_code} from Warcraft Logs…"):
                token = _get_token()
                report = fetch_report(report_code, token=token)
        except Exception as exc:
            st.error(
                f"Couldn't fetch report `{report_code}`: {exc}. "
                "Double-check the URL or your API credentials."
            )
            return
        st.session_state[report_cache_key] = report
        st.session_state[f"{report_cache_key}::token"] = token
        cached_report = report
    report = cached_report
    token = st.session_state.get(f"{report_cache_key}::token", "")

    if not report.fights:
        st.warning(
            f"Report `{report_code}` has no fights. The report may still be uploading "
            "or only contain trash pulls outside any encounter."
        )
        return

    # Fight picker — default to the URL-anchored fight when one was given.
    fight_options = list(range(len(report.fights)))
    default_idx = 0
    if default_fight_id is not None:
        for i, f in enumerate(report.fights):
            if f.id == default_fight_id:
                default_idx = i
                break

    # Key includes ``default_fight_id`` so when the URL changes from
    # ``?fight=A`` to ``?fight=B`` the widget resets to the new default.
    # Streamlit only honors ``index=`` on the first render of a given key,
    # so a key scoped only to ``report_code`` would silently keep the
    # previous selection when the user pasted a new URL into the same
    # report — the exact bug Brutoh hit on 2026-05-27 (paste ``?fight=19``,
    # UI kept showing the previously-selected fight). Mirrors the
    # ``{report_code}::{fight_id}``-scoped key on the target picker below.
    pick = st.selectbox(
        "Fight",
        fight_options,
        index=default_idx,
        format_func=lambda i: _format_wcl_fight(report.fights[i]),
        key=f"v9_wcl_fight_pick::{report_code}::{default_fight_id}",
        help="Pick a fight from the report to analyze.",
    )
    fight = report.fights[pick]

    # Character picker: try playerDetails first so the user gets a
    # role-sorted dropdown. Cache per (report, fight) since the call is one
    # GraphQL hit and reruns of the Streamlit page would otherwise re-fetch
    # on every keystroke elsewhere. On API failure, fall back silently — the
    # picker drops to a free-text input.
    players_cache_key = f"v9_wcl_players::{report_code}::{fight.id}"
    players = st.session_state.get(players_cache_key)
    if players is None:
        # Public guard (ADR 0001): player-details is one GraphQL call per fight
        # selection — without a gate, a visitor flipping the fight picker spends
        # the shared owner key uncapped. On refusal, fall back to the free-text
        # target picker (players=[]) and DON'T cache, so a retry can fetch once
        # the window frees.
        if public and not wcl_budget.public_guard().check()[0]:
            players = []
        else:
            try:
                with st.spinner("Loading players from this fight…"):
                    players = fetch_player_details(report_code, fight.id, token=token)
            except Exception:
                players = []
            st.session_state[players_cache_key] = players

    target = _pick_wcl_target(
        players,
        report_code,
        fight.id,
        prefill=character_name,
        source_actor_id=url_to_source_id(url),
    )
    if not target:
        return

    st.caption(
        "Note: WCL imports don't yet hydrate your Character from the report's "
        "gear/stats. The analysis uses whatever character is loaded on the Gear "
        "tab for survivability stats — paste a `/simc` export there for accurate "
        "stat-weight numbers, or proceed with defaults for an approximate read. "
        "Defensive-coverage coaching still checks the picked player's own "
        "detected spec, not the loaded character's."
    )

    target_actor_id, target_class_spec = _resolve_wcl_target_info(players, target, class_spec)

    analyze_key = f"v9_wcl_analyzed::{report_code}::{fight.id}::{target}"
    success_key = f"{analyze_key}::ok"
    clicked = st.button("Analyze this fight", type="primary", key="v9_wcl_analyze")
    if clicked:
        st.session_state[analyze_key] = True
    if not st.session_state.get(analyze_key):
        return

    # Public-mode rate-limit guard (ADR 0001). Gate only a *fresh* analysis:
    # once this fight has analyzed in-session, reruns re-render from the disk
    # cache without re-spending budget, so they skip the gate + single-flight.
    already_ok = bool(st.session_state.get(success_key))
    if public and not already_ok:
        gate_msg = _public_wcl_analyze_gate(fight, token)
        if gate_msg is not None:
            st.session_state[analyze_key] = False  # require a fresh click
            st.info(gate_msg)
            return

        with wcl_budget.wcl_slot() as slot:
            if not slot:
                st.session_state[analyze_key] = False
                st.info("Another analysis is running right now — try again in a few seconds.")
                return
            bundle = _run_wcl_analysis(
                analyze_wcl_fight, report, fight, target, token, target_actor_id
            )
    else:
        bundle = _run_wcl_analysis(analyze_wcl_fight, report, fight, target, token, target_actor_id)

    if bundle is None:
        return  # error already surfaced by _run_wcl_analysis

    if public:
        st.session_state[success_key] = True
        st.session_state["_wcl_last_analyze_ts"] = time.monotonic()

    if bundle.summary.event_count == 0:
        st.warning(
            f"No damage-taken events for `{target}` in this fight — they likely "
            "weren't in the pull. Pick a different player from the dropdown above."
        )
        return

    # Defensive-coverage coaching for the WCL fight: build the hit-vs-coverage
    # report from fetched self-buff windows + the bundle's damage events, and
    # hand it to the shared renderer. Additive and best-effort — any failure
    # leaves it None so the rest of the surface still renders.
    #
    # The Buffs fetch is one extra GraphQL query. It is NOT wrapped by the
    # analyze single-flight slot, so it is memoised per session here (and disk-
    # cached on success inside build_wcl_coverage_report) — a rate-limited /
    # failed fetch is attempted at most ONCE per session rather than re-firing
    # on every rerun. A fresh analysis has already passed the session-cooldown +
    # rolling-window gate immediately above; reruns reuse the memoised outcome.
    coverage_report = None
    if target_class_spec:
        coverage_report = _wcl_coverage_for_render(
            st.session_state,
            f"{analyze_key}::cov::{target_class_spec}",
            lambda: build_wcl_coverage_report(
                report,
                fight,
                target,
                token,
                events=bundle.events,
                run=bundle.run,
                class_spec=target_class_spec,
                target_actor_id=target_actor_id,
            ),
        )

    # cd_plan_context=None — the CD-plan panel needs a local log to
    # `load_replay` from. The diagnostic surface (verdict, mit table,
    # per-segment risk, defensive coverage) runs without it.
    render_log_analysis(
        bundle.summary,
        bundle.mit_stats,
        bundle.death_events,
        class_spec=target_class_spec,
        run=bundle.run,
        events=bundle.events,
        deaths=bundle.deaths,
        segments=bundle.segments,
        cross_log_rows=[],
        cross_log_n_logs=1,
        cross_log_n_runs=0,
        cd_plan_context=None,
        coverage_report=coverage_report,
    )

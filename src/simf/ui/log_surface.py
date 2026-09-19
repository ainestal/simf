"""simf UI — the Why-died surface entry point + local-log flow.

``render_surface_log`` is the sole entry app.py calls; it dispatches to the
local-log flow (``_render_local_log_flow``: upload + picker + gated hydrate +
5-step cached analysis + Prot-Warrior skill inference) or, on the public
instance, straight to the WCL flow. Extracted from ``log_view.py`` (PR 3/3).
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from simf.io.combat_log import PartyMember
from simf.io.death_analysis import cross_log_npc_attribution
from simf.io.log_analysis_cache import get_or_compute
from simf.io.log_preflight import TARGET_COMBAT_LOG_VERSION, LogHealthReport, log_health_preflight
from simf.ui.log_analysis import render_log_analysis
from simf.ui.log_data import (
    UPLOADS_DIR,
    _cached_all_deaths_for_target,
    _cached_death_analysis,
    _cached_hydrate_character,
    _cached_log_summary,
    _cached_mitigation_audit,
    _cached_party_roles,
    _cached_run_events,
    _cached_runs,
    _log_picker_label,
    _resolve_log_path,
    list_logs,
)
from simf.ui.log_formatters import _OTHER_SENTINEL, _format_party_member, _format_run_identity
from simf.ui.log_wcl import _render_wcl_url_flow

# The narrated analysis sequence below (damage summary, mitigation audit,
# death reconstruction, run segmentation, cross-run aggregation) is really
# five cached scans, not four — a fifth, unnumbered spinner used to follow
# the "step 4 of 4" one, silently understating the remaining work. One
# constant so every step string and the total can never desync again.
_ANALYSIS_STEPS = 5


def _handle_log_upload() -> str | None:
    """File-uploader widget. Returns the basename of a newly saved upload
    (so the caller can preselect it in the dropdown) or None.

    Uploads are written to ``UPLOADS_DIR`` (``~/.simf/logs/``) to keep them
    out of the repo's ``examples/`` directory. Streamlit's uploader returns
    a fresh ``UploadedFile`` on every rerun until the user clears it, so we
    track the last-saved name in session state to avoid re-saving on each
    Streamlit rerun.
    """
    upload = st.file_uploader(
        "Upload a combat log",
        type=["txt"],
        key="v9_log_upload",
        help=(
            "Drop your `WoWCombatLog-*.txt` file here. simf keeps it between "
            "sessions so you don't have to re-upload. Turn on Advanced Combat "
            "Logging in WoW → System → Network before your key for the richest data."
        ),
    )
    if upload is None:
        return None
    # Browser-supplied filename is attacker-controlled and unsanitized by
    # Streamlit — `Path(...).name` strips any directory component (both
    # `/`- and `\`-separated, and an absolute path, which pathlib's `/`
    # operator would otherwise let override UPLOADS_DIR entirely) so a
    # crafted upload can't write outside UPLOADS_DIR.
    safe_name = Path(upload.name.replace("\\", "/")).name
    last_saved = st.session_state.get("v9_log_upload_last")
    if last_saved == safe_name:
        return safe_name
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS_DIR / safe_name
    dest.write_bytes(upload.getvalue())
    st.session_state["v9_log_upload_last"] = safe_name
    # New file landed on disk — invalidate the cached file listing so
    # the dropdown picks it up on this same rerun.
    list_logs.clear()
    # Preselect the upload in the dropdown. Setting the widget's key in
    # session_state BEFORE the selectbox renders is the only way to
    # override a previously-set selection (Streamlit ignores `index=`
    # once a widget has any stored state).
    st.session_state["v9_log_pick"] = safe_name
    st.success(f"Uploaded `{safe_name}` — {dest.stat().st_size / 1_000_000:.1f} MB.")
    return safe_name


def _render_log_health_banner(report: LogHealthReport) -> bool:
    """Render a calm, non-alarming preflight banner for `report`.

    Bias toward silence on the common case (ACL on, version matches) — a
    caption earns its place by being actionable, not decorative. Returns
    False when the file fails the basic structural check (empty, or not a
    single recognizable combat-log line found) so the caller can stop
    before spending 5 spinner steps analyzing a file that can't possibly
    parse; True otherwise.
    """
    if report.is_empty or not report.has_recognizable_line:
        st.error(
            "This file doesn't look like a WoW combat log — simf couldn't find a "
            "single recognizable line in it. Double-check you picked the right "
            "`WoWCombatLog-*.txt` file."
        )
        return False

    if report.acl_enabled is False:
        st.caption(
            "This log wasn't recorded with Advanced Combat Logging — some detail "
            "(spec/talent detection, defensive-coverage coaching) will be less "
            "precise. Turn it on next time: WoW → System → Network → Advanced "
            "Combat Logging."
        )
    if report.version_matches is False:
        st.caption(
            f"This log's format is version {report.found_log_version}, not the "
            f"version {TARGET_COMBAT_LOG_VERSION} simf is built against — some "
            "fields may not parse as expected."
        )
    return True


def _pick_target_name(detected: list[PartyMember], *, prefill: str = "") -> str | None:
    """Render the character-name picker, return the chosen name or None.

    If detection found at least one player, render a selectbox with the
    detected names (tank-first, role-icon-prefixed) plus an "Other…"
    escape hatch. Otherwise fall back to the legacy free-text input.

    Returns the chosen target name, or None when nothing actionable is set
    (no detected names and an empty text field, or "Other" picked with
    nothing typed yet). The caller treats None as "stop, don't analyze".
    """
    if not detected:
        # Corrupt log or one with no combat — let the user type and fall
        # through to validation downstream.
        target = st.text_input(
            "Character name",
            prefill,
            key="v9_log_target",
            help="Your character name as it appears in the log, e.g. `Name-Realm-Region`.",
        )
        if not target.strip():
            st.info(
                "simf couldn't auto-detect any players who took damage in this log. "
                "Type your character name in the `Name-Realm-Region` format above to continue."
            )
            return None
        return target.strip()

    options = [m.name for m in detected] + [_OTHER_SENTINEL]
    # Tank-first ordering means index 0 = tank when there's no prefill match.
    default_idx = options.index(prefill) if prefill and prefill in options else 0

    label_for = {m.name: _format_party_member(m) for m in detected}
    label_for[_OTHER_SENTINEL] = _OTHER_SENTINEL

    pick = st.selectbox(
        "Character to analyze",
        options,
        index=default_idx,
        format_func=lambda v: label_for.get(v, v),
        key="v9_log_target_pick",
        help=(
            "Auto-detected from the log. 🛡 Tank · 💚 Healer · ⚔ DPS. "
            "Pick the healer to see what's threatening their survival, or "
            "pick **Other** to type a name simf didn't auto-detect."
        ),
    )

    if pick != _OTHER_SENTINEL:
        return pick

    # "Other" branch — surface the free-text input for the rare case the
    # auto-detect missed someone (e.g. a player who never took damage and
    # never cast a heal in the window — usually impossible in M+).
    typed = st.text_input(
        "Character name",
        prefill,
        key="v9_log_target",
        help="Your character name as it appears in the log, e.g. `Name-Realm-Region`.",
    ).strip()
    if not typed:
        st.info("Type a character name above, or pick a detected name from the dropdown.")
        return None
    detected_names = {m.name for m in detected}
    if typed not in detected_names:
        st.warning(
            f"`{typed}` wasn't auto-detected in this run. simf will still run the "
            "analysis — if it returns zero events, check the spelling against the "
            f"detected names ({', '.join(list(detected_names)[:3])}…)."
        )
    return typed


def render_surface_log(
    character_name: str = "",
    class_spec: str | None = None,
    char_dict: dict | None = None,
    healer_profile: str = "m+_high_key_healer",
    uncalibrated_warning: str = "",
    public: bool = False,
) -> None:
    """Surface 2 entry — local log file picker + analysis trigger.

    Two sibling input paths:

      - **Local log**: the original ``WoWCombatLog-*.txt`` upload + picker
        flow. Untouched by the WCL addition — every existing test still
        covers it.
      - **Warcraft Logs URL**: paste a ``warcraftlogs.com/reports/…`` URL,
        pick a fight, simf fetches via the v2 GraphQL API and runs the
        same analysis pipeline. Surfaces a "credentials not configured"
        banner when ``WCL_CLIENT_ID`` / ``WCL_CLIENT_SECRET`` are absent.

    ``public`` (ADR 0001): on the public instance, render the WCL-URL flow
    ONLY — no local-file upload (the dominant OOM/abuse vector) — and apply
    the shared-key rate-limit guard inside ``_render_wcl_url_flow``.
    """
    # This is now the ONE page-title h2 for the surface — it renders on
    # every visit, cold or not, so it's the right element to carry that
    # level (mirrors `load.py`'s "Load a character" h2, one level under the
    # page's single h1 "simf"). A round-1 accessibility audit (2026-07-05)
    # flagged an H1→H3 skip here on a COLD visit (before any verdict
    # exists), since `log_analysis.py`'s `_verdict_card` only renders its
    # own heading once an analysis runs. Fixed by demoting `_verdict_card`
    # to h3 (see its docstring) — it's a subsection of this page, sibling to
    # "Death timeline" / "Where you died" / "Who keeps killing you", not a
    # second top-level heading — so the CD-plan prescription card's existing
    # "one h2 verdict per surface" test (test_cd_plan_tab.py) still holds:
    # there is exactly one h2 on this page, and it's this title.
    st.markdown("## Why did I die?")

    if public:
        st.caption(
            "Reconstruct a death from a Warcraft Logs report — what killed you, which "
            "abilities hit hardest, and where mitigation had gaps. No local-log upload "
            "on the public site; run simf locally for that."
        )
        _render_wcl_url_flow(
            character_name=character_name,
            class_spec=class_spec,
            public=True,
        )
        return

    st.caption(
        "Drop in a combat log or paste a Warcraft Logs URL. simf shows what killed "
        "you, which abilities hit hardest, and where mitigation had gaps. Turn on "
        "**Advanced Combat Logging** in WoW → System → Network before your key for "
        "the richest analysis."
    )

    tab_local, tab_wcl = st.tabs(["Local log", "Warcraft Logs URL"])

    with tab_local:
        _render_local_log_flow(
            character_name=character_name,
            class_spec=class_spec,
            char_dict=char_dict,
            healer_profile=healer_profile,
            uncalibrated_warning=uncalibrated_warning,
        )
    with tab_wcl:
        _render_wcl_url_flow(
            character_name=character_name,
            class_spec=class_spec,
        )


def _render_local_log_flow(
    character_name: str = "",
    class_spec: str | None = None,
    char_dict: dict | None = None,
    healer_profile: str = "m+_high_key_healer",
    uncalibrated_warning: str = "",
) -> None:
    """The original local-log upload + picker + analysis flow. Extracted
    out of ``render_surface_log`` so the WCL URL tab is a clean sibling."""
    just_uploaded = _handle_log_upload()

    logs = list_logs()
    if not logs:
        st.info(
            "No combat logs uploaded yet. Drop a `WoWCombatLog-*.txt` file above "
            "to get started — WoW writes these to the `Logs/` folder inside your "
            "game install."
        )
        return

    # Pass `index=` only when session_state doesn't already pin a selection
    # for this key — passing both triggers Streamlit's "default + session
    # state" warning. The upload handler sets session_state["v9_log_pick"]
    # to preselect a freshly-uploaded file; in that case let session_state
    # drive the selection on its own.
    selectbox_kwargs: dict = {"key": "v9_log_pick"}
    if "v9_log_pick" not in st.session_state:
        selectbox_kwargs["index"] = logs.index(just_uploaded) if just_uploaded in logs else 0
    # Only enrich the currently-selected row; siblings render as bare filenames.
    # Streamlit calls `format_func` once per option on every render, and enriching
    # all 10 capped rows scanned ~9s of combat-log data on a 23-log examples/ dir
    # — enough to tip AppTest's 30s timeout (see test_log_surface_link_…). The
    # selected row is the one whose runs we're about to scan anyway, so this
    # enrichment is free. Opening the dropdown shows filenames; the death-count
    # badge inside `_log_picker_label` uses the same scoped-to-selected-row
    # discipline (UNIT_DIED scan over one run's byte window, cached).
    #
    # KNOWN BUG, deliberately NOT fixed this round (round-2 review,
    # 2026-07-05): after a manual reselect, the OPTIONS LIST correctly
    # re-enriches the newly-picked row (confirmed live: reopening the
    # dropdown shows it right) — but the CLOSED box's own label goes stale
    # and shows the bare filename, surviving even an unrelated full rerun.
    # Root cause, confirmed empirically: making `format_func` unconditional
    # (`format_func=_log_picker_label`, dropping the `n == selected_name`
    # gate) DOES fix the closed-label staleness — but it doubles this
    # exact perf-sensitive test's runtime (18.9s -> 38.3s measured live),
    # confirming the original comment's cost concern is still real on
    # today's larger examples/ corpus. Trading a cosmetic label-staleness
    # bug for a confirmed 2x AppTest-timeout-margin regression is the
    # wrong trade for a rushed round-2 patch. A real fix needs either a
    # cheaper enrichment path or a widget-remount strategy (e.g. keying
    # the selectbox on the selection itself) — deferred, named, not
    # forgotten.
    selected_name = st.session_state.get("v9_log_pick")
    if selected_name is None and "index" in selectbox_kwargs:
        selected_name = logs[selectbox_kwargs["index"]]
    log_pick = st.selectbox(
        "Log file",
        logs,
        format_func=lambda n: _log_picker_label(n) if n == selected_name else n,
        **selectbox_kwargs,
    )

    # Cheap structural preflight — ACL on/off, COMBAT_LOG_VERSION match, file
    # not empty/corrupt — as early as possible after a log is resolved, ahead
    # of even the cheap CHALLENGE_MODE scan below. Scans only the first ~200
    # lines, not a second full parse. Bails early only on a genuinely
    # unparseable file; the ACL/version findings render as calm, non-blocking
    # captions so the user knows upfront why some detail may be less precise.
    log_health = log_health_preflight(_resolve_log_path(log_pick))
    if not _render_log_health_banner(log_health):
        return

    # Logs commonly contain multiple runs (back-to-back keys, possibly with
    # different parties). Pick the run before detecting names so detection
    # locks onto the *correct* party — in a 4-hour session log the first
    # 50 MB might be a friend's group that doesn't include the user at all.
    all_runs = _cached_runs(log_pick)
    if not all_runs:
        st.info(
            "This log doesn't contain any Mythic+ runs simf can analyze. To "
            "capture one in WoW:\n\n"
            "  1. Type `/combatlog` in chat\n"
            "  2. Open **System → Network → Advanced Combat Logging**\n"
            "  3. Run your key\n"
            "  4. Upload the resulting `WoWCombatLog-*.txt` above, or pick "
            "another log from the dropdown."
        )
        return

    if len(all_runs) == 1:
        run_index = 0
    else:

        def _label(i: int) -> str:
            # Full identity per row — dungeon + key + result + duration +
            # date so the user can disambiguate runs without clicking.
            # Brutoh idea #a (2026-05-25): the old selectbox label
            # ("Ara-Kara +18 · 28m · TIMED ✓") showed only the active
            # row's identity once the dropdown closed, and several
            # back-to-back keys at the same level read as duplicates.
            return _format_run_identity(all_runs[i])

        # Default to the latest *successful* run if any, otherwise the latest.
        successful = [i for i, r in enumerate(all_runs) if r.success]
        default_idx = successful[-1] if successful else len(all_runs) - 1
        # `st.radio` instead of `st.selectbox` so every row's identity is
        # visible at the same time AND the active row is visually distinct
        # via the native radio dot plus the bolded label rule painted by
        # the `.st-key-v9_log_run_pick` CSS in app.py. Selectboxes collapse
        # to a single row when closed — readers couldn't see which other
        # runs were in the log, defeating the "now analyzing" framing.
        #
        # The wrapper div is rendered around the radio to give layout
        # regression tests something to grep for, and exists as a backup
        # CSS hook in case the `.st-key-…` selector breaks across a
        # Streamlit upgrade.
        st.markdown('<div class="run-picker">', unsafe_allow_html=True)
        run_index = st.radio(
            "Run",
            list(range(len(all_runs))),
            index=default_idx,
            format_func=_label,
            key="v9_log_run_pick",
            help="This log contains more than one Mythic+ run — pick the one to analyze.",
        )
        st.markdown("</div>", unsafe_allow_html=True)

    # Auto-detect the party for the picked run, role-classified. Tank by
    # most damage-taken hits, healer by most heal-events cast, rest DPS.
    # Free-text was a silent-failure trap: combat logs use
    # `Name-Server-Region` (e.g. `Brutoh-Uldum-EU`) and a partial match
    # (`Brutoh`) yields zero events with no diagnostic.
    detected = _cached_party_roles(log_pick, run_index)
    target = _pick_target_name(detected, prefill=character_name)
    if target is None:
        # Either no players detected (corrupt / non-combat log) or the user
        # picked "Other" and hasn't typed yet. Bail before the Analyze button
        # so they don't fire off a 30-second scan that returns zero events.
        return

    # Gate BOTH the hydrate below and the analysis on an explicit Analyze
    # click. A defaulted log-picker value is not user intent: on a cold
    # `?view=log` visit the picker auto-selects the newest bundled example log,
    # and render-time hydrate would silently load that stranger's character
    # into the Gear / CD-plan surfaces. Hydrating only after the user analyzes
    # a run they chose keeps the skip-the-SimC-paste convenience for their own
    # log while killing the silent stranger-load. Sticky-keyed by
    # (log, target, run): a bare `if not st.button(): return` only holds for the
    # rerun that *was* the click — any later interaction (Find best plan, etc.)
    # reruns the script, the button returns False, and the analysis vanishes.
    analyze_key = f"v9_log_analyzed::{log_pick}::{target}::{run_index}"
    if st.button("Analyze this run", type="primary", key="v9_log_analyze"):
        st.session_state[analyze_key] = True
    if not st.session_state.get(analyze_key):
        return

    # Brutoh idea #b — auto-hydrate Character from COMBATANT_INFO. When the
    # log is ACL-on, the row carries spec / talents / gear / ratings already,
    # so the SimC paste step is busywork. Try hydrate; fall back to whatever
    # char_dict the caller passed (which may already be from SimC) if not.
    # Skipped when the user explicitly opts to override via SimC paste.
    hydrate_pin_key = f"v9_log_hydrate_pinned::{log_pick}::{target}::{run_index}"
    override_key = "v9_log_simc_override_active"
    auto_loaded_for = st.session_state.get("char_data_loaded_from_log_for")
    if not st.session_state.get(override_key):
        with st.spinner("Reading character from log…"):
            hydrated = _cached_hydrate_character(log_pick, target, run_index)
        if hydrated is not None:
            # Only write to session state when the (log, target, run) tuple
            # changes — avoids stomping a user's trial-swap state on every
            # rerun. The pin remembers the last write so re-rendering this
            # function doesn't re-mark `_load_summary`.
            pin_value = st.session_state.get(hydrate_pin_key)
            already_pinned = pin_value == hydrated.summary
            if not already_pinned:
                # Detect when we're switching characters (different spec /
                # name vs whatever was previously loaded). Header / spec
                # warnings are rendered at the top of the page BEFORE this
                # function runs, so a state change here doesn't repaint
                # them until the next user interaction. Force a rerun on
                # the FIRST write of a new character so the user sees a
                # consistent page on the very next paint — otherwise the
                # "Guardian Druid uncalibrated" warning from the previously
                # loaded character lingers atop a Protection Warrior page.
                prev_char = st.session_state.get("char_data") or {}
                spec_changed = prev_char.get("class_spec") != hydrated.char_data.get("class_spec")
                name_changed = prev_char.get("name") != hydrated.char_data.get("name")
                st.session_state["char_data"] = hydrated.char_data
                st.session_state["simc_equipped"] = hydrated.equipped
                # Logs don't carry bag/vault inventory — those stay empty.
                st.session_state.setdefault("simc_bag_items", {})
                st.session_state.setdefault("simc_vault_items", {})
                st.session_state["char_data_loaded_from_log_for"] = (
                    log_pick,
                    target,
                    run_index,
                )
                st.session_state[hydrate_pin_key] = hydrated.summary
                # Toast the load so the user sees it propagate to Gear/CD-plan.
                st.session_state["_load_summary"] = hydrated.summary
                # Gear-tab provenance: a log-hydrated character has no item
                # names (COMBATANT_INFO carries item ids only) and no
                # bag/vault — `_render_run_config_strip` reads this to warn
                # the Gear tab instead of leaving that silent.
                st.session_state["_gear_from_log"] = os.path.basename(str(log_pick))
                if spec_changed or name_changed:
                    st.rerun()
            # Live banner on this surface so the cause-and-effect is visible
            # without scrolling — the toast disappears after one rerun.
            st.success(hydrated.summary, icon="🛡️")
            with st.expander("Paste `/simc` to override the auto-loaded character"):
                st.caption(
                    "Auto-loaded characters use ratings + gear from the log's "
                    "`COMBATANT_INFO` row. Paste a `/simc` export here if you want "
                    "to override with your current in-game state (raid buffs off, "
                    "fresh enchants, etc.)."
                )
                if st.button(
                    "Switch to SimC paste",
                    key=f"v9_log_simc_override_btn::{log_pick}::{target}",
                    help="Routes you back to the SimC paste form. "
                    "Your trial-swap state is preserved.",
                ):
                    st.session_state[override_key] = True
                    # Clear the auto-loaded character so the app shell
                    # routes back to the SimC paste view on next rerun.
                    for k in (
                        "char_data",
                        "simc_equipped",
                        "simc_bag_items",
                        "simc_vault_items",
                        "char_data_loaded_from_log_for",
                        "_gear_from_log",
                    ):
                        st.session_state.pop(k, None)
                    st.rerun()
        elif auto_loaded_for is not None:
            # Previously auto-loaded for a different target — leave the
            # earlier load in place rather than yanking it under the user.
            pass

    # Five separate cached file scans on first analyze. Each takes ~5-10s
    # on a small server on a ~80MB log. A bare "Parsing log..." spinner
    # left users staring at the same string for 30+s with no progress
    # signal — surface the current step so they know it's not hung.
    with st.spinner(f"Summarizing damage taken (step 1 of {_ANALYSIS_STEPS})…"):
        summary, all_runs = _cached_log_summary(log_pick, target, run_index)
    if summary is None:
        st.error("simf couldn't summarize the selected run. Try another run or log.")
        return
    if summary.event_count == 0:
        # Common silent-failure mode: target name doesn't match the log's
        # `Name-Server-Region` format. The selectbox + detection upstream
        # makes this rare, but the freeform 'Other' branch can still mis-type.
        detected_names = [m.name for m in detected]
        suggestion = (
            f" Detected names in this run: **{', '.join(detected_names)}**."
            if detected_names
            else ""
        )
        st.error(
            f"Found zero damage-taken events for `{target}` in this run.{suggestion} "
            "Combat logs spell characters as `Name-Realm-Region` — a partial name "
            "matches nothing."
        )
        return

    with st.spinner(f"Auditing mitigation uptime (step 2 of {_ANALYSIS_STEPS})…"):
        mit_stats = _cached_mitigation_audit(log_pick, target, run_index)
    with st.spinner(f"Reconstructing deaths (step 3 of {_ANALYSIS_STEPS})…"):
        death_events = _cached_death_analysis(log_pick, target, run_index)
    with st.spinner(
        f"Splitting the run into boss and trash segments (step 4 of {_ANALYSIS_STEPS})…"
    ):
        run, events, deaths, _encs, segments = _cached_run_events(log_pick, target, run_index)

    # Cross-run NPC threats: aggregate fatal-window damage by NPC across
    # every run in the picked log. Cheap (one file, cached) — multi-log
    # aggregation is gated behind an opt-in toggle below because each log
    # is 50-250 MB and "scan everything" silently is a 5-minute trap.
    cross_log_rows: list[dict] = []
    cross_log_n_logs = 1
    cross_log_n_runs = 0
    with st.spinner(f"Checking your other runs in this log (step 5 of {_ANALYSIS_STEPS})…"):
        all_runs_with_deaths = _cached_all_deaths_for_target(log_pick, target)
    cross_log_n_runs = len(all_runs_with_deaths)

    # Opt-in multi-log scan — only when the user explicitly asks. Stored in
    # session state so subsequent reruns don't re-prompt.
    multi_log_key = f"v9_log_multi_log_{target}"
    if len(logs) > 1:
        if st.session_state.get(multi_log_key):
            extras: list[tuple[str, list]] = []
            extra_n = len(logs) - 1
            extra_word = "log" if extra_n == 1 else "logs"
            other_logs = [other_log for other_log in logs if other_log != log_pick]
            # Per-log progress — this loop can run 30s+ per file with no
            # per-file signal otherwise, reading as hung on a large corpus.
            with st.status(f"Scanning {extra_n} other {extra_word}…", expanded=False) as status:
                for i, other_log in enumerate(other_logs, start=1):
                    status.update(
                        label=f"Scanning log {i} of {extra_n} — {os.path.basename(other_log)}…"
                    )
                    runs_des = _cached_all_deaths_for_target(other_log, target)
                    if runs_des:
                        cross_log_n_logs += 1
                        extras.extend(runs_des)
                status.update(label=f"Scanned {extra_n} other {extra_word}.", state="complete")
            all_runs_with_deaths = list(all_runs_with_deaths) + extras
            cross_log_n_runs = len(all_runs_with_deaths)
        else:
            extra_n = len(logs) - 1
            extra_word = "log" if extra_n == 1 else "logs"
            if st.button(
                f"Also scan {extra_n} other {extra_word}",
                key=f"v9_log_multi_log_btn_{target}",
                help=(
                    "Searches every other combat log for the same character. "
                    "First scan can take 30 seconds or more per log."
                ),
            ):
                st.session_state[multi_log_key] = True
                st.rerun()
            st.caption("Each log takes ~30s or more the first time; results are cached afterwards.")

    if cross_log_n_runs >= 2:
        cross_log_rows = cross_log_npc_attribution(all_runs_with_deaths, top_n=6)

    cd_plan_context = {
        "log_name": log_pick,
        "target": target,
        "run_index": run_index,
        "char_dict": char_dict,
        "healer_profile": healer_profile,
        "uncalibrated_warning": uncalibrated_warning,
    }

    # Phase 2.10b/c — Skill-tier inference + anchored coaching callouts
    # from the user's actual Shield Block casts in this run. Stored in
    # session_state keyed on log path so the Gear-surface ladder can
    # render `← you` + the worst SB-gap moment on the matching rung.
    # Prot Warrior only — non-Warrior specs ignore skill_modifier in
    # their policies, so an inferred tier wouldn't anchor to a meaningful
    # spread on the ladder.
    if class_spec == "protection_warrior":
        try:
            from simf.core.skill_inference import compute_top_sb_gaps, infer_tier_from_casts
            from simf.io.combat_log import parse_cast_events

            log_path = _resolve_log_path(log_pick)
            # SB-gap diagnosis is part of the cached analysis bundle —
            # wrapping these two `parse_cast_events` calls with the
            # disk cache means a Streamlit *restart* on the same log
            # avoids a fresh 80 MB scan for Shield Block + Demoralizing
            # Shout casts. Same-session reruns still hit the legacy
            # in-memory caches above. Cache key includes the spell_id so
            # SB and DS get separate disk entries.
            sb_casts = get_or_compute(
                log_path,
                f"sb_casts::{target}",
                run_index,
                lambda: parse_cast_events(
                    log_path,
                    source_name=target,
                    spell_name="Shield Block",
                    spell_id=2565,
                    start_time_s=run.start_time_s,
                    end_time_s=run.end_time_s,
                    start_byte_offset=run.start_byte_offset,
                ),
            )
            # v3 (2026-05-21): pair the SB signal with Demoralizing Shout
            # cadence so the inference matches the engine's
            # `_skill_allows_press()` gate, which fires on both spells.
            ds_casts = get_or_compute(
                log_path,
                f"ds_casts::{target}",
                run_index,
                lambda: parse_cast_events(
                    log_path,
                    source_name=target,
                    spell_name="Demoralizing Shout",
                    spell_id=1160,
                    start_time_s=run.start_time_s,
                    end_time_s=run.end_time_s,
                    start_byte_offset=run.start_byte_offset,
                ),
            )
            # PR #2 (2026-05-21): if the Gear surface has already computed
            # the skill ladder, pull the per-tier sim SB uptimes for
            # nearest-neighbor matching, and the top-tier (modifier=1.0)
            # bottleneck breakdown for the trust caption. Falls back to
            # absolute threshold matching when the ladder cache is missing.
            ladder_sb_uptimes: list[tuple[str, float]] = []
            sb_ceiling_pct = 0.0
            sb_ceiling_rage_starved_pct = 0.0
            sb_ceiling_charge_limited_pct = 0.0
            ladder_cache = st.session_state.get("_skill_ladder_cache")
            if isinstance(ladder_cache, dict):
                ladder = ladder_cache.get("ladder")
                points = getattr(ladder, "points", None) if ladder else None
                if points:
                    ladder_sb_uptimes = [(p.tier_id, p.mean_sb_uptime) for p in points]
                    top = points[0]
                    sb_ceiling_pct = top.mean_sb_uptime
                    sb_ceiling_rage_starved_pct = top.mean_sb_rage_starved_pct
                    sb_ceiling_charge_limited_pct = top.mean_sb_charge_limited_pct

            inferred = infer_tier_from_casts(
                sb_casts,
                duration_s=run.duration_s(),
                start_time_s=run.start_time_s,
                ds_casts=ds_casts,
                ladder_sb_uptimes=ladder_sb_uptimes or None,
                sb_ceiling_pct=sb_ceiling_pct,
                sb_ceiling_rage_starved_pct=sb_ceiling_rage_starved_pct,
                sb_ceiling_charge_limited_pct=sb_ceiling_charge_limited_pct,
            )
            if inferred.cast_count > 0 and inferred.tier_id:
                # v2.10c — compute the worst SB gaps. Reuses the events
                # list `_cached_run_events` already loaded; no new log scan.
                top_gaps = compute_top_sb_gaps(
                    sb_casts,
                    events,
                    duration_s=run.duration_s(),
                    start_time_s=run.start_time_s,
                    top_n=3,
                )
                # Key on log+run so a fresh log selection or run pick
                # invalidates the stale tier on read, no cleanup logic
                # needed (advisor flag, 2026-05-20).
                st.session_state["inferred_skill_tier"] = {
                    "log_name": log_pick,
                    "run_index": run_index,
                    "tier_id": inferred.tier_id,
                    "sb_uptime_pct": inferred.sb_uptime_pct,
                    "cast_count": inferred.cast_count,
                    "ds_press_rate": inferred.ds_press_rate,
                    "ds_cast_count": inferred.ds_cast_count,
                    "sb_tier_id": inferred.sb_tier_id,
                    "ds_tier_id": inferred.ds_tier_id,
                    "sb_ceiling_pct": inferred.sb_ceiling_pct,
                    "sb_ceiling_rage_starved_pct": inferred.sb_ceiling_rage_starved_pct,
                    "sb_ceiling_charge_limited_pct": inferred.sb_ceiling_charge_limited_pct,
                    "sb_missed_pressable_pct": inferred.sb_missed_pressable_pct,
                    "top_gaps": [
                        {
                            "time_phrase": g.time_phrase,
                            "duration_s": g.duration_s,
                            "damage_during_gap": g.damage_during_gap,
                        }
                        for g in top_gaps
                    ],
                }
        except Exception:  # noqa: S110 — best-effort fallback
            # Inference is a UX bonus, never block the surface on it.
            pass

    render_log_analysis(
        summary,
        mit_stats,
        death_events,
        class_spec=class_spec,
        run=run,
        events=events,
        deaths=deaths,
        segments=segments,
        cross_log_rows=cross_log_rows,
        cross_log_n_logs=cross_log_n_logs,
        cross_log_n_runs=cross_log_n_runs,
        cd_plan_context=cd_plan_context,
    )

"""simf UI — L0 pure formatters for the Why-died log surface.

Run-outcome / run-identity / duration string helpers plus the two thin
``st.markdown`` wrappers (``_verdict_card``, ``_render_run_identity_header``)
that render them. No session state. Extracted from ``log_view.py`` (PR 1/3).
"""

from __future__ import annotations

import html

import streamlit as st

from simf.io.combat_log import PartyMember
from simf.io.spec_ids import class_spec_display_name


def _verdict_card(title: str, detail: str = "", warn: bool = False) -> None:
    # h3, not h2: the surface's real h2 is `log_surface.py`'s "Why did I
    # die?" page title, which renders on every visit (cold or not). This
    # card is a subsection of that page — sibling to "Death timeline" /
    # "Where you died" / "Who keeps killing you" (also h3) — not a second
    # top-level heading. Was h2 until a 2026-07-05 accessibility audit
    # flagged the H1->H3 skip that produced on a cold visit (before this
    # card had ever rendered); demoting this fixed the skip without
    # duplicating the page's one h2 (test_cd_plan_tab.py pins the sibling
    # CD-plan prescription card to the same h3 level for the same reason).
    cls = "verdict-warn" if warn else "verdict-best"
    st.markdown(
        f'<div class="{cls}"><h3>{title}</h3><p>{detail}</p></div>',
        unsafe_allow_html=True,
    )


def _fmt_mmss(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _format_run_outcome(run) -> str:
    """Render a ChallengeModeRun's outcome — TIMED / OVER TIME /
    ABANDONED / COMPLETED — without lying.

    WoW's ``CHALLENGE_MODE_END.success`` is "completed?" not "timed?";
    a completed-over-time run shows ``success=1`` in the log. The
    fix is to compare ``duration_ms`` to ``par_time_ms`` (sourced from
    ``dungeons.yaml``) and only claim TIMED when that comparison holds.

    When the par time is unknown (catalog has ``par_time_ms: null``),
    drop the ✓/✗ glyph and say "COMPLETED" rather than guess.
    """
    if run.is_abandoned():
        return "ABANDONED"
    timed = run.is_timed()
    if timed is True:
        return "TIMED ✓"
    if timed is False:
        return "OVER TIME ✗"
    if run.success:
        return "COMPLETED"  # par_time_ms unknown — don't claim timing
    if run.success is None:
        return "INCOMPLETE"  # START with no matching END (truncated log)
    return "COMPLETED"


def _format_run_outcome_short(run) -> str:
    """Sentence-case outcome label for the run-identity header.

    Same four-state truth table as ``_format_run_outcome`` but rendered in
    sentence case (``Timed`` / ``Over Time +52s`` / ``Abandoned`` /
    ``Incomplete`` / ``Completed``) so it reads as part of a sentence
    rather than a label dump. Over-time runs carry the par-delta in
    seconds — WoW convention is to surface how far past par the run
    landed, e.g. ``+52s`` for a 31:52 run on a 31:00 par.

    Kept as a sibling to ``_format_run_outcome`` (which still renders the
    upper-case TIMED ✓ / OVER TIME ✗ glyph form used by the per-segment
    risk caption and the existing run picker labels). Brutoh idea #a,
    2026-05-25.
    """
    if run.is_abandoned():
        return "Abandoned"
    timed = run.is_timed()
    if timed is True:
        return "Timed"
    if timed is False:
        # Par-delta in seconds — duration_ms and par_time_ms are both
        # set when is_timed() returns False (the guard inside is_timed()
        # already eliminated the None branches).
        delta_s = round((run.duration_ms - run.par_time_ms) / 1000.0)
        # Negative or zero shouldn't reach this branch (is_timed would
        # have been True). Defend by clamping to >=1s anyway — better
        # to under-report by a second than print "+0s".
        delta_s = max(delta_s, 1)
        return f"Over Time +{delta_s}s"
    if run.success:
        return "Completed"  # par unknown — don't claim timing
    if run.success is None:
        return "Incomplete"  # START with no matching END (truncated log)
    return "Completed"


def _format_run_date(run) -> str:
    """Render the run's start date as ``YYYY-MM-DD``.

    ``start_time_s`` is a Unix epoch timestamp (set by
    ``parse_combat_log_line`` via ``datetime.strptime(...).timestamp()``).
    Uses the local timezone — WoW's combat log writes wall-clock time
    in the player's locale, so converting to local-tz is the
    round-trip-correct choice. ``fromtimestamp`` without a tz argument
    does exactly that.

    Returns an empty string when ``start_time_s`` is zero or negative,
    which only happens in synthetic test fixtures.
    """
    from datetime import datetime

    if run.start_time_s <= 0:
        return ""
    return datetime.fromtimestamp(run.start_time_s).strftime("%Y-%m-%d")


def _format_run_identity(run) -> str:
    """One-line run identity for the analysis-surface header and the
    multi-run picker rows.

    Format: ``Ara-Kara +18 · Timed 28:34 · 2026-05-22``
    Or:     ``Algeth'ar Academy +14 · Over Time +52s 31:52 · 2026-05-18``

    Drops the date suffix when ``start_time_s`` is missing (synthetic
    tests). Drops the duration when both ``end_time_s`` and
    ``duration_ms`` are unknown — never prints ``0:00``.
    """
    outcome = _format_run_outcome_short(run)
    dur_s = run.duration_s()
    date = _format_run_date(run)

    # map_name is sourced from Warcraft Logs' fight data — escaped since
    # this string is later embedded via unsafe_allow_html.
    map_name = html.escape(run.map_name, quote=False)
    parts = [f"{map_name} +{run.key_level}"]
    if dur_s > 0:
        parts.append(f"{outcome} {_fmt_mmss(dur_s)}")
    else:
        parts.append(outcome)
    if date:
        parts.append(date)
    return " · ".join(parts)


def _render_run_identity_header(run) -> None:
    """Render the run-identity header at the top of the analysis surface.

    "Now analyzing: Ara-Kara +18 · Timed 28:34 · 2026-05-22"

    The single-line variant — same content the multi-run picker rows
    carry, but framed as a header so the user always knows which run
    they're looking at, even with a single-run log where no picker
    renders. CSS class ``run-identity-header`` is defined inline in
    ``app.py`` alongside the other surface-level typography.

    Light/dark mode handled by the underlying ``--surface-elev`` and
    ``--text-secondary`` tokens — same palette family as the verdict
    card above it.
    """
    identity = _format_run_identity(run)
    st.markdown(
        f'<div class="run-identity-header"><span class="run-identity-label">'
        f'Now analyzing:</span> <span class="run-identity-body">{identity}</span></div>',
        unsafe_allow_html=True,
    )


def _link_md(text: str, url: str | None) -> str:
    """Markdown link, plain text fallback when url is None.

    Centralised so the attribution table renders the same way whether or
    not the underlying event carried a parseable NPC ID / spell ID.

    ``text`` is a spell/NPC name sourced from Warcraft Logs' API — escaped
    before embedding, since the caller renders via
    ``st.markdown(..., unsafe_allow_html=True)``.
    """
    text = html.escape(text, quote=False)
    return f"[{text}]({url})" if url else text


# ── shared picker constants + party/WCL member formatters (log_view split, PR 3/3) ──
# Used by BOTH the local-log picker (log_surface) and the WCL picker (log_wcl);
# they live here in the leaf formatter module so neither flow module has to
# import the other (which would cycle).

_OTHER_SENTINEL = "Other (type a name)…"

# Role → emoji. Kept inline rather than in constants.yaml because these are
# pure UI affordances, not game-mechanics data. The U+FE0F variation
# selector after `🛡` and `⚔` forces colored-emoji presentation — without
# it, Firefox renders both as monochrome text glyphs (the crossed-swords
# codepoint defaults to text-style in DejaVu / Noto Sans Mono).
_ROLE_ICONS = {"tank": "🛡️", "healer": "💚", "dps": "⚔️"}
_ROLE_LABELS = {"tank": "Tank", "healer": "Healer", "dps": "DPS"}


def _format_party_member(m: PartyMember) -> str:
    """One-line selectbox label: icon · name · spec-or-role · deaths.

    When `class_spec` is known (ACL on, COMBATANT_INFO present) the label
    shows the actual spec ("Protection Warrior") — more informative than
    the generic role bucket. When unknown (ACL off), falls back to plain
    "Tank / Healer / DPS".

    Deaths is the headline signal for picking who to analyze — `0 deaths`
    on a "Why did I die?" surface is itself information ("nobody died on
    this run; pick someone who did"). Replaces the older "hits taken"
    label, which was activity noise rather than survival signal.
    """
    icon = _ROLE_ICONS.get(m.role, "·")
    if m.class_spec:
        descriptor = class_spec_display_name(m.class_spec)
    else:
        descriptor = _ROLE_LABELS.get(m.role, m.role)
    deaths_label = "1 death" if m.deaths == 1 else f"{m.deaths} deaths"
    return f"{icon}  {m.name}  ·  {descriptor}  ·  {deaths_label}"


def _format_wcl_fight(fight) -> str:
    """One-line fight label for the WCL fight picker.

    Renders ``Ara-Kara, City of Echoes +18 · 28:34`` for keys, or just the
    encounter name + duration for boss attempts (``key_level`` is None /
    zero on raid pulls). Same shape as `_format_run_identity` so the WCL
    tab and the local-log tab read consistently.
    """
    dur_s = (fight.end_time_ms - fight.start_time_ms) / 1000.0
    m, s = divmod(int(dur_s), 60)
    timestamp = f"{m}:{s:02d}"
    if fight.key_level:
        return f"{fight.name} +{fight.key_level} · {timestamp}"
    return f"{fight.name} · {timestamp}"


def _format_wcl_player(p) -> str:
    """Selectbox label for a WCL player — icon · name · spec-or-role.

    Mirrors `_format_party_member` for the local-log picker so the WCL flow
    feels like the same product. WCL playerDetails doesn't carry per-fight
    death counts, so we drop the `· N deaths` suffix the local flow uses.
    """
    icon = _ROLE_ICONS.get(p.role, "·")
    if p.class_spec:
        descriptor = class_spec_display_name(p.class_spec)
    else:
        descriptor = _ROLE_LABELS.get(p.role, p.role)
    return f"{icon}  {p.name}  ·  {descriptor}"

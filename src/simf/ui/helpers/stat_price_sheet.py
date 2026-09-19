"""Stat price sheet — collapsed "how the engine prices your stats" panel.

Every gear/gem/enchant recommendation on the Gear tab is, underneath, one
number: this character's cached per-stat eHP marginal (``_marginals_for``,
``ui/marginals.py``) multiplied by a stat delta. That per-stat table has
never been shown to the player directly — only its *outputs* (a card's
"+1,204 eHP" swap) are visible, so there's no way to check the arithmetic
without reading source. This module renders the table itself as a
collapsed-by-default expander so a skeptical reader (elite_tank persona)
can verify a recommendation without trusting it blind, while staying out
of the way of anyone who doesn't care (novice/engaged tank personas).

## 2026-07-10 readability restructure

A follow-up 6-persona workshop on the freshly-shipped eHP-transparency
feature (novice/engaged/elite tank, ui-craft-critic, copy-microcopy-editor,
calibration-scientist) found this specific panel was the weakest link: a
~180-word always-shown intro paragraph that front-loaded the arithmetic
recipe before a first-time reader even knew what "eHP" meant, and a
``st.dataframe`` table whose cells packed a point estimate + an opaque
"(95% CI +130 to +155)" string together with no legend explaining what a
confidence interval even is or what to do with one. The user's own verbatim
complaint: "the CI amount, and then some numbers, but there is no
explanation about what those mean."

The restructure (top to bottom inside the expander now):

1. ``render_ehp_gloss(class_spec)`` moved to the very TOP — "eHP" is the
   unit on every column header and every cell below it, so it has to be
   defined before it's used, not after.
2. A bridging caption (``_EHP_EQUIVALENT_BRIDGE``) resolving a real
   contradiction moving #1 to the top makes more visible: the eHP
   definition says it excludes per-hit rolls and cooldown presses, but
   Mastery/Haste/Crit price nonzero here precisely because they work
   THROUGH those rolls/presses. The prices are eHP-*equivalents* — measured
   in the Monte Carlo sim, then converted onto the eHP scale via the
   Stamina anchor — not the same literal thing the definition describes.
3. A one-sentence lede replacing the old front-loaded intro, plus a pointer
   to the calibration badge above (a different question: how well the
   whole model matches real logs, vs. this table's own internal math).
4. The optional closed-form-fallback caveat (unchanged content, just
   repositioned).
5. A plain-English "Range (95%)" legend hitting the trust-voice four beats
   (Number/Confidence/Cause/Action, ``docs/ui_copy_voice.md``) BEFORE the
   table, not buried two paragraphs above it.
6. The table itself, now a hand-built HTML table (``st.dataframe`` wraps
   long sentences poorly — needed once the "Why it helps" column landed):
   point estimate and its 95% range live in separate adjacent columns (no
   more "+142 eHP (95% CI +130 to +155)" run-on string, and no redundant
   "eHP" unit repeated in every cell now that it's in the column header).
   A row/cell with no range shows WHY, distinctly per cause — see
   ``NoCiReason`` — so "we're certain" (Stamina's closed-form anchor) can
   never be confused with "we didn't measure this" (an un-bootstrapped
   cell) the way a bare number conflated them before.
7. A reproducibility-basis caption naming the exact iteration/seed/
   resample count the ranges came from (``marginal_noise_basis_label()``,
   ``ui/marginals.py`` — never re-hardcoded here).
8. The old arithmetic-verification recipe + trinket-registry exception,
   demoted into a nested ``<details>`` disclosure ("for the skeptical") —
   real Streamlit ``st.expander`` can't nest inside the outer one, so this
   uses the same native ``<details>``/``<summary>`` trick
   ``stat_composition.py``'s "+N more" fold already uses.

Mirrors ``hp_trough_ledger.py``'s / ``segment_risk_timeline.py``'s split: a
frozen dataclass + a pure ``compute_*`` (unit-testable without Streamlit)
+ a thin ``render_*``. ``streamlit`` and the ``ui.marginals``/``ui.state``
fetch calls are imported LOCALLY inside the render functions (not at
module scope) so a test can monkeypatch ``simf.ui.marginals._marginals_for``
/ ``_marginals_ci_for`` and have the patch land even though this module is
exercised under a Streamlit ``AppTest`` run.

Lives at ``src/simf/ui/helpers/`` alongside its siblings; imports only the
L0/L1 foundation (``core.character``, ``core.survivability_weights`` for
the canonical stat registry, ``optimizer.stat_mechanisms`` for the "why it
helps" column, ``ui.marginals``, ``ui.state`` for the user's selected
progression dungeons, ``ui.helpers.ehp_gloss``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from html import escape

from simf.core.character import Character
from simf.core.survivability_weights import PERTURBED_STATS
from simf.optimizer.stat_mechanisms import mechanism_tag

# Reuse marginals.py's own stat -> plain-English label map instead of a
# second copy that could drift ("vers" vs "versatility", etc.) —
# `summarize_marginal_noise` (same module) has the identical need and
# already solved it.
from simf.ui.marginals import _NOISE_SUMMARY_LABELS

_EXPANDER_TITLE = "How the engine prices your stats"


class NoCiReason(Enum):
    """Why a cell has no 95% range, when it doesn't.

    A bare number used to conflate three completely different trust
    signals into one rendering — the 2026-07-10 workshop's highest-
    confidence correctness finding (not just polish): "we measured this
    exactly" and "we never measured this" looked identical. The three real
    structural cases in the engine (``core/survivability_weights.py``):

    - ``EXACT_ANCHOR``: Stamina's marginal IS the closed-form derivative
      (see ``_closed_form_dehp_per_stam`` and the "closed-form anchor —
      zero-width by construction" comment in ``_bootstrap_marginal_ci``) —
      the highest possible confidence, not a gap.
    - ``PHYSICS_PIN``: armor's magic-school column is hardcoded ``0.0``
      because armor cannot mitigate magic damage in this engine (see the
      "Physics-exact pin" comment, same file) — there is nothing to bound
      because there is no claim to make, not because nobody measured it.
    - ``NOT_MEASURED``: everything else with no real bootstrap CI this
      session — a noise-floor-zeroed marginal, a closed-form fallback (no
      bootstrap ever ran), or simply not computed yet. This is the ONLY one
      of the three that means "lower confidence."
    """

    EXACT_ANCHOR = "exact_anchor"
    PHYSICS_PIN = "physics_pin"
    NOT_MEASURED = "not_measured"


_NO_CI_REASON_LABELS: dict[NoCiReason, str] = {
    NoCiReason.EXACT_ANCHOR: "exact — anchor",
    NoCiReason.PHYSICS_PIN: "n/a — armor doesn't reduce magic",
    NoCiReason.NOT_MEASURED: "not measured this session",
}

# Inline styles (not CSS classes) deliberately — this cluster's file scope
# is just this module + its test, and the shared `--text-muted`/
# `--accent-good` custom properties are already injected unconditionally by
# `app.py`'s static `<style>` block, so referencing them via `var(...)`
# needs no new CSS file touched. `EXACT_ANCHOR` reuses the same steel
# "trust this" token every other confident trust signal on the page uses;
# `PHYSICS_PIN` and `NOT_MEASURED` share the muted meta tone (a physics
# pin isn't a warning, just informational, hence also italic) — neither is
# a warning color, since neither is bad news.
_NO_CI_REASON_STYLE: dict[NoCiReason, str] = {
    NoCiReason.EXACT_ANCHOR: "color: var(--accent-good); font-weight: 600;",
    NoCiReason.PHYSICS_PIN: "color: var(--text-muted); font-style: italic;",
    NoCiReason.NOT_MEASURED: "color: var(--text-muted);",
}


def _no_ci_reason(stat: str, school: str) -> NoCiReason:
    """The structural reason ``stat``'s ``school`` column has no CI.

    Pure lookup against the two known-named structural cases in
    ``core/survivability_weights.py`` (see ``NoCiReason``'s docstring for
    the exact source lines) — never re-derived from live data, so it can't
    disagree with the engine as it drifts. Everything else defaults to
    ``NOT_MEASURED``, the honest "we don't have one this session" case.
    """
    if stat == "stamina":
        return NoCiReason.EXACT_ANCHOR
    if stat == "armor_from_gear" and school == "m":
        return NoCiReason.PHYSICS_PIN
    return NoCiReason.NOT_MEASURED


@dataclass(frozen=True)
class StatPriceRow:
    """One stat's eHP-per-point price, physical and magic, plus its
    bootstrap 95% CI when one is actually available for that cell, plus a
    structural reason when it isn't, plus the plain-language mechanism
    behind the price.

    ``ci_p``/``ci_m`` are ``None`` whenever no real CI exists for that
    cell — a marginal the noise floor zeroed out, a closed-form-fallback
    compute (no bootstrap ever ran), or simply no CI computed this
    session. ``None`` must never be rendered as a fabricated "±0" — it
    means "no claim," the same convention `_marginals_ci_for` documents.

    ``no_ci_reason_p``/``no_ci_reason_m`` are populated ONLY when the
    matching ``ci_*`` is ``None`` (a real range needs no reason attached —
    see ``NoCiReason``) so a renderer can distinguish "certain" from
    "unmeasured" instead of treating every CI-less cell as the same kind
    of gap.

    ``mechanism`` is the plain-English "why does this stat help me
    survive" gloss from ``optimizer.stat_mechanisms.mechanism_tag``, gated
    on this character's own live marginals — ``None`` when nothing is
    catalogued for this (class_spec, stat) pair, or (rarer) when a real
    catalogued mechanism exists but currently prices at zero for this
    build.
    """

    stat: str
    label: str
    ehp_p: float
    ehp_m: float
    ci_p: tuple[float, float] | None
    ci_m: tuple[float, float] | None
    no_ci_reason_p: NoCiReason | None
    no_ci_reason_m: NoCiReason | None
    mechanism: str | None


def compute_stat_price_rows(
    marginals: dict, ci: dict | None, class_spec: str | None = None
) -> list[StatPriceRow]:
    """Turn a ``_marginals_for``-shaped dict (+ optional
    ``_marginals_ci_for``-shaped dict) into one row per stat, in
    ``PERTURBED_STATS`` canonical order — the exact registry
    ``compute_survivability_marginals`` perturbs, so a stat can never
    appear here that isn't actually priced by the engine, and no priced
    stat is ever silently dropped.

    Pure function, no Streamlit — unit-testable directly. ``ci=None`` (no
    CI available at all this session) and a per-stat/per-school ``None``
    inside a real ``ci`` dict are both treated identically: that cell's
    ``ci_p``/``ci_m`` comes back ``None``, never a fabricated zero-width
    bound; ``no_ci_reason_p``/``no_ci_reason_m`` fill in why (see
    ``_no_ci_reason``).

    ``class_spec`` is optional (defaults to ``None``, matching every
    existing caller/test that doesn't care about the mechanism column) —
    passed straight through to ``mechanism_tag`` per stat.
    """
    rows: list[StatPriceRow] = []
    for stat in PERTURBED_STATS:
        vals = marginals.get(stat, {})
        stat_ci = (ci or {}).get(stat, {})
        ci_p = stat_ci.get("p")
        ci_m = stat_ci.get("m")
        rows.append(
            StatPriceRow(
                stat=stat,
                label=_NOISE_SUMMARY_LABELS.get(stat, stat).title(),
                ehp_p=vals.get("p", 0.0),
                ehp_m=vals.get("m", 0.0),
                ci_p=ci_p,
                ci_m=ci_m,
                no_ci_reason_p=None if ci_p is not None else _no_ci_reason(stat, "p"),
                no_ci_reason_m=None if ci_m is not None else _no_ci_reason(stat, "m"),
                # `mechanism_tag` requires a real `class_spec` str; `None`
                # (no character in scope, e.g. an old direct test call)
                # means "no catalogue to consult," not "call it anyway."
                mechanism=(
                    mechanism_tag(class_spec, stat, marginals) if class_spec is not None else None
                ),
            )
        )
    return rows


def compute_school_mix_blend(dungeons: list[dict]) -> tuple[float, float] | None:
    """Average (physical_fraction, magic_fraction) across ``dungeons``'
    ``school_mix`` entries, equally weighted per dungeon.

    This is the exact blend weight every ΔeHP card on the Gear/Vault tabs
    actually applies (``optimizer.per_dungeon._score_for_school_mix``,
    averaged per dungeon by ``average_composition_terms`` /
    ``score_item_across_dungeons`` over the user's selected progression
    dungeons — ``ui.state._selected_dungeons()``), not an approximation of
    it: because averaging is linear and a swap's own stat delta doesn't
    depend on which dungeon is being scored, averaging the school_mix
    FRACTIONS first and then blending against the price-sheet columns
    produces byte-identical arithmetic to averaging the per-dungeon
    BLENDED SCORES the real callers compute (see
    ``test_school_mix_blend_matches_per_dungeon_average`` for the proof).

    Non-physical schools (arcane/fire/frost/shadow/nature/holy/...) are
    lumped into one "magic" fraction — the same simplification
    ``_score_for_school_mix`` makes, since the price-sheet columns
    (``ehp_p``/``ehp_m``) only carry a physical/magic split, not a
    per-school one.

    Returns ``None`` for an empty dungeon list — nothing to average, and
    the caller should omit the blend clause rather than claim a 0%/0% split.
    """
    if not dungeons:
        return None
    phys_fractions = [float((d.get("school_mix") or {}).get("physical", 0.0)) for d in dungeons]
    avg_phys = sum(phys_fractions) / len(phys_fractions)
    return avg_phys, 1.0 - avg_phys


def _fmt_point(value: float) -> str:
    """``"+142"`` — the point estimate alone, no repeated "eHP" unit (it's
    already the column header) and no CI baked into the same string."""
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:,.0f}"


def _range_cell_html(ci: tuple[float, float] | None, reason: NoCiReason | None) -> str:
    """The adjacent "Range (95%)" cell: the literal ``"+130 to +155"``
    bounds when a real bootstrap CI exists, or a distinctly-styled reason
    when it doesn't (see ``NoCiReason`` — never the same rendering for
    "exact" and "unmeasured").

    Absolute bounds (not a relative "±20%" summary) are shown deliberately:
    the CI legend's "if two stats' ranges overlap, treat them as tied"
    action only works if two different stats' intervals can be visually
    compared side by side without the reader doing the ± arithmetic
    themselves first.
    """
    if ci is not None:
        lo, hi = ci
        lo_sign = "+" if lo >= 0 else ""
        hi_sign = "+" if hi >= 0 else ""
        return f"{lo_sign}{lo:,.0f} to {hi_sign}{hi:,.0f}"
    if reason is None:
        label = "not measured this session"
        style = "color: var(--text-muted);"
    else:
        label = _NO_CI_REASON_LABELS.get(reason, "not measured this session")
        style = _NO_CI_REASON_STYLE.get(reason, "color: var(--text-muted);")
    # `quote=False` — these labels are our own static copy, never user
    # input, and contain a real apostrophe ("armor doesn't reduce magic")
    # that `html.escape`'s default would otherwise mangle into `&#x27;`.
    return f'<span style="{style}">{escape(label, quote=False)}</span>'


def _mechanism_cell_html(mechanism: str | None) -> str:
    """The "Why it helps" cell — the plain-language mechanism sentence, or
    a muted honest placeholder when nothing is catalogued (a spec/stat
    pair with no entry) or the catalogued mechanism prices at zero for
    this particular build (e.g. a talent not taken). A stat that's
    STRUCTURALLY never modeled (``mechanism_tag``'s
    ``NOT_MODELED_GLOSS`` sentinel, e.g. Agility on a plate tank) renders
    as that real sentence, not this placeholder — the sentinel text is
    itself the honest answer in that case."""
    if mechanism:
        # `quote=False` — mechanism sentences come from
        # `data/stat_mechanisms.yaml`, curated copy (not user input) that
        # routinely contains a real apostrophe ("Bear Form's passive...",
        # "Elune's Chosen") the default `html.escape` would mangle into
        # `&#x27;`.
        return escape(mechanism, quote=False)
    return (
        '<span style="color: var(--text-muted); font-style: italic;">'
        "no mechanism credit for this build</span>"
    )


def _render_table_html(rows: list[StatPriceRow]) -> str:
    """Hand-built HTML table (not ``st.dataframe``) — the "Why it helps"
    column holds full sentences that a dataframe grid cell wraps poorly;
    an HTML table lets that one column wrap normally while every numeric
    column stays ``nowrap`` and unambiguous. Wrapped in a horizontally
    scrollable div so a narrow viewport clips the table, not the page."""
    border = "border-bottom: 1px solid var(--border-default);"
    header_cells = [
        ("left", "Stat"),
        ("right", "eHP / point — physical"),
        ("left", "Range (95%) — physical"),
        ("right", "eHP / point — magic"),
        ("left", "Range (95%) — magic"),
        ("left", "Why it helps"),
    ]
    header_html = "".join(
        f'<th style="text-align:{align}; padding:4px 10px 4px 0; {border} '
        f'white-space:nowrap;">{escape(text)}</th>'
        for align, text in header_cells
    )
    row_border = "border-bottom: 1px solid var(--border-subtle);"
    body_html = ""
    for row in rows:
        body_html += (
            "<tr>"
            f'<td style="padding:6px 10px 6px 0; {row_border} font-weight:600; '
            f'white-space:nowrap;">{escape(row.label)}</td>'
            f'<td style="padding:6px 10px; {row_border} text-align:right; '
            f'white-space:nowrap;">{_fmt_point(row.ehp_p)}</td>'
            f'<td style="padding:6px 10px; {row_border} white-space:nowrap;">'
            f"{_range_cell_html(row.ci_p, row.no_ci_reason_p)}</td>"
            f'<td style="padding:6px 10px; {row_border} text-align:right; '
            f'white-space:nowrap;">{_fmt_point(row.ehp_m)}</td>'
            f'<td style="padding:6px 10px; {row_border} white-space:nowrap;">'
            f"{_range_cell_html(row.ci_m, row.no_ci_reason_m)}</td>"
            f'<td style="padding:6px 0 6px 10px; {row_border}">'
            f"{_mechanism_cell_html(row.mechanism)}</td>"
            "</tr>"
        )
    return (
        '<div style="overflow-x:auto;">'
        '<table style="width:100%; border-collapse:collapse; font-size:13px; '
        f'line-height:1.4;"><thead><tr>{header_html}</tr></thead>'
        f"<tbody>{body_html}</tbody></table></div>"
    )


def _math_disclosure_html(dungeons: list[dict] | None) -> str:
    """The nested "for the skeptical" arithmetic-verification recipe +
    trinket-registry exception — a native ``<details>``/``<summary>``
    element, not a second ``st.expander`` (Streamlit does not allow
    expanders to nest inside one another; this whole panel already lives
    inside one). Mirrors ``stat_composition.py``'s "+N more" fold, the
    same trick for the same structural reason.

    This is the demoted tail of the old always-shown ~180-word intro
    paragraph: genuinely useful to the reader who wants to reproduce a
    card's arithmetic by hand, dead weight to everyone else — the
    2026-07-10 workshop's copy-microcopy-editor + both novice/engaged tank
    reviewers all flagged it as the first thing they skipped past.
    """
    blend = compute_school_mix_blend(dungeons or [])
    if blend is not None:
        phys_pct, mag_pct = blend
        blend_line = (
            "Most ΔeHP numbers shown elsewhere on this page blend the two eHP "
            f"columns above — weighted {phys_pct * 100:.0f}% physical / "
            f"{mag_pct * 100:.0f}% magic, the average school split across your "
            "selected progression dungeons (the Prog dungeons picker above), "
            "not either column alone."
        )
        recipe_line = (
            "To check a card's arithmetic yourself: (a swap's stat difference) "
            f"× (physical price × {phys_pct * 100:.0f}% + magic price × "
            f"{mag_pct * 100:.0f}%), summed across every stat that changed."
        )
    else:
        blend_line = (
            "Most ΔeHP numbers shown elsewhere on this page blend the two eHP "
            "columns above by your selected progression dungeons' physical/"
            "magic school split — not either column alone."
        )
        recipe_line = (
            "To check a card's arithmetic yourself: (a swap's stat difference) "
            "× (physical price × its school share + magic price × its school "
            "share), summed across every stat that changed."
        )
    return (
        '<details style="margin-top:6px; font-size:12px; color:var(--text-muted);">'
        '<summary style="cursor:pointer;">Show the exact arithmetic (for the '
        "skeptical)</summary>"
        f'<div style="margin-top:6px;">{escape(blend_line, quote=False)} '
        f"{escape(recipe_line, quote=False)} "
        "<strong>Exception:</strong> trinket ΔeHP comes from a separate "
        "proc/on-use effect registry, not this table's stat-marginal "
        "arithmetic — those rows won't reproduce from this sheet at all.</div>"
        "</details>"
    )


# ─── copy blocks (kept as named constants so tests can assert on them) ─────

_EHP_EQUIVALENT_BRIDGE = (
    "A note on that definition: the prices below are eHP-EQUIVALENTS, not "
    "the literal thing eHP measures. A stat that works through a per-hit "
    "roll (like a block chance) or a cooldown's economy (like how often you "
    "can afford to press a defensive) is measured directly in the Monte "
    "Carlo sim below and then converted onto the eHP scale via the Stamina "
    "anchor — so it can carry a real price here even though the definition "
    "above excludes those same rolls and presses from what eHP itself "
    "counts. The exclusion describes the yardstick eHP uses, not which "
    "mechanics are allowed to earn a stat its price."
)

_INTRO_LEDE = (
    "This table is the per-stat exchange rate every gem, enchant, gear, and "
    "vault recommendation on this page is computed from — how many eHP one "
    "point of a stat is worth for this character, physical and magic damage "
    "priced separately. See the calibration chip at the top of the page for "
    "how well the underlying model matches real logs — a separate question "
    "from this table's own arithmetic."
)

_CI_LEGEND = (
    "**Range (95%)** is the sim's own sampling noise on that price — the "
    "span it would land in about 19 times out of 20 if the sim re-rolled "
    "the same random damage rolls with this character and these settings. "
    "A wide range means a noisier price (trust it less); a narrow range — "
    "or no range at all, explained right in that cell — means a firmer "
    "one. This is sampling noise ONLY, separate from (and stacked on top "
    "of) the model-error figure in that same calibration popover, which "
    "measures how well the whole model matches real combat logs. If two "
    "stats' ranges overlap, treat their prices as tied rather than ranking "
    "one ahead of the other."
)

_FALLBACK_CAPTION = (
    "This character's numbers came from a simplified closed-form formula "
    "instead of the full per-stat simulation (see the caution above this "
    "panel) — haste, crit, and mastery read as +0 eHP in that path because "
    "it doesn't model them, not because those stats are truly worthless "
    "for this build."
)


def render_stat_price_sheet(char: Character) -> None:
    """Mount point for the Gear surface.

    Fetches this character's cached marginals + bootstrap CI + fallback
    reason (all already computed/cached elsewhere — this call is free on
    a warm cache, see ``ui/marginals.py``) and renders the collapsed
    expander. The ``ui.marginals`` names are imported HERE, inside the
    function body, rather than at module scope, so a test can monkeypatch
    any of them and have this call pick up the patched version — see
    module docstring.
    """
    from simf.ui.marginals import (
        _marginals_ci_for,
        _marginals_for,
        _surv_marginals_fallback_warning,
    )
    from simf.ui.state import _selected_dungeons

    marginals = _marginals_for(char)
    ci = _marginals_ci_for(char)
    fallback_reason = _surv_marginals_fallback_warning(char)
    dungeons = _selected_dungeons()
    _render_price_sheet_body(marginals, ci, fallback_reason, dungeons, char.class_spec)


def _render_price_sheet_body(
    marginals: dict,
    ci: dict | None,
    fallback_reason: str | None,
    dungeons: list[dict] | None = None,
    class_spec: str | None = None,
) -> None:
    """The actual render, split out from ``render_stat_price_sheet`` so a
    test can drive it directly with a synthetic marginals/CI dict instead
    of paying for a real sim or a disk-cache round trip.

    ``dungeons``/``class_spec`` default to ``None`` so existing direct
    callers (and tests) that don't care about the blend-weight caption or
    the per-spec eHP gloss keep working unchanged — the caption degrades to
    a spec/dungeon-agnostic phrasing rather than crashing or guessing.

    Render order (see the module docstring's "2026-07-10 readability
    restructure" section for why this exact order): eHP definition ->
    eHP-equivalents bridge -> one-line lede -> optional closed-form-
    fallback caveat -> the CI legend -> the table (+ nested skeptical-math
    disclosure) -> the reproducibility-basis line.
    """
    import streamlit as st

    from simf.ui.helpers.ehp_gloss import render_ehp_gloss
    from simf.ui.marginals import marginal_noise_basis_label

    rows = compute_stat_price_rows(marginals, ci, class_spec)
    with st.expander(_EXPANDER_TITLE, expanded=False):
        # 1. Lead with the definition — "eHP" is used in every column
        # header and cell below, so it must be defined before it's used.
        render_ehp_gloss(class_spec)
        # 2/8. Bridge the eHP-gloss-vs-table contradiction moving #1 to the
        # top makes more visible (co-required with #1 — see module docstring).
        st.caption(_EHP_EQUIVALENT_BRIDGE)
        # 3/7. Short lede replacing the old ~180-word front-loaded intro.
        st.caption(_INTRO_LEDE)
        if fallback_reason:
            st.caption(_FALLBACK_CAPTION)
        # 5. Plain-English CI legend, immediately above the table.
        st.caption(_CI_LEGEND)
        # 6. The table itself + the nested "for the skeptical" disclosure.
        st.markdown(
            _render_table_html(rows) + _math_disclosure_html(dungeons),
            unsafe_allow_html=True,
        )
        # 7. Reproducibility basis for the ranges just shown — reused, never
        # re-hardcoded, from the exact compute that produced them.
        st.caption(f"**Basis for the ranges above:** {marginal_noise_basis_label()}.")

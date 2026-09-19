"""simf UI — "Where your survivability comes from" disposition ledger.

Protection Warrior's mitigation chain (`core/mitigation.py`) now snapshots
how much of every hit's raw damage is removed by each stage — avoidance,
block, armor, versatility, every other DR layer, and absorbs — before
whatever's left lands as HP loss ("dealt"). `core/metrics.py` aggregates
those per-event snapshots into `SimResult.disposition_*_share` fields
(mean shares across a Monte Carlo run); this module is the surfacing job,
same split as `tail_risk_chart.py`: pure data functions that are
unit-testable without Streamlit, plus a thin `render_*` function that
does the Plotly/`st.*` wiring.

Property of the WHOLE simulated run, not a per-swap delta — there is no
"disposition ledger for this gear change," only "this is where your
current build's survivability comes from." Protection Warrior only today;
every other spec renders a plain "not yet instrumented" caption instead of
a bar (`SimResult.disposition_instrumented` is False for them — see
`metrics.py`'s docstring for why: their per-spec mitigation modules don't
populate the chain-breakdown keys at all, so a bar built from zeros would
misrepresent absence as a real measurement).

Framing rule (this project has hit this exact mistake before — see
CONTRIBUTING.md's DR-stacking-arithmetic-discipline lesson): this chart reads
the chain in the order the game actually applies it. It is NOT a
counterfactual "remove one layer and see what changes" — chaining
one-at-a-time removals on a multiplicative DR stack overstates each
layer's individual contribution by 3-5x, which is exactly the arithmetic
error that lesson is about. The captions below say this explicitly rather
than let a reader infer the wrong mental model from the chart shape alone.

## 2026-07-10 readability workshop

A follow-up 6-persona workshop on the freshly-shipped eHP-transparency
feature found this panel's own captions were dense enough to bury the
chart's actual good idea: an always-shown ~120-word paragraph led with the
chain-order restatement, then the multiplicative-chain caveat, then the
not-a-log-read disclaimer, all in one block — every reviewing persona
(mostly minor findings, but all six touched it) flagged it as the first
thing a novice reader skips past. Fixed by splitting into three pieces:

1. `_what_this_shows_caption()` — cut to the ONE sentence a first-time
   reader actually needs (chart's read order), always shown.
2. `_FOR_THE_CURIOUS_HTML` — the multiplicative-chain caveat + the
   not-a-log-read disclaimer, demoted into a collapsed disclosure. A real
   `st.expander` can't nest inside the key-level verdict panel's own outer
   expander (`verdict.py`'s call site), so this uses the same native
   `<details>`/`<summary>` trick `stat_composition.py`'s "+N more" fold and
   `stat_price_sheet.py`'s "for the skeptical" disclosure already use.
3. `_provenance_caption()` — kept, but now interpolates the REAL
   `_LEDGER_ITERATIONS`/`_LEDGER_SEED` constants instead of leaving the
   sim's basis silent, matching every other number on this workshop's
   surfaces now naming its own basis (`marginal_noise_basis_label()` one
   panel over in `stat_price_sheet.py` is the same discipline).

Also: `_legend_label()` appends each segment's rounded share (e.g. "· 23%")
to its legend entry for any segment at or above `_MIN_LABELED_SHARE`
(~5%), so shares are readable straight off the legend without hovering
every segment. This is a LEGEND-suffix, not an on-bar `text`/`texttemplate`
label, deliberately: an on-bar label sitting on top of these (intentionally
dark) steel/bronze segment fills would need its own WCAG 1.4.3 TEXT-
contrast check, separate from the fills' existing NON-text 3:1 floor
(`test_reduction_colors_each_clear_wcag_contrast_floor_on_paper` checks
fills against paper, not text against fills) — the lightest reduction
bucket (`#5188b8`) only clears ~3.3:1 against white on-bar text, short of
the 4.5:1 normal-text floor. A legend suffix reads on the paper background
instead, inheriting the same ink-on-cream contrast every other label on
this page already has, and — because each segment gets its own fixed
legend row — can never collide the way an on-bar label could on a
sub-5%-wide sliver (obviating the need for a separate min-width guard).

The segment colors themselves, the legend `traceorder`, and the "no share
CI" decision are UNCHANGED — see the `_REDUCTION_COLORS` docstring and this
module's own git history for why those were already settled.
"""

from __future__ import annotations

from dataclasses import dataclass

from simf.core.character import Character

_LEDGER_ITERATIONS = 500
_LEDGER_SEED = 42

# Below this share, a numeric legend suffix (`_legend_label`) is more
# clutter than signal for a slice this small — a reader who cares about a
# sub-5% share can still hover it. ~5% chosen to match the brief's own
# "for any slice over ~5%" framing, not re-derived from anything deeper.
_MIN_LABELED_SHARE = 0.05

# The 7 reduction-bucket colors, hand-picked to stay in the "steel family"
# (the module docstring's committed semantic: reduction layers are steel,
# "Landed as damage" is the one bronze accent) while still being tellable
# apart at a glance. `codex_tints(CODEX_COLORWAY[0], 7)` — a single hue at
# 7 opacity steps ramping 1.0 -> 0.31 — was a real bug (5/6 live reviewers,
# 2026-07-09/10 triage): on a stacked bar, adjacent segments only a few
# points of alpha apart read as visually identical, especially the back
# half of the ramp (three segments all in the ~30-55% alpha range).
#
# This is a real, non-arbitrary sequential-multi-hue technique (the same
# idea ColorBrewer's "GnBu"/"YlGnBu" sequential-with-hue-drift schemes
# use): lightness increases monotonically left-to-right (mirroring the
# chain's read order — darkest/most-saturated first) while hue drifts a
# modest ~30° within the cool blue-to-slate band (not a new invented
# earth-tone the way the reverted 2026-05-25 8-hue CODEX_COLORWAY
# extension was — see `tests/test_plotly_codex.py`'s
# `test_colorway_matches_spec_two_accents`, which still commits
# CODEX_COLORWAY itself to exactly steel+bronze; this tuple is local to
# the ledger, not an extension of that shared palette). Every entry
# independently clears the WCAG 1.4.11 3:1 contrast floor against the
# `#f4f1ea` paper tone (checked in `tests/test_disposition_ledger_ui.py`,
# the same bar `CODEX_COLORWAY`'s own colors are held to) and sits far
# (RGB-distance) from the bronze accent, so no reduction segment reads
# as a false "this is the damage segment."
_REDUCTION_COLORS: tuple[str, ...] = (
    "#182449",  # avoided — darkest/most-saturated: dodging damage outright
    "#22385e",  # blocked
    "#2d4f71",  # armor
    "#376781",  # vers
    "#417f90",  # dr_layers
    "#488da8",  # absorbed_ip
    "#5188b8",  # absorbed_healer — lightest reduction bucket, right before bronze
)


@dataclass(frozen=True)
class DispositionSegment:
    """One stacked-bar segment — a share of raw incoming damage."""

    key: str
    label: str
    share: float
    color: str


def _legend_label(seg: DispositionSegment) -> str:
    """`seg`'s legend entry: its short label, plus a rounded share-percent
    suffix ("· 23%") for any segment at or above `_MIN_LABELED_SHARE`
    (~5%) — so shares are readable straight off the legend without
    hovering every segment. Below that threshold the bare label alone is
    shown; see this module's docstring for why the suffix lives in the
    legend rather than as on-bar `text`."""
    if seg.share >= _MIN_LABELED_SHARE:
        return f"{seg.label} · {seg.share * 100:.0f}%"
    return seg.label


def compute_disposition_segments(result) -> list[DispositionSegment]:
    """Build the 8 ordered chain segments from a `SimResult`.

    Returns `[]` when `result.disposition_instrumented` is falsy (every
    non-Protection-Warrior spec today) — the caller renders the "not yet
    instrumented" message instead of a bar; a bar built from structural
    zeros would read as "this build avoids/blocks/mitigates nothing,"
    which is absence-of-measurement, not a real finding.

    Order matches the chain `apply_mitigation` actually applies:
    avoidance → block → armor → versatility → every other DR layer →
    absorbs → whatever's left (dealt). The 7 damage-reduction buckets get
    distinct, hand-picked steel-family colors (`_REDUCTION_COLORS` — see
    its own docstring for why a single-hue opacity ramp doesn't cut it
    here); bronze (damage) marks the final "landed as damage" segment —
    same two-accent semantic every other Codex chart in this project uses.
    """
    if not getattr(result, "disposition_instrumented", False):
        return []

    from simf.ui.helpers.plotly_codex import CODEX_COLORWAY

    # Short labels — this is a legend entry, not a caption. The full
    # "avoid, then block, then armor, then versatility, then every other
    # DR layer, then absorbs" chain-order explanation lives in
    # `render_disposition_ledger`'s caption text; cramming the same
    # detail into the legend (8 entries, one shared row) was the direct
    # cause of a real layout bug caught live (2026-07-09) — the legend
    # overflowed its allotted height and Plotly fell back to a scrollable
    # floating overlay ON TOP of the bar instead of a clean row beneath
    # it. `_render_stacked_bar` fixes the layout half; short labels fix
    # the content half.
    reduction_buckets = [
        ("avoided", "Avoided", result.disposition_avoided_share),
        ("blocked", "Blocked", result.disposition_blocked_share),
        ("armor", "Armor", result.disposition_armor_share),
        ("vers", "Versatility", result.disposition_vers_share),
        ("dr_layers", "Other DR layers", result.disposition_dr_layers_share),
        ("absorbed_ip", "Absorbed (Ignore Pain)", result.disposition_absorbed_ip_share),
        ("absorbed_healer", "Absorbed (healer)", result.disposition_absorbed_healer_share),
    ]
    segments = [
        DispositionSegment(key=k, label=label, share=share, color=_REDUCTION_COLORS[i])
        for i, (k, label, share) in enumerate(reduction_buckets)
    ]
    segments.append(
        DispositionSegment(
            key="dealt",
            label="Landed as damage",
            share=result.disposition_dealt_share,
            color=CODEX_COLORWAY[1],
        )
    )
    return segments


def compute_healed_back_shares(result) -> tuple[float, float] | None:
    """(self_heal_share, external_heal_share) of the damage that landed —
    NOT part of the raw-damage conservation identity `compute_disposition_
    segments` renders (see `metrics.py`'s docstring for why: cumulative
    healing over a fight isn't bound to any single event's raw damage the
    way the mitigation chain is). Returns `None` when the run isn't
    disposition-instrumented, mirroring `compute_disposition_segments`.
    """
    if not getattr(result, "disposition_instrumented", False):
        return None
    return (
        getattr(result, "disposition_healed_self_share", 0.0),
        getattr(result, "disposition_healed_external_share", 0.0),
    )


def _ledger_char_key(char: Character, key_level: int | None, damage_multiplier: float) -> tuple:
    """Cache key for the one dedicated sim this panel runs.

    Reuses ``_char_marginals_signature`` — the same signature the marginals
    cache uses — rather than a hand-picked field list. A hand-picked list is
    exactly the bug class this project has already paid for twice (PR #275:
    `_char_marginals_signature` itself was missing `agility`; PR #321: two
    independently-tested fixes combined to break the marginals cache's own
    signature round-trip): a field this ledger's sim actually depends on
    (haste, talents, race, shield_armor, active buffs, ...) but this key
    omits would serve a stale cached ledger after a trial swap that only
    changes that field.
    """
    from simf.ui.marginals import _char_marginals_signature

    return (*_char_marginals_signature(char), key_level, round(damage_multiplier, 4))


def _compute_ledger_result(char: Character, damage_multiplier: float):
    """Run the one dedicated sim this panel needs. Not a sweep — a single
    run against simf's modeled Mythic+ boss/tank-buster profile at the
    given damage multiplier (the same per-key multiplier the key-level
    verdict sweep already computed for whichever key the caller is
    showing tail-risk/skill-ladder detail for — see `verdict.py`'s call
    site), so this panel's numbers are scaled consistently with the rest
    of the expander it renders inside.
    """
    from simf.core.profiles import load_damage_profile, load_healing_profile, scale_damage_profile
    from simf.core.runner import run_simulation

    damage_profile = load_damage_profile("m+_boss_tankbuster")
    healing_profile = load_healing_profile("m+_high_key_healer")
    scaled = scale_damage_profile(damage_profile, damage_multiplier)
    return run_simulation(
        character=char,
        damage_profile=scaled,
        healing_profile=healing_profile,
        iterations=_LEDGER_ITERATIONS,
        seed=_LEDGER_SEED,
    )


# ─── caption copy (named so tests can assert against it directly) ─────────

# The multiplicative-chain caveat + "this is a simulated run, not a read
# of your own logs" disclaimer — demoted out of the always-shown caption
# into a collapsed native `<details>` disclosure (see module docstring,
# "2026-07-10 readability workshop"). Plain `<details>`/`<summary>`, not a
# second `st.expander`: this whole panel already renders inside the
# key-level verdict's own outer expander (`verdict.py`'s call site), and
# Streamlit does not allow expanders to nest inside one another.
_FOR_THE_CURIOUS_HTML = (
    '<details style="margin-top:2px; font-size:12px; color:var(--text-muted);">'
    '<summary style="cursor:pointer;">For the curious: how to read this chart</summary>'
    '<div style="margin-top:6px;">'
    "This is <strong>not</strong> a counterfactual 'remove one layer and "
    "see what changes' — chaining removals like that overstates each "
    "layer's own contribution by 3-5x on a multiplicative chain like this "
    "one; the chart instead reads the chain in the SAME order the game "
    "actually applies it (avoid, then block, then armor, then "
    "versatility, then every other DR layer, then absorbs), ending in "
    "what actually landed as HP loss. These are mean shares from simf's "
    "own Monte Carlo model of a Mythic+ boss/tank-buster stream, not a "
    "read of your own combat logs — see the <strong>Why did I die?"
    "</strong> tab for what actually happened in a real pull."
    "</div></details>"
)


def _what_this_shows_caption(key_level: int | None) -> str:
    """The always-visible, ONE-sentence explanation of the chart above.

    Replaces a ~120-word paragraph that bundled the chain-read-order
    restatement, the multiplicative-chain caveat, and the not-a-log-read
    disclaimer into a single always-shown block — all six reviewing
    personas in the 2026-07-10 readability workshop flagged it as the
    first thing a novice reader skips past. The caveat and disclaimer
    moved into `_FOR_THE_CURIOUS_HTML`'s collapsed disclosure rather than
    being cut outright.

    "calibration baseline" is glossed inline the one place it appears
    (`key_level is None`) — `docs/ui_copy_voice.md` rule #1: a term load-
    bearing to the sentence around it needs a definition in the same
    breath the first time a reader can see it, not a term left undefined
    on a panel with no popover for it.
    """
    if key_level is not None:
        key_phrase = f"At +{key_level}"
    else:
        key_phrase = (
            "At simf's calibration baseline — the flat, unscaled damage "
            "profile every key level's multiplier is measured against"
        )
    return (
        f"**What this shows.** {key_phrase}, this splits your incoming "
        "damage into where it actually went, in the order the game "
        "applies your defenses — left to right."
    )


def _provenance_caption() -> str:
    """The "these are simulated shares, at this damage scale" caption.

    Interpolates the REAL `_LEDGER_ITERATIONS`/`_LEDGER_SEED` constants —
    the exact ones `_compute_ledger_result` runs with — instead of leaving
    the sim's basis silent on a page where every other number now names
    its own basis (mirrors `marginal_noise_basis_label()` one panel over
    in `stat_price_sheet.py`: never re-hardcoded, always read from the
    same constants the actual compute uses, so this text can't silently
    drift out of sync with the real run).
    """
    return (
        f"Shares are the mean across this run's {_LEDGER_ITERATIONS}-"
        f"iteration fixed-seed run (seed {_LEDGER_SEED}) at this key's "
        "damage scale. simf's per-key scaling multiplies swing and "
        "tank-buster damage but not every mob's scripted spell casts, so "
        "the MIX of damage types — not just its size — can shift between "
        "key levels; don't assume any one share (like 'Absorbed') moves "
        "the same direction at every key without checking. Re-run after "
        "a gear or talent change to see the split move."
    )


def render_disposition_ledger(
    char: Character,
    key_level: int | None = None,
    damage_multiplier: float = 1.0,
) -> None:
    """Render the "Where your survivability comes from" stacked bar.

    `key_level`/`damage_multiplier` should be the same values the caller
    is already using for the tail-risk/skill-ladder panels at this point
    in the key-level verdict expander — pass `None`/`1.0` (the defaults)
    if no specific key is in play, which runs the sweep's calibration-
    baseline profile unscaled.

    Non-Protection-Warrior specs get a one-line "not yet instrumented"
    caption instead of a bar — see this module's docstring for why a
    bar built from an un-instrumented spec's structural zeros would be
    dishonest, not just incomplete.
    """
    import streamlit as st

    st.markdown("---")
    st.markdown("#### Where your survivability comes from")

    if char.class_spec != "protection_warrior":
        spec_label = char.class_spec.replace("_", " ").title()
        st.caption(
            f"Disposition ledger not yet instrumented for **{spec_label}** — simf "
            "tracks exactly how much damage each mitigation layer removes "
            "(avoidance, block, armor, versatility, absorbs, ...) only for "
            "Protection Warrior today. The death-rate and burst-risk numbers "
            "above don't depend on this breakdown, so they're unaffected."
        )
        return

    from simf.ui.state import _ss

    cache_key = "_disposition_ledger_cache"
    char_key = _ledger_char_key(char, key_level, damage_multiplier)
    cached = _ss().get(cache_key)
    if not (cached and cached.get("char_key") == char_key):
        result = _compute_ledger_result(char, damage_multiplier)
        _ss()[cache_key] = {"char_key": char_key, "result": result}
    else:
        result = cached["result"]

    segments = compute_disposition_segments(result)
    if not segments:
        # Defensive — shouldn't happen for protection_warrior, but a bar
        # built from an empty list is a worse failure than a plain caption.
        st.caption("Disposition ledger unavailable for this build.")
        return

    _render_stacked_bar(segments, key_level)

    st.caption(_what_this_shows_caption(key_level))
    st.markdown(_FOR_THE_CURIOUS_HTML, unsafe_allow_html=True)
    st.caption(_provenance_caption())

    healed = compute_healed_back_shares(result)
    if healed is not None:
        self_share, external_share = healed
        if self_share > 0.0 or external_share > 0.0:
            if self_share > 0.0:
                self_clause = f"this build's own kit put back **{self_share * 100:.0f}%**"
                tail = "the rest stayed as net HP loss for the fight."
            else:
                # Not every spec's self-sustain is wired into this
                # bookkeeping path yet — `apply_self_heal` is only ever
                # called from `classes/guardian_druid.py` today (see
                # `SimResult.disposition_healed_self`'s docstring in
                # `core/metrics.py`), so for every spec that actually
                # reaches this ledger (Protection Warrior only), this
                # share is a structural 0.0, not a per-build measurement.
                # A bare "0%" here would tell the reader "this kit heals
                # for nothing," which isn't a claim this breakdown can
                # actually make yet.
                self_clause = (
                    "this build's own self-sustain **isn't yet credited** in this breakdown"
                )
                tail = "the true net HP loss may be lower than the remainder implies."
            st.caption(
                "**Separately** (not part of the bar above — total healing "
                "over a fight isn't bounded the same way a single hit's raw "
                "damage is): of the damage that landed, this run's healer "
                f"stream put back **{external_share * 100:.0f}%**, and "
                f"{self_clause} — {tail}"
            )


def _render_stacked_bar(segments: list[DispositionSegment], key_level: int | None) -> None:
    import plotly.graph_objects as go
    import streamlit as st

    from simf.ui.helpers.plotly_codex import apply_codex_layout

    fig = go.Figure()
    for seg in segments:
        fig.add_trace(
            go.Bar(
                name=_legend_label(seg),
                x=[seg.share * 100],
                y=["Raw incoming damage"],
                orientation="h",
                marker_color=seg.color,
                hovertemplate=f"{seg.label}: %{{x:.1f}}%<extra></extra>",
            )
        )
    fig.update_layout(
        barmode="stack",
        # A vertical right-side legend, not a horizontal one below the
        # plot — 8 entries in a one-row-tall figure has no room for a
        # horizontal legend to wrap into, and Plotly's fallback for an
        # overflowing legend is a scrollable overlay floating ON TOP of
        # the chart, not a clean row beneath it (caught live 2026-07-09,
        # see this module's screenshot-verification notes). The right
        # margin below is sized to fit "Absorbed (Ignore Pain) · 100%",
        # the longest label after the legend-label shortening in
        # `compute_disposition_segments` PLUS the widest possible
        # `_legend_label` share suffix (2026-07-10 readability workshop).
        height=260,
        margin=dict(l=10, r=240, t=10, b=40),
        xaxis_title="% of raw incoming damage",
        yaxis_visible=False,
        legend=dict(
            orientation="v",
            yanchor="middle",
            y=0.5,
            xanchor="left",
            x=1.02,
            # Plotly's own default for `barmode="stack"` reverses legend
            # order relative to trace-add order (a convention meant for
            # VERTICAL stacks, where reading the legend top-to-bottom
            # then matches the bar bottom-to-top) — left un-set, that
            # reversal makes THIS horizontal bar's legend read bottom-to-
            # top opposite the bar's actual left-to-right chain order
            # (avoided → ... → dealt), exactly backwards (5/6 live
            # reviewers, 2026-07-09/10 triage). Traces are added in that
            # same chain order above, so pinning `traceorder="normal"`
            # forces the legend to list them top-to-bottom in add order,
            # matching the bar's left-to-right read.
            traceorder="normal",
        ),
    )
    apply_codex_layout(fig)
    chart_key = f"disposition_ledger_chart_{key_level if key_level is not None else 'baseline'}"
    st.plotly_chart(fig, width="stretch", key=chart_key)

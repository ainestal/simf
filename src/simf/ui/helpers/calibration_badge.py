"""Canonical calibration-tier badge helper (ROADMAP Batch H, 2026-07-08).

Every page load with a character loaded rendered `_uncalibrated_spec_warning()`
(``ui/state.py``) as an always-expanded ``st.warning()`` wall of text — as of
this module's original writing (2026-07-08), for five of the app's six specs
(every spec but Prot Warrior/Guardian Druid, both `calibrated` at the time;
both have since been downgraded to `characterized` — see `constants.yaml`'s
per-spec `calibration_tier` comments for the current, authoritative state)
that meant a full amber caveat paragraph sat above the verdict on every single
visit, whether or not the player cared about the calibration math that day.
A compact, always-agreeing badge belongs there instead; the full paragraph
still exists (nothing is deleted), it just moves into a collapsed expander
next to the badge.

This module is the single source of truth for turning a spec's
``calibration_tier`` (``data/constants.yaml``, one of `core.constants
.CALIBRATION_TIERS`) into a badge. It deliberately does NOT re-derive
calibration status on its own — it reads the exact same
``spec_cfg.get("calibration_tier", "placeholder")`` lookup
``_uncalibrated_spec_warning()`` uses, so the badge and the (unchanged)
caveat text can never disagree. This codebase has a documented history of
"two surfaces disagree about calibration status" bugs (see the incident
history in ``log_cd_plan.py`` right above its own truthiness check on
``_uncalibrated_spec_warning()``'s return value) — a second, parallel way of
deciding "is this spec calibrated" is exactly the class of bug to avoid.

Mirrors ``damage_school_badge.py``'s shape: a ``compute_*`` pure function
(unit-testable without Streamlit) + a ``render_*_html`` function returning a
``<span class="calibration-badge calibration-badge-{tier}">`` for
``st.markdown(unsafe_allow_html=True)`` sites. WCAG 1.4.1 — the badge always
carries a real text label (``"Calibrated"`` / ``"Characterized"`` /
``"Placeholder"``) alongside the icon; color/icon alone never carries the
meaning.

Icon vocabulary reused, not invented — but NOT the emoji forms. ``✓`` and
``!`` are plain text glyphs, each inheriting its tier's CSS color
(``--accent-good`` / ``--accent-warn``) the way ``±`` inherits neutral gray
for the middle ``characterized`` tier (``--border-default``/``--text-muted``
— "in progress," not "caution"; see ``_render_calibration_badge_css()`` in
``ui/css.py``). Round-1 review (2026-07-08, ui-craft-critic) flagged the
original 🟡 traffic-light emoji on the ``characterized`` tier as a
one-component palette violation — a full-color emoji renders in its own
baked-in color regardless of CSS ``color``. That review swapped 🟡→± but
missed that ``placeholder``'s own ``⚠️`` has the exact same defect (round-2
review, same day): the badge shipped with a bright multicolor warning emoji
sitting next to bronze ``--accent-warn`` text — two different colors inside
one pill, the identical bug the docstring had just argued against for the
tier next to it. ``⚠️`` → ``!`` closes it for all three tiers at once.

## 2026-07-10 readability workshop — trailing trust clause

A follow-up 6-persona readability workshop on the (separately shipped)
eHP-transparency feature flagged this badge as a smaller, cheap, on-voice
fix: the pill's short tier word ("Calibrated" / "Characterized" /
"Placeholder") doesn't tell a first-time reader HOW MUCH to trust the page
without opening the separate collapsed caveat expander next to it —
``docs/ui_copy_voice.md`` rule #1 ("gloss jargon inline on first
appearance") applies to the tier word itself, exactly like an undefined
acronym. Each tier now carries a short trailing plain-English clause baked
into the same pill (``_TIER_TRAILING_CLAUSES``), honest and distinct per
tier rather than a single vague reassurance — the full caveat paragraph in
the existing collapsed expander is completely untouched; this is a cheap
companion clause, not a duplicate of it. Kept inside this module (not
``app.py``) so the fix stays file-isolated from the sibling cluster that
owns ``app.py`` this same workshop.
"""

from __future__ import annotations

from dataclasses import dataclass

# Plain-English label per tier — UI-string content (kept here, not in
# constants.yaml, mirroring damage_school_badge.py's `_SCHOOL_LABELS`
# comment: colors/mechanics data lives in YAML, display strings live in
# Python next to the thing that renders them).
_TIER_LABELS: dict[str, str] = {
    "calibrated": "Calibrated",
    "characterized": "Characterized",
    "placeholder": "Placeholder",
}

# Icon per tier — see module docstring for why these three and not new ones.
_TIER_ICONS: dict[str, str] = {
    "calibrated": "✓",
    "characterized": "±",
    "placeholder": "!",
}

# Short trailing plain-English clause per tier — see the module docstring's
# "2026-07-10 readability workshop" section. Honest and distinct per tier
# (never a single vague "results may vary" reassurance that would apply
# equally to all three): each clause states plainly how much the *whole
# page* should be trusted, in the same breath as the tier word that names
# it. The full caveat text (mechanism, corpus size, RMSE, ...) stays in the
# existing collapsed expander unchanged — this is a cheap companion, not a
# replacement for it.
_TIER_TRAILING_CLAUSES: dict[str, str] = {
    "calibrated": "matches real combat logs closely",
    "characterized": "checked against a few logs — trust less",
    "placeholder": "not checked against real logs yet",
}

_DEFAULT_TIER = "placeholder"


@dataclass(frozen=True)
class CalibrationBadge:
    """One spec's calibration-tier badge, ready to render.

    ``tier`` is always one of `core.constants.CALIBRATION_TIERS` — never the
    raw, possibly-malformed YAML value (``compute_calibration_badge`` clamps
    an unrecognized value to the same ``"placeholder"`` default
    ``spec_is_calibrated`` / ``_uncalibrated_spec_warning`` use, so a typo in
    ``constants.yaml`` degrades to "least confident," never a crash or a
    silent over-claim).

    ``trailing_clause`` is the short plain-English "how much to trust this"
    companion text (see ``_TIER_TRAILING_CLAUSES``) — kept as its own field,
    not baked directly into ``label``, so a caller/test can inspect or
    reformat it independently of the tier's short badge word."""

    tier: str
    label: str
    icon: str
    trailing_clause: str = ""


def compute_calibration_badge(spec: str, constants: dict) -> CalibrationBadge:
    """Read `spec`'s calibration tier out of `constants` (a loaded
    `constants.yaml` dict) and return its badge.

    Reads `constants["specs"][spec]["calibration_tier"]`, defaulting to
    `"placeholder"` on a missing spec entry or a missing/unrecognized
    `calibration_tier` key — the SAME default
    `_uncalibrated_spec_warning()` and `spec_is_calibrated()` use, so a
    character with no matching spec entry never silently reads as
    calibrated.
    """
    from simf.core.constants import CALIBRATION_TIERS

    spec_cfg = ((constants or {}).get("specs") or {}).get(spec) or {}
    tier = spec_cfg.get("calibration_tier", _DEFAULT_TIER)
    if tier not in CALIBRATION_TIERS:
        tier = _DEFAULT_TIER
    return CalibrationBadge(
        tier=tier,
        label=_TIER_LABELS[tier],
        icon=_TIER_ICONS[tier],
        trailing_clause=_TIER_TRAILING_CLAUSES[tier],
    )


def render_calibration_badge_html(badge: CalibrationBadge) -> str:
    """Render `badge` as an HTML pill for `st.markdown(unsafe_allow_html=True)`.

    Returns a `<span class="calibration-badge calibration-badge-{tier}">`
    carrying the icon, the text label, AND (2026-07-10) a short trailing
    "how much to trust this" clause — WCAG 1.4.1, never icon-only, and never
    just the bare tier word either: a first-time reader shouldn't have to
    already know what "Characterized" means on this page to act on it. CSS
    layout + per-tier color live in `ui/css.py`'s
    `_render_calibration_badge_css()`, injected once at app load (same split
    as `render_school_badge` / `_render_school_badge_css`). `trailing_clause`
    is optional (defaults to `""` on a hand-built `CalibrationBadge` that
    doesn't set one) so it degrades to the old icon+label-only rendering
    rather than an ugly trailing " — " with nothing after it.

    As of the F-001/F-002 chip redesign (review round R2, 2026-07-17) this
    HTML-pill form has no production call site — the run-config strip renders
    the badge as a plain-text popover label (`_calibration_chip_label` in
    ``ui/load.py``), which can't carry a `<span class>`. It's kept as the
    module's canonical pill renderer (this module is the single source of
    truth for the badge) and stays under test, ready for any future
    non-popover surface that needs the styled pill.
    """
    classes = f"calibration-badge calibration-badge-{badge.tier}"
    clause = f" — {badge.trailing_clause}" if badge.trailing_clause else ""
    return f'<span class="{classes}">{badge.icon} {badge.label}{clause}</span>'

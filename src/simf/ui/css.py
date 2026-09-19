"""simf UI — L0 CSS / script injectors.

Functions that emit ``<style>`` / ``<script>`` blocks driven by
``constants.yaml``. They call ``st.*`` (markdown / html) INSIDE their bodies —
never at import time — so importing this module is side-effect-free. The static
CSS block and the module-level CALL SITES that invoke these injectors stay in
``app.py`` (which must run ``st.set_page_config`` first); only the definitions
live here.
"""

from __future__ import annotations

import streamlit as st

from simf.core.constants import load_constants


def _render_school_badge_css() -> None:
    """Inject per-school `.school-badge.<school>` rules driven by
    `data/constants.yaml: damage_school_colors`.

    The static CSS block above carries the badge layout (padding,
    font-size, border-radius); the actual colors live in YAML so a
    designer can edit them without touching Python. Keeping the
    layout + colors split also means a future palette refresh only
    touches the YAML — the contrast pairs are pre-measured per
    school there.

    Each entry expands into two rules:
      - `.school-badge.<school>` with the fg/bg pair from YAML
      - For "physical", also a `.school-badge.physical-bleed` variant
        with a dashed border and a slightly lighter fill so the eye
        spots armor-bypass DOTs ("Rake / Rip / Open Wound / …")
        without the user having to read the parenthetical.

    The bg color in YAML is the saturated pill fill; here we apply
    a ~22% alpha so the pill reads as a tint rather than a flat
    block — the fg ink hits 4.5:1 against the card background
    (--surface-elev) regardless.
    """
    colors = load_constants().get("damage_school_colors", {}) or {}
    if not colors:
        return

    def _hex_to_rgba(hex_color: str, alpha: float) -> str:
        """`#aabbcc` → `rgba(170,187,204,0.36)`. Tolerates malformed
        input by falling back to a neutral grey so a typo in YAML
        doesn't render a blank pill."""
        h = (hex_color or "").lstrip("#")
        if len(h) != 6:
            return f"rgba(204, 204, 204, {alpha})"
        try:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        except ValueError:
            return f"rgba(204, 204, 204, {alpha})"
        return f"rgba({r}, {g}, {b}, {alpha})"

    rules: list[str] = []
    for school, entry in colors.items():
        fg = entry.get("fg", "#4a4a4a")
        bg = entry.get("bg", "#cccccc")
        rules.append(
            f"  .school-badge.{school} {{ color: {fg}; background: {_hex_to_rgba(bg, 0.36)}; }}"
        )
    # Bleed variant — derived from the physical entry so a future
    # palette shift keeps the bleed badge in the same hue family.
    physical = colors.get("physical", {})
    p_fg = physical.get("fg", "#7a3d2c")
    p_bg = physical.get("bg", "#c9a594")
    rules.append(
        f"  .school-badge.physical-bleed {{ color: {p_fg}; "
        f"background: {_hex_to_rgba(p_bg, 0.22)}; "
        "border-style: dashed; }"
    )
    st.markdown("<style>\n" + "\n".join(rules) + "\n</style>", unsafe_allow_html=True)


def _render_track_badge_css() -> None:
    """Inject per-track `.track-badge.track-<name>` color rules driven by
    `data/constants.yaml: gear.upgrade_track_colors`.

    Same split as the school badges: the static CSS block above owns the
    badge layout, the per-track tint lives in YAML so it can be tuned
    without touching Python. A track with no entry keeps the muted
    fallback color from the static rule."""
    colors = (load_constants().get("gear", {}) or {}).get("upgrade_track_colors", {}) or {}
    if not colors:
        return
    rules = [f"  .track-badge.track-{track} {{ color: {hexc}; }}" for track, hexc in colors.items()]
    st.markdown("<style>\n" + "\n".join(rules) + "\n</style>", unsafe_allow_html=True)


_WOWHEAD_TOOLTIPS_JS = """
<script>
(function() {
  var doc = window.parent ? window.parent.document : document;
  var win = window.parent || window;
  var rescan = function() {
    if (win.$WowheadPower && typeof win.$WowheadPower.refreshLinks === 'function') {
      win.$WowheadPower.refreshLinks();
    }
  };
  if (!doc.getElementById('simf-wowhead-power')) {
    var cfg = doc.createElement('script');
    cfg.textContent = "var whTooltips = {colorLinks: false, iconizeLinks: false, " +
      "renameLinks: false, iconSize: 'medium'};";
    doc.head.appendChild(cfg);
    var s = doc.createElement('script');
    s.id = 'simf-wowhead-power';
    s.src = 'https://wow.zamimg.com/widgets/power.js';
    s.async = true;
    doc.head.appendChild(s);
  }
  // Re-scan after the current render flushes (and again after late paints).
  setTimeout(rescan, 200);
  setTimeout(rescan, 1500);
})();
</script>
"""


def _render_calibration_badge_css() -> None:
    """Inject the `.calibration-badge` family of rules (ROADMAP Batch H,
    2026-07-08 — see `ui/helpers/calibration_badge.py`).

    Not YAML-driven, unlike `_render_school_badge_css()` /
    `_render_track_badge_css()` — there are only 3 fixed tiers
    (`core.constants.CALIBRATION_TIERS`), so there's no per-key data table to
    thread through YAML; this is a small fixed rule set in the same spirit as
    `_render_reduced_motion_css()`. Deliberately reuses the SAME two semantic
    accent tokens every other trust signal on this page already uses
    (`--accent-good` steel = "trust this," `--accent-warn` bronze = "treat
    with caution"), plus the neutral `--border-default` / `--text-muted`
    pair for the middle "characterized" tier — no new hues, per the
    ROADMAP's "no new visual identity" veto.

    Layout mirrors `.tier-badge` (bordered pill on `--surface-elev`, not a
    saturated fill) so this reads as one badge family with the existing
    tier-set badge next to the run-config strip rather than a new shape.
    """
    st.markdown(
        """<style>
  .calibration-badge {
    display: inline-block;
    padding: 3px 9px;
    border-radius: var(--radius-chip);
    font-size: 12px;
    font-weight: 600;
    line-height: 1.4;
    white-space: nowrap;
    background: var(--surface-elev);
    border: 1px solid var(--border-default);
    color: var(--text-primary);
  }
  .calibration-badge-calibrated {
    border-color: var(--accent-good);
    color: var(--accent-good);
  }
  .calibration-badge-characterized {
    border-color: var(--border-default);
    color: var(--text-muted);
  }
  .calibration-badge-placeholder {
    border-color: var(--accent-warn);
    color: var(--accent-warn);
  }
  /* The calibration CHIP (ui/load.py `_render_run_config_strip`) is a popover
     trigger button wrapped in a per-tier keyed container so it carries the
     SAME tier accent the inline .calibration-badge pill uses — the green ✓ is
     the one signal a casual tank reads at a glance (novice_tank, review round
     R2, 2026-07-17), so the chip must keep it. Descendant selector, not a
     child combinator: the button sits several Streamlit wrappers deep under
     `.st-key-*` (the display:contents nested-wrapper gotcha), so `>` would
     miss it. `characterized` keeps the neutral default button border/text —
     the "in progress, not caution" treatment, no override needed. */
  .st-key-calibration-chip-calibrated button {
    border-color: var(--accent-good) !important;
    color: var(--accent-good) !important;
  }
  .st-key-calibration-chip-placeholder button {
    border-color: var(--accent-warn) !important;
    color: var(--accent-warn) !important;
  }
  /* Left-align the chip label so Streamlit's default nowrap+ellipsis
     actually shows a readable "start of text…" truncation instead of a
     confusing symmetric clip from BOTH ends (live-UI review, 2026-07-18:
     at 200% browser zoom the label renders wider than the button, and
     Streamlit centers button labels by default — text-overflow:ellipsis is
     ambiguous/broken on centered nowrap text, so the visible slice was the
     MIDDLE of the sentence with no "…" at all, e.g. "cterized — checked
     against a few logs — trus"). Targets every tier's chip container, not
     just calibrated/placeholder above, since this is alignment, not color.
     `*` reaches Streamlit's nested label wrapper divs (all centered by
     their own default), not just the outer <button>. */
  [class*="st-key-calibration-chip-"] button,
  [class*="st-key-calibration-chip-"] button * {
    justify-content: flex-start !important;
    text-align: left !important;
  }
  /* Bound the chip to its column and let overflow show as a real "…"
     instead of a hard mid-word clip — confirmed missing at ≤390px width
     AND 200% zoom (R3 review, 2026-07-19/20). Root cause, confirmed via a
     live DOM/computed-style probe, not guessed: Streamlit's own label `<p>`
     already carries `overflow:hidden; text-overflow:ellipsis` — the ellipsis
     mechanism was never missing. What was missing is `min-width: 0` on the
     `<p>`'s flex-container ANCESTORS inside the button (Streamlit wraps the
     label in 2-3 nested flex divs for the icon+label+chevron row); a flex
     item's default `min-width: auto` refuses to shrink below its content's
     natural size, so those wrapper divs rendered ~400px wide — wider than
     the 358px button box — and the `<p>` never got squeezed down to a width
     where its own ellipsis could take effect. This is the same bug CLASS as
     the `.gear-col` missing-`min-width:0` overflow (PR #279) applied to a
     flex row instead of a grid column. `*` reaches every nested wrapper
     regardless of Streamlit's internal (unstable, version-specific)
     class names — targeting those directly would be a landmine. */
  [class*="st-key-calibration-chip-"] button,
  [class*="st-key-calibration-chip-"] button * {
    min-width: 0 !important;
  }
  [class*="st-key-calibration-chip-"] button {
    max-width: 100% !important;
  }
</style>""",
        unsafe_allow_html=True,
    )


def _render_reduced_motion_css() -> None:
    """Inject a global `prefers-reduced-motion` guard.

    Mirrors the `_render_school_badge_css()` / `_render_track_badge_css()`
    idiom (a small idempotent `<style>` block emitted once per script run),
    but this one isn't YAML-driven — it's a fixed accessibility floor that
    applies to every element on the page, present or future.

    Scope note: this only reaches CSS `transition`/`animation` properties
    (the app's one existing use is the log-run-picker hover transition in
    the static block above). It does NOT reach Plotly's own animation
    engine (`layout.transition`, `animation_frame`) — Plotly animates via
    its own JS, not CSS. No chart in this repo uses that yet (see
    `plotly_codex.py` for the pointer comment); if one ever does, it needs
    its own reduced-motion check independent of this CSS guard.
    """
    st.markdown(
        """<style>
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
</style>""",
        unsafe_allow_html=True,
    )


def _inject_wowhead_tooltips() -> None:
    """Load Wowhead Power on the parent document so item links get rich
    tooltips on hover. Idempotent — guarded by a script-id check."""
    st.html(_WOWHEAD_TOOLTIPS_JS, unsafe_allow_javascript=True)

"""Canonical damage-school badge helper (Brutoh idea #d, 2026-05-25).

Every "Why did I die?" surface (top damage abilities, ability danger
ranking, mitigation audit, death recap, per-segment risk) names spells
but historically dropped the damage type. Tank decision-making is
school-routed — "this is Magic → Spell Reflect, not Shield Block" — so
the school is load-bearing context, not decoration.

This module is the single source of truth for rendering school badges
across the UI. Two output modes are supported:

  - ``style="html"`` — for ``st.markdown(unsafe_allow_html=True)`` sites
    (per-segment risk, death attribution, top-ability lists). Returns
    a colored ``<span class="school-badge ...">`` pill that the CSS in
    ``app.py`` styles. Colors come from ``data/constants.yaml`` →
    ``damage_school_colors`` so they're never hardcoded in Python.

  - ``style="text"`` — for ``st.dataframe`` cells (mitigation audit,
    ability danger ranking, death timeline). Streamlit's dataframe
    doesn't render HTML, so we ship plain text with the same canonical
    label (``"Physical"``, ``"Fire"``, ``"Physical (bleed)"`` …).

WCAG 1.4.1 — every supported school renders a non-empty TEXT label. The
HTML pill's color is decoration; readers (and screen readers) get the
school name as text.

Bleed treatment: bleeds are a subset of physical-school DOTs that bypass
armor mitigation (see ``core/bleed_detection.py``). When ``is_bleed=True``
and ``school=="physical"`` the badge renders as ``"Physical (bleed)"``.
Other (school, is_bleed=True) combinations fall back to the school's
normal label — there's no known non-physical bleed in 12.0.5, so the
combination is treated as "is_bleed is ignored unless physical."
"""

from __future__ import annotations

from functools import lru_cache

# Canonical school names (lowercase). Imported by tests so the
# "every supported school has a text label" tripwire stays exhaustive.
SUPPORTED_SCHOOLS: tuple[str, ...] = (
    "physical",
    "holy",
    "fire",
    "nature",
    "frost",
    "shadow",
    "arcane",
    "unknown",
)

# Plain-English label per school. Kept here (not in constants.yaml)
# because it's UI-string content, not game-mechanics data — the
# color/contrast values live in constants.yaml.
_SCHOOL_LABELS: dict[str, str] = {
    "physical": "Physical",
    "holy": "Holy",
    "fire": "Fire",
    "nature": "Nature",
    "frost": "Frost",
    "shadow": "Shadow",
    "arcane": "Arcane",
    "unknown": "Unknown",
}


@lru_cache(maxsize=1)
def _school_colors() -> dict[str, dict[str, str]]:
    """Lazy-load + cache the school → {fg, bg} colour map from constants.yaml.

    Lazy so the import of this module doesn't pull in the full constants
    loader (and its yaml dependency) at every UI module load. Cached
    because the YAML file doesn't change at runtime.
    """
    from simf.core.constants import load_constants

    colors = load_constants().get("damage_school_colors", {}) or {}
    # Normalize keys to lowercase strings; tolerate missing entries by
    # falling back to the "unknown" tint at render time.
    return {str(k).lower(): dict(v) for k, v in colors.items()}


def school_label(school: str | None, is_bleed: bool = False) -> str:
    """Return the canonical text label for a school + is_bleed combination.

    ``"Physical"``, ``"Fire"``, ``"Physical (bleed)"``, ``"Unknown"`` —
    used both as the visible text inside an HTML pill and as the cell
    content in a Streamlit dataframe. Always non-empty so the badge
    text is the WCAG 1.4.1 non-color signifier.

    Unknown / unsupported school names fall back to ``"Unknown"`` rather
    than echoing the raw string, so a malformed combat-log school flag
    doesn't leak ``"voodoo"`` into the UI.
    """
    key = (school or "unknown").lower()
    base = _SCHOOL_LABELS.get(key, _SCHOOL_LABELS["unknown"])
    if is_bleed and key == "physical":
        return f"{base} (bleed)"
    return base


def render_school_badge(
    school: str | None,
    is_bleed: bool = False,
    *,
    style: str = "html",
) -> str:
    """Render a damage-school badge for a given school.

    ``style="html"`` returns a ``<span class="school-badge {key}">``
    pill with the text label inside. CSS in ``app.py`` styles the pill
    using the per-school fg/bg tokens from ``constants.yaml``.

    ``style="text"`` returns the bare text label (``"Physical"``,
    ``"Physical (bleed)"`` …) — suitable for ``st.dataframe`` cells
    where HTML doesn't render.

    Bleed: when ``is_bleed=True`` and ``school=="physical"`` the pill
    label reads ``"Physical (bleed)"`` AND the span gets an extra
    ``physical-bleed`` modifier class so the CSS can dim it slightly
    (signalling "armor didn't apply here").

    ``school=None`` or an unsupported school name is rendered as the
    ``unknown`` badge (neutral grey) — never raises.
    """
    key = (school or "unknown").lower()
    if key not in SUPPORTED_SCHOOLS:
        key = "unknown"
    label = school_label(key, is_bleed=is_bleed)

    if style == "text":
        return label

    if style != "html":
        # Defensive: unknown styles are a programmer error, not a
        # runtime crash — fall back to plain text and let the caller
        # see the bare label in the rendered surface.
        return label

    # CSS hook: `.school-badge.<key>` for the per-school color, plus
    # `.school-badge.physical-bleed` modifier for the dimmed bleed
    # variant. The label text inside the pill is the WCAG 1.4.1
    # non-color signifier.
    classes = f"school-badge {key}"
    if is_bleed and key == "physical":
        classes += " physical-bleed"
    return f'<span class="{classes}">{label}</span>'


def dominant_school_for_ability(
    events,
    spell_name: str,
) -> tuple[str, bool]:
    """Pick the dominant (school, is_bleed) tuple for a given ability.

    Walks the event list (typically a run's `iter_damage_events` output),
    sums damage per (school) bucket for the named spell, and returns
    the largest-share bucket. is_bleed is derived from the spell name
    via ``core.bleed_detection.is_bleed`` — the helper is the engine
    & UI single source of truth, so the audit table never drifts from
    what the mitigation engine treats as a bleed.

    Returns ``("unknown", False)`` when no events match the spell name.
    """
    from simf.core.bleed_detection import is_bleed as _is_bleed

    totals: dict[str, int] = {}
    for e in events:
        if getattr(e, "spell_name", None) != spell_name:
            continue
        school = (getattr(e, "school", None) or "unknown").lower()
        totals[school] = totals.get(school, 0) + int(getattr(e, "amount", 0))
    if not totals:
        return ("unknown", False)
    dominant = max(totals.items(), key=lambda kv: kv[1])[0]
    # is_periodic=True: this is a per-ABILITY badge (one label for every
    # event sharing this name), not a per-event mitigation decision, and
    # some abilities carry both a direct hit and a periodic tick under the
    # same name (e.g. Rake). Cosmetic label only — doesn't feed the sim.
    return (dominant, _is_bleed(spell_name, is_periodic=True))


def build_ability_school_map(events) -> dict[str, tuple[str, bool]]:
    """Build a single {spell_name: (school, is_bleed)} map over `events`.

    Cheaper than calling `dominant_school_for_ability` once per ability
    name when you have many — single pass instead of N. Used at the
    render sites that need to badge every row of a large table.
    """
    from simf.core.bleed_detection import is_bleed as _is_bleed

    # nested totals: {spell: {school: amount}}
    by_spell: dict[str, dict[str, int]] = {}
    for e in events:
        spell = getattr(e, "spell_name", None)
        if not spell:
            continue
        school = (getattr(e, "school", None) or "unknown").lower()
        bucket = by_spell.setdefault(spell, {})
        bucket[school] = bucket.get(school, 0) + int(getattr(e, "amount", 0))

    result: dict[str, tuple[str, bool]] = {}
    for spell, totals in by_spell.items():
        if not totals:
            continue
        dominant = max(totals.items(), key=lambda kv: kv[1])[0]
        # is_periodic=True — see dominant_school_for_ability's comment above.
        result[spell] = (dominant, _is_bleed(spell, is_periodic=True))
    return result

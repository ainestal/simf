"""Canonical eHP definition — single shared source (2026-07-09).

"eHP" is the load-bearing term behind almost every survivability number
this app renders (Vault, Gear, key-level verdict, cooldown planner). Until
2026-07-09 the definition was one hardcoded string that named Protection
Warrior's own always-on passive ("Defensive Stance") and excluded
cooldowns ("Shield Block/Shield Wall") — and that exact string rendered
for every spec, so a Guardian/Paladin/DK/Monk/DH reading their own eHP
definition saw Warrior abilities they don't have.

``ehp_gloss_text(class_spec)`` fixes that: it names THIS character's own
modeled always-on passive and excluded active-mitigation examples, falling
back to spec-agnostic wording when the spec is unmodeled/unknown/``None``.
The two example maps below are sourced from the actual code paths, not
re-derived independently:

- ``_ALWAYS_ON_PASSIVE_EXAMPLE_BY_SPEC`` mirrors exactly what
  ``Character._always_on_dr()`` (``core/character.py``) multiplies into
  ``effective_hp_physical``/``effective_hp_magic`` for that spec. Protection
  Paladin, Blood DK, and Vengeance DH are deliberately ABSENT — those three
  specs have no always-on-passive term in ``_always_on_dr()`` today (their
  eHP is armor + versatility only), so naming a passive for them would be
  a fabrication, not a simplification. They get the generic passive clause
  instead.
- ``_EXCLUDED_ACTIVE_MIT_EXAMPLE_BY_SPEC`` names each spec's own
  button-press active mitigation / defensive CD — the per-event thing eHP
  deliberately does NOT count (a one-shot doesn't wait for your GCD). This
  is a small map local to this module rather than an import of
  ``ui.format_html._SKILL_ASSUMPTION_CDS_BY_SPEC``: that map mixes in
  healing-spell levers (Word of Glory, Death Strike) for a *different*
  "did you play your kit" caveat, and importing ``ui/format_html.py`` from
  a ``ui/helpers/`` module would invert this codebase's layered DAG
  (helpers sit below the ``ui/*.py`` surfaces — see ``app.py``'s
  module-split docstring). A drift risk between the two lists is accepted
  and named here rather than hidden.

The exclusion claim in the generic fallback is not invented — it's lifted
from ``Character.effective_hp_physical``'s own docstring:

    "Includes armor DR, versatility DR, and always-on passives (Defensive
    Stance 15% all-schools, Indomitable 4% all-damage). Excludes
    button-press CDs (Shield Block, Shield Wall) and per-event rolls
    (block, dodge, parry) — those don't apply to a single one-shot."

(``effective_hp_magic`` includes the same versatility/always-on-passive
scope but excludes school-specific party auras instead of block/dodge/
parry, which aren't a magic-damage mechanic in this engine — the physical
docstring's exclusion list is still the complete one to draw examples
from.)

``EHP_GLOSS`` (the module-level constant) is exactly ``ehp_gloss_text(None)``
— the fully spec-agnostic phrasing — kept around because it's still the
right choice for any caller that genuinely has no character in scope.
Every surface that DOES have a character should call ``render_ehp_gloss``
/ ``ehp_gloss_text`` with its ``class_spec`` instead of reaching for the
constant directly. ``vault_panel.py`` (PR #282's original ``_EHP_GLOSS``
private copy) now does exactly that, closing the one remaining drift risk
this module's docstring used to flag as an open follow-up.
"""

from __future__ import annotations

# Per-spec named example of the always-on passive damage-taken reduction
# `Character._always_on_dr()` actually applies for that spec — see the
# module docstring for why Protection Paladin / Blood DK / Vengeance DH
# are deliberately absent. Brewmaster's row is conditional in the engine
# (gated on a detected hero-talent buff aura) but still named here — "a
# passive like X, when detected" is more useful than the fully generic
# fallback for a spec that DOES have a modeled ledger, even though not
# every Brewmaster build lights it up.
_ALWAYS_ON_PASSIVE_EXAMPLE_BY_SPEC: dict[str, str] = {
    "protection_warrior": "Defensive Stance",
    "guardian_druid": "Thick Hide and Bear Form's passive damage reduction",
    "brewmaster_monk": "a detected hero-talent passive like Predictive Training",
}

# Per-spec named example of the button-press active mitigation / defensive
# cooldowns eHP deliberately excludes. See module docstring for why this
# is a small local map rather than importing format_html's sibling list.
_EXCLUDED_ACTIVE_MIT_EXAMPLE_BY_SPEC: dict[str, str] = {
    "protection_warrior": "Shield Block/Shield Wall",
    "protection_paladin": "Shield of the Righteous/Ardent Defender",
    "blood_death_knight": "Vampiric Blood/Icebound Fortitude",
    "vengeance_demon_hunter": "Demon Spikes/Metamorphosis",
    "brewmaster_monk": "Purifying Brew/Fortifying Brew",
    "guardian_druid": "Ironfur/Survival Instincts",
}

_GENERIC_PASSIVE_CLAUSE = "any modeled always-on passive damage reduction"
_GENERIC_EXCLUDED_CLAUSE = "your on-cooldown defensive presses"


def ehp_gloss_core_text(class_spec: str | None = None) -> str:
    """The one-sentence core eHP definition, naming ``class_spec``'s own
    always-on passive example (falling back to generic phrasing — see
    ``ehp_gloss_text``'s docstring for the fallback rule).

    Split out from ``ehp_gloss_exclusion_text`` (2026-07-26, mobile
    chrome-before-verdict round 2) so a caller can render just the core
    definition eagerly and put the longer "what it leaves out" caveat
    behind a click — the caveat is second-read "why is my number lower
    than expected" reassurance, not information a first-time reader needs
    before the term is used, unlike the core definition itself (see
    ``vault_panel.py``'s call site, the only one that needs the split;
    ``ehp_gloss_text`` below still returns both joined for every other
    caller, unchanged in meaning)."""
    passive = _ALWAYS_ON_PASSIVE_EXAMPLE_BY_SPEC.get(class_spec or "")
    passive_clause = f"passives like {passive}" if passive else _GENERIC_PASSIVE_CLAUSE
    return (
        "eHP = effective HP: damage a full health bar survives after "
        f"armor, versatility, and {passive_clause}."
    )


def ehp_gloss_exclusion_text(class_spec: str | None = None) -> str:
    """The "what eHP leaves out" caveat, naming ``class_spec``'s own
    excluded-active-mitigation example. Meant to sit behind a click
    (``st.popover``) beneath ``ehp_gloss_core_text`` — see that
    function's docstring."""
    excluded = _EXCLUDED_ACTIVE_MIT_EXAMPLE_BY_SPEC.get(class_spec or "")
    excluded_clause = f"cooldown presses like {excluded}" if excluded else _GENERIC_EXCLUDED_CLAUSE
    return (
        f"It excludes per-hit dodge/parry/block rolls and {excluded_clause}: "
        "per-event randomness that doesn't apply to a single worst-case hit."
    )


def ehp_gloss_text(class_spec: str | None = None) -> str:
    """Build the full eHP definition (core + exclusion caveat), naming
    ``class_spec``'s own always-on passive + excluded-active-mitigation
    examples when it's a spec this module has a real (code-sourced)
    example for.

    Falls back to the spec-agnostic generic phrasing for ``None``, a
    not-yet-modeled tank spec, or any DPS/healer spec — never fabricates
    an example for a spec that doesn't actually have one. The prefix
    sentence ("eHP = effective HP...") is identical in every case, so a
    caller matching on that substring (e.g. a test) is spec-independent.
    """
    return f"{ehp_gloss_core_text(class_spec)} {ehp_gloss_exclusion_text(class_spec)}"


# Fully spec-agnostic phrasing — the right choice for any caller with no
# character in scope. See module docstring: callers that DO have a
# character should prefer `render_ehp_gloss(class_spec)` / `ehp_gloss_text`.
EHP_GLOSS = ehp_gloss_text(None)


def render_ehp_gloss(class_spec: str | None = None) -> None:
    """``st.caption`` the eHP definition for ``class_spec`` (or the
    generic phrasing when omitted/unmodeled).

    Thin on purpose — the only reason this is a function and not just
    ``st.caption(ehp_gloss_text(class_spec))`` at the call site is so every
    surface renders it the same way (same widget type, same lack of
    markdown surprises) and a future format change (e.g. adding an icon)
    has one call site to edit.
    """
    import streamlit as st

    st.caption(ehp_gloss_text(class_spec))

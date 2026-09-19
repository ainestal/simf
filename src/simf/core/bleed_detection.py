"""Bleed detection — physical DOT events that bypass armor in WoW.

WoW's damage formula treats bleeds (a subset of physical-school
``SPELL_PERIODIC_DAMAGE`` events) as armor-ignoring: an Ironbark
Druid's Rake tick on a plate tank deals close to the same fraction
of HP as it would on a leather tank, because the armor curve doesn't
apply. simf's pre-2026-05-23 ``apply_mitigation`` was running every
physical event through ``armor / (armor + K)`` DR uniformly, which
over-mitigated bleed events by ~+37pp in the per-school audit
(`docs/validation/magic_mit_gap_remeasurement_2026_05_23.md`).

Detection is by spell-name fragment match because WoW combat logs
expose only the spell name + spell ID + school flags — there's no
"is_bleed" flag in the log line itself, and the school flag is
"physical" + DOT-tick which is also true for non-bleed physical
DOTs like Rend (which IS a bleed) vs. a fire-aspected bleed
(which... doesn't exist in 12.0.5 to my knowledge). The fragment
set is authoritative for the spells observed in the corpus and
should be expanded as new bleeds appear in future patches.

A bleed in WoW is, by mechanical definition, a over-time (periodic)
effect — there is no such thing as a one-shot "bleed hit". Several
abilities that fragment-match a bleed name (e.g. Rake, Searing Rend,
Rending Gore) ALSO have a direct, non-periodic damage component under
the same displayed name (Rake's initial strike; a boss's direct
"Searing Rend" swing alongside its "Searing Rend" DoT tick) — that
direct component is ordinary armor-mitigated physical damage, not a
bleed, even though its name matches the fragment. ``is_periodic``
callers pass whether the SPECIFIC event being classified is a
periodic tick (``SPELL_PERIODIC_DAMAGE`` / WCL ``tick_flag``); a
non-periodic event always returns False regardless of name.

Found 2026-07-18 via per-hit forensics on the ratified Warrior +
Guardian calibration corpora: production's ``is_bleed()`` call sites
(``log_replay.py``, ``wcl_replay.py``) had no periodicity gate, so a
direct "Searing Rend"/"Rending Gore" hit (school=physical,
event_type=SPELL_DAMAGE) was skipping armor DR it should not have —
its real mitigation tracked the fight's ordinary physical armor chain
to within ~3pp, not the ~50pp gap a true bleed shows. See
``docs/validation/bleed_fragment_periodicity_gap_2026_07_18.md``.

The helper is the single source of truth for the
``DamageEvent.is_bleed`` flag the log-replay path sets, and is
re-used by ``scripts/per_school_mitigation_gap.py`` so the audit
and the engine never drift on detection rules.
"""

from __future__ import annotations

# Spell-name fragments that flag an event as a bleed. Each is matched
# as a substring of the log's spell name so set entries can be short
# (e.g. "Rend" matches "Rend", "Rend (Glyph)", "Mass Rend").
BLEED_SPELL_FRAGMENTS: frozenset[str] = frozenset(
    {
        "Rip",
        "Rake",
        "Rend",
        "Mortal Wound",
        "Deep Wounds",
        "Garrote",
        "Bleed",
        "Crippling Bleed",
        "Vicious Wound",
        "Hemorrhage",
        "Lacerate",
        "Open Wound",
        "Gushing Wound",
        "Savage Slash",
    }
)


def is_bleed(spell_name: str | None, is_periodic: bool) -> bool:
    """Return True iff the event is a periodic tick whose spell name
    fragment-matches a known bleed.

    ``is_periodic`` is required (no default) so a new call site can't
    silently reintroduce the 2026-07-18 gap: pass
    ``event_type == "SPELL_PERIODIC_DAMAGE"`` (local log) or
    ``tick_flag`` (WCL). A non-periodic event — even one whose name
    matches a fragment, like Rake's initial direct hit or a boss's
    direct "Searing Rend" swing — always returns False: WoW has no
    such thing as a one-shot bleed.

    ``None`` / empty ``spell_name`` returns False — the caller
    (typically a log-event constructor) shouldn't have to
    special-case missing names. Auto-attacks and melee swings carry
    empty / "auto-attack" spell names and correctly return False here.
    """
    if not is_periodic or not spell_name:
        return False
    return any(frag in spell_name for frag in BLEED_SPELL_FRAGMENTS)


def event_is_periodic(event: object) -> bool:
    """Best-effort periodicity check for a ``DamageTakenEvent``-shaped
    object — the single place every UI/coaching caller derives the
    ``is_periodic`` arg ``is_bleed()`` requires, so they never drift from
    each other on the WCL-vs-local-log distinction.

    ``tick_flag`` is the reliable signal for WCL-sourced events (WCL's
    ``event_type`` stays a generic string, never widened to
    ``SPELL_PERIODIC_DAMAGE`` — see ``io/wcl_api.py``); ``event_type ==
    "SPELL_PERIODIC_DAMAGE"`` is the reliable signal for local-log-sourced
    events, where ``tick_flag`` defaults False and is never set. Checking
    both covers either source uniformly. Missing attributes (a bare dict,
    or an object that doesn't carry either field) default to False.
    """
    return bool(getattr(event, "tick_flag", False)) or (
        getattr(event, "event_type", None) == "SPELL_PERIODIC_DAMAGE"
    )

"""Pure helpers for the upgrade-normalized slot comparison.

The Tuesday-vault problem: every weekly-vault choice tends to land *below*
a maxed player's equipped item level, so an as-dropped ΔeHP comparison shows
every option as a downgrade and teaches nothing. The slot dialog answers the
real question — "is this piece's *itemization* better if I invest crests to
match my gear?" — by rescaling both sides to a common target ilvl.

This module keeps the control's state machine, the slider bounds, the
slot→category ceiling lookup, and the crest/upgrade-step cost estimate out of
the Streamlit layer so they're unit-testable without booting an app. Nothing
here imports ``streamlit``.
"""

from __future__ import annotations

from dataclasses import dataclass

# "Compare at item level" modes — shared by the Vault tab AND the slot dialog
# so both surfaces present one mental model. The labels double as the radio
# option strings, so they read in plain English.
COMPARE_MATCH_GEAR = "Match my gear"  # default — rescale to the equipped piece's ilvl
COMPARE_MAX_UPGRADE = "Max upgrade"  # rescale to the slot's reachable ceiling
COMPARE_AS_DROPPED = "As dropped"  # today's literal, no-rescale comparison
COMPARE_CUSTOM = "Custom"  # rescale to a player-chosen ilvl (single-slot dialog only)
# The Vault grid spans slots with different equipped ilvls / ceilings, so it
# offers the three semantic modes; the single-slot dialog also appends Custom.
COMPARE_BASE_MODES = (COMPARE_MATCH_GEAR, COMPARE_MAX_UPGRADE, COMPARE_AS_DROPPED)


def resolve_target_ilvl(
    mode: str,
    *,
    equipped_ilvl: int | None = None,
    ceiling: int | None = None,
    custom_value: int | None = None,
) -> int | None:
    """Map a compare ``mode`` + per-slot context to the ilvl to normalize to.

    Returns ``None`` for *As dropped* (no rescale — today's behaviour). For the
    other modes it degrades gracefully when a slot lacks an equipped ilvl or a
    known ceiling, so a partially-known slot still gets a sensible target rather
    than silently falling back to as-dropped:

    - ``Match my gear`` → the equipped piece's ilvl (apples-to-apples), or the
      ceiling if the slot is empty.
    - ``Max upgrade``   → the slot's reachable ceiling, or the equipped ilvl.
    - ``Custom``        → ``custom_value`` (the slot dialog's slider).
    """
    if mode == COMPARE_AS_DROPPED:
        return None
    if mode == COMPARE_MATCH_GEAR:
        return equipped_ilvl or ceiling
    if mode == COMPARE_MAX_UPGRADE:
        return ceiling or equipped_ilvl
    if mode == COMPARE_CUSTOM:
        return custom_value
    return None


# Slot → item category, used to pick the reachable ilvl ceiling. Trinkets and
# weapons reach a higher season ceiling than armor (empirically 298 vs 289 in
# Midnight 12.0.5 exports); everything else is "armor".
_TRINKET_SLOTS = {"trinket1", "trinket2"}
_WEAPON_SLOTS = {"main_hand", "off_hand", "ranged"}


def category_for_slot(slot: str) -> str:
    """Item-level-ceiling category for ``slot``: trinket / weapon / armor."""
    if slot in _TRINKET_SLOTS:
        return "trinket"
    if slot in _WEAPON_SLOTS:
        return "weapon"
    return "armor"


def category_ceiling(
    slot: str,
    ceilings_by_category: dict | None,
    *,
    account_ceiling: int | None = None,
) -> int | None:
    """Reachable ilvl ceiling for ``slot``.

    Takes the smaller of the season category ceiling (from constants) and the
    player's own account watermark ceiling (from their export) — never invents
    headroom the player can't reach. Returns None only when neither source
    knows a ceiling.
    """
    cat = category_for_slot(slot)
    season = None
    if ceilings_by_category:
        season = ceilings_by_category.get(cat)
    candidates = [c for c in (season, account_ceiling) if c and c > 0]
    return min(candidates) if candidates else None


@dataclass(frozen=True)
class CompareBounds:
    """Resolved bounds for the 'compare at ilvl' control."""

    default_target: int | None  # the 'match my gear' target (equipped's ilvl)
    min_target: int  # floor — the lowest ilvl in play (lets the slider show as-dropped)
    max_target: int  # reachable ceiling for this slot


def compare_bounds(
    equipped_ilvl: int | None,
    candidate_ilvls: list[int],
    *,
    ceiling_ilvl: int | None = None,
) -> CompareBounds:
    """Bounds for the slider / custom-ilvl input on one slot.

    ``default_target`` is the equipped piece's ilvl — the apples-to-apples
    "match my gear" comparison. ``min_target`` is the lowest item level among
    the equipped + candidate pieces, so the slider's bottom equals the
    as-dropped state. ``max_target`` is the reachable ceiling, clamped so it's
    never below what the player already wears (a maxed piece must still be
    representable).
    """
    ilvls = [int(i) for i in ([equipped_ilvl, *candidate_ilvls]) if i and int(i) > 0]
    lo = min(ilvls) if ilvls else 0
    top_in_play = max(ilvls) if ilvls else 0
    hi = ceiling_ilvl if (ceiling_ilvl and ceiling_ilvl > 0) else top_in_play
    # Never let the ceiling sit below the equipped piece or the highest piece
    # in play — those are reachable by definition (the player has them).
    hi = max(hi, top_in_play, equipped_ilvl or 0, lo)
    default = equipped_ilvl if (equipped_ilvl and equipped_ilvl > 0) else (top_in_play or None)
    return CompareBounds(default_target=default, min_target=lo, max_target=hi)


def upgrade_levels_between(
    from_ilvl: int | None,
    to_ilvl: int | None,
    ilvl_per_level: int,
) -> int:
    """Approximate number of upgrade-track levels to go ``from_ilvl`` →
    ``to_ilvl`` at ``ilvl_per_level`` ilvls per level.

    Rounds *up* — a partial level still costs a full upgrade click — and never
    returns negative (a downgrade target costs zero levels). Returns 0 when the
    inputs are unusable so the caller can omit the cost line rather than show a
    bogus number.
    """
    if not from_ilvl or not to_ilvl or ilvl_per_level <= 0:
        return 0
    delta = int(to_ilvl) - int(from_ilvl)
    if delta <= 0:
        return 0
    return -(-delta // ilvl_per_level)  # ceil division


def crest_cost_label(
    from_ilvl: int | None,
    to_ilvl: int | None,
    ilvl_per_level: int,
) -> str:
    """Human-readable upgrade cost, e.g. "+30 ilvl · ≈10 upgrade levels".

    Empty string when there's no upgrade to describe (target ≤ current). The
    crest *count* is deliberately framed as upgrade levels, not a specific
    crest tier+quantity — that mapping is drift-prone per patch and is marked
    approximate in the UI until verified against a current data source.
    """
    if not from_ilvl or not to_ilvl:
        return ""
    delta = int(to_ilvl) - int(from_ilvl)
    if delta <= 0:
        return ""
    levels = upgrade_levels_between(from_ilvl, to_ilvl, ilvl_per_level)
    lvl_txt = f" · ≈{levels} upgrade level{'s' if levels != 1 else ''}" if levels else ""
    return f"+{delta} ilvl{lvl_txt}"


def curve_ilvl_points(
    base_ilvl: int,
    max_ilvl: int,
    ilvl_per_level: int,
    *,
    include_base: bool = True,
) -> list[int]:
    """Item-level points for a piece's upgrade curve: ``base_ilvl`` stepping by
    ``ilvl_per_level`` up to and including ``max_ilvl``.

    Always includes the ceiling even when it isn't on a clean step boundary, so
    the curve's top point is the real reachable max. Returns ``[]`` for
    degenerate inputs. ``include_base=False`` drops the zero-gain base anchor
    (useful when the caller only wants the upgrade tail).
    """
    if base_ilvl <= 0 or max_ilvl <= 0 or ilvl_per_level <= 0 or max_ilvl < base_ilvl:
        return []
    points = list(range(base_ilvl, max_ilvl + 1, ilvl_per_level))
    if points and points[-1] != max_ilvl:
        points.append(max_ilvl)
    if not include_base and points and points[0] == base_ilvl:
        points = points[1:]
    return points

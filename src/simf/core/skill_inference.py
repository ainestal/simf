"""Phase 2.10b — playstyle inference from a real combat log.

The Phase 2.10 ladder shows a column of survival-by-skill-tier numbers.
On its own that's "what if I played differently." This module connects
the player's *measured* play to the right rung of the ladder so the
verdict gains a `← you` indicator anchored in their actual log.

Two signals read from the log:

1. **Shield Block coverage** — the time fraction of the run during which
   the SB buff was active. SB has a fixed duration (per
   `constants.yaml.active_mitigation.shield_block.duration_s`); each cast
   opens a window `[cast_time, cast_time + duration]`. Overlapping windows
   get merged before summing — Anger Management or rapid presses can stack
   two charges and an unmerged sum would over-count coverage.

2. **Demoralizing Shout cadence** (v3, 2026-05-21) — actual cast count
   divided by the theoretical max press count at the base cooldown
   (`actual_casts / (duration_s / cooldown_s)`), capped at 1.0. DS has a
   short duration relative to its CD (10s / 45s), so uptime would peak at
   ~22% — not comparable to SB. Press rate normalizes both signals onto
   `[0.0, 1.0]` so the per-tier threshold semantics match.

Combined tier inference takes the **lower** of the two per-signal tiers:
the engine's `_skill_allows_press()` gates SB and DS with the same
Bernoulli draw, so a player should clear *both* thresholds to claim a
tier. A high-SB / low-DS player gets surfaced as the lower tier with
both numbers shown in the trust caption — the weaker signal is what to
work on.

Threshold buckets live in `constants.yaml.skill_tiers[*]` —
`min_sb_uptime` and `min_ds_press_rate`. Order matters in the YAML —
the inference does a top-down match: a 90%-uptime / 80%-press-rate
player buckets as `in_the_zone`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .constants import load_constants, load_skill_tiers

if TYPE_CHECKING:
    from collections.abc import Iterable

    from simf.io.combat_log import CastEvent


@dataclass(frozen=True)
class InferredSkillTier:
    """The output of an inference pass over one log segment.

    `tier_id` is the combined verdict — the lower of `sb_tier_id` and
    `ds_tier_id`. The per-signal tier ids are kept so the UI can render
    *which* signal floored the verdict (the weaker rung is what to work
    on next).

    `cast_count` is SB casts for backward compatibility with the v2.10b
    surface; `ds_cast_count` is the new field.

    Ceiling fields (`sb_ceiling_*`, `sb_missed_pressable_pct`) carry the
    talent-build floor from the modifier=1.0 ladder run. When set, the
    UI surfaces them in the trust caption so the player can tell build
    limits from execution gap. Zero when ladder data wasn't available
    at inference time (graceful fallback to v3 caption shape).
    """

    tier_id: str
    sb_uptime_pct: float
    cast_count: int
    duration_s: float
    ds_press_rate: float = 0.0
    ds_cast_count: int = 0
    sb_tier_id: str = ""
    ds_tier_id: str = ""
    sb_ceiling_pct: float = 0.0
    sb_ceiling_rage_starved_pct: float = 0.0
    sb_ceiling_charge_limited_pct: float = 0.0
    sb_missed_pressable_pct: float = 0.0


@dataclass(frozen=True)
class SBGap:
    """Phase 2.10c — one Shield Block coverage gap in a real log.

    A `gap` is a contiguous time interval during which the SB buff was
    not active. `damage_during_gap` is the physical damage the player
    took while uncovered — the actionable "you lost X HP here" number
    behind anchored coaching callouts. `segment_label` is a human
    readable phrase ("Gemellus run-up" / "trash before Selin") when
    we can attribute the gap to a segment of the run; falls back to
    a bare `M:SS` time.
    """

    start_time_s: float
    end_time_s: float
    duration_s: float
    damage_during_gap: float
    time_phrase: str
    segment_label: str = ""


def shield_block_duration_s() -> float:
    """Read the canonical SB duration from constants so we don't drift if
    the patch changes it. 6.0s as of Midnight 12.0.5."""
    c = load_constants()
    return float(c["active_mitigation"]["shield_block"]["duration_s"])


def demoralizing_shout_cooldown_s() -> float:
    """Read the canonical Demo Shout base cooldown from constants. 45.0s
    as of Midnight 12.0.5. Thunderlord cuts it 30% (to 31.5s) — the
    inference deliberately ignores that so the signal stays parseable
    without talent knowledge; see `compute_ds_press_rate_from_casts`."""
    c = load_constants()
    return float(c["active_mitigation"]["demoralizing_shout"]["cooldown_s"])


def compute_ds_press_rate_from_casts(
    casts: Iterable[CastEvent],
    duration_s: float,
    *,
    cooldown_s: float | None = None,
) -> float:
    """Compute Demoralizing Shout press rate: actual casts / theoretical
    max casts at the base cooldown. Capped at 1.0.

    Returns 0.0 if `duration_s <= 0` or the cooldown is non-positive.

    **Known Thunderlord blindness (by design):** the inference uses the
    base 45s CD as the denominator, not the Thunderlord-reduced 31.5s CD.
    A Thunderlord player casting on the buffed CD will hit the 1.0 cap
    even though the engine policy would expect ~40% more presses. This
    is intentional for v1 — talent inference from ACL-off logs is a
    separate (unshipped) feature, and the cap means the inference is
    *generous* to Thunderlord, never punitive. When per-character talent
    inference lands, this denominator can switch to the effective CD.
    """
    if duration_s <= 0:
        return 0.0
    if cooldown_s is None:
        cooldown_s = demoralizing_shout_cooldown_s()
    if cooldown_s <= 0:
        return 0.0
    max_casts = duration_s / cooldown_s
    if max_casts <= 0:
        return 0.0
    cast_count = sum(1 for _ in casts)
    return min(cast_count / max_casts, 1.0)


def compute_sb_uptime_from_casts(
    casts: Iterable[CastEvent],
    duration_s: float,
    *,
    sb_duration_s: float | None = None,
    start_time_s: float = 0.0,
) -> float:
    """Compute Shield Block uptime as a fraction of `duration_s`.

    Each cast opens a window `[t, t + sb_duration_s]`. Windows are merged
    so two overlapping casts don't double-count. The end of the last
    window is clipped to `start_time_s + duration_s` so a cast 3 seconds
    before the run ends doesn't credit 3 seconds of buff after the run.

    Returns 0.0 if `duration_s <= 0` to be safe — a zero-duration segment
    can't have meaningful coverage, and inference should fall through to
    "no signal" rather than divide by zero.

    **Known undercount bias (validator, 2026-05-20):** in-game, re-casting
    SB while the buff is still up extends it via pandemic — up to 1.3×
    base (7.8s on a 6s buff). This function models each cast as a fixed
    6.0s window, so a chain-refresh pattern undercounts by up to
    ~0.3 × 6s per refresh. Bounded and small for normal play (Brutoh's
    Nexus +12 has 94 mostly-disjoint casts, negligible impact). A
    pandemic-accurate implementation reads `SPELL_AURA_APPLIED` +
    `SPELL_AURA_REMOVED` on buff ID 132404 directly — future tightening
    when bucket precision needs to exceed ~3 pp.
    """
    if duration_s <= 0:
        return 0.0
    if sb_duration_s is None:
        sb_duration_s = shield_block_duration_s()

    end_clip = start_time_s + duration_s
    raw: list[tuple[float, float]] = []
    for cast in casts:
        start = max(cast.time_s, start_time_s)
        end = min(cast.time_s + sb_duration_s, end_clip)
        if end > start:
            raw.append((start, end))
    if not raw:
        return 0.0
    raw.sort()

    # Merge overlapping intervals in one pass.
    merged_total = 0.0
    cur_start, cur_end = raw[0]
    for s, e in raw[1:]:
        if s <= cur_end:
            cur_end = max(cur_end, e)
        else:
            merged_total += cur_end - cur_start
            cur_start, cur_end = s, e
    merged_total += cur_end - cur_start
    return merged_total / duration_s


def infer_skill_tier(
    sb_uptime_pct: float,
    tiers: list[dict] | None = None,
    *,
    threshold_key: str = "min_sb_uptime",
) -> str:
    """Bucket a measured signal into a skill tier id.

    Top-down match against `tiers[*][threshold_key]` — first threshold the
    player meets or exceeds wins. The YAML is ordered top-tier-first so
    the highest tier they qualify for sticks. If `tiers` is empty,
    returns the empty string ("no tier defined").

    `threshold_key` lets the same tier list bucket either signal — SB
    uses `min_sb_uptime` (default), Demo Shout uses `min_ds_press_rate`.
    """
    tiers = tiers if tiers is not None else load_skill_tiers()
    if not tiers:
        return ""
    for tier in tiers:
        threshold = float(tier.get(threshold_key, 0.0))
        if sb_uptime_pct >= threshold:
            return str(tier["id"])
    # Fallback — should never hit because the lowest tier has 0.0, but
    # guard against a misconfigured YAML.
    return str(tiers[-1]["id"])


def match_sb_tier_to_ladder(
    actual_uptime: float,
    ladder_uptimes: list[tuple[str, float]],
) -> str:
    """Nearest-neighbor match: pick the tier whose simulated SB uptime is
    closest to the player's measured `actual_uptime`.

    `ladder_uptimes` is a list of `(tier_id, sim_uptime)` from the
    skill-ladder run, top-tier-first. The "nearest" tier corresponds
    bit-exactly to the simulated death rate of that row — which is what
    the ladder's verdict ranks on. So a player matched to `reading`
    survives like the `reading` row of the ladder, regardless of *why*
    they're at that uptime.

    Ties (equidistant from two tiers) resolve to the higher tier — gives
    the player the benefit of the doubt while staying deterministic. If
    `ladder_uptimes` is empty, returns the empty string ("no signal").
    """
    if not ladder_uptimes:
        return ""
    best_tier = ladder_uptimes[0][0]
    best_dist = abs(actual_uptime - ladder_uptimes[0][1])
    for tier_id, sim_uptime in ladder_uptimes[1:]:
        dist = abs(actual_uptime - sim_uptime)
        # Strict `<` keeps the earlier (higher) tier on ties; preserves
        # generosity since the YAML is ordered top-tier-first.
        if dist < best_dist:
            best_dist = dist
            best_tier = tier_id
    return best_tier


def _combined_tier(
    sb_tier_id: str,
    ds_tier_id: str,
    tiers: list[dict],
) -> str:
    """Return the lower-ranked of two tier ids per the YAML ordering.

    YAML order is top-tier-first (`in_the_zone` → ... → `learning`), so
    the *higher* index in the list is the *lower* tier. If either id is
    empty, return the other; if both empty, return empty.
    """
    if not sb_tier_id:
        return ds_tier_id
    if not ds_tier_id:
        return sb_tier_id
    order = {str(t["id"]): i for i, t in enumerate(tiers)}
    sb_rank = order.get(sb_tier_id, len(tiers))
    ds_rank = order.get(ds_tier_id, len(tiers))
    return sb_tier_id if sb_rank >= ds_rank else ds_tier_id


def _merge_sb_windows(
    casts: list,
    sb_duration_s: float,
    start_time_s: float,
    end_clip: float,
) -> list[tuple[float, float]]:
    """Return the merged covered windows in time order. Shared by uptime
    + gap computation so they can't disagree about coverage."""
    raw: list[tuple[float, float]] = []
    for cast in casts:
        start = max(cast.time_s, start_time_s)
        end = min(cast.time_s + sb_duration_s, end_clip)
        if end > start:
            raw.append((start, end))
    if not raw:
        return []
    raw.sort()
    merged: list[tuple[float, float]] = []
    cur_start, cur_end = raw[0]
    for s, e in raw[1:]:
        if s <= cur_end:
            cur_end = max(cur_end, e)
        else:
            merged.append((cur_start, cur_end))
            cur_start, cur_end = s, e
    merged.append((cur_start, cur_end))
    return merged


def _format_mss(elapsed_s: float) -> str:
    """Format a positive elapsed-seconds offset as `M:SS`."""
    if elapsed_s < 0:
        elapsed_s = 0.0
    total = int(elapsed_s)
    return f"{total // 60}:{total % 60:02d}"


def compute_top_sb_gaps(
    casts: Iterable[CastEvent],
    damage_events: Iterable,
    duration_s: float,
    *,
    start_time_s: float = 0.0,
    sb_duration_s: float | None = None,
    min_gap_s: float = 5.0,
    min_damage: float = 50_000.0,
    top_n: int = 3,
) -> list[SBGap]:
    """Find the worst Shield Block coverage gaps and the damage taken
    inside them.

    A `gap` is a time interval during which SB was not active. We walk
    the same merged coverage windows the uptime function uses (no
    disagreement possible), invert to the gap intervals, then sum the
    `amount` of every `DamageTakenEvent` whose `time_s` lies inside
    each gap. Returns the top `top_n` gaps ranked by damage_during_gap
    desc, filtered by `min_gap_s` (skip the noisy 1-2s gaps between
    rapid presses) and `min_damage` (skip gaps where nothing was
    happening — a quiet stretch is not an indictment of play).

    Phase 2.10c — the anchored half of the coaching layer. The skill
    ladder + inferred tier (v2.10b) tells the player *what* bucket
    they sit in; this function answers *where* on this specific log
    they earned that bucket.
    """
    if sb_duration_s is None:
        sb_duration_s = shield_block_duration_s()
    casts_list = list(casts)
    events_list = list(damage_events)
    if duration_s <= 0 or not events_list:
        return []

    end_clip = start_time_s + duration_s
    covered = _merge_sb_windows(casts_list, sb_duration_s, start_time_s, end_clip)

    # Invert covered → uncovered. Walk covered intervals left to right;
    # emit a gap between (prev_end → next_start) and the leading +
    # trailing tails. If there's no coverage at all, the whole segment
    # is one gap (rare; means the player never pressed SB).
    gaps: list[tuple[float, float]] = []
    cursor = start_time_s
    for s, e in covered:
        if s > cursor:
            gaps.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < end_clip:
        gaps.append((cursor, end_clip))

    # Sum physical-damage-into-the-tank inside each gap. Block only
    # mitigates physical-school hits, so non-physical damage in an SB
    # gap is not the player's "missing SB" fault.
    out: list[SBGap] = []
    for gap_start, gap_end in gaps:
        gap_duration = gap_end - gap_start
        if gap_duration < min_gap_s:
            continue
        gap_damage = 0.0
        for evt in events_list:
            t = getattr(evt, "time_s", None)
            if t is None or t < gap_start or t >= gap_end:
                continue
            # Only count physical damage — SB blocks physical, so a
            # shadow nuke inside an SB gap isn't an SB-press failure.
            school = getattr(evt, "school", "") or ""
            if school != "physical":
                continue
            amount = getattr(evt, "amount", 0) or 0
            gap_damage += float(amount)
        if gap_damage < min_damage:
            continue
        out.append(
            SBGap(
                start_time_s=gap_start,
                end_time_s=gap_end,
                duration_s=gap_duration,
                damage_during_gap=gap_damage,
                time_phrase=_format_mss(gap_start - start_time_s),
            )
        )
    out.sort(key=lambda g: g.damage_during_gap, reverse=True)
    return out[:top_n]


def infer_tier_from_casts(
    casts: Iterable[CastEvent],
    duration_s: float,
    *,
    start_time_s: float = 0.0,
    sb_duration_s: float | None = None,
    tiers: list[dict] | None = None,
    ds_casts: Iterable[CastEvent] | None = None,
    ds_cooldown_s: float | None = None,
    ladder_sb_uptimes: list[tuple[str, float]] | None = None,
    sb_ceiling_pct: float = 0.0,
    sb_ceiling_rage_starved_pct: float = 0.0,
    sb_ceiling_charge_limited_pct: float = 0.0,
) -> InferredSkillTier:
    """Top-level convenience: cast events → InferredSkillTier.

    `casts` is the Shield Block cast stream (preserved as the first
    positional arg for backward compatibility with v2.10b callers).
    `ds_casts` is the optional Demoralizing Shout cast stream — when
    provided the combined tier is `min(sb_tier, ds_tier)` per the YAML
    ordering; when None the inference falls back to SB-only behaviour
    and `ds_*` fields stay at zero.

    `ladder_sb_uptimes` is an optional list of `(tier_id, sim_uptime)`
    from the skill-ladder run, top-tier-first. When provided, SB tier
    matching uses nearest-neighbor against the simulated uptimes (PR
    #2, 2026-05-21) — bit-exactly talent-aware because each row's
    death rate is what the ladder predicts at that uptime. When None,
    falls back to absolute threshold matching against
    `tiers[*].min_sb_uptime` (v2.10b behaviour).

    `sb_ceiling_pct` + the rage/charge pcts come from the modifier=1.0
    ladder run. They describe the *build floor* — what rage/charge
    gaps cost at perfect play — not the player's actual rage usage.
    `sb_missed_pressable_pct` is derived: `max(0, ceiling - actual)`.
    Defensible because at modifier=1.0 the engine always presses when
    policy permits, so anything beyond the build floor is missed
    pressable presses.
    """
    casts_list = list(casts)
    uptime = compute_sb_uptime_from_casts(
        casts_list,
        duration_s,
        sb_duration_s=sb_duration_s,
        start_time_s=start_time_s,
    )
    resolved_tiers = tiers if tiers is not None else load_skill_tiers()
    if ladder_sb_uptimes:
        sb_tier_id = match_sb_tier_to_ladder(uptime, ladder_sb_uptimes)
    else:
        sb_tier_id = infer_skill_tier(uptime, tiers=resolved_tiers)

    missed_pp = max(0.0, sb_ceiling_pct - uptime) if sb_ceiling_pct > 0 else 0.0

    if ds_casts is None:
        return InferredSkillTier(
            tier_id=sb_tier_id,
            sb_uptime_pct=uptime,
            cast_count=len(casts_list),
            duration_s=duration_s,
            sb_tier_id=sb_tier_id,
            sb_ceiling_pct=sb_ceiling_pct,
            sb_ceiling_rage_starved_pct=sb_ceiling_rage_starved_pct,
            sb_ceiling_charge_limited_pct=sb_ceiling_charge_limited_pct,
            sb_missed_pressable_pct=missed_pp,
        )

    ds_list = list(ds_casts)
    ds_rate = compute_ds_press_rate_from_casts(ds_list, duration_s, cooldown_s=ds_cooldown_s)
    ds_tier_id = infer_skill_tier(ds_rate, tiers=resolved_tiers, threshold_key="min_ds_press_rate")
    combined = _combined_tier(sb_tier_id, ds_tier_id, resolved_tiers)
    return InferredSkillTier(
        tier_id=combined,
        sb_uptime_pct=uptime,
        cast_count=len(casts_list),
        duration_s=duration_s,
        ds_press_rate=ds_rate,
        ds_cast_count=len(ds_list),
        sb_tier_id=sb_tier_id,
        ds_tier_id=ds_tier_id,
        sb_ceiling_pct=sb_ceiling_pct,
        sb_ceiling_rage_starved_pct=sb_ceiling_rage_starved_pct,
        sb_ceiling_charge_limited_pct=sb_ceiling_charge_limited_pct,
        sb_missed_pressable_pct=missed_pp,
    )

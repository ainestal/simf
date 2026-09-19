"""Survivability metrics — TMI / ETMI (Theck-Meloree Index) per the
[Standard Reference Document](https://sacreddutydotnet.wordpress.com/theck-meloree-index-standard-reference-document/).

simf SRD v1 conventions:
- **TMI** uses a **6-second** rolling window over self-mitigated damage taken.
  External healing and absorbs are *excluded* — measures pure self-mitigation.
- **ETMI** uses the same window but *includes* external heals and absorbs.
- For Mythic+ we additionally report **TMI-12 / ETMI-12** (12s window) — modern
  M+ burst patterns last 8–14s, so a wider window is more interpretable.
- Formula: ``TMI = 10⁴ · ln[(N₀/N) · Σᵢ exp(SRD_EXPONENT · MAᵢ)]``
  where MAᵢ is rolling damage normalized to max HP, summed over N bins
  of width ``BIN_DT``, with ``N₀ = 450 / BIN_DT`` (450s baseline fight).

Naming convention: ``"TMI"`` when a number can be a 6s reading; otherwise
``"TMI-9"`` / ``"TMI-12"`` etc. ``"ETMI"`` indicates externals-included.
"""

import math
from dataclasses import dataclass, field

import numpy as np

# SRD v1 constants — DO NOT change without bumping the SRD version.
SRD_DEFAULT_WINDOW_S = 6.0  # TMI / ETMI default
SRD_M_PLUS_WINDOW_S = 12.0  # TMI-12 / ETMI-12 (M+ burst-window-aware)
SRD_EXPONENT = 10.0  # exp coefficient inside the log-sum-exp
SRD_BIN_DT = 0.5  # seconds per histogram bin
SRD_BASELINE_FIGHT_S = 450.0  # N₀ scaling — historical reference fight length


@dataclass
class IterationResult:
    died: bool
    time_to_die_s: float | None
    final_hp_pct: float
    raw_damage_total: float
    dealt_damage_total: float
    healing_total: float
    damage_timeline: list[tuple[float, float]] = field(default_factory=list)
    heal_timeline: list[tuple[float, float]] = field(default_factory=list)
    shield_block_uptime_pct: float = 0.0
    # Lowest HP fraction reached during the iteration (not just at the end —
    # final_hp_pct alone can't tell "recovered from a scary dip" apart from
    # "never dropped"). Recorded post-cheat-death-save, pre-cosmetic-reset;
    # see runner.py's hot loop. Default 1.0 = "never below full" for any
    # legacy construction site that doesn't track it.
    min_hp_pct: float = 1.0
    # SB gap diagnosis: seconds SB was down due to rage shortage vs charge shortage
    sb_rage_starved_s: float = 0.0
    sb_charge_limited_s: float = 0.0

    # Disposition ledger (2026-07-09) — "where your survivability comes
    # from." Per-iteration SUMS of the read-only per-event snapshots
    # `apply_mitigation` takes around its existing `damage *= 1 - x` chain
    # (see `core/mitigation.py`'s protection_warrior branch). All default
    # to 0.0 so every existing `IterationResult(...)` construction site —
    # across the whole codebase and test suite — keeps working unmodified.
    #
    # `disposition_instrumented` is True only when this iteration's spec
    # is Protection Warrior — every other spec's per-spec mitigation
    # module (`classes/*.py`) doesn't populate the avoided/blocked/armor/
    # vers/dr_layers buckets, so those FIVE sums are genuinely 0.0 for them
    # (not measured, just absent) and callers must check this flag before
    # treating a 0.0 bucket as "measured zero mitigation from that layer."
    # NOTE this does NOT extend to `disposition_absorbed_ip`/`_healer` below
    # — every spec's apply_*_mitigation populates those two from its own
    # absorb model, so they carry real values regardless of this flag; only
    # the five chain-reduction buckets above are Protection-Warrior-only.
    disposition_instrumented: bool = False
    # Raw-damage disposition chain. Exact invariant per event (see
    # `apply_mitigation`'s docstring comment and
    # tests/test_disposition_ledger_conservation.py):
    #   raw == avoided_raw + blocked_cut + armor_cut + vers_cut
    #        + dr_layers_cut + absorbed_ip + absorbed_healer + dealt
    # so these 7 sums plus `dealt_damage_total` divide `raw_damage_total`
    # into 8 shares that add to 1.0 (Protection Warrior only).
    disposition_avoided_raw: float = 0.0
    disposition_blocked_cut: float = 0.0
    disposition_armor_cut: float = 0.0
    disposition_vers_cut: float = 0.0
    disposition_dr_layers_cut: float = 0.0
    disposition_absorbed_ip: float = 0.0
    disposition_absorbed_healer: float = 0.0
    # Healed-back split — NOT gated on `disposition_instrumented`: these
    # sums come from `runner.py`'s spec-agnostic healing bookkeeping
    # (`state.self_heal_events` + the warrior-only Fueled by Violence
    # heal vs. the baseline/external/reactive healer streams), not from
    # the per-spec mitigation chain above, so they're real measurements
    # for Protection Warrior and Guardian Druid (both route their self-heal
    # through `state.self_heal_events`, drained in `runner.py`). NOT a
    # complete measurement for Blood DK/Protection Paladin/Vengeance DH —
    # their signature self-sustain (Death Strike, Word of Glory, Soul
    # Cleave) mutates `state.hp` directly rather than going through
    # `apply_self_heal`, so it lands in neither bucket today (a pre-existing
    # gap this instrumentation didn't introduce — HRPS/ETMI already miss
    # the same HP). Not part of the raw-damage conservation identity —
    # total healing over a fight isn't bounded by any single event's raw
    # damage, so these are reported as a separate, secondary split (see
    # `ui/helpers/disposition_ledger.py`), never folded into the same
    # 100%-stacked bar as the chain above.
    disposition_healed_self: float = 0.0
    disposition_healed_external: float = 0.0


@dataclass
class SimResult:
    iterations: int
    deaths: int
    death_rate: float
    death_times: list[float]
    mean_dtps: float
    p50_dtps: float
    p99_dtps: float
    mean_5s_window: float
    p95_5s_window: float
    p99_5s_window: float
    p99_10s_window: float
    p99_15s_window: float
    # SRD-compliant TMI suite. Headline for M+ is etmi_12.
    tmi_6: float
    etmi_6: float
    tmi_12: float
    etmi_12: float
    mean_sb_uptime: float = 0.0
    # SB gap diagnosis (Prot Warrior only; 0.0 for other specs)
    mean_sb_rage_starved_pct: float = 0.0
    mean_sb_charge_limited_pct: float = 0.0
    # A representative iteration's raw timelines for visualization (HP trace,
    # damage histogram). Picked to be the *worst-spike* iteration so the UI
    # surfaces what actually threatens the tank.
    sample_max_hp: float = 0.0
    sample_duration_s: float = 0.0
    sample_damage_timeline: list[tuple[float, float]] = field(default_factory=list)
    sample_heal_timeline: list[tuple[float, float]] = field(default_factory=list)
    # Whether the sampled iteration died, and if so, when (2026-07-28, HP
    # trace chart) — lets a renderer truncate the reconstructed HP curve at
    # the real death point instead of continuing through the engine's
    # cosmetic post-death HP reset (`runner.py` sets `state.hp = 1.0` after
    # death purely to keep window-stat accumulation running; replaying that
    # through subsequent heal/damage events would fabricate a HP recovery
    # the tank never actually had).
    sample_died: bool = False
    sample_time_to_die_s: float | None = None
    # Phase 5.4: int constants version bumped on every mechanical change.
    # Embedded so cached `SimResult` objects auto-expire when the user
    # upgrades simf or the patch refits constants.
    constants_version: int = 0

    # Phase 6.2 — HRPS (Healing Required Per Second). The amount of
    # healing per second the healer must provide to keep the tank alive
    # after self-sustain (Brutal Vitality / Death Strike / Soul Cleave
    # / Tooth-and-Claw heals). Computed as
    #   hrps = (total_damage_dealt - total_self_heal) / duration_s
    # Lower HRPS means easier-to-heal → healer can dedicate more time
    # to DPS → faster keys. No other tank tool surfaces this number.
    mean_hrps: float = 0.0

    # Phase 6.3 — Normalized Tank Score. Composite of death-rate,
    # HRPS, and DTPS, each normalized against a level-appropriate
    # baseline. Range [0.0, 1.0] where 1.0 is best. The tank equivalent
    # of a DPS parse — a single number that compares specs, builds, and
    # gear without requiring users to understand ETMI-12.
    normalized_tank_score: float = 0.0

    # Tail-risk surfacing (2026-07-06 retrospective, Top-5 #2). p5 of
    # per-iteration min_hp_pct — "in your worst 1-in-20 pulls, how low did
    # your HP actually get" (accounts for cumulative damage/healing state,
    # unlike p99_10s_window which is a single burst assumed in isolation).
    # None, not 0.0, is the "unknown" sentinel — a SimResult constructed
    # before this field existed (a stale cache, a hand-built test fixture)
    # must not silently render as "you hit 0% HP." UI code must check for
    # None, not just falsiness.
    p5_min_hp_pct: float | None = None

    # Disposition ledger (2026-07-09) — "where your survivability comes
    # from," aggregated across every iteration. Mean fraction of
    # `mean_dtps`-scale raw damage (i.e. mean bucket sum ÷ mean
    # `raw_damage_total`) each chain bucket accounts for. Protection
    # Warrior only; `disposition_instrumented` tells callers whether the
    # shares below are a real measurement (True) or structurally absent
    # zeros from an un-instrumented spec (False) — see
    # `IterationResult.disposition_instrumented`'s docstring for why.
    # These 7 shares plus `disposition_dealt_share` sum to 1.0 when
    # instrumented (same invariant as the per-event chain, averaged).
    disposition_instrumented: bool = False
    disposition_avoided_share: float = 0.0
    disposition_blocked_share: float = 0.0
    disposition_armor_share: float = 0.0
    disposition_vers_share: float = 0.0
    disposition_dr_layers_share: float = 0.0
    disposition_absorbed_ip_share: float = 0.0
    disposition_absorbed_healer_share: float = 0.0
    disposition_dealt_share: float = 0.0
    # Healed-back split, expressed as a share of `dealt_damage_total`
    # (the damage that actually landed) rather than of raw damage — see
    # `IterationResult.disposition_healed_self`'s docstring for why this
    # is a separate, non-conservation-bound split. Clamped to [0, 1] in
    # `_aggregate` since cumulative effective healing over a full fight
    # is only loosely bounded by cumulative damage dealt (both start from
    # a full-HP tank, but aren't a strict per-event identity), so a
    # measured ratio fractionally over 1.0 is a real possibility this
    # clamp defensively absorbs rather than lets render as ">100% healed."
    disposition_healed_self_share: float = 0.0
    disposition_healed_external_share: float = 0.0

    # Per-iteration results, populated only when
    # run_simulation(keep_iteration_results=True). Lets callers compute
    # paired-bootstrap CIs on metric deltas between common-random-number
    # runs. Heavy (full timelines); callers must drop the reference after
    # extracting what they need — never cache a SimResult carrying these.
    iteration_results: list[IterationResult] | None = None

    @property
    def m_plus_tmi(self) -> float:
        """Headline M+ survivability metric (12s window, externals included)."""
        return self.etmi_12


def death_rate_stderr_pp(p: float, n: int) -> float:
    """Standard error of a fraction estimated from ``n`` Bernoulli trials.

    Returned in percentage points (x100). For p=0.5, n=50 this is 7.07pp —
    so a delta of 1pp at a modest iteration budget is well below the noise
    floor. Originally the CD-plan verdict card's local helper
    (raid-lead 2026-05-17); promoted here so every death_rate-surfacing
    UI panel (CD-plan, key-level verdict) shares the same formula rather
    than each defining its own copy. See Top-5 #3, 2026-07-06
    retrospective — the same "respect the noise floor" treatment the
    gem/enchant gate got.
    """
    if n <= 0:
        return 0.0
    p = max(0.0, min(1.0, p))
    return ((p * (1 - p) / n) ** 0.5) * 100


def compute_window_max(
    timeline: list[tuple[float, float]], window_s: float, total_duration: float, dt: float = 0.5
) -> float:
    """Sliding-window max damage taken over `window_s` seconds.

    Implementation: bin events at width *dt*, then take the max of a rolling
    *window_bins*-wide sum via a cumulative-sum trick — O(events + n_bins)
    instead of the original O(n_starts × events). On a 2000-iter Brutoh run
    this is ~50% of total wall time, so the speedup matters.
    """
    if not timeline:
        return 0.0
    times = np.fromiter((t for t, _ in timeline), dtype=np.float64, count=len(timeline))
    damages = np.fromiter((d for _, d in timeline), dtype=np.float64, count=len(timeline))

    n_starts = int(total_duration / dt) + 1
    window_bins = max(1, round(window_s / dt))
    # Reserve enough bins for the latest window's tail to extend past total_duration,
    # matching the half-open `[t, t + window_s)` semantics of the original loop.
    n_bins = n_starts + window_bins

    bin_idx = (times / dt).astype(np.int64)
    in_range = (bin_idx >= 0) & (bin_idx < n_bins)
    bin_idx = bin_idx[in_range]
    damages = damages[in_range]
    if bin_idx.size == 0:
        return 0.0

    damage_per_bin = np.zeros(n_bins, dtype=np.float64)
    np.add.at(damage_per_bin, bin_idx, damages)

    cs = np.empty(n_bins + 1, dtype=np.float64)
    cs[0] = 0.0
    np.cumsum(damage_per_bin, out=cs[1:])
    rolling = cs[window_bins : window_bins + n_starts] - cs[:n_starts]
    return float(rolling.max())


def tmi_iteration_expsums(
    iterations: list[IterationResult],
    max_hp: float,
    duration_s: float,
    window_s: float = SRD_DEFAULT_WINDOW_S,
    include_externals: bool = False,
    bin_dt: float = SRD_BIN_DT,
) -> tuple[np.ndarray, int]:
    """Per-iteration ``Σ exp(SRD_EXPONENT · MAᵢ)`` terms of the TMI formula.

    TMI is a log-sum-exp over *all* iterations' window bins; the per-iteration
    exp-sums are independent additive terms inside the log, which makes them
    the right unit for paired-bootstrap confidence intervals on a TMI delta:
    resampling iterations only requires re-summing these terms, never
    re-binning timelines.

    Returns ``(expsums, bins_per_iteration)``. ``bins_per_iteration`` is the
    rolling-window count each iteration contributes to N in the SRD formula
    (constant across iterations at fixed duration).
    """
    n_bins = int(duration_s / bin_dt) + 1
    window_bins = max(1, int(window_s / bin_dt))
    bins_per_iter = 1 if window_bins >= n_bins else n_bins + 1 - window_bins

    expsums = np.zeros(len(iterations))
    for idx, it in enumerate(iterations):
        damage_bins = np.zeros(n_bins)
        for t, d in it.damage_timeline:
            i = int(t / bin_dt)
            if 0 <= i < n_bins:
                damage_bins[i] += d

        if include_externals:
            heal_bins = np.zeros(n_bins)
            for t, h in it.heal_timeline:
                i = int(t / bin_dt)
                if 0 <= i < n_bins:
                    heal_bins[i] += h
            net = damage_bins - heal_bins
        else:
            net = damage_bins

        if window_bins >= n_bins:
            rolling = np.array([net.sum()])
        else:
            cumsum = np.cumsum(np.concatenate(([0.0], net)))
            rolling = cumsum[window_bins:] - cumsum[:-window_bins]

        ma = rolling / max_hp
        # Clip extremes to keep exp() finite. exp(50) is huge but representable;
        # exp(100) overflows. ma>5 means damage > 5× max HP in the window, which
        # is already so bad the user's character would have died many times over.
        clipped = np.clip(ma, -10, 5)
        expsums[idx] = float(np.exp(SRD_EXPONENT * clipped).sum())

    return expsums, bins_per_iter


def tmi_from_expsums(
    expsums: np.ndarray,
    bins_per_iter: int,
    bin_dt: float = SRD_BIN_DT,
) -> float:
    """Assemble the SRD TMI from per-iteration exp-sums.

    ``TMI = 10⁴ · ln[(N₀/N) · Σᵢ expsumᵢ]`` with ``N = iterations ×
    bins_per_iter``. Accumulates sequentially so the result is bit-identical
    to the pre-refactor single-pass ``compute_tmi`` loop.
    """
    n = len(expsums) * bins_per_iter
    total_sum = 0.0
    for v in expsums:
        total_sum += float(v)
    if n == 0 or total_sum <= 0:
        return 0.0
    n0 = int(SRD_BASELINE_FIGHT_S / bin_dt)
    return 1e4 * math.log((n0 / n) * total_sum)


def compute_tmi(
    iterations: list[IterationResult],
    max_hp: float,
    duration_s: float,
    window_s: float = SRD_DEFAULT_WINDOW_S,
    include_externals: bool = False,
    bin_dt: float = SRD_BIN_DT,
) -> float:
    """SRD-compliant TMI / ETMI calculation.

    ``include_externals=False`` → TMI (self-mitigation only).
    ``include_externals=True``  → ETMI (heals/absorbs subtract from net damage).
    """
    if not iterations:
        return 0.0
    expsums, bins_per_iter = tmi_iteration_expsums(
        iterations, max_hp, duration_s, window_s, include_externals, bin_dt
    )
    return tmi_from_expsums(expsums, bins_per_iter, bin_dt)


# Backward-compat shim — old code may import this name.
def compute_m_plus_tmi(
    iterations: list[IterationResult],
    max_hp: float,
    duration_s: float,
    window_s: float = SRD_M_PLUS_WINDOW_S,
    bin_dt: float = SRD_BIN_DT,
) -> float:
    """Compute ETMI at the given window. Defaults to M+ window (12s)."""
    return compute_tmi(
        iterations, max_hp, duration_s, window_s=window_s, include_externals=True, bin_dt=bin_dt
    )

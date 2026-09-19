"""Per-run F (base-to-applied damage multiplier) — the coarse, gate-tier
sibling of ``per_hit_mitigation_forensics.py``.

Top-5 #4 (2026-07-06 retrospective): `docs/validation/
phase4_brewmaster_physical_gap_decomposition_2026_07_04.md` found a
universal ~5-7% run-scoped multiplicative wedge between logged
``base_amount`` and what the armor-curve + versatility model predicts —
present even on the CALIBRATED Warrior corpus — plus one outlier run
carrying an extra ~11%. Its own recommendation: measure F per run and
gate corpus admission on F-consistency, never bake F into constants.yaml
(it's a per-run log artifact — server-side tuning — not a character
property).

This module is the CHEAP version of that measurement, meant to run
automatically inside every ``calibrate_spec_from_logs.py`` pass rather
than requiring the full forensics script's CD/PT/debuff-window binning
by hand. It deliberately skips tank-CD-window exclusion and just takes
the MEDIAN residual across every hit in the run — CDs are active a
minority of fight time, and the median is robust to a minority-fraction
contaminating subset. For a root-cause decomposition (not a gate),
``per_hit_mitigation_forensics.py`` remains the CD-window-clean,
attribution-capable deep-dive tool.

    r     = (amount + absorbed + blocked) / base_amount
    expl  = (1 - armor_live/(armor_live+K)) * (1 - vers)   [physical, non-bleed]
          = (1 - vers)                                      [other schools, and physical bleeds]
    F_hit = r / expl

F is relative to the armor+versatility model ONLY — it does not divide out
any spec's own always-on flat DR (Defensive Stance, Thick Hide, etc.), so
raw F magnitudes are NOT comparable across specs with different always-on
chains; only within-corpus (same spec) consistency is meaningful, which is
exactly how the F-consistency gate above uses it.

Returns None (not a fabricated F=1.0) when the log has no per-hit live
armor — ACL-off logs, or the WCL import path, don't carry it. That's the
honest degradation: no F evidence, not neutral F evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from simf.core.bleed_detection import is_bleed
from simf.io.character_from_combatant_info import _resolve_target_guid
from simf.io.combat_log_core import parse_combat_log_line
from simf.io.combat_log_damage import parse_damage_event


@dataclass(frozen=True)
class FMeasurement:
    median_f: float
    n_hits: int
    p25: float
    p75: float


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, round(p * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def measure_run_f(
    log_path: Path,
    tank_name: str,
    *,
    start_time_s: float,
    end_time_s: float | None,
    start_byte_offset: int = 0,
    k: float,
    vers: float = 0.0,
) -> FMeasurement | None:
    """Median per-hit F over one run window, or None if no per-hit armor data."""
    tank_guid = _resolve_target_guid(log_path, tank_name, start_byte_offset=start_byte_offset)
    if tank_guid is None:
        return None

    f_values: list[float] = []
    with log_path.open(encoding="utf-8", errors="replace") as f:
        if start_byte_offset:
            f.seek(start_byte_offset)
        for line in f:
            if tank_name not in line and tank_guid not in line:
                continue
            parsed = parse_combat_log_line(line)
            if not parsed:
                continue
            time_s, event_type, fields = parsed
            if time_s < start_time_s:
                continue
            if end_time_s is not None and time_s > end_time_s:
                break
            if event_type not in (
                "SPELL_DAMAGE",
                "SPELL_PERIODIC_DAMAGE",
                "SWING_DAMAGE_LANDED",
                "RANGE_DAMAGE",
            ):
                continue
            # SWING_DAMAGE_LANDED shares SWING_DAMAGE's field layout; its
            # info-block unit (armor_live source) is the destination, unlike
            # SWING_DAMAGE itself (source) — see combat_log_damage.py and
            # per_hit_mitigation_forensics.py for the same trick.
            spoof = "SWING_DAMAGE" if event_type == "SWING_DAMAGE_LANDED" else event_type
            evt = parse_damage_event(time_s, spoof, fields, tank_name)
            if evt is None or evt.source_name == tank_name:
                continue
            info_start = 8 if spoof.startswith("SWING") else 11
            try:
                info_guid = fields[info_start]
                armor_live = int(fields[info_start + 6])
            except (ValueError, IndexError):
                continue
            if info_guid != tank_guid or armor_live < 0 or evt.base_amount <= 0:
                continue
            r = (evt.amount + evt.absorbed + evt.blocked) / evt.base_amount
            # Bleeds bypass armor in WoW (core/mitigation.py's own rule,
            # `not event.is_bleed` gates the armor-DR step there) — running
            # a bleed tick through the armor curve here would inflate its F
            # by ~1/(1-armor_dr), biasing the median/IQR on any bleed-heavy
            # corpus even though it's currently a no-op on the two corpora
            # this tool has been run against (validator finding, 2026-07-07).
            # is_periodic gates on the ORIGINAL event_type (pre-swing-spoof):
            # a direct, non-periodic hit sharing a bleed's name (e.g. a boss's
            # direct "Searing Rend" swing) is ordinary armor-mitigated
            # physical damage, not a bleed (2026-07-18).
            is_periodic = event_type == "SPELL_PERIODIC_DAMAGE"
            if evt.school == "physical" and not is_bleed(evt.spell_name, is_periodic=is_periodic):
                armor_dr = armor_live / (armor_live + k)
                expl = (1 - armor_dr) * (1 - vers)
            else:
                expl = 1 - vers
            if expl <= 0:
                continue
            f_values.append(r / expl)

    if not f_values:
        return None
    f_values.sort()
    n = len(f_values)
    mid = n // 2
    median = f_values[mid] if n % 2 else (f_values[mid - 1] + f_values[mid]) / 2
    return FMeasurement(
        median_f=median,
        n_hits=n,
        p25=_percentile(f_values, 0.25),
        p75=_percentile(f_values, 0.75),
    )

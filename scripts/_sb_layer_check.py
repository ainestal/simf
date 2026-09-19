"""One-off diagnostic (not part of the shipped tool): pool Shield-Block-active
vs Shield-Block-inactive physical-hit armor+vers-only residuals across the
WHOLE ratified 16-log corpus, to check with real statistical power whether
mitigation.py's `active_mitigation.shield_block.physical_dr: 0.30` layer
shows up as a measurable extra cut in Brutoh's own logs (a 3-run spot check
during tool development gave inconsistent, small-N-noisy signals: -30%-ish on
one run, ~0% on another, +9% (wrong direction) on a third).

Not wired into full_chain_wedge.py's shipped output — this is a targeted
investigation, run once, with its finding reported in the validation doc.
"""

from __future__ import annotations

from pathlib import Path

from simf.cli import load_character
from simf.core.bleed_detection import is_bleed
from simf.core.constants import DATA_DIR, load_constants
from simf.io.calibration_corpus import load_calibration_corpus
from simf.io.character_from_combatant_info import _resolve_target_guid
from simf.io.combat_log import parse_challenge_modes
from simf.io.combat_log_buffs import parse_self_buff_windows
from simf.io.combat_log_core import parse_combat_log_line
from simf.io.combat_log_damage import parse_damage_event

LOG_TARGET = "Brutoh-Uldum-EU"
SW, SB, BSV = 871, 132404, 386397


def _in_win(ws: list[tuple[float, float]], t: float) -> bool:
    return any(a <= t <= b for a, b in ws)


def main() -> None:
    corpus = load_calibration_corpus(DATA_DIR / "calibration_corpora" / "prot_warrior_2026_05.yaml")
    char = load_character(corpus.character_path)
    vers = char.versatility_dr()
    k = load_constants()["armor"]["k_constant"]
    logs_dir = Path(__file__).resolve().parent.parent / "examples"

    sb_pairs: list[tuple[float, float]] = []  # (base_weight, av_resid)
    clean_pairs: list[tuple[float, float]] = []

    for filename, run_index in corpus.replays:
        lf = logs_dir / filename
        if not lf.exists():
            continue
        runs = parse_challenge_modes(lf)
        run = runs[run_index]
        tank_guid = _resolve_target_guid(lf, LOG_TARGET, start_byte_offset=run.start_byte_offset)
        if tank_guid is None:
            continue
        windows = parse_self_buff_windows(
            lf,
            LOG_TARGET,
            frozenset({SW, SB, BSV}),
            start_time_s=run.start_time_s,
            end_time_s=run.end_time_s,
            start_byte_offset=run.start_byte_offset,
        )
        sw_w, sb_w, bsv_w = windows.get(SW, []), windows.get(SB, []), windows.get(BSV, [])

        with lf.open(encoding="utf-8", errors="replace") as f:
            if run.start_byte_offset:
                f.seek(run.start_byte_offset)
            for line in f:
                if LOG_TARGET not in line and tank_guid not in line:
                    continue
                parsed = parse_combat_log_line(line)
                if not parsed:
                    continue
                t, et, fields = parsed
                if t < run.start_time_s:
                    continue
                if run.end_time_s is not None and t > run.end_time_s:
                    break
                if et not in (
                    "SPELL_DAMAGE",
                    "SPELL_PERIODIC_DAMAGE",
                    "SWING_DAMAGE_LANDED",
                    "RANGE_DAMAGE",
                ):
                    continue
                spoof = "SWING_DAMAGE" if et == "SWING_DAMAGE_LANDED" else et
                evt = parse_damage_event(t, spoof, fields, LOG_TARGET)
                if evt is None or evt.source_name == LOG_TARGET or evt.school != "physical":
                    continue
                info_start = 8 if spoof.startswith("SWING") else 11
                try:
                    info_guid = fields[info_start]
                    armor_live = int(fields[info_start + 6])
                except (ValueError, IndexError):
                    continue
                if info_guid != tank_guid or armor_live < 0 or evt.base_amount <= 0:
                    continue
                is_periodic = et == "SPELL_PERIODIC_DAMAGE"
                if is_bleed(evt.spell_name, is_periodic=is_periodic):
                    continue
                if _in_win(sw_w, t) or _in_win(bsv_w, t):
                    continue  # keep SW/BSV out of both buckets
                r = (evt.amount + evt.absorbed + evt.blocked) / evt.base_amount
                av = r / ((1 - armor_live / (armor_live + k)) * (1 - vers))
                if _in_win(sb_w, t):
                    sb_pairs.append((evt.base_amount, av))
                else:
                    clean_pairs.append((evt.base_amount, av))

    def wmean(pairs: list[tuple[float, float]]) -> float:
        wsum = sum(w for w, _ in pairs)
        return sum(w * v for w, v in pairs) / wsum

    print(f"SB-active:   n={len(sb_pairs)}   wmean={wmean(sb_pairs):.4f}")
    print(f"SB-inactive: n={len(clean_pairs)}   wmean={wmean(clean_pairs):.4f}")
    print(f"ratio (SB-active / SB-inactive): {wmean(sb_pairs) / wmean(clean_pairs):.4f}")
    print("(a real -30% SB layer predicts ratio ~0.70; ~1.0 means no measurable extra cut)")


if __name__ == "__main__":
    main()

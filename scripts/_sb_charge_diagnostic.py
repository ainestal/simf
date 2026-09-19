"""One-off: compare modeled SB uptime / charge-limited / rage-starved splits
BEFORE (haste=0, reproduces the pre-fix un-hasted recharge exactly, since
sb["recharge_s"] / (1 + 0) == sb["recharge_s"]) vs AFTER (real haste) the
2026-07-21 hasted-Shield-Block-recharge fix, across the ratified corpus.
Not part of any shipped tool — a diagnostic for the validation doc only.
"""

from __future__ import annotations

from dataclasses import replace as dc_replace
from pathlib import Path

from simf.cli import load_character
from simf.core.constants import DATA_DIR
from simf.core.profiles import load_healing_profile
from simf.core.runner import run_simulation
from simf.io.calibration_corpus import load_calibration_corpus
from simf.io.combat_log import parse_challenge_modes
from simf.io.log_replay import load_replay

CORPUS_MANIFEST = DATA_DIR / "calibration_corpora" / "prot_warrior_2026_05.yaml"
LOG_TARGET = "Brutoh-Uldum-EU"
LOGS_DIR = Path(__file__).resolve().parent.parent / "examples"


def main() -> None:
    corpus = load_calibration_corpus(CORPUS_MANIFEST)
    char = load_character(corpus.character_path)
    heal = load_healing_profile("m+_high_key_healer")

    for haste_label, char_variant in (
        ("AFTER (real haste)", char),
        ("BEFORE (haste=0, ≡ pre-fix un-hasted recharge)", dc_replace(char, haste_rating=0)),
    ):
        uptimes, charge_lim, rage_starv = [], [], []
        for filename, run_index in corpus.replays:
            lf = LOGS_DIR / filename
            if not lf.exists():
                continue
            runs = parse_challenge_modes(lf)
            replay = load_replay(lf, LOG_TARGET, run_index=run_index, runs=runs)
            heal_r = dc_replace(
                heal, baseline_hps_abs=replay.actual_dealt / replay.duration_s * 1.1
            )
            sim = run_simulation(
                char_variant,
                None,
                heal_r,
                iterations=300,
                seed=42,
                events_override=replay.events,
                duration_override=replay.duration_s,
                compute_metrics=False,
            )
            uptimes.append(sim.mean_sb_uptime)
            charge_lim.append(sim.mean_sb_charge_limited_pct)
            rage_starv.append(sim.mean_sb_rage_starved_pct)
        n = len(uptimes)
        print(f"{haste_label}:")
        print(f"  mean_sb_uptime            = {sum(uptimes) / n:.1%}")
        print(f"  mean_sb_charge_limited_pct = {sum(charge_lim) / n:.1%}")
        print(f"  mean_sb_rage_starved_pct   = {sum(rage_starv) / n:.1%}")
        print()


if __name__ == "__main__":
    main()

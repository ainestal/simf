"""One-off, READ-ONLY check: what party compositions (healer/dps class_spec)
appear across the ratified 16-log Prot Warrior corpus?

Motivation: research question 2 (is there a common party/raid buff providing
magic DR shared across the runs that show the "clean wedge" residual). If
the residual's magnitude/presence tracked a specific healer or DPS spec's
presence, that spec's aura kit would be a strong candidate. If the party
comp is heterogeneous across all 16 runs (different healers/dps every time)
while the residual is present in all of them, that argues AGAINST a
party-buff explanation and FOR something universal (base-game mechanic) or
per-tank/self (Warrior-side).

Does not touch any production file. Read-only.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from simf.io.combat_log import detect_party_roles, parse_challenge_modes

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "src/simf/data/calibration_corpora/prot_warrior_2026_05.yaml"
EXAMPLES_DIR = REPO_ROOT / "examples"


def main() -> None:
    manifest = yaml.safe_load(MANIFEST.read_text())

    for replay in manifest["replays"]:
        log_path = EXAMPLES_DIR / replay["file"]
        if not log_path.exists():
            print(f"MISSING: {log_path}")
            continue
        runs = parse_challenge_modes(log_path)
        run_index = replay["run_index"]
        if run_index < 0:
            run_index = len(runs) + run_index
        if not (0 <= run_index < len(runs)):
            continue
        run = runs[run_index]
        members = detect_party_roles(
            log_path, start_time_s=run.start_time_s, end_time_s=run.end_time_s
        )
        summary = ", ".join(
            f"{m.name}:{m.class_spec or '?'}({m.role}/{m.role_source})" for m in members
        )
        print(f"{replay['dungeon']:32s} | {summary}")


if __name__ == "__main__":
    main()

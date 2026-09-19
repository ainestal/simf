"""One-off, READ-ONLY check: what buffs (SPELL_AURA_APPLIED/_REFRESH/_APPLIED_DOSE)
actually land on the tank across the ratified 16-log Prot Warrior corpus?

Motivation: research question 2 (a shared party/raid buff providing magic
DR). Buff-apply lines are logged UNCONDITIONALLY (not gated on Advanced
Combat Logging), unlike COMBATANT_INFO -- so this works even on the 8/16
runs where ACL was off and party class/spec is otherwise unknown. This
gives a direct, comprehensive inventory of every aura Brutoh actually
carries in this corpus, rather than guessing from class presence.

Does not touch any production file. Read-only.
"""

from __future__ import annotations

import csv
import io
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from simf.io.combat_log import parse_challenge_modes

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "src/simf/data/calibration_corpora/prot_warrior_2026_05.yaml"
EXAMPLES_DIR = REPO_ROOT / "examples"

AURA_EVENTS = {
    "SPELL_AURA_APPLIED",
    "SPELL_AURA_REFRESH",
    "SPELL_AURA_APPLIED_DOSE",
}

# Candidate magic-DR-relevant buffs to specifically flag if seen (name substrings,
# case-insensitive). Not exhaustive -- just the ones worth eyeballing.
CANDIDATES = [
    "devotion aura",
    "aura mastery",
    "anti-magic zone",
    "anti-magic shell",
    "power word: barrier",
    "darkness",
    "blessing of protection",
    "blessing of sacrifice",
    "pain suppression",
    "guardian spirit",
    "ironbark",
    "spirit link",
    "life cocoon",
    "blessing of the bronze",
    "chaos brand",
    "mystic touch",
    "resistance",
    "warding",
]


def parse_line_fields(line: str) -> tuple[float, str, list[str]] | None:
    sep = line.find("  ")
    if sep < 0:
        return None
    ts_str = line[:sep].strip()
    rest = line[sep + 2 :].strip()
    if not rest:
        return None
    try:
        dt = datetime.strptime(ts_str, "%m/%d/%Y %H:%M:%S.%f")
    except ValueError:
        return None
    reader = csv.reader(io.StringIO(rest))
    try:
        fields = next(reader)
    except StopIteration:
        return None
    if not fields:
        return None
    return dt.timestamp(), fields[0], fields[1:]


def main() -> None:
    manifest = yaml.safe_load(MANIFEST.read_text())
    target_name = manifest["log_target"]

    global_counter: Counter[str] = Counter()
    per_run_hits: dict[str, list[str]] = {}
    per_run_candidate_names: dict[str, set[str]] = {}

    for replay in manifest["replays"]:
        log_path = EXAMPLES_DIR / replay["file"]
        if not log_path.exists():
            continue
        runs = parse_challenge_modes(log_path)
        run_index = replay["run_index"]
        if run_index < 0:
            run_index = len(runs) + run_index
        if not (0 <= run_index < len(runs)):
            continue
        run = runs[run_index]

        run_counter: Counter[str] = Counter()
        run_candidates: set[str] = set()

        with log_path.open() as f:
            for line in f:
                if target_name not in line:
                    continue
                parsed = parse_line_fields(line)
                if not parsed:
                    continue
                time_s, event_type, fields = parsed
                if time_s < run.start_time_s or time_s > run.end_time_s:
                    continue
                if event_type not in AURA_EVENTS:
                    continue
                # fields: sourceGUID, sourceName, sourceFlags, sourceRaidFlags,
                #         destGUID, destName, destFlags, destRaidFlags,
                #         spellId, spellName, spellSchool, [auraType...]
                if len(fields) < 10:
                    continue
                dest_name = fields[5]
                if dest_name != target_name:
                    continue
                spell_name = fields[9]
                source_name = fields[1]
                key = f"{spell_name} (from {source_name})"
                run_counter[key] += 1
                global_counter[key] += 1
                lname = spell_name.lower()
                for cand in CANDIDATES:
                    if cand in lname:
                        run_candidates.add(key)

        per_run_hits[replay["dungeon"]] = [k for k, _ in run_counter.most_common(15)]
        per_run_candidate_names[replay["dungeon"]] = run_candidates

    print("=" * 100)
    print("Candidate magic-DR-relevant buffs seen landing on the tank, per run")
    print("=" * 100)
    any_candidate_found = False
    for dungeon, cands in per_run_candidate_names.items():
        if cands:
            any_candidate_found = True
            print(f"{dungeon:32s} -> {sorted(cands)}")
    if not any_candidate_found:
        print("(none of the CANDIDATES list matched anything landing on the tank, in ANY run)")

    print()
    print("=" * 100)
    print("Top 25 most common buffs landing on the tank, aggregated across corpus")
    print("=" * 100)
    for name, count in global_counter.most_common(40):
        print(f"{count:6d}  {name}")


if __name__ == "__main__":
    main()

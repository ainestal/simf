from functools import lru_cache
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@lru_cache(maxsize=1)
def load_constants() -> dict:
    with (DATA_DIR / "constants.yaml").open() as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def load_key_level_scaling() -> dict:
    path = DATA_DIR / "key_level_scaling.yaml"
    with path.open() as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def load_dungeon_catalog() -> list[dict]:
    path = DATA_DIR / "dungeons.yaml"
    with path.open() as f:
        data = yaml.safe_load(f)
    return data.get("dungeons", [])


@lru_cache(maxsize=1)
def load_danger_pulls() -> dict[str, list[dict]]:
    """Per-dungeon "danger pull" cheat-sheet entries (Batch G / Season 2
    Readiness bucket A) — see `data/danger_pulls.yaml`'s own header for the
    schema. A dungeon id absent from the file (not just present with an
    empty list) must still resolve to `[]`, never a `KeyError` — every
    Season 2 dungeon is seeded with an empty list today (no real content
    exists yet), but a caller shouldn't have to know which state that is;
    both mean "nothing to show, render the honest placeholder."
    """
    path = DATA_DIR / "danger_pulls.yaml"
    with path.open() as f:
        data = yaml.safe_load(f)
    return data.get("dungeons", {})


@lru_cache(maxsize=1)
def load_skill_tiers() -> list[dict]:
    """Phase 2.10 skill tiers. Returned in display order (top tier first)."""
    return list(load_constants().get("skill_tiers", []))


# Tiered calibration claims (Top-5 #4, 2026-07-06 retrospective). Replaces
# the old `calibrated: true/false` boolean — a spec with real log
# characterization work (Blood DK, Brewmaster, VDH, Prot Paladin all have
# docs/validation/ entries) reads very differently from a spec nobody has
# ever run a real log through, but the boolean collapsed both to "false".
# Promotion criteria (documented here, not enforced by this function — a
# human still ratifies the tier bump alongside a validation doc):
#   placeholder -> characterized: >=2 real logs at canonical K, per-run
#     deltas published in a docs/validation/ doc, dual-validator run.
#   characterized -> calibrated: >=8 F-consistent runs (see
#     scripts/measure_run_f.py), |mean signed delta| <= 5%, >=75% of runs
#     within +/-15%, RMSE <= 0.15, the LOO-CV gate in
#     scripts/calibrate_spec_from_logs.py passes, AND (added 2026-07-25,
#     human-ratified after Prot Warrior's own promotion turned out not to
#     generalize past the one player it was calibrated on — see
#     docs/validation/protwarrior_cross_player_validation_gate_2026_07_25.md)
#     a passing cross-player validation gate: >=5 independent players'
#     WCL fights run through scripts/cross_player_validation.py, with
#     |mean signed delta| <= 8% and >=70% of players within +/-15% (looser
#     than the same-player bar above — one fight per player, no repeated
#     same-player runs to average per-pull noise out of).
CALIBRATION_TIERS = ("placeholder", "characterized", "calibrated")


def spec_is_calibrated(spec_cfg: dict) -> bool:
    """True iff `spec_cfg` (one entry of constants.yaml's `specs:` block)
    is at the top calibration tier. `calibration_tier` unset defaults to
    "placeholder" (never silently reads as calibrated)."""
    return spec_cfg.get("calibration_tier", "placeholder") == "calibrated"


@lru_cache(maxsize=1)
def load_meta_builds() -> dict:
    """Per-spec meta-baseline builds for the two-build diff card.

    Hand-curated data refreshed like trinkets — NOT scraped. Each entry
    points at a `talent_loadouts` key in constants.yaml plus display
    metadata (label, source, updated)."""
    path = DATA_DIR / "meta_builds.yaml"
    with path.open() as f:
        return yaml.safe_load(f) or {}

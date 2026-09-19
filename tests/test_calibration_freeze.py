"""The May-2026 calibration character is frozen; the living one moved on.

2026-06-10: brutoh.yaml was refreshed to the June Armory sheet (post-vault
gear). The K=3430 calibration corpus (16 logs, RMSE 0.068) is May-era and its
baselines were measured against the May stat block — so that block is frozen
in brutoh-calibration-2026-05.yaml and the validation-facing CLI commands
(calibrate-k, replay-log) default to it. The user-facing commands (cd-plan,
compare) follow the living character. These tests pin the whole contract.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CHARS = REPO_ROOT / "src" / "simf" / "data" / "characters"
CLI_SRC = (REPO_ROOT / "src" / "simf" / "cli.py").read_text()

# The exact 2026-05-06 in-game sheet block the K calibration was measured
# against. If this test fails, someone edited the frozen file — don't.
# 2026-06-28 recalibration: haste/crit/vers rescaled from the old self-fit %×100
# units to REAL lvl-90 units (rating_per_pct 44/46/54), PRESERVING the in-game %
# (23.18 / 13.91 / 2.96) so K=3430/RMSE 0.068 stay bit-identical. Mastery stays /100.
MAY_VALUES = {
    "strength": 2182,
    "stamina": 34176,
    "armor_from_gear": 5015,
    "shield_armor": 989,
    "haste_rating": 1020,  # 23.18% × 44 (was 2318 @ self-fit 100)
    "crit_rating": 640,  # 13.91% × 46 (was 1391)
    "mastery_rating": 1608,  # 16.08% × 100 (per-spec mastery conversion — unchanged)
    "versatility_rating": 160,  # 2.96% × 54 (was 296)
    "max_hp_override": 751872,
}


def test_frozen_may_snapshot_is_bit_identical_to_calibration_era():
    d = yaml.safe_load((CHARS / "brutoh-calibration-2026-05.yaml").read_text())
    for key, expected in MAY_VALUES.items():
        assert d[key] == expected, f"frozen {key} changed: {d[key]} != {expected}"
    assert d["race"] == "earthen"
    assert d["class_spec"] == "protection_warrior"
    # Stats-only snapshot: the demo's bundled-simc plumbing stays out of it.
    assert "simc_path" not in d


def test_living_brutoh_yaml_moved_to_the_june_sheet():
    d = yaml.safe_load((CHARS / "brutoh.yaml").read_text())
    # Spot-pin the June Armory sheet so a silent revert to May fails loudly.
    assert d["stamina"] == 37736
    assert d["haste_rating"] == 1060  # 24.09% × 44 (real lvl-90 units; was 2409 @ self-fit 100)
    assert d["max_hp_override"] == 830192
    # The living file still bundles the demo SimC export — now under the
    # packaged data dir (ships in the wheel / public container), not examples/.
    from simf.core.constants import DATA_DIR

    assert (DATA_DIR / d["simc_path"]).exists()


def test_validation_cli_defaults_use_the_frozen_snapshot():
    cal = CLI_SRC.index("def calibrate_k(")
    replay = CLI_SRC.index("def replay_log(")
    for fn_start, name in ((cal, "calibrate_k"), (replay, "replay_log")):
        window = CLI_SRC[fn_start : fn_start + 600]
        assert "brutoh-calibration-2026-05.yaml" in window, (
            f"{name} default no longer points at the frozen May snapshot"
        )


def test_user_facing_cli_defaults_follow_the_living_character():
    for fn in ("def cd_plan(", "def compare("):
        window = CLI_SRC[CLI_SRC.index(fn) : CLI_SRC.index(fn) + 600]
        assert 'characters" / "brutoh.yaml"' in window, (
            f"{fn} should default to the living brutoh.yaml"
        )
        assert "brutoh-calibration" not in window

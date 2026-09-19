"""The demo character's gear must ship INSIDE the package (Phase 5 public deploy).

A wheel-installed / containerized public instance has no repo root or examples/
dir, so the demo's `simc_path` must resolve under the packaged data dir — else
the public "Load sample build" lands an empty paperdoll (stats load from the
YAML, but the equipped/bag/vault items come from the simc file).
"""

from __future__ import annotations

import yaml

from simf.core.constants import DATA_DIR
from simf.io.simc_import import load_simc_file


def _demo_simc_rel() -> str:
    cfg = yaml.safe_load((DATA_DIR / "characters" / "brutoh.yaml").read_text())
    rel = cfg.get("simc_path")
    assert rel, "demo character must declare a simc_path for its gear"
    return rel


def test_demo_simc_resolves_under_packaged_data_dir():
    rel = _demo_simc_rel()
    # Must NOT be an examples/ path — examples/ is .dockerignored / not in the wheel.
    assert not rel.startswith("examples"), (
        f"demo gear must ship in the package, not examples/: {rel}"
    )
    assert (DATA_DIR / rel).exists(), f"demo gear missing from packaged data dir: {DATA_DIR / rel}"


def test_demo_gear_parses_to_populated_equipped_and_vault():
    """The packaged simc parses to a real loadout — the demo's whole point on
    the public URL (gear surface + the Tuesday-vault story)."""
    sim = load_simc_file(DATA_DIR / _demo_simc_rel())
    assert sim.items, "demo equipped gear must parse"
    assert sim.vault_items, "demo vault must parse"

"""Manual recalibration trigger for the community WCL log corpus.

Reads ``src/simf/data/community_corpus.yaml`` (registry of opt-in submitted
Warcraft Logs reports — see that file's header and
``docs/validation/phase6_5_community_log_corpus_decision_2026_07_06.md``),
groups every ``consented: true`` entry by spec, and re-runs
``calibrate_spec_from_wcl.py``'s existing K-sweep/characterization check
against each spec's growing pool of fights.

Deliberately manual, not scheduled — every tier promotion in this project
has been human-ratified with a validation doc (see docs/calibration.md), and
a community corpus is exactly the place to keep that discipline, not relax
it. Run this after adding a new hand-reviewed entry to the registry, read
the output, and write/update a docs/validation/*.md finding yourself before
touching ``calibration_tier`` in constants.yaml.

Reuses ``calibrate_spec_from_wcl.cmd_calibrate`` rather than re-implementing
the same K-sweep/promotion-bar check a second time — loaded dynamically via
the same file-path-based technique ``cli.py``'s ``_load_run_loo_cv`` uses for
its own sibling script, since ``scripts/`` is not an importable package.

Usage::

    python scripts/recalibrate_from_community_corpus.py
    python scripts/recalibrate_from_community_corpus.py --spec vengeance_demon_hunter
    python scripts/recalibrate_from_community_corpus.py --iters 300
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections import defaultdict
from pathlib import Path

import yaml

from simf.core.constants import DATA_DIR

CORPUS_PATH = DATA_DIR / "community_corpus.yaml"


def _load_cmd_calibrate():
    """Dynamically load ``cmd_calibrate`` from ``calibrate_spec_from_wcl.py``
    (a sibling script, not an importable package member) — same technique as
    ``simf.cli._load_run_loo_cv``. Returns ``None`` on any load failure
    rather than raising, so a broken sibling script degrades to a clear
    "couldn't load" message instead of a traceback pointing at the wrong file.
    """
    script_path = Path(__file__).resolve().parent / "calibrate_spec_from_wcl.py"
    if not script_path.exists():
        return None
    mod_name = "_recalibrate_community_corpus_calibrate_spec_from_wcl"
    spec = importlib.util.spec_from_file_location(mod_name, script_path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        del sys.modules[mod_name]
        return None
    return getattr(mod, "cmd_calibrate", None)


def load_consented_fights_by_spec(corpus_path: Path = CORPUS_PATH) -> dict[str, list[tuple]]:
    """Parse the registry into ``{spec: [(code, fight_id, source_id, name), ...]}``,
    the exact tuple shape ``calibrate_spec_from_wcl.cmd_calibrate`` expects.

    Silently skips (with no error — an empty registry is the normal starting
    state) any entry missing ``consented: true``; this is the one safety net
    between a hand-reviewed registry and actually running a fetch against a
    submitted report, on top of the registry itself being append-only and
    hand-reviewed before merge.
    """
    if not corpus_path.exists():
        return {}
    with open(corpus_path) as f:
        data = yaml.safe_load(f) or {}
    by_spec: dict[str, list[tuple]] = defaultdict(list)
    for entry in data.get("submissions") or []:
        if not entry.get("consented"):
            continue
        by_spec[entry["spec"]].append(
            (
                entry["wcl_code"],
                int(entry["fight_id"]),
                entry.get("source_id"),
                entry["target_name"],
            )
        )
    return dict(by_spec)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--spec", help="only recalibrate this class_spec slug (default: every spec in the registry)"
    )
    ap.add_argument("--iters", type=int, default=300)
    ap.add_argument("--ks", default="2000,2500,3000,3500,4000")
    args = ap.parse_args()

    by_spec = load_consented_fights_by_spec()
    if not by_spec:
        print(
            f"No consented submissions in {CORPUS_PATH} yet. "
            "Nothing to recalibrate — the registry starts empty by design."
        )
        return

    cmd_calibrate = _load_cmd_calibrate()
    if cmd_calibrate is None:
        print(
            "Could not load calibrate_spec_from_wcl.cmd_calibrate — is scripts/calibrate_spec_from_wcl.py present?"
        )
        raise SystemExit(1)

    ks = [int(x) for x in args.ks.split(",") if x.strip()]
    specs = [args.spec] if args.spec else sorted(by_spec)
    for spec in specs:
        fights = by_spec.get(spec)
        if not fights:
            print(f"=== {spec}: no consented submissions — skipping ===\n")
            continue
        print(f"=== {spec}: {len(fights)} consented submission(s) from the community corpus ===")
        cmd_calibrate(spec, fights, args.iters, ks)
        print()


if __name__ == "__main__":
    main()

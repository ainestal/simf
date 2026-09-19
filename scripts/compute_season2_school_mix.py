"""Aggregate real Season 2 WCL damage-taken events into a per-dungeon
`school_mix` — the data half of ROADMAP.md's "replay real S2 logs -> fill
school_mix/par_time -> promote" step (the mechanical promotion half is
`scripts/promote_season2_catalog.py`, which refuses to promote a
`season_2_catalog:` entry until `school_mix` + `recommended_profile` are
real).

For one dungeon, pass 2+ `--fight CODE:FIGHT_ID:PLAYER_NAME` entries — ideally
from distinct reports/players for pull-composition diversity, the same
"WCL aggregates" convention `dungeons.yaml`'s own header comment documents for
every existing entry. Sums each event's pre-mitigation `raw_amount` (armor/DR
not yet applied — matches "approximate fraction of pre-mitigation damage by
magic school", `dungeons.yaml`'s own field definition) by `school` across all
given fights and prints the resulting fraction, renormalized to sum to 1.0.

Usage::

    python scripts/compute_season2_school_mix.py \\
        --fight ExampleCode4444444:102:AnonPlayerX5 \\
        --fight ExampleCode5555555:1:AnonPlayerX6 \\
        --fight ExampleCode6666666:5:AnonPlayerX7 \\
        --wcl-cache-dir examples/wcl_cache
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from simf.io.wcl_replay import wcl_to_replay_data


def compute_school_mix(
    fights: list[tuple[str, int, str]], *, cache_dir: Path | None = None
) -> tuple[dict[str, float], list[dict]]:
    """Return (school_fractions, per_fight_detail) for the given fights.

    `fights`: list of (report_code, fight_id, player_name). Each fight is
    fetched independently via `wcl_to_replay_data` (same WCL path
    `cross_player_validation.py` uses), so a bad player name / missing report
    raises the same `ValueError` that function already raises.
    """
    totals: dict[str, float] = defaultdict(float)
    per_fight: list[dict] = []
    for report_code, fight_id, player_name in fights:
        replay = wcl_to_replay_data(report_code, fight_id, player_name, cache_dir=cache_dir)
        fight_totals: dict[str, float] = defaultdict(float)
        counted_events = 0
        for ev in replay.events:
            # Self-inflicted events (Brewmaster Stagger ticks) carry the
            # POST-mitigation HP delta in `raw_amount`, not a pre-mitigation
            # amount (see `wcl_replay.adapt_events`) — a different, smaller
            # basis than every other event, and a delayed echo of damage a
            # normal event already counted once. Mixing it into a
            # pre-mitigation "fraction of damage by school" sum both uses
            # the wrong number and double-counts real incoming damage.
            if ev.is_self_inflicted:
                continue
            fight_totals[ev.school] += ev.raw_amount
            counted_events += 1
        for school, amount in fight_totals.items():
            totals[school] += amount
        per_fight.append(
            {
                "report_code": report_code,
                "fight_id": fight_id,
                "player": player_name,
                "event_count": counted_events,
                "totals": dict(fight_totals),
            }
        )
    grand_total = sum(totals.values())
    fractions = (
        {school: amount / grand_total for school, amount in totals.items()} if grand_total else {}
    )
    return fractions, per_fight


def _parse_fight_arg(raw: str) -> tuple[str, int, str]:
    code, fight_id, player = raw.split(":", 2)
    return code, int(fight_id), player


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--fight",
        action="append",
        required=True,
        dest="fights",
        metavar="CODE:FIGHT_ID:PLAYER_NAME",
        help="repeatable — one entry per WCL fight to aggregate",
    )
    ap.add_argument("--wcl-cache-dir", type=Path, default=None)
    args = ap.parse_args()

    fights = [_parse_fight_arg(f) for f in args.fights]
    fractions, per_fight = compute_school_mix(fights, cache_dir=args.wcl_cache_dir)

    total_events = sum(pf["event_count"] for pf in per_fight)
    print(f"{total_events} events across {len(fights)} fights\n")
    for school, frac in sorted(fractions.items(), key=lambda kv: -kv[1]):
        print(f"  {school:10s}: {frac:.3f}")
    print()
    for pf in per_fight:
        print(
            f"  fight {pf['report_code']}:{pf['fight_id']} ({pf['player']}): "
            f"{pf['event_count']} events"
        )


if __name__ == "__main__":
    main()

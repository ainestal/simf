"""Promote `season_2_catalog:` into the live `dungeons:` key — the mechanical
half of the launch-day step ROADMAP.md's Season 2 Readiness bucket A names:
"replay real S2 logs -> fill school_mix/par_time -> promote each entry into
`dungeons:` (and retire the S1 list to a `season_1_catalog:` archive)".

Pre-staged now (2026-07-26), deliberately INERT until explicitly run with
--apply on launch day: `season_2_catalog:` entries today have only
id/name/origin (no school_mix/recommended_profile), so the completeness gate
below refuses to promote them as-is. The only thing this script does before
real Season 2 data exists is confirm (in --dry-run, the default) exactly
which fields are still missing.

Why this is TEXT surgery, not a yaml.safe_load -> yaml.safe_dump round-trip:
`dungeons.yaml` carries extensive, valuable per-dungeon prose (calibration
notes, WCL citations, gap history) as YAML comments. PyYAML's dumper has no
comment-preservation mode (unlike ruamel.yaml, not a dependency here and not
worth adding for a once-a-season script) -- a full parse+redump would
silently destroy every comment in the file. Instead this locates the
top-level `dungeons:` / `season_2_catalog:` keys by their exact column-0
text span and swaps the two blocks' raw text verbatim, so every existing
comment inside each block survives untouched. `yaml.safe_load` is used only
on an EXTRACTED copy of the `season_2_catalog:` block, purely to validate
completeness -- never on the whole file, and never for the actual rewrite.

Usage::

    python scripts/promote_season2_catalog.py              # dry-run (default)
    python scripts/promote_season2_catalog.py --apply       # write dungeons.yaml
    python scripts/promote_season2_catalog.py --force --apply  # skip the completeness gate (NOT for normal use)
"""

from __future__ import annotations

import argparse
import re
from datetime import date

import yaml

from simf.core.constants import DATA_DIR

DUNGEONS_YAML_PATH = DATA_DIR / "dungeons.yaml"
DAMAGE_PROFILES_DIR = DATA_DIR / "profiles" / "damage"

# A required field is one whose ABSENCE would make a promoted entry
# operationally misleading (e.g. an empty school_mix silently credits no
# school, understating every recommendation for that dungeon) even though
# every consumer's `.get(...)` fallback means it wouldn't crash. map_id/
# abbrev/par_time_ms are deliberately NOT required — they degrade
# gracefully (abbrev falls back to id, par_time_ms is allowed to stay
# `null` per this file's own existing convention for un-verified pars) and
# are often only obtainable once real CHALLENGE_MODE_START/END log events
# exist, which is a slower process than the school_mix/profile read.
REQUIRED_FIELDS = ("school_mix", "recommended_profile")

_TOP_LEVEL_KEY_RE = re.compile(r"^(\w[\w-]*):\s*$", re.MULTILINE)


def _known_damage_profiles() -> set[str]:
    return {p.stem for p in DAMAGE_PROFILES_DIR.glob("*.yaml")}


def _find_block(text: str, key: str) -> tuple[int, int]:
    """Return (start, end) character offsets of the `key:` block — from the
    `key:` line itself up to (not including) the next top-level key or EOF.
    Raises ValueError if `key` isn't a top-level key in `text`."""
    matches = list(_TOP_LEVEL_KEY_RE.finditer(text))
    for i, m in enumerate(matches):
        if m.group(1) == key:
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            return start, end
    raise ValueError(f"top-level key {key!r} not found")


def _entry_gaps(entry: dict, known_profiles: set[str]) -> list[str]:
    gaps = []
    for field in REQUIRED_FIELDS:
        val = entry.get(field)
        if not val:
            gaps.append(f"missing {field}")
    profile = entry.get("recommended_profile")
    if profile and profile not in known_profiles:
        gaps.append(
            f"recommended_profile {profile!r} doesn't match any file in {DAMAGE_PROFILES_DIR}"
        )
    return gaps


def check_completeness(dungeons_yaml_text: str) -> dict[str, list[str]]:
    """Return {dungeon_id: [gap, ...]} for every season_2_catalog entry with
    at least one missing/invalid required field. An empty dict means every
    entry is ready to promote."""
    start, end = _find_block(dungeons_yaml_text, "season_2_catalog")
    block = yaml.safe_load(dungeons_yaml_text[start:end])
    entries = block.get("season_2_catalog") or []
    known_profiles = _known_damage_profiles()
    gaps_by_id: dict[str, list[str]] = {}
    for entry in entries:
        gaps = _entry_gaps(entry, known_profiles)
        if gaps:
            gaps_by_id[entry["id"]] = gaps
    return gaps_by_id


_ARCHIVE_HEADER = """# ─── Season 1 archive — patch 12.0.5, superseded by Season 2 on promotion ───
#
# This is the FULL Season 1 dungeons: block, promoted out of active service
# when season_2_catalog: was promoted into dungeons: (see
# scripts/promote_season2_catalog.py). Kept for historical calibration
# reference (per-dungeon school_mix/gap notes below are still real, measured
# data about THAT season) — not read by load_dungeon_catalog(), which only
# reads the live dungeons: key above.

"""


# The file-level header (everything before the `dungeons:` key) explains
# the schema (map_id/abbrev/school_mix/... — still accurate post-promotion)
# but its title line and closing note name THIS season specifically and
# would read as false the moment a different season's data lands in
# `dungeons:`. Matched by content pattern, not the exact current wording,
# so a header reworded before the real promotion date doesn't silently
# stop matching.
_HEADER_TITLE_RE = re.compile(r"^#.*M\+ dungeon catalog.*$", re.MULTILINE)
_HEADER_CLOSING_NOTE_RE = re.compile(
    r"^# Confirmed dungeons from CHALLENGE_MODE_START.*$", re.MULTILINE
)


def _refresh_header(before_dungeons: str) -> str:
    today = date.today().isoformat()
    before_dungeons = _HEADER_TITLE_RE.sub(
        f"# M+ dungeon catalog — active season (promoted {today} by scripts/promote_season2_catalog.py)",
        before_dungeons,
        count=1,
    )
    before_dungeons = _HEADER_CLOSING_NOTE_RE.sub(
        f"# Promoted from season_2_catalog: on {today}. See season_1_catalog: below for the prior season's data.",
        before_dungeons,
        count=1,
    )
    return before_dungeons


def build_promoted_text(dungeons_yaml_text: str) -> str:
    """Return the new dungeons.yaml text with season_2_catalog: promoted to
    dungeons: and the old dungeons: archived to season_1_catalog: — pure
    text-block rearrangement, every comment in both blocks preserved
    verbatim (except the file-level header's title/closing note, which
    name the outgoing season specifically and would read as false
    otherwise — see `_refresh_header`). Caller must have already confirmed
    completeness."""
    d_start, d_end = _find_block(dungeons_yaml_text, "dungeons")
    s2_start, s2_end = _find_block(dungeons_yaml_text, "season_2_catalog")

    before_dungeons = _refresh_header(dungeons_yaml_text[:d_start])
    # `_find_block`'s "end" is the next top-level key, so the raw
    # `dungeons:` span includes the "season 2 not live yet" prose sitting
    # between the two blocks (nothing else delimits it) — trim trailing
    # comment-only/blank lines back to the true end of the last dungeon
    # entry so that commentary (guaranteed WRONG post-promotion — it says
    # "NOT LIVE") gets dropped entirely rather than archived alongside
    # real season-1 data. The archive header above replaces it with
    # commentary that stays true.
    dungeons_lines = dungeons_yaml_text[d_start:d_end].splitlines(keepends=True)
    real_end = len(dungeons_lines)
    while real_end > 0 and (
        dungeons_lines[real_end - 1].strip() == ""
        or dungeons_lines[real_end - 1].strip().startswith("#")
    ):
        real_end -= 1
    old_dungeons_block = "".join(dungeons_lines[:real_end])
    old_s2_block = dungeons_yaml_text[s2_start:s2_end]
    after_s2 = dungeons_yaml_text[s2_end:]

    new_dungeons_block = old_s2_block.replace("season_2_catalog:", "dungeons:", 1)
    new_archive_block = _ARCHIVE_HEADER + old_dungeons_block.replace(
        "dungeons:", "season_1_catalog:", 1
    )

    return before_dungeons + new_dungeons_block + "\n\n" + new_archive_block + after_s2


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="write the promoted dungeons.yaml (default: dry-run, print only)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="skip the completeness gate — NOT for normal use, every season_2_catalog entry should have real school_mix/recommended_profile from replayed logs before this ever runs for real",
    )
    args = ap.parse_args()

    text = DUNGEONS_YAML_PATH.read_text()
    gaps = check_completeness(text)
    if gaps and not args.force:
        print(f"NOT READY to promote — {len(gaps)} of the season_2_catalog entries are incomplete:")
        for dungeon_id, entry_gaps in gaps.items():
            print(f"  {dungeon_id}: {', '.join(entry_gaps)}")
        print(
            "\nFill these in from replayed Season 2 logs, then re-run. Refusing to promote (pass --force to override, not recommended)."
        )
        raise SystemExit(1)
    if gaps and args.force:
        print(
            f"--force set: promoting anyway despite {len(gaps)} incomplete entries (NOT recommended)."
        )

    new_text = build_promoted_text(text)

    if not args.apply:
        print(
            "DRY RUN (pass --apply to write) — every season_2_catalog entry is complete, ready to promote."
        )
        print(f"Would write {len(new_text):,} bytes to {DUNGEONS_YAML_PATH}")
        return

    DUNGEONS_YAML_PATH.write_text(new_text)
    print(f"Promoted season_2_catalog -> dungeons in {DUNGEONS_YAML_PATH}")


if __name__ == "__main__":
    main()

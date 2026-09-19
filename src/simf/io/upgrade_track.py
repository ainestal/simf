"""Upgrade-track classification for the gear paperdoll badge.

Maps an item's rank ``bonus_id`` to its Midnight upgrade-track position
(track + rank) so the paperdoll can show ``Myth 6/6 · i289`` instead of a
bare ilvl.

HONESTY CONTRACT. The classification is honest-by-construction:

* Only rank ids listed in ``constants.yaml: gear.upgrade_track_ranks`` —
  each VERIFIED against the export corpus — name a track. Anything else
  degrades to ilvl-only.
* We NEVER infer a track from ilvl alone. ``ilvl → (track, rank)`` is
  many-to-one (272 = Hero 5/6 OR Myth 1/6), so guessing would lie.
* Crafted gear uses a different bonus space (no rank id) and degrades to
  ilvl-only; PvP / catch-up / unrecognised ids do the same.

The displayed ilvl always comes from the item's own parsed ``ilvl`` when
present (ground truth from the export), falling back to the table's ilvl
only when the item carries none.
"""

from __future__ import annotations

from dataclasses import dataclass

from simf.core.constants import load_constants


@dataclass(frozen=True)
class TrackBadge:
    """An item's upgrade-track position, as much as is honestly known.

    Any field may be ``None``: a recognised armor rank id fills all four,
    a weapon/trinket ceiling id fills ``track`` + ``ilvl`` (rank doesn't map
    to the 6-rank armor ladder), and an unrecognised / crafted item fills at
    most ``ilvl``.
    """

    track: str | None
    rank: int | None
    max_rank: int | None
    ilvl: int | None

    @property
    def has_track(self) -> bool:
        return bool(self.track)

    def label(self) -> str:
        """Plain-text badge, progressively degrading so it never overclaims:

        * track + rank + ilvl → ``"Myth 6/6 · i289"``
        * track + ilvl        → ``"Myth · i298"``
        * ilvl only           → ``"i289"``
        * nothing             → ``""``
        """
        ilvl_str = f"i{self.ilvl}" if self.ilvl else ""
        if self.track and self.rank and self.max_rank:
            head = f"{self.track} {self.rank}/{self.max_rank}"
        elif self.track:
            head = self.track
        else:
            return ilvl_str
        return f"{head} · {ilvl_str}" if ilvl_str else head


def is_crafted(item: object) -> bool:
    """Crafted gear carries ``crafted_stats`` / ``crafting_quality`` and a
    crafting bonus cluster with NO upgrade-track rank id — classifying it
    would grab a crafting-quality id and mislabel the track."""
    return bool(getattr(item, "crafted_stats", None)) or (
        getattr(item, "crafting_quality", None) is not None
    )


def classify_upgrade_track(item: object, constants: dict | None = None) -> TrackBadge:
    """Classify an ``ItemSpec`` into a :class:`TrackBadge`.

    Returns an ilvl-only badge (track ``None``) for crafted gear, items with
    no recognised rank id, and ``None`` items — never a guessed track.
    """
    ilvl = getattr(item, "ilvl", None)
    if item is None or is_crafted(item):
        return TrackBadge(None, None, None, ilvl)

    consts = constants if constants is not None else load_constants()
    table = (consts.get("gear", {}) or {}).get("upgrade_track_ranks", {}) or {}
    for bid in getattr(item, "bonus_ids", None) or []:
        try:
            entry = table.get(int(bid))
        except (TypeError, ValueError):
            entry = None
        if entry:
            return TrackBadge(
                track=entry.get("track"),
                rank=entry.get("rank"),
                max_rank=entry.get("max_rank"),
                ilvl=ilvl or entry.get("ilvl"),
            )
    return TrackBadge(None, None, None, ilvl)

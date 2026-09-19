"""Survivability coaching — the hit-vs-coverage join.

The validated wedge (ROADMAP "Survivability coaching from the sim-vs-log gap",
2026-06-29): take the tank's top-N biggest damage-taken events and, for each
one, look up which defensive cooldown was active at that timestamp. Hits that
landed with **no** major defensive up are the actionable signal ("pre-press a
cooldown there"). Hits a cooldown *did* cover are credited.

Why this lives apart from the sim: it reads 100% from the combat log (damage
events + buff/debuff aura windows the caller supplies) and touches **no
simulation**. That makes it model-independent AND spec-agnostic — it works the
same on a ``calibrated: false`` spec as on a calibrated one, because nothing
here depends on the engine's mitigation math. The sim-vs-log gap is the
counterfactual no other tool computes, but this coaching surface bills nothing
to the model.

Honesty rules (see ``data/constants.yaml`` ``coaching:`` block for the full
charter):

* **No deleted abilities.** Rage of the Sleeper (spell 200851) was removed in
  patch 12.0.0 and stripped from the engine (PR #218). It is not in the
  registry and must never be. If you find 200851 referenced anywhere in the
  coaching path, it is a bug.
* **Talent-gated on positive in-log evidence.** A registry lever is only
  *considered* when the log shows ≥1 application/cast of it (i.e. the caller
  found ≥1 window for it). A lever the player never used is silently omitted —
  never flagged "unused", because the player may simply not have talented it.
* **Role decides the metric.** ``role: coverage`` levers (reactive/emergency
  DR) are judged by spike coverage — they belong in the join. ``role:
  continuous`` levers (Shield Block, Ironfur) are judged by uptime — they get a
  plain log-derived uptime readout and are EXCLUDED from the "no defensive up"
  determination (a continuous tool not being up for one spike is not a
  failure). No "achievable" benchmark is ever computed: that would re-run the
  sim and bill model error to the player (circular).
* **School scope gates coverage.** A physical-only lever never covers a magic
  hit.
* **Interruptible is evidence-only, never assumed.** A hit is flagged
  interruptible only when its exact spell_id was proven interruptible by a
  real ``SPELL_INTERRUPT`` elsewhere in *this same log* (see
  ``io.combat_log.parse_interrupted_spell_ids``). There is no hand-curated
  "these boss abilities are interruptible" table — that would be exactly the
  kind of external, patch-fragile assumption this module avoids everywhere
  else. A genuinely interruptible cast that nobody ever kicked in the
  available log is silently not flagged, same fail-safe shape as every other
  rule here: under-show, never lie.
* **Kick availability IS safe to hand-curate, unlike boss abilities.** The
  cooldown-aware "was your kick even up" split (``HitCoverage.kick_was_ready``)
  reads each tank spec's baseline interrupt ability from
  ``constants.yaml``'s ``coaching.interrupts`` registry. This is the one
  exception to "no hand-curated tables" in this module: a spec's baseline
  interrupt (Pummel, Rebuke, Mind Freeze, ...) is a long-stable, non-talented
  ability, not a patch-fragile boss mechanic — the same distinction
  ``docs/validation/interruptible_cast_coverage_lever_2026_07_03.md``
  draws when deferring this feature originally. Only computed for hits
  already flagged ``interruptible``; a spec with no registry entry, or a hit
  that isn't interruptible in the first place, leaves it ``None``
  (not-assessable), never a guessed ``False``.

This module is pure: it does no file/network/sim I/O. The caller (the UI layer)
parses buff/debuff windows from the log and hands them in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise

from simf.core.bleed_detection import event_is_periodic

# Schools that are NOT physical. A hit's "family" is `physical` (incl. bleeds,
# which carry school `physical`) or `magic`; an `unknown`-school hit is only
# ever covered by an `all`-scope lever.
_MAGIC_SCHOOLS = frozenset({"holy", "fire", "nature", "frost", "shadow", "arcane"})


def school_family(school: str | None) -> str:
    """Collapse a damage school to the family a defensive scope is keyed on."""
    s = (school or "unknown").lower()
    if s == "physical":
        return "physical"
    if s in _MAGIC_SCHOOLS:
        return "magic"
    return "unknown"


def _scope_covers(scope: str, family: str) -> bool:
    """Does a lever with `school_scope == scope` apply to a hit of `family`?"""
    if scope == "all":
        return True
    return scope == family


@dataclass(frozen=True)
class Lever:
    """One defensive cooldown candidate from the registry."""

    spell_id: int
    name: str
    school_scope: str  # all | physical | magic
    role: str  # coverage | continuous
    detect: str  # buff | debuff_on_source
    duration_s: float | None = None  # safety cap for debuff_on_source windows
    # Base recharge time, coverage levers only. Powers the "was a cooldown even
    # AVAILABLE?" split on uncovered hits — see `_lever_available_at`. Uses the
    # base CD (no charges/haste/talents) so it OVER-states recharge and the split
    # errs toward "kit was spent" (charitable). None → lever sits out the split.
    cooldown_s: float | None = None
    # tier distinguishes a personal "big-button" DR (`major` — Shield Wall,
    # Survival Instincts, Incarnation, …) from a weaker / partial lever
    # (`minor` — Demoralizing Shout, a −20% enemy debuff). A hit covered ONLY
    # by a minor lever is NOT a clean soak: it renders as "partial" and never
    # flips the verdict to "every hit had a major defensive up." This stops the
    # over-credit where a 20% shave reads the same as a 40-50% personal CD.
    tier: str = "major"  # major | minor


@dataclass(frozen=True)
class HitCoverage:
    """A single big hit annotated with which coverage levers were active."""

    time_s: float
    rel_s: float  # seconds since run start
    amount: int
    spell_name: str
    spell_id: int | None
    school: str
    source_name: str
    source_npc_id: int | None
    is_bleed: bool
    # major_by: active + scope-applicable MAJOR coverage levers (a real soak).
    # minor_by: active + scope-applicable MINOR levers only (e.g. Demo Shout's
    #           −20% enemy debuff). Kept separate so a minor-only hit reads as
    #           "partial", never as a clean major-CD soak and never as a stark
    #           "nothing up".
    major_by: tuple[str, ...]
    minor_by: tuple[str, ...] = ()
    # Availability split for UNCOVERED hits (no major up). `cd_assessable` is
    # True when ≥1 in-scope major lever has a known cooldown_s, so we can tell
    # "kit was spent" from "we just don't have the data." `available_major_levers`
    # are the in-scope major levers that were OFF cooldown (pressable) at this
    # hit — empty when the whole kit was on cooldown. See `_lever_available_at`.
    cd_assessable: bool = False
    available_major_levers: tuple[str, ...] = ()
    # Direct cast-evidence cross-check for the availability split above.
    # `_lever_available_at` only ever infers cooldown state from a lever's
    # OWN buff/debuff window starts BEFORE this hit, combined with the
    # registry's `cooldown_s` — it never checks what the player actually did
    # right after the hit. `pressed_late_levers` closes that gap: the
    # in-scope MAJOR coverage levers whose real cast landed within
    # `_LATE_PRESS_WINDOW_S` seconds AFTER this hit. A cast that close behind
    # the hit is direct evidence the lever was NOT buried deep in a long
    # cooldown at hit-time — it falsifies "kit already spent" for this hit
    # and reframes it as a reaction/window-timing miss instead. Empty when no
    # in-scope major lever was cast in that trailing window.
    pressed_late_levers: tuple[str, ...] = ()
    # This exact spell_id was proven interruptible by a real SPELL_INTERRUPT
    # elsewhere in this log (see `parse_interrupted_spell_ids`) — a kick
    # prevents the damage entirely, independent of whether a defensive was
    # also up. Evidence-only: never flagged from external ability knowledge.
    interruptible: bool = False
    # Cooldown-aware split for INTERRUPTIBLE hits only, mirroring
    # had_cd_available/kit_spent but for the tank's OWN interrupt instead of
    # their defensive kit: True = their kick was off cooldown right then (a
    # personally-preventable miss), False = it was on cooldown (not on them
    # specifically), None = not assessable (hit isn't interruptible, or this
    # spec has no registry entry in `coaching.interrupts`).
    kick_was_ready: bool | None = None

    @property
    def covered_by(self) -> tuple[str, ...]:
        """All levers active on this hit (major first), for display."""
        return self.major_by + self.minor_by

    @property
    def covered(self) -> bool:
        """A *clean* soak — a major defensive was up. Minor-only is `partial`."""
        return bool(self.major_by)

    @property
    def partial(self) -> bool:
        return not self.major_by and bool(self.minor_by)

    @property
    def uncovered(self) -> bool:
        """Nothing up at all — neither a major nor a minor lever."""
        return not self.major_by and not self.minor_by

    @property
    def kit_spent(self) -> bool:
        """Uncovered AND every in-scope major lever was on cooldown.

        The honest "this was a pull-size / route problem, not a reaction miss"
        signal: there was no cooldown left to press. Only meaningful when
        `cd_assessable` — without cooldown data we don't make the claim.

        Gated on `not pressed_late_levers`: if the tank actually cast an
        in-scope major within a few seconds AFTER this hit, that real cast is
        stronger evidence than the registry's (possibly wrong) base cooldown
        — it proves the lever wasn't buried in a long cooldown, so this must
        NOT read as "kit already spent."
        """
        return (
            self.uncovered
            and self.cd_assessable
            and not self.available_major_levers
            and not self.pressed_late_levers
        )

    @property
    def had_cd_available(self) -> bool:
        """Uncovered but a major lever WAS off cooldown — a genuine 'press it'."""
        return self.uncovered and bool(self.available_major_levers)

    @property
    def pressed_late(self) -> bool:
        """Uncovered, but a major lever's real cast landed shortly after.

        Direct cast evidence (not a `cooldown_s` inference) that the lever
        was available around the time of the hit — the tank just pressed it
        a beat too late to matter. This is a reaction/window-timing miss,
        the opposite lesson from `kit_spent`'s "nothing to press."
        """
        return self.uncovered and bool(self.pressed_late_levers)

    @property
    def kick_missed(self) -> bool:
        """An interruptible hit where the tank's OWN kick was off cooldown —
        a personally-preventable miss, independent of defensive coverage."""
        return self.interruptible and self.kick_was_ready is True

    @property
    def kick_on_cooldown(self) -> bool:
        """An interruptible hit where the tank's OWN kick was on cooldown —
        not a miss on them specifically; someone else's job that run."""
        return self.interruptible and self.kick_was_ready is False

    @property
    def partial_major_ready(self) -> bool:
        """A *partial* hit (minor lever only) where a MAJOR was off cooldown.

        The signal the `uncovered`-gated split above misses: you soaked the
        spike on a weak −20% debuff while a real major (Shield Wall) sat ready.
        Less severe than a fully-uncovered hit — you had *something* up — but
        still an actionable "press the major, not just the debuff." Kept off the
        `uncovered` count so the existing kit_spent / had_cd_available semantics
        (which are about hits with NOTHING up) stay honest.
        """
        return self.partial and bool(self.available_major_levers)


@dataclass(frozen=True)
class InterruptAbility:
    """A tank spec's baseline interrupt — powers the kick-availability split.

    Deliberately minimal (no school_scope/role/detect/tier like `Lever`):
    every tank spec has exactly one baseline interrupt, it's always a
    self-cast, and it never gates a defensive-coverage school check.
    """

    spell_id: int
    name: str
    cooldown_s: float


def interrupt_ability_for_spec(spec: str | None, constants: dict) -> InterruptAbility | None:
    """Look up a spec's baseline interrupt from the registry, or None.

    None for an unknown spec, a spec with no registry entry, or a malformed
    row — the caller treats None as "not assessable," never a guess.
    """
    coaching = (constants or {}).get("coaching", {}) or {}
    row = (coaching.get("interrupts", {}) or {}).get(spec)
    if not row:
        return None
    try:
        return InterruptAbility(
            spell_id=int(row["spell_id"]),
            name=str(row["name"]),
            cooldown_s=float(row["cooldown_s"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ContinuousUptime:
    """Log-derived uptime for a continuous mitigation tool (no benchmark)."""

    name: str
    spell_id: int
    school_scope: str
    uptime_pct: float  # 0..100, fraction of the run window the buff was up


@dataclass
class CoverageReport:
    """Everything the UI needs to render the defensive-coverage section."""

    spec: str
    top_hits: list[HitCoverage] = field(default_factory=list)
    n_total: int = 0
    n_covered: int = 0  # hits with a MAJOR defensive up (clean soaks)
    coverage_levers_considered: tuple[str, ...] = ()  # all detected (major+minor)
    major_levers_considered: tuple[str, ...] = ()  # detected major-tier only
    minor_levers_considered: tuple[str, ...] = ()  # detected minor-tier only
    continuous: list[ContinuousUptime] = field(default_factory=list)
    headline: str = ""
    detail: str = ""
    has_data: bool = False  # ≥1 coverage lever detected AND enough events
    too_short: bool = False  # run had fewer than min_events damage events
    # Name of this spec's baseline interrupt (e.g. "Pummel"), when the
    # registry has an entry — powers the kick-availability caption. None
    # means "not assessable for this spec," not "no kick was ready."
    interrupt_ability_name: str | None = None
    # Major coverage levers whose OBSERVED minimum gap between two of this
    # run's own casts came in below the registry's `cooldown_s` for that
    # lever — direct evidence the base-cooldown availability model
    # (`_lever_available_at`) is running on a wrong (too-long) recharge
    # assumption for THIS run. Non-empty means "kit already on cooldown"
    # claims for this run should be softened to uncertain, never asserted
    # flatly — see `_write_verdict`.
    cd_model_uncertain_levers: tuple[str, ...] = ()

    @property
    def n_partial(self) -> int:
        return sum(1 for h in self.top_hits if h.partial)

    @property
    def n_uncovered(self) -> int:
        """Hits with NO coverage at all (no major and no minor lever)."""
        return sum(1 for h in self.top_hits if not h.major_by and not h.minor_by)

    @property
    def uncovered_hits(self) -> list[HitCoverage]:
        """Hits the player ate with nothing up — the actionable 'pre-press' set."""
        return [h for h in self.top_hits if not h.major_by and not h.minor_by]

    @property
    def n_uncovered_kit_spent(self) -> int:
        """Uncovered hits where every in-scope major cooldown was already spent."""
        return sum(1 for h in self.top_hits if h.kit_spent)

    @property
    def n_uncovered_had_cd(self) -> int:
        """Uncovered hits where a major cooldown was off CD and could've been pressed."""
        return sum(1 for h in self.top_hits if h.had_cd_available)

    @property
    def n_pressed_late(self) -> int:
        """Uncovered hits where a major's real cast landed shortly after."""
        return sum(1 for h in self.top_hits if h.pressed_late)

    @property
    def n_interruptible(self) -> int:
        """Hits that landed from a cast proven interruptible elsewhere in this log."""
        return sum(1 for h in self.top_hits if h.interruptible)

    @property
    def n_kick_ready(self) -> int:
        """Interruptible hits where the tank's own kick was off cooldown."""
        return sum(1 for h in self.top_hits if h.kick_missed)

    @property
    def n_kick_on_cooldown(self) -> int:
        """Interruptible hits where the tank's own kick was on cooldown."""
        return sum(1 for h in self.top_hits if h.kick_on_cooldown)

    @property
    def n_partial_major_ready(self) -> int:
        """Partial hits (minor-only) where a major cooldown was off CD and unused."""
        return sum(1 for h in self.top_hits if h.partial_major_ready)


def levers_for_spec(spec: str | None, constants: dict) -> list[Lever]:
    """Parse the registry block for a spec into `Lever` objects.

    Returns ``[]`` for an unknown/None spec — the caller renders nothing.
    """
    coaching = (constants or {}).get("coaching", {}) or {}
    by_spec = coaching.get("defensives", {}) or {}
    rows = by_spec.get(spec, []) or []
    out: list[Lever] = []
    for r in rows:
        try:
            out.append(
                Lever(
                    spell_id=int(r["spell_id"]),
                    name=str(r["name"]),
                    school_scope=str(r.get("school_scope", "all")),
                    role=str(r.get("role", "coverage")),
                    detect=str(r.get("detect", "buff")),
                    duration_s=(
                        float(r["duration_s"]) if r.get("duration_s") is not None else None
                    ),
                    cooldown_s=(
                        float(r["cooldown_s"]) if r.get("cooldown_s") is not None else None
                    ),
                    tier=str(r.get("tier", "major")),
                )
            )
        except (KeyError, TypeError, ValueError):
            # A malformed row is skipped rather than crashing the surface.
            continue
    return out


def _active_at(windows: list[tuple[float, float]], t: float) -> bool:
    return any(s <= t <= e for s, e in windows)


# How many seconds AFTER an uncovered top hit a lever's real cast still
# counts as direct falsifying evidence against "the kit was already spent"
# (see `HitCoverage.pressed_late`). A cast landing inside this window could
# not have been buried deep in a long cooldown at hit-time — the honest
# lesson there is late reaction / window timing, not pacing. A plain Python
# constant on purpose, like `top_n`/`min_events` below: this is a coaching
# heuristic threshold, not a simulation/calibration number, so it does not
# belong in `constants.yaml`.
_LATE_PRESS_WINDOW_S: float = 5.0


def _lever_available_at(windows: list[tuple[float, float]], t: float, cooldown_s: float) -> bool:
    """Was a coverage lever OFF cooldown (pressable) at time `t`?

    We treat each detected window's start as a cast that begins the cooldown.
    The lever is available iff its most recent cast before `t` is at least
    `cooldown_s` ago — or it had never been cast before `t` (cooldowns start
    the run ready). Using the base cooldown (no charges/haste/talent cuts)
    over-states recharge, so this under-reports availability: it errs toward
    "on cooldown / kit spent", never wrongly claiming a cooldown was ready.
    """
    last_start = max((s for s, _ in windows if s <= t), default=None)
    if last_start is None:
        return True
    return (t - last_start) >= cooldown_s


def _uptime_pct(windows: list[tuple[float, float]], run_start: float, run_end: float) -> float:
    dur = run_end - run_start
    if dur <= 0:
        return 0.0
    covered = 0.0
    for s, e in windows:
        lo = max(s, run_start)
        hi = min(e, run_end)
        if hi > lo:
            covered += hi - lo
    return max(0.0, min(100.0, covered / dur * 100.0))


def _fmt_mmss(rel_s: float) -> str:
    m, s = divmod(round(rel_s), 60)
    return f"{m}:{s:02d}"


def build_coverage_report(
    spec: str | None,
    damage_events: list,
    *,
    levers: list[Lever],
    buff_windows: dict[int, list[tuple[float, float]]],
    debuff_windows_by_source: dict[int, dict[str, list[tuple[float, float]]]],
    run_start: float,
    run_end: float | None,
    is_bleed_fn,
    top_n: int = 8,
    min_events: int = 20,
    interrupted_spell_ids: frozenset[int] = frozenset(),
    interrupt_ability: InterruptAbility | None = None,
    own_interrupt_cast_times: tuple[float, ...] = (),
) -> CoverageReport:
    """The hit-vs-coverage join. Pure — caller supplies windows.

    `damage_events` are `io.combat_log.DamageTakenEvent`-shaped (need
    ``time_s, amount, spell_name, spell_id, school, source_name,
    source_npc_id, source_guid, tick_flag, event_type``).
    `is_bleed_fn(spell_name, is_periodic) -> bool` is passed in to avoid a
    UI/engine import cycle (matches `core.bleed_detection.is_bleed`'s own
    signature, so callers just pass that function directly).

    `buff_windows` is ``{spell_id: [(start_s, end_s), ...]}`` for self-buff
    levers; `debuff_windows_by_source` is ``{spell_id: {enemy_guid:
    [(start, end), ...]}}`` for ``debuff_on_source`` levers. A lever with no
    windows is treated as "not used / not talented" and silently dropped —
    this is the talent gate.

    `interrupted_spell_ids` (see `io.combat_log.parse_interrupted_spell_ids`)
    are spell IDs proven interruptible by a real SPELL_INTERRUPT elsewhere in
    this log — a hit whose spell_id is in this set was a fully-preventable
    cast, independent of defensive coverage. Defaults to empty so existing
    callers are unaffected.

    `interrupt_ability` (see `interrupt_ability_for_spec`) + `own_interrupt_cast_times`
    (absolute-second timestamps the tank cast that ability, party-filtering
    already done by the caller) together power the cooldown-aware
    `HitCoverage.kick_was_ready` split for hits already flagged
    `interruptible`. Either defaulting to empty/None leaves `kick_was_ready`
    `None` on every hit — existing callers are unaffected.
    """
    report = CoverageReport(spec=spec or "")
    report.interrupt_ability_name = interrupt_ability.name if interrupt_ability else None
    kick_windows = [(t, t) for t in own_interrupt_cast_times]

    # A truncated log may not record an end timestamp; fall back to the last
    # damage event so uptime math + window clamping never divide by None.
    if run_end is None:
        ev_times = [e.time_s for e in damage_events if getattr(e, "time_s", None) is not None]
        run_end = max(ev_times) if ev_times else run_start

    coverage_levers = [lv for lv in levers if lv.role == "coverage"]
    continuous_levers = [lv for lv in levers if lv.role == "continuous"]

    # ── Talent gate: keep only coverage levers with ≥1 detected window ──
    detected: list[Lever] = []
    for lv in coverage_levers:
        if lv.detect == "buff":
            if buff_windows.get(lv.spell_id):
                detected.append(lv)
        elif lv.detect == "debuff_on_source":
            by_src = debuff_windows_by_source.get(lv.spell_id) or {}
            if any(by_src.values()):
                detected.append(lv)
    report.coverage_levers_considered = tuple(lv.name for lv in detected)
    report.major_levers_considered = tuple(lv.name for lv in detected if lv.tier != "minor")
    report.minor_levers_considered = tuple(lv.name for lv in detected if lv.tier == "minor")

    # ── Cross-check the availability model against this run's OWN casts ──
    # `_lever_available_at` (below) infers "still on cooldown" purely from the
    # registry's `cooldown_s` — it has no way to notice when that base value
    # is itself wrong for this build/run. If a major buff lever's own two
    # closest casts in THIS run landed closer together than its registered
    # `cooldown_s`, that is direct proof the registry value over-states this
    # lever's real recharge here — every "kit already on cooldown" claim the
    # split makes off that lever's `cooldown_s` is then unreliable, not just
    # wrong on the specific pair of casts that triggered it.
    cd_model_uncertain: list[str] = []
    for lv in detected:
        if lv.tier == "minor" or lv.detect != "buff" or lv.cooldown_s is None:
            continue
        starts = sorted(s for s, _ in buff_windows.get(lv.spell_id, []))
        if len(starts) < 2:
            continue
        min_gap = min(b - a for a, b in pairwise(starts))
        if min_gap < lv.cooldown_s:
            cd_model_uncertain.append(lv.name)
    report.cd_model_uncertain_levers = tuple(dict.fromkeys(cd_model_uncertain))

    # ── Continuous uptime (log-derived, no benchmark) ──
    for lv in continuous_levers:
        wins = buff_windows.get(lv.spell_id) or []
        if not wins:
            continue  # talent-gated: not used → not shown
        report.continuous.append(
            ContinuousUptime(
                name=lv.name,
                spell_id=lv.spell_id,
                school_scope=lv.school_scope,
                uptime_pct=_uptime_pct(wins, run_start, run_end),
            )
        )

    # ── Pick the biggest hits ──
    hits = [e for e in damage_events if (getattr(e, "amount", 0) or 0) > 0]
    n_events = len(hits)
    hits.sort(key=lambda e: -(e.amount or 0))
    top = hits[:top_n]

    if n_events < min_events:
        report.too_short = True

    for e in top:
        family = school_family(getattr(e, "school", None))
        hit_is_bleed = bool(is_bleed_fn(getattr(e, "spell_name", "") or "", event_is_periodic(e)))
        major_by: list[str] = []
        minor_by: list[str] = []
        # Availability split: among the in-scope MAJOR levers, which were OFF
        # cooldown (pressable) at this hit? Only assessable for levers carrying a
        # cooldown_s. Lets an uncovered hit read "kit was spent" (none available)
        # vs "you had X ready" — instead of always "you should have pressed one".
        avail_major: list[str] = []
        cd_assessable = False
        # Direct cast-evidence cross-check (see `HitCoverage.pressed_late`):
        # in-scope MAJOR levers whose real cast landed shortly AFTER this hit.
        pressed_late: list[str] = []
        for lv in detected:
            if not _scope_covers(lv.school_scope, family):
                continue
            # Bleeds bypass armor (and parry), so an armor/parry-based
            # physical-scope lever (Incarnation, Dancing Rune Weapon) does NOT
            # mitigate a bleed tick — never credit it as coverage. Only an
            # all-school %DR lever (Survival Instincts, Barkskin) covers a bleed.
            if lv.school_scope == "physical" and hit_is_bleed:
                continue
            lv_windows = (
                buff_windows.get(lv.spell_id, [])
                if lv.detect == "buff"
                else (debuff_windows_by_source.get(lv.spell_id) or {}).get(
                    getattr(e, "source_guid", "") or "", []
                )
            )
            active = _active_at(lv_windows, e.time_s)
            if active:
                (minor_by if lv.tier == "minor" else major_by).append(lv.name)
            # A major buff lever with a known cooldown contributes to the split.
            # (Minor levers like Demo Shout aren't "the big button"; debuff
            # levers aren't self-buffs we track recharge for.)
            if lv.tier != "minor" and lv.detect == "buff" and lv.cooldown_s is not None:
                cd_assessable = True
                if not active and _lever_available_at(
                    buff_windows.get(lv.spell_id, []), e.time_s, lv.cooldown_s
                ):
                    avail_major.append(lv.name)
            if (
                lv.tier != "minor"
                and not active
                and any(e.time_s < s <= e.time_s + _LATE_PRESS_WINDOW_S for s, _ in lv_windows)
            ):
                pressed_late.append(lv.name)
        hit_interruptible = getattr(e, "spell_id", None) in interrupted_spell_ids
        kick_was_ready = None
        if hit_interruptible and interrupt_ability is not None:
            kick_was_ready = _lever_available_at(
                kick_windows, e.time_s, interrupt_ability.cooldown_s
            )
        report.top_hits.append(
            HitCoverage(
                time_s=e.time_s,
                rel_s=e.time_s - run_start,
                amount=int(e.amount or 0),
                spell_name=getattr(e, "spell_name", "") or "",
                spell_id=getattr(e, "spell_id", None),
                school=getattr(e, "school", "") or "unknown",
                source_name=getattr(e, "source_name", "") or "",
                source_npc_id=getattr(e, "source_npc_id", None),
                is_bleed=hit_is_bleed,
                major_by=tuple(major_by),
                minor_by=tuple(minor_by),
                cd_assessable=cd_assessable,
                available_major_levers=tuple(avail_major),
                pressed_late_levers=tuple(dict.fromkeys(pressed_late)),
                interruptible=hit_interruptible,
                kick_was_ready=kick_was_ready,
            )
        )

    report.n_total = len(report.top_hits)
    report.n_covered = sum(1 for h in report.top_hits if h.covered)

    # has_data: we can only do the join if we detected ≥1 coverage lever and
    # the run was long enough to be worth coaching on.
    report.has_data = bool(detected) and report.n_total > 0 and not report.too_short

    _write_verdict(report)
    return report


def _hits(n: int) -> str:
    return "hit" if n == 1 else "hits"


def _write_verdict(report: CoverageReport) -> None:
    """Answer-first headline + detail. NO percentage in the headline."""
    n = report.n_total
    if report.too_short:
        report.headline = "Too few hits in this run to coach on coverage."
        report.detail = (
            "This run didn't record enough damage-taken events to rank your "
            "biggest hits meaningfully."
        )
        return
    if not report.coverage_levers_considered:
        # No reactive cooldowns detected. Don't blame the player — they may
        # have correctly tanked the run on continuous mitigation, or their
        # build's cooldowns aren't in our registry. If we DID measure
        # continuous uptime, lead with that credit instead of "weren't pressed".
        if report.continuous:
            cont = _join_names(tuple(c.name for c in report.continuous))
            report.headline = "No emergency cooldowns were needed on your biggest hits."
            report.detail = (
                f"Your continuous mitigation ({cont}) carried this run and no big "
                f"reactive cooldown was pressed — see its uptime below."
            )
        else:
            report.headline = "No major defensive cooldowns detected in this run."
            report.detail = (
                "We read your combat log for the big reactive cooldowns your spec "
                "uses to soak spikes and didn't find any cast. Either they weren't "
                "pressed, or your build's cooldowns aren't in our registry yet."
            )
        return

    major_phrase = _join_names(report.major_levers_considered)
    minor_phrase = _join_names(report.minor_levers_considered)
    uncovered = report.uncovered_hits
    n_major = report.n_covered
    n_partial = report.n_partial

    # A short note crediting the minor-tier (partial) coverage, kept distinct
    # from a clean major-CD soak so a −20% debuff never reads as a full save.
    # When a partial hit had a MAJOR sitting ready, say so — otherwise a benign
    # "partial" masks the actionable "you had Shield Wall ready for that one."
    partial_note = ""
    if n_partial and minor_phrase:
        partial_note = f" {n_partial} more had only {minor_phrase}'s partial reduction up."
        ready_partials = [h for h in report.top_hits if h.partial_major_ready]
        if ready_partials:
            ready_names = _join_names(
                tuple(
                    dict.fromkeys(name for h in ready_partials for name in h.available_major_levers)
                )
            )
            npr = len(ready_partials)
            partial_note += (
                f" {npr} of those had {ready_names} ready — press the major, not just the debuff."
            )

    if not uncovered:
        # Every hit had *something* up. Only claim a clean major soak when no
        # hit was minor-only; otherwise be explicit that some were partial.
        if not n_partial:
            report.headline = f"Every one of your {n} biggest {_hits(n)} had a major defensive up."
            report.detail = f"All {n} covered by {major_phrase}. Clean coverage."
        elif report.n_partial_major_ready:
            # The masking the fix targets: nothing was fully uncovered, so the
            # old verdict read "you had a defensive up for all N" — but on some
            # of those you had a real major ready and soaked the spike on a
            # −20% debuff instead. Lead with that, don't bury it.
            npr = report.n_partial_major_ready
            report.headline = (
                f"You had a major cooldown ready for {npr} of your {n} biggest "
                f"{_hits(npr)} but soaked {'it' if npr == 1 else 'them'} on a partial instead."
            )
            credit = f"{n_major} under a major cooldown ({major_phrase})." if n_major else ""
            report.detail = f"{credit}{partial_note}".strip()
        else:
            report.headline = f"You had a defensive up for all {n} of your biggest {_hits(n)}."
            credit = f"{n_major} under a major cooldown ({major_phrase})." if n_major else ""
            report.detail = f"{credit}{partial_note}".strip()
        return

    # The verdict's timestamp lists read chronologically (the table itself stays
    # damage-sorted) so the player can walk their own timeline.
    def _times(hits: list[HitCoverage]) -> str:
        ts = [_fmt_mmss(h.rel_s) for h in sorted(hits, key=lambda h: h.rel_s)]
        shown = ", ".join(ts[:3])
        return shown + (f", +{len(ts) - 3} more" if len(ts) > 3 else "")

    k = len(uncovered)
    credit = ""
    if n_major:
        credit = f"{n_major}/{n} were covered by {major_phrase}.{partial_note} "
    elif partial_note:
        credit = f"{partial_note.strip()} "

    # Availability split — the honesty fix. A hit you ate with your whole major
    # kit already on cooldown is a pacing/pull problem, NOT a reaction miss, and
    # must not read as "you should have pressed something." Only claimed when we
    # have cooldown data (cd_assessable); otherwise fall back to the plain line.
    #
    # Three mutually exclusive buckets for cd_assessable hits (see
    # `HitCoverage.kit_spent`/`had_cd_available`/`pressed_late` — the
    # properties themselves guarantee this): `had_cd` (a major sat ready and
    # simply wasn't pressed — the cleanest miss), `pressed_late_only` (a major
    # WAS pressed, just a beat after the hit — direct cast evidence against
    # "kit already spent," reframing the lesson as reaction/window timing),
    # and `spent` (no cast evidence either way, and the base-cooldown model
    # says the whole kit was still recharging).
    had_cd = [h for h in uncovered if h.had_cd_available]
    pressed_late_only = [h for h in uncovered if h.pressed_late and not h.had_cd_available]
    spent = [h for h in uncovered if h.kit_spent]

    def _pressed_late_note(hits: list[HitCoverage]) -> str:
        pl_names = _join_names(
            tuple(dict.fromkeys(name for h in hits for name in h.pressed_late_levers))
        )
        return (
            f" {len(hits)} more ({_times(hits)}) landed just before you pressed "
            f"{pl_names} — window timing, not pacing."
        )

    def _spent_note(hits: list[HitCoverage]) -> str:
        return (
            f" The other {len(hits)} ({_times(hits)}) landed with your whole kit "
            f"already on cooldown — that's pacing, not reaction: bank a major for them."
        )

    if had_cd:
        # The genuinely actionable miss: a major was off cooldown and unused.
        lever_names = tuple(
            dict.fromkeys(name for h in had_cd for name in h.available_major_levers)
        )
        ready = _join_names(lever_names)
        # Was: hardcoded singular "was"/"it" regardless of how many lever
        # names got joined — read as "Barkskin and Survival Instincts WAS
        # off cooldown," reproduced live on two independent real logs
        # (round-3 review, 2026-07-05).
        ready_was = "was" if len(lever_names) == 1 else "were"
        ready_it = "it" if len(lever_names) == 1 else "them"
        hc = len(had_cd)
        report.headline = (
            f"You had a major cooldown ready for {hc} of your {n} biggest "
            f"{_hits(hc)} but ate {'it' if hc == 1 else 'them'} anyway."
        )
        remainder_note = ""
        if spent:
            remainder_note += _spent_note(spent)
        if pressed_late_only:
            remainder_note += _pressed_late_note(pressed_late_only)
        report.detail = (
            f"{credit}{ready} {ready_was} off cooldown at {_times(had_cd)} — pre-press "
            f"{ready_it} into the spike.{remainder_note}"
        )
    elif pressed_late_only:
        # The cast evidence directly falsifies "kit already spent": the tank
        # DID press a major, just a beat after the hit had already landed.
        # This is the opposite lesson from `spent` below — react/press
        # earlier, not "there was nothing left to press."
        pl_names = _join_names(
            tuple(dict.fromkeys(name for h in pressed_late_only for name in h.pressed_late_levers))
        )
        pl = len(pressed_late_only)
        report.headline = (
            f"You had {pl_names} available for {pl} of your {n} biggest {_hits(pl)} — "
            f"you pressed {'it' if pl == 1 else 'them'} just after the hit landed."
        )
        remainder_note = _spent_note(spent) if spent else ""
        report.detail = (
            f"{credit}{pl_names} came online within {_LATE_PRESS_WINDOW_S:.0f}s AFTER "
            f"{_times(pressed_late_only)} — that's a reaction/window-timing miss, not a "
            f"lack of cooldown: press earlier into the wind-up next time.{remainder_note}"
        )
    elif spent:
        k_spent = len(spent)
        if report.cd_model_uncertain_levers:
            # Part (b) of the honesty fix: this run's OWN casts recharged
            # faster than the registry `cooldown_s` we're basing "on
            # cooldown" on, so the base-CD availability model is empirically
            # unreliable for this run — don't flatly assert "kit already
            # spent," which is exactly the inverted claim that prompted this.
            uncertain_phrase = _join_names(report.cd_model_uncertain_levers)
            plural = len(report.cd_model_uncertain_levers) != 1
            report.headline = (
                f"{k_spent} of your {n} biggest {_hits(k_spent)} landed while our "
                f"cooldown model says your kit was spent — but that estimate looks "
                f"unreliable this run."
            )
            report.detail = (
                f"{credit}This run's own {uncertain_phrase} cast{'s' if plural else ''} "
                f"recharged faster than the cooldown we assume for {'them' if plural else 'it'}, "
                f"so we can't confidently say your kit was on cooldown at {_times(spent)}. Treat "
                f"the availability estimate as uncertain, not a pacing verdict."
            )
        else:
            # Reframe: the kit was spent, so "press a cooldown" is the wrong lesson.
            report.headline = (
                f"Your major cooldowns were already spent when {k_spent} of your {n} "
                f"biggest {_hits(k_spent)} landed."
            )
            # Deliberately NOT naming the levers here: which majors were in-scope
            # varies per hit, and naming a physical-only CD (Incarnation) next to a
            # magic spike would imply armor could have helped — the exact scope
            # misconception the charter guards against.
            report.detail = (
                f"{credit}They landed at {_times(spent)} with your kit already on cooldown — "
                f"this is pacing/pulls, not reaction. Hold a major for these spikes, or cut the "
                f"incoming damage (interrupt the caster, route, smaller pulls)."
            )
    else:
        # No cooldown data to assess (e.g. a spec without cooldown_s yet).
        report.headline = (
            f"You ate {k} of your {n} biggest {_hits(k)} with no defensive cooldown up."
        )
        report.detail = (
            f"{credit}Uncovered at {_times(uncovered)} — pre-press a cooldown into "
            f"those spikes next time."
        )


def _join_names(names) -> str:
    names = list(names)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"

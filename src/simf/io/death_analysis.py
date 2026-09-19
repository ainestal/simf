"""Death reconstruction: find UNIT_DIED events and the 5-second damage window before each."""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .combat_log import (
    CastEvent,
    ChallengeModeRun,
    DamageTakenEvent,
    DeathRecord,
    EnergizeEvent,
    iter_damage_events,
    iter_death_events,
)

WINDOW_S = 5.0

# Rage starvation attribution: how far either side of a starvation window a
# fatal damage event can sit and still be tagged as "rage-starved." 2 seconds
# captures the GCD-plus-reaction window where the tank physically couldn't
# have pressed a defensive even if they wanted to. Keep this in sync with the
# narrative in `annotate_rage_starvation`'s docstring.
STARVATION_PROXIMITY_S = 2.0

# HP-reconciliation guard: how much of the drop-to-zero is allowed to stay
# unaccounted-for before we stop trusting the "top killer" attribution. The
# advanced-logging block's current_hp/max_hp fields (ACL-on local logs only —
# None on WCL/ACL-off logs, see DamageTakenEvent) let us check whether logged
# damage actually explains the death, instead of just naming whichever logged
# hit happened to be biggest. 25% tolerates ordinary rounding/latency noise
# between the last HP sample and the kill blow while still catching the real
# case this guards against: a fall, void zone, or scripted wipe-reset kill
# that Blizzard never emits a DAMAGE line for at all.
UNEXPLAINED_DEATH_THRESHOLD_FRAC = 0.25


def wowhead_npc_url(npc_id: int | None) -> str | None:
    """Wowhead link for an NPC catalog page, or None when the ID is unknown."""
    if not npc_id:
        return None
    return f"https://www.wowhead.com/npc={npc_id}"


def wowhead_spell_url(spell_id: int | None) -> str | None:
    """Wowhead link for a spell tooltip, or None for auto-attacks / unknown."""
    if not spell_id:
        return None
    return f"https://www.wowhead.com/spell={spell_id}"


@dataclass
class NPCAttribution:
    """One row in a per-death attribution table — a (mob, ability) pair.

    The unit of attribution is the (NPC, spell) tuple rather than the
    individual event, because that's what a player can act on next pull:
    "stop letting Circuit Seer Overcharged-Discharge me." Position data
    is intentionally absent at this layer — the table answers "what hit
    me" not "where was I."
    """

    npc_id: int | None
    npc_name: str
    spell_id: int | None
    spell_name: str
    school: str
    hits: int
    damage_to_hp: int
    share_of_window: float  # 0.0–1.0
    npc_url: str | None
    spell_url: str | None


@dataclass
class LogVerdict:
    """Plain-English summary of why deaths happened in a run.

    headline: one-sentence outcome ("Died twice — both to spike physical damage").
    top_killer: the spell/source/school most responsible for fatal damage.
    suggested_fix: spec-aware recommendation ("Cover the swing window with Shield Block").
    """

    headline: str
    top_killer: str
    suggested_fix: str
    n_deaths: int
    top_share_pct: float  # share of fatal-window damage attributable to top killer


@dataclass
class DeathEvent:
    death: DeathRecord
    preceding: list[DamageTakenEvent]  # damage events in the 5s before death
    total_damage_window: int  # sum of amounts in window
    max_hit: int  # largest single hit in window
    num_hits: int


def reconstruct_deaths(
    log_path: Path,
    target_name: str,
    run: ChallengeModeRun,
) -> list[DeathEvent]:
    """Return a DeathEvent for each UNIT_DIED within the run, with 5s preceding damage context."""
    all_damage = list(iter_damage_events(log_path, target_name, run.start_time_s, run.end_time_s))
    deaths = list(iter_death_events(log_path, target_name, run.start_time_s, run.end_time_s))

    result: list[DeathEvent] = []
    for death in deaths:
        window = [e for e in all_damage if death.time_s - WINDOW_S <= e.time_s <= death.time_s]
        total = sum(e.amount for e in window)
        max_hit = max((e.amount for e in window), default=0)
        result.append(
            DeathEvent(
                death=death,
                preceding=window,
                total_damage_window=total,
                max_hit=max_hit,
                num_hits=len(window),
            )
        )

    return result


# Spec-aware "how do I avoid this next time" suggestions. Generic falls back when
# the active spec doesn't match — keeps the verdict useful for non-Prot-Warrior tanks.
_SUGGESTIONS_BY_SPEC_PHYSICAL = {
    "protection_warrior": "Cover the swing window with Shield Block · top up Ignore Pain before the pack.",
    "protection_paladin": "Hold Shield of the Righteous for the burst · save Ardent Defender for chains.",
    "blood_death_knight": "Death Strike just before the spike — heal yourself out of the deficit.",
    "brewmaster_monk": "Stagger-purify before the next Stagger tick · save Fortifying Brew.",
    "guardian_druid": "Stack Ironfur · pop Survival Instincts for unavoidable burst.",
    "vengeance_demon_hunter": "Demon Spikes up before the swing · Metamorphosis for emergencies.",
}
_SUGGESTIONS_BY_SPEC_MAGIC = {
    "protection_warrior": "Defensive Stance up · Spell Reflect the cast if it's reflectable.",
    "protection_paladin": "Pop Ardent Defender · pre-Word-of-Glory if you're low.",
    "blood_death_knight": "Anti-Magic Shell the cast · Vampiric Blood for emergencies.",
    "brewmaster_monk": "Diffuse Magic the cast · Stagger doesn't help vs spells.",
    "guardian_druid": "Barkskin · Survival Instincts — armor doesn't help here.",
    "vengeance_demon_hunter": "Darkness · don't rely on armor; magic ignores it.",
}


def npc_attribution(death_event: DeathEvent, top_n: int = 5) -> list[NPCAttribution]:
    """Aggregate the 5s death window by (NPC, ability) and rank by damage.

    Returns rows sorted by damage_to_hp descending, capped at top_n. Each
    row aggregates every event in the window where (source_npc_id or
    source_name, spell_id or spell_name) matched — i.e., three auto-attack
    swings from the same Circuit Seer collapse to one row with hits=3.
    """
    if not death_event.preceding:
        return []
    totals: dict[tuple, dict] = {}
    total_window = 0
    for ev in death_event.preceding:
        key = (
            ev.source_npc_id if ev.source_npc_id is not None else ev.source_name,
            ev.spell_id if ev.spell_id is not None else ev.spell_name,
        )
        if key not in totals:
            # "nil" appears as source_name for environment damage and a few
            # boss-spawn-event cases. Surface it as a friendlier label.
            raw_name = ev.source_name or ""
            display_name = "environment / unknown" if raw_name in ("", "nil") else raw_name
            totals[key] = {
                "npc_id": ev.source_npc_id,
                "npc_name": display_name,
                "spell_id": ev.spell_id,
                "spell_name": ev.spell_name or ev.event_type,
                "school": ev.school or "unknown",
                "hits": 0,
                "damage": 0,
            }
        totals[key]["hits"] += 1
        totals[key]["damage"] += ev.amount
        total_window += ev.amount
    if total_window == 0:
        return []
    rows = [
        NPCAttribution(
            npc_id=v["npc_id"],
            npc_name=v["npc_name"],
            spell_id=v["spell_id"],
            spell_name=v["spell_name"],
            school=v["school"],
            hits=v["hits"],
            damage_to_hp=v["damage"],
            share_of_window=v["damage"] / total_window,
            npc_url=wowhead_npc_url(v["npc_id"]),
            spell_url=wowhead_spell_url(v["spell_id"]),
        )
        for v in totals.values()
    ]
    rows.sort(key=lambda r: -r.damage_to_hp)
    return rows[:top_n]


def kill_blow(death_event: DeathEvent) -> DamageTakenEvent | None:
    """Return the single event closest to (and at or before) the death timestamp.

    This is "the last hit" — useful for the verdict sentence even when it
    isn't the biggest damage in the window. Returns None if the preceding
    window is empty.
    """
    if not death_event.preceding:
        return None
    return max(death_event.preceding, key=lambda e: e.time_s)


def cross_log_npc_attribution(
    death_events_by_run: list[tuple[str, list[DeathEvent]]],
    top_n: int = 8,
) -> list[dict]:
    """Aggregate fatal-window damage by NPC across runs.

    Each input tuple is (run_label, death_events). Returns a list of dicts
    sorted by total fatal damage descending — one row per NPC that ever
    appeared in a fatal 5s window, with kill-count, damage total, runs
    seen in, and the top ability that NPC used.
    """
    by_npc: dict[tuple[int | None, str], dict] = {}
    for run_label, des in death_events_by_run:
        for de in des:
            seen_in_this_death: set[tuple[int | None, str]] = set()
            for ev in de.preceding:
                key = (ev.source_npc_id, ev.source_name or "?")
                if key not in by_npc:
                    by_npc[key] = {
                        "npc_id": ev.source_npc_id,
                        "npc_name": ev.source_name or "?",
                        "fatal_kills": 0,
                        "fatal_damage": 0,
                        "runs": set(),
                        "by_ability": Counter(),
                    }
                by_npc[key]["fatal_damage"] += ev.amount
                by_npc[key]["by_ability"][ev.spell_name or ev.event_type] += ev.amount
                by_npc[key]["runs"].add(run_label)
                seen_in_this_death.add(key)
            for key in seen_in_this_death:
                by_npc[key]["fatal_kills"] += 1
    rows = []
    for v in by_npc.values():
        top_ability = v["by_ability"].most_common(1)[0][0] if v["by_ability"] else "?"
        rows.append(
            {
                "npc_id": v["npc_id"],
                "npc_name": v["npc_name"],
                "fatal_kills": v["fatal_kills"],
                "fatal_damage": v["fatal_damage"],
                "runs": len(v["runs"]),
                "top_ability": top_ability,
                "npc_url": wowhead_npc_url(v["npc_id"]),
            }
        )
    rows.sort(key=lambda r: -r["fatal_damage"])
    return rows[:top_n]


def _death_explained_by_logged_damage(
    de: DeathEvent, threshold_frac: float = UNEXPLAINED_DEATH_THRESHOLD_FRAC
) -> bool:
    """True when logged damage plausibly accounts for this death's drop to zero.

    Finds the last event in `de.preceding` that carries a real HP reading
    (`current_hp`/`max_hp` from the advanced-logging block — see
    `DamageTakenEvent`), then checks whether the damage logged strictly
    *after* that reading is big enough to finish the kill. If a large chunk
    of that HP is still unaccounted for, the death wasn't actually caused by
    the damage simf attributed it to — it's a fall, void zone, or
    scripted/unlogged kill (common on deliberate wipe resets), and any
    "top killer" percentage we'd compute from the window is a confidently
    wrong causal claim, not just an imprecise one.

    Returns True (treat as fully explained — the existing behaviour) when
    there's no HP reading to check against at all: most logs (WCL-sourced,
    or ACL-off local logs) never populate this field, and we can't flag
    what we can't measure.
    """
    hp_events = [ev for ev in de.preceding if ev.current_hp is not None and ev.max_hp]
    if not hp_events:
        return True
    last_hp_event = max(hp_events, key=lambda ev: ev.time_s)
    current_hp, max_hp = last_hp_event.current_hp, last_hp_event.max_hp
    if current_hp is None or not max_hp:
        return True  # unreachable given the hp_events filter above, but keeps mypy honest
    later_damage = sum(ev.amount for ev in de.preceding if ev.time_s > last_hp_event.time_s)
    unexplained = current_hp - later_damage
    return unexplained <= threshold_frac * max_hp


def _dominant_death_event(death_events: list[DeathEvent], top_key: tuple[str, str]) -> DeathEvent:
    """Return whichever death contributed the most damage to `top_key`.

    `top_key` is the (spell, source) pair `log_verdict` names as the "top
    killer." With a single death this is trivially that death; with several,
    we need to know which specific death to run the HP-reconciliation guard
    against — the guard is about whether *that* death's fatal window is
    real, not the corpus as a whole.
    """
    if len(death_events) == 1:
        return death_events[0]
    best_de = death_events[0]
    best_amt = -1
    for de in death_events:
        amt = sum(
            ev.amount
            for ev in de.preceding
            if (ev.spell_name or ev.event_type, ev.source_name or "?") == top_key
        )
        if amt > best_amt:
            best_amt = amt
            best_de = de
    return best_de


def _unexplained_death_headline_and_fix(n: int, de: DeathEvent) -> tuple[str, str, str]:
    """Build the demoted (headline, top_killer, suggested_fix) trust-voice triple.

    Four-beat: Number (X% accounted for) → Confidence (simf can't name the
    cause) → Cause (unlogged damage, common on wipe resets) → Action (check
    the pull for a hazard/reset instead of a defensive cooldown).
    """
    max_hp = next((ev.max_hp for ev in de.preceding if ev.max_hp), None)
    covered_pct = (de.total_damage_window / max_hp * 100.0) if max_hp else 0.0
    covered_pct = max(0.0, min(covered_pct, 100.0))

    if n == 1:
        headline = (
            f"Died once, but logged damage only accounts for ~{covered_pct:.0f}% of the "
            "drop to zero — likely a fall, void zone, or unlogged/scripted kill (common on "
            "deliberate wipe resets). simf can't name the cause here."
        )
    else:
        headline = (
            f"Died **{n} times** — one of those deaths isn't explained by logged damage "
            f"(only ~{covered_pct:.0f}% of its drop to zero is accounted for). That death is "
            "likely a fall, void zone, or unlogged/scripted kill (common on deliberate wipe "
            "resets); simf can't confidently name a top killer across all deaths."
        )
    top_killer = "(unresolved — logged damage doesn't reconcile to the fatal HP drop)"
    suggested_fix = (
        "This doesn't look like something a defensive cooldown fixes — check the pull for an "
        "environmental hazard (chasm, void zone) or a deliberate wipe/reset instead."
    )
    return headline, top_killer, suggested_fix


def log_verdict(
    death_events: list[DeathEvent],
    class_spec: str | None = None,
) -> LogVerdict | None:
    """Produce a plain-English verdict from a list of reconstructed deaths.

    Returns None when there are zero deaths (the caller should render a
    "you survived everything" message instead).
    """
    if not death_events:
        return None

    n = len(death_events)

    # Tally fatal-window damage by (spell + source) and by school.
    by_attribution: Counter = Counter()
    by_school: Counter = Counter()
    total_window_damage = 0
    for de in death_events:
        for ev in de.preceding:
            key = (ev.spell_name or ev.event_type, ev.source_name or "?")
            by_attribution[key] += ev.amount
            by_school[ev.school or "unknown"] += ev.amount
            total_window_damage += ev.amount

    if not by_attribution:
        return LogVerdict(
            headline=f"Died {n} time{'s' if n > 1 else ''} — no damage events in the 5-second windows.",
            top_killer="(no damage attribution available)",
            suggested_fix="The log may be missing pre-death events. Try a different log.",
            n_deaths=n,
            top_share_pct=0.0,
        )

    (top_spell, top_source), top_amt = by_attribution.most_common(1)[0]
    top_school = by_school.most_common(1)[0][0] if by_school else "unknown"
    top_share = (top_amt / total_window_damage) if total_window_damage else 0.0

    # HP-reconciliation guard: before naming a top killer, check that the
    # death actually attributable to that attribution is explained by
    # logged damage. An unexplained drop (e.g. a fall/void/scripted kill
    # that never logs a DAMAGE line) means the "top killer" would be a
    # confidently wrong causal claim, not just an imprecise one — demote
    # the verdict instead of naming a killer with a share percentage.
    dominant_de = _dominant_death_event(death_events, (top_spell, top_source))
    if not _death_explained_by_logged_damage(dominant_de):
        headline, top_killer, suggested_fix = _unexplained_death_headline_and_fix(n, dominant_de)
        return LogVerdict(
            headline=headline,
            top_killer=top_killer,
            suggested_fix=suggested_fix,
            n_deaths=n,
            top_share_pct=0.0,
        )

    # Headline: how many deaths, and the dominant pattern.
    if n == 1:
        headline = (
            f"Died once — top killer was **{top_spell}** ({top_school}) from "
            f"**{top_source}**, {top_share * 100:.0f}% of fatal-window damage."
        )
    else:
        headline = (
            f"Died **{n} times** — most fatal damage came from **{top_spell}** "
            f"({top_school}) from **{top_source}**, "
            f"{top_share * 100:.0f}% of fatal-window damage across all deaths."
        )

    # Suggested fix: spec-aware lookup by dominant school.
    is_physical = top_school == "physical"
    suggestion_map = _SUGGESTIONS_BY_SPEC_PHYSICAL if is_physical else _SUGGESTIONS_BY_SPEC_MAGIC
    if class_spec and class_spec in suggestion_map:
        suggested_fix = suggestion_map[class_spec]
    elif is_physical:
        suggested_fix = "Cover the swing window with your spec's physical-mitigation button (Shield Block / SotR / Death Strike / Stagger / Ironfur / Demon Spikes)."
    else:
        suggested_fix = "Pop your spec's magic-defensive (Defensive Stance / AD / AMS / Diffuse Magic / Barkskin / Darkness) before the cast lands."

    top_killer = f"{top_spell} ({top_school}) from {top_source}"
    return LogVerdict(
        headline=headline,
        top_killer=top_killer,
        suggested_fix=suggested_fix,
        n_deaths=n,
        top_share_pct=top_share,
    )


# ─── Rage-flow audit (Brutoh ask 2026-05-24) ──────────────────────────────────
#
# Rage starvation is a death-contributing failure mode that none of the
# damage-side surfaces catches: the tank had no rage banked, so Shield Block
# was unavailable, so the next physical swing went through unmitigated. The
# audit reconstructs a continuous rage timeline from SPELL_ENERGIZE (gains)
# and SPELL_CAST_SUCCESS (spends), flags windows where rage stayed under a
# threshold for a sustained duration, and tags death events that fell inside
# (or very near) those windows.
#
# Limitations — be honest about these in the UI:
#   - Revenge is modelled at fixed cost (constants yaml); the free-with-proc
#     case slightly inflates "spend" → slightly more starvation. Safer
#     direction (over-report rather than under).
#   - Anger Management CDR not modelled. Booming Voice rage gen IS captured
#     empirically because it shows up as SPELL_ENERGIZE in the log.
#   - Unbridled Wrath cap-detection deferred — `over_energize` is parsed and
#     surfaced on the EnergizeEvent for a future pass.

# A simple 2-tuple alias keeps the timeline cheap to pass around. Real
# dataclass-per-sample would be ~10× overhead for a 20-minute run sampled at
# 0.5s = 2400 points × N runs in a session.
RageTimelinePoint = tuple[float, float]  # (time_s, rage)
StarvationWindow = tuple[float, float]  # (start_time_s, end_time_s)


def _load_rage_costs() -> dict[str, float]:
    """Load `rage.costs.*` from constants.yaml. Lazy to avoid a top-of-module
    cycle (constants loader imports yaml; tests need a stable module).
    """
    from ..core.constants import load_constants

    rage = load_constants().get("rage", {}) or {}
    costs = rage.get("costs", {}) or {}
    # Normalise to lowercase keys so we can look up by spell-name slug.
    return {k.lower(): float(v) for k, v in costs.items()}


# Cast-name → rage-cost-key map. Combat logs emit the localised spell name in
# the `spellName` field, but for Brutoh (EU/English) those match the names
# below. If non-English logs become a real use case, swap this for spell-ID
# matching (Shield Block = 2565, Ignore Pain = 190456, Revenge = 6572,
# Thunder Clap = 6343).
_CAST_TO_COST_KEY: dict[str, str] = {
    "Shield Block": "shield_block",
    "Ignore Pain": "ignore_pain",
    "Revenge": "revenge",
    "Revenge!": "revenge",  # proc-spawned variant uses the same name + "!"
    "Thunder Clap": "thunder_clap",
}


def compute_rage_timeline(
    energize_events: list[EnergizeEvent],
    cast_events: list[CastEvent],
    t_start: float,
    t_end: float,
    dt: float = 0.5,
    *,
    initial_rage: float = 0.0,
    rage_cap: float = 100.0,
    cast_name_lookup: dict[int, str] | None = None,
) -> list[RageTimelinePoint]:
    """Reconstruct the actor's rage bar across `[t_start, t_end]` sampled at `dt`.

    Walks both event streams in time order, applying each gain (energize.amount)
    and spend (rage cost from constants) to a running rage value, then emits
    the running value at each `dt`-spaced sample tick. Rage is clamped to
    `[0, rage_cap]` — we don't model overflow, just the bar the player sees.

    `cast_events` is expected to be a single combined list across all the
    cast spell IDs we care about. Each `CastEvent.spell_id` is looked up
    against `_CAST_TO_COST_KEY` indirectly via `cast_name_lookup` (spell_id
    → human-name slug). Pass `cast_name_lookup` when callers parsed the
    casts with a name they want resolved to a cost key; the default falls
    back to "if the spell_id is one of the four hard-coded rage spell IDs."

    Returns: ``[(time_s, rage), …]`` length ``ceil((t_end - t_start) / dt) + 1``.
    """
    costs = _load_rage_costs()

    # Default name lookup: hard-code the four spells we model. Spell IDs from
    # Wowhead — Shield Block 2565, Ignore Pain 190456, Revenge 6572, Thunder
    # Clap 6343. Callers can override for non-English logs or talent variants.
    default_lookup = {
        2565: "Shield Block",
        190456: "Ignore Pain",
        6572: "Revenge",
        6343: "Thunder Clap",
    }
    lookup = cast_name_lookup if cast_name_lookup is not None else default_lookup

    # Merge both event streams into one time-ordered list. We tag each entry
    # with a sign — gain (+amount) or spend (-cost). Unmodelled spells get 0
    # and are dropped early to keep the merge cheap.
    deltas: list[tuple[float, float]] = []
    for ev in energize_events:
        if t_start <= ev.time_s <= t_end:
            deltas.append((ev.time_s, ev.amount))
    for ce in cast_events:
        if not (t_start <= ce.time_s <= t_end):
            continue
        spell_name = lookup.get(ce.spell_id)
        if spell_name is None:
            continue
        cost_key = _CAST_TO_COST_KEY.get(spell_name)
        if cost_key is None:
            continue
        cost = costs.get(cost_key)
        if cost is None or cost <= 0:
            continue
        deltas.append((ce.time_s, -cost))
    deltas.sort(key=lambda d: d[0])

    timeline: list[RageTimelinePoint] = []
    rage = float(initial_rage)
    cursor = 0  # index into deltas
    n_deltas = len(deltas)
    # Use a stable sample tick — `arange`-style but no numpy dependency.
    t = t_start
    while t <= t_end + 1e-9:
        # Apply every delta that has occurred at or before this sample tick.
        while cursor < n_deltas and deltas[cursor][0] <= t:
            rage += deltas[cursor][1]
            if rage < 0.0:
                rage = 0.0
            elif rage > rage_cap:
                rage = rage_cap
            cursor += 1
        timeline.append((t, rage))
        t += dt
    return timeline


def detect_starvation_windows(
    timeline: list[RageTimelinePoint],
    threshold: float = 20.0,
    min_duration: float = 3.0,
) -> list[StarvationWindow]:
    """Find contiguous (time, rage) runs where rage < threshold for ≥ min_duration.

    A run starts at the first sample where rage < threshold and ends at the
    LAST sample (inclusive) before rage recovers. We compare the run's span
    `(end_t - start_t)` against `min_duration` — runs shorter than that are
    dropped as ordinary GCD flow rather than starvation.

    Returns `[(start_t, end_t), …]` in time order.
    """
    windows: list[StarvationWindow] = []
    in_window = False
    win_start: float = 0.0
    last_t: float = 0.0
    for t, rage in timeline:
        if rage < threshold:
            if not in_window:
                in_window = True
                win_start = t
            last_t = t
        else:
            if in_window:
                if (last_t - win_start) >= min_duration:
                    windows.append((win_start, last_t))
                in_window = False
    # Close any window still open at the tail of the timeline.
    if in_window and (last_t - win_start) >= min_duration:
        windows.append((win_start, last_t))
    return windows


def annotate_rage_starvation(
    death_events: list[DeathEvent],
    windows: list[StarvationWindow],
    proximity_s: float = STARVATION_PROXIMITY_S,
) -> dict[int, set[int]]:
    """Tag damage events inside (or within `proximity_s` of) a starvation window.

    Per the audit's design note above, we don't mutate `DamageTakenEvent` —
    instead we return a sparse map `{death_index: {preceding_event_index, …}}`
    that the UI can join against to paint red markers. Death indices are the
    positions in the input `death_events` list; event indices are positions
    in each death's `.preceding` list.

    Bounds: an event at `t_event` is "near" a window `(start, end)` if
    `start - proximity_s <= t_event <= end + proximity_s`. Default proximity
    = 2s captures the GCD-plus-reaction window — a hit that lands 2s after
    rage recovers is no longer "rage-starved." A hit 5s after recovery is
    NOT tagged (the player had time to bank rage and didn't).
    """
    out: dict[int, set[int]] = {}
    if not windows:
        return out
    for di, de in enumerate(death_events):
        for ei, ev in enumerate(de.preceding):
            for w_start, w_end in windows:
                if (w_start - proximity_s) <= ev.time_s <= (w_end + proximity_s):
                    out.setdefault(di, set()).add(ei)
                    break
    return out

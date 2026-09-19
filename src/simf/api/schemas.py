"""Pydantic v2 request/response schemas for the FastAPI surface.

Phase 5.1 of the ROADMAP. These shapes are derived from the existing
`Character` / `SimResult` dataclasses but use Pydantic for HTTP-side
validation. The mapping is intentionally lossless — every field on the
internal dataclass appears on the external schema and vice versa.

Distinct from the internal dataclasses because:
- Pydantic gives JSON-Schema export + automatic OpenAPI docs.
- API consumers may add validation rules (max iter, stat caps) that
  the engine itself shouldn't enforce.
- Versioning the API schema independently of the engine lets us evolve
  the wire format without churning the engine.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CharacterRequest(BaseModel):
    """HTTP shape of `core.character.Character`. Stat fields default to
    zero so a minimal request (name + race + class_spec + talents) is
    enough to instantiate a stub character."""

    name: str = Field(..., description="Character name (display only).")
    race: str = Field(..., description="Race slug, e.g. `earthen`, `tauren`.")
    class_spec: str = Field(
        ...,
        description=(
            "One of: protection_warrior, protection_paladin, "
            "blood_death_knight, vengeance_demon_hunter, brewmaster_monk, "
            "guardian_druid."
        ),
    )
    talents: str = Field(..., description="Talent loadout name from constants.yaml.")

    strength: int = 0
    stamina: int = 0
    armor_from_gear: int = 0

    haste_rating: int = 0
    crit_rating: int = 0
    mastery_rating: int = 0
    versatility_rating: int = 0
    parry_rating: int = 0

    max_hp_override: int | None = None

    def to_dict(self) -> dict:
        return self.model_dump(exclude_none=True)


class SimulateRequest(BaseModel):
    """`POST /simulate` request body. Damage + healing profile names are
    looked up in `data/profiles/`; iterations and seed are passed
    through to `run_simulation`."""

    character: CharacterRequest
    damage_profile: str = Field(
        "m+_pull_caster",
        description="Profile slug from `data/profiles/damage/`.",
    )
    healing_profile: str = Field(
        "m+_high_key_healer",
        description="Profile slug from `data/profiles/healing/`.",
    )
    iterations: int = Field(1000, ge=10, le=10000)
    seed: int = Field(42)


class SimResultResponse(BaseModel):
    """Mirrors `core.metrics.SimResult`. JSON-safe (no numpy / dataclass
    leaks). Timelines are returned as lists of `(t, amount)` tuples."""

    iterations: int
    deaths: int
    death_rate: float
    death_times: list[float]

    mean_dtps: float
    p50_dtps: float
    p99_dtps: float
    mean_5s_window: float
    p95_5s_window: float
    p99_5s_window: float
    p99_10s_window: float
    p99_15s_window: float

    tmi_6: float
    etmi_6: float
    tmi_12: float
    etmi_12: float
    mean_sb_uptime: float

    # Phase 3 SB-gap diagnosis (Prot Warrior only; 0.0 for other specs).
    # Surfaced over HTTP so API consumers can render the same "X% of
    # gaps were rage-starved vs Y% charge-limited" breakdown the
    # Streamlit UI does.
    mean_sb_rage_starved_pct: float = 0.0
    mean_sb_charge_limited_pct: float = 0.0

    # Phase 6.2 — HRPS (Healing Required Per Second). Net healing the
    # healer must supply after the tank's self-sustain. Lower is better.
    mean_hrps: float = 0.0
    # Phase 6.3 — Normalized Tank Score [0..1]. Composite of
    # death-rate / HRPS / DTPS against per-key-level baselines.
    normalized_tank_score: float = 0.0

    constants_version: int

    @classmethod
    def from_sim_result(cls, result) -> SimResultResponse:
        """Build a response from the internal `SimResult` dataclass.

        Skipped fields: sample_damage_timeline / sample_heal_timeline.
        Those are large and intended for in-process visualization; a
        future `/simulate/timeline` endpoint can opt-in to including
        them.
        """
        return cls(
            iterations=result.iterations,
            deaths=result.deaths,
            death_rate=result.death_rate,
            death_times=list(result.death_times),
            mean_dtps=result.mean_dtps,
            p50_dtps=result.p50_dtps,
            p99_dtps=result.p99_dtps,
            mean_5s_window=result.mean_5s_window,
            p95_5s_window=result.p95_5s_window,
            p99_5s_window=result.p99_5s_window,
            p99_10s_window=result.p99_10s_window,
            p99_15s_window=result.p99_15s_window,
            tmi_6=result.tmi_6,
            etmi_6=result.etmi_6,
            tmi_12=result.tmi_12,
            etmi_12=result.etmi_12,
            mean_sb_uptime=result.mean_sb_uptime,
            mean_sb_rage_starved_pct=getattr(result, "mean_sb_rage_starved_pct", 0.0),
            mean_sb_charge_limited_pct=getattr(result, "mean_sb_charge_limited_pct", 0.0),
            mean_hrps=getattr(result, "mean_hrps", 0.0),
            normalized_tank_score=getattr(result, "normalized_tank_score", 0.0),
            constants_version=getattr(result, "constants_version", 0),
        )


class KeyLevelVerdictRequest(BaseModel):
    """`POST /verdict/key-level` body — runs the Phase 2.1 sweep."""

    character: CharacterRequest
    iterations: int = Field(200, ge=10, le=2000)
    seed: int = 42
    affix: str = Field("fortified", description="`fortified` or `tyrannical`")


class KeyLevelPointResponse(BaseModel):
    key_level: int
    damage_multiplier: float
    death_rate: float
    mean_dtps: float
    p99_5s_window: float
    band: str


class KeyLevelVerdictResponse(BaseModel):
    headline: str
    detail: str
    points: list[KeyLevelPointResponse]
    comfortable_max: int | None
    prog_ceiling: int | None
    affix: str

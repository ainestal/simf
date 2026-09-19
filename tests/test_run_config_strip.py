"""Tests for the v0.9 always-visible run-config strip.

Pure string builder — Streamlit just renders the output. Hard requirement
from elite-tank persona: this MUST be visible by default, not behind a
toggle. The text must be Discord-pasteable so a reader can verify the build.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from simf.ui.helpers.run_config import (
    RunConfig,
    compute_reproduction_hash,
    format_build_footer,
    format_log_surface_no_character_summary,
    format_run_config,
    format_run_config_details,
)


def _stub_char(**overrides):
    """Minimal stand-in for Character — compute_reproduction_hash only
    reads named attributes (duck-typed), so a full Character construction
    (many required fields, irrelevant here) isn't needed."""
    base = dict(
        class_spec="protection_warrior",
        race="human",
        talents="archon-meta",
        stamina=25000,
        armor_from_gear=100000,
        strength=3000,
        agility=0,
        haste_rating=2000,
        crit_rating=1000,
        mastery_rating=500,
        versatility_rating=1500,
        parry_rating=800,
        shield_armor=12000,
        max_hp_override=None,
        stamina_in_caster_form=False,
        active_buff_spell_ids=frozenset({202770, 12975}),
        detected_talent_spell_ids=frozenset({31850}),
        decoded_talents=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def baseline_config():
    return RunConfig(
        k_active=2700,
        global_rmse=0.079,
        log_count=6,
        worst_residual_dungeon="Algeth'ar",
        worst_residual_pct=-0.125,
        iterations=1000,
        seed=42,
        constants_version="v0.7",
        simf_sha="abc1234",
        item_db_date="2026-05-12",
        policy_version="v3",
        dungeons=["WR+18", "NW+18"],
        healer_profile="standard",
        talent_hash="def5678",
    )


def test_format_contains_all_required_fields(baseline_config):
    """Elite-tank required fields: K, RMSE (with worst residual), iter, seed,
    constants ver, simf SHA, item-DB date, policy ver, dungeon set, healer,
    talent hash. Every field appears in the output."""
    s = format_run_config(baseline_config)
    assert "K=2700" in s
    assert "0.079" in s and "6 logs" in s
    assert "Algeth'ar" in s and "-12.5%" in s
    assert "iter=1000" in s or "iter=1,000" in s
    assert "seed=42" in s
    assert "v0.7" in s  # constants version
    assert "abc1234" in s  # simf sha
    assert "2026-05-12" in s  # item-db date
    assert "v3" in s  # policy version
    assert "WR+18" in s and "NW+18" in s
    assert "standard" in s  # healer profile
    assert "def5678" in s  # talent hash


def test_format_is_single_line(baseline_config):
    """Trust banner must fit above the page — no newlines."""
    s = format_run_config(baseline_config)
    assert "\n" not in s


def test_format_handles_missing_optional_fields():
    """A bare config with no optional metadata should still produce a
    legible string — never None, never KeyError."""
    s = format_run_config(
        RunConfig(
            k_active=2700,
            global_rmse=None,
            log_count=0,
            worst_residual_dungeon=None,
            worst_residual_pct=None,
            iterations=1000,
            seed=42,
            constants_version="v0.7",
            simf_sha="unknown",
            item_db_date="unknown",
            policy_version="v1",
            dungeons=[],
            healer_profile="default",
            talent_hash="",
        )
    )
    assert "K=2700" in s
    assert "iter=1000" in s or "iter=1,000" in s


def test_format_separator_is_dot(baseline_config):
    """Per the plan mockup: fields separated by ' · ' for readability."""
    s = format_run_config(baseline_config)
    assert " · " in s


_TIER_BADGE = '<span class="tier-badge"><strong>4pc</strong> Night Ender\'s Vesture</span>'


def test_strip_body_separates_summary_and_tier_badge_with_a_space_not_a_middot():
    """F-003 (review round R1, 2026-07-17, ui-craft-critic): the model-error
    trust statement and the gear tier-set badge are DIFFERENT categories, but
    a ' · ' middot welded them into one CSV-style statement ("±6.8% model
    error · 4pc Night Ender's Vesture"). A plain space unwelds them (the
    badge's own 12px margin + bordered pill carry the visual separation)
    while preserving the copy-paste / screen-reader text boundary the
    2026-07-05 copy audit added (without it, extracted text ran together as
    "…error4pc…")."""
    from simf.ui.load import _compose_run_config_strip_html

    body = _compose_run_config_strip_html("±6.8% model error", _TIER_BADGE)
    # No middot welding the two unrelated statements (fails on the old code).
    assert " · " not in body
    # Exact shape: summary, one real space, then the badge — the space is the
    # copy-paste boundary, not decoration.
    assert body == f"±6.8% model error {_TIER_BADGE}"


def test_strip_body_has_no_trailing_separator_when_no_tier_badge():
    """No badge (no active tier set) → the summary stands alone, with no
    dangling separator character of any kind."""
    from simf.ui.load import _compose_run_config_strip_html

    assert _compose_run_config_strip_html("±6.8% model error", "") == "±6.8% model error"


def test_details_renders_per_dungeon_residuals_ranked_worst_first():
    """The Run-details popover must surface every per-dungeon residual,
    not just the single worst one. Algeth'ar -12.5% must be visible
    when it's in the calibration block (engaged_tank 2026-05-15)."""
    cfg = RunConfig(
        k_active=2700,
        global_rmse=0.079,
        log_count=6,
        worst_residual_dungeon="algethar",
        worst_residual_pct=-0.125,
        iterations=2000,
        seed=42,
        constants_version="v0.7",
        simf_sha="abc1234",
        item_db_date="2026-05-15",
        policy_version="v3",
        dungeons=["WR", "PoS", "Algaz"],
        healer_profile="m+_high_key_healer",
        talent_hash="abcd",
        per_dungeon_residuals={
            "workshop": -0.063,
            "algethar": -0.125,
            "necrotic_pours": 0.093,
        },
    )
    out = format_run_config_details(cfg)
    assert "algethar" in out
    assert "-12.5%" in out
    assert "workshop" in out
    assert "-6.3%" in out
    assert "necrotic_pours" in out
    assert "+9.3%" in out
    # Worst-first ordering — algethar (12.5) before necrotic_pours (9.3) before workshop (6.3)
    pos_alg = out.find("algethar")
    pos_nec = out.find("necrotic_pours")
    pos_wks = out.find("workshop")
    assert pos_alg < pos_nec < pos_wks


def test_details_worst_gap_clause_carries_its_own_k_basis_note(baseline_config):
    """Round-2 review (2026-07-05): the worst-dungeon clause quotes the
    identical K=3200 number the per-dungeon table already labels, but sits
    near the K=3430 header with no basis note of its own — the same disease
    round 1 fixed for the table, freshly re-instanced one clause over. The
    R2 (2026-07-17) F-004 reorder renamed the clause ("Worst single dungeon")
    but MUST keep its own K=3200 basis note (regression guard)."""
    out = format_run_config_details(baseline_config)
    assert "Worst single dungeon" in out
    assert "measured at K=3200, not this run's K=3430" in out


def test_details_does_not_claim_an_unverified_k3430_shift():
    """Round-3 review (2026-07-05): a round-2 fix labeled the table's
    K=3200 basis with a "shifts ~+5pp at K=3430" note — but that shift is
    itself an unverified, un-remeasured hand-wave: applying it flips the
    ranking (the flagged-worst dungeon becomes near-best, an unflagged one
    becomes the real worst), while the global RMSE barely moves between
    the two Ks. Narrating a re-ranking that can't be stood behind is the
    same disease as the original unlabeled table. The popover must state
    the measured basis without editorializing an unverified shift."""
    cfg = RunConfig(
        k_active=3430,
        global_rmse=0.068,
        log_count=16,
        worst_residual_dungeon="Algeth'ar Academy",
        worst_residual_pct=-0.06,
        iterations=1000,
        seed=42,
        constants_version="12.0.5",
        simf_sha="abc1234",
        item_db_date="2026-05-12",
        policy_version="v3",
        dungeons=["Windrunner Spire"],
        healer_profile="m+_high_key_healer",
        talent_hash="brutoh-actual",
        per_dungeon_residuals={
            "Windrunner Spire": 0.038,
            "Algeth'ar Academy": -0.06,
        },
    )
    out = format_run_config_details(cfg)
    assert "shifts" not in out
    assert "+5" not in out
    assert "measured at K=3200" in out
    assert "not yet re-measured per-dungeon at this run's K=3430" in out


# ── F-004: one descending hierarchy in the popover (review round R2) ──────────


def test_details_leads_with_authoritative_run_average(baseline_config, monkeypatch):
    """F-004 (R2, 2026-07-17): the popover must read as one descending
    hierarchy — the authoritative run-average ±X% first, then the worst
    dungeon, then the single-hit figure, each a finer grain of the same
    number. Before this, three independently-true error numbers sat with no
    stated relationship and both tank personas trusted the tool LESS for it.

    "The number to trust" is a `calibrated`-tier-only claim (added
    2026-07-25 alongside the cross-player downgrade — see the sibling test
    below for the corpus spec's real, `characterized`-tier framing), so the
    tier is faked `calibrated` here to test the ordering in isolation from
    whichever tier the corpus spec actually holds today."""
    import simf.ui.helpers.run_config as run_config_mod
    from simf.core.constants import load_constants

    real = load_constants()
    fake = dict(real)
    fake["specs"] = {
        **real["specs"],
        "protection_warrior": {
            **real["specs"]["protection_warrior"],
            "calibration_tier": "calibrated",
        },
    }
    monkeypatch.setattr(run_config_mod, "load_constants", lambda: fake)
    out = format_run_config_details(baseline_config)
    assert "number to trust" in out
    pos_avg = out.find("number to trust")
    pos_worst = out.find("Worst single dungeon")
    pos_hit = out.find("Any one hit")
    assert pos_avg != -1 and pos_worst != -1 and pos_hit != -1
    # Run-average leads; worst-dungeon and single-hit sit beneath it in order.
    assert pos_avg < pos_worst < pos_hit


def test_details_corpus_spec_at_characterized_tier_does_not_claim_number_to_trust(
    baseline_config,
):
    """Prot Warrior (`CALIBRATION_CORPUS_SPEC`) sits at `characterized`
    today (the 2026-07-25 cross-player downgrade — see
    `test_no_spec_is_calibrated_today` in test_constants.py) while
    `global_rmse` itself stayed excellent. The popover must not claim "the
    number to trust" one row below a confidence chip that reads "trust
    less" — it names the real number as Brutoh's own single-player fit
    instead, and keeps the same descending ordering."""
    out = format_run_config_details(baseline_config)
    assert "number to trust" not in out
    lead = f"±{baseline_config.global_rmse * 100:.1f}%"
    assert lead in out
    assert "characterized" in out
    pos_lead = out.find(lead)
    pos_worst = out.find("Worst single dungeon")
    pos_hit = out.find("Any one hit")
    assert pos_lead != -1 and pos_worst != -1 and pos_hit != -1
    assert pos_lead < pos_worst < pos_hit


def test_log_surface_no_character_summary_is_honest_and_short():
    """The Why-did-I-die surface with no character loaded is a state where
    NOTHING on screen is sim-derived (the Cooldown Planner, the only panel
    that would run a sim, needs a loaded character and short-circuits
    without one) — the compact strip must say so plainly, not reuse the
    Warrior-corpus ±X% forward-sim error or the generic "Uncalibrated
    build" fallback (dual-reviewed 2026-07-26, closing a Season 2
    punch-list item)."""
    s = format_log_surface_no_character_summary()
    assert "\n" not in s
    assert "%" not in s
    assert "model error" not in s
    assert "Uncalibrated build" not in s
    assert "log" in s.lower()


def test_details_log_surface_no_character_replaces_the_calibration_clause(baseline_config):
    """`log_surface_no_character=True` must override BOTH corpus-spec
    framings entirely — it's a third state, not a variant of
    `is_corpus_spec` — and skip the per-dungeon-residuals table too (also
    forward-sim-specific). Passing `is_corpus_spec=False` alongside must
    have zero effect: this flag wins regardless."""
    cfg = replace(baseline_config, per_dungeon_residuals={"Algeth'ar": -0.125, "WR": 0.05})
    out = format_run_config_details(cfg, is_corpus_spec=False, log_surface_no_character=True)
    assert "Read straight from your log" in out
    assert "not a prediction" in out
    assert "number to trust" not in out
    assert "reference" not in out.lower()
    assert "Worst single dungeon" not in out
    assert "Any one hit" not in out
    assert "Model error by dungeon" not in out
    # The real number still appears, but scoped to the Gear tab, not this page.
    assert f"±{cfg.global_rmse * 100:.1f}%" in out
    assert "Gear tab" in out
    # Generic run-settings lines are untouched — still real, not error claims.
    assert f"K={cfg.k_active}" in out or str(cfg.k_active) in out
    assert str(cfg.seed) in out


def test_details_log_surface_no_character_without_rmse_still_names_the_action():
    """No calibration data at all (e.g. a build with a blank corpus) must
    still degrade gracefully — same honest lead, just without a number to
    scope, plus the same "load a character" action."""
    cfg = RunConfig(
        k_active=3430,
        global_rmse=None,
        log_count=0,
        worst_residual_dungeon=None,
        worst_residual_pct=None,
        iterations=1000,
        seed=42,
        constants_version="12.0.5",
        simf_sha="abc1234",
        item_db_date="unknown",
        policy_version="v3",
        dungeons=[],
        healer_profile="standard",
        talent_hash="x",
    )
    out = format_run_config_details(cfg, log_surface_no_character=True)
    assert "Read straight from your log" in out
    assert "Load your character" in out
    # No NUMERIC percentage (there's no RMSE/residual data to show one for) —
    # not a bare "%" character check, which broke the moment the ship-day-
    # gated patch-currency caveat (constants_version="12.0.5" + today >=
    # 2026-08-11) legitimately started firing here too, adding its own
    # unrelated "absorb %, healing %" prose (no digit before the sign).
    assert not re.search(r"\d%", out)


def test_details_worst_dungeon_does_not_claim_inside_the_band(baseline_config):
    """Advisor catch (R2): RMSE does NOT bound an individual dungeon's
    residual, so the worst-dungeon line must state a factual comparison
    ("not a large outlier"), never a guarantee that it's "inside"/"within"
    the ±X% band — a claim the math can't make and the mechanic-curious tank
    would object to."""
    out = format_run_config_details(baseline_config).lower()
    assert "inside the" not in out
    assert "within the" not in out
    assert "not a large outlier" in out


def test_details_uncalibrated_build_states_no_measured_error():
    """No RMSE data → the popover must not imply a measured error; it says so
    plainly and falls through to the run's settings."""
    cfg = RunConfig(
        k_active=3430,
        global_rmse=None,
        log_count=0,
        worst_residual_dungeon=None,
        worst_residual_pct=None,
        iterations=1000,
        seed=42,
        constants_version="12.0.5",
        simf_sha="abc1234",
        item_db_date="2026-05-12",
        policy_version="v3",
        dungeons=["Windrunner Spire"],
        healer_profile="m+_high_key_healer",
        talent_hash="x",
    )
    out = format_run_config_details(cfg)
    assert "number to trust" not in out
    assert "isn't available for this build yet" in out


# ── F-001/F-002: one calibration chip (confidence + honest number) ────────────


def _badge(tier, trailing):
    from simf.ui.helpers.calibration_badge import _TIER_ICONS, _TIER_LABELS, CalibrationBadge

    return CalibrationBadge(
        tier=tier,
        label=_TIER_LABELS[tier],
        icon=_TIER_ICONS[tier],
        trailing_clause=trailing,
    )


def test_chip_label_corpus_spec_carries_the_number():
    """The ±X% number rides in the chip ONLY for the calibrated corpus spec
    (Prot Warrior) — one element pairing confidence + number (F-001/F-002)."""
    from simf.ui.load import _calibration_chip_label

    label = _calibration_chip_label(
        _badge("calibrated", "matches real combat logs closely"),
        global_rmse=0.068,
        is_corpus_spec=True,
    )
    assert label == "✓ Calibrated · ±6.8% off real logs"


def test_chip_label_calibrated_non_corpus_spec_shows_no_number():
    """The advisor blocker: global_rmse is Warrior's 16-log number with no
    per-spec variant, so a DIFFERENT calibrated-tier spec (e.g. Guardian,
    real RMSE ~1.7x Warrior's) must NOT display Warrior's ±X% as if it were
    its own. It shows the tier word + trailing clause, no number."""
    from simf.ui.load import _calibration_chip_label

    label = _calibration_chip_label(
        _badge("calibrated", "matches real combat logs closely"),
        global_rmse=0.068,
        is_corpus_spec=False,
    )
    assert "±6.8%" not in label
    assert "6.8" not in label
    assert label == "✓ Calibrated — matches real combat logs closely"


def test_chip_label_non_calibrated_tiers_show_confidence_only():
    """Characterized / placeholder specs carry confidence + their honest
    trailing clause, never the corpus number (even for the corpus spec — the
    number is a calibrated-tier claim)."""
    from simf.ui.load import _calibration_chip_label

    char = _calibration_chip_label(
        _badge("characterized", "checked against a few logs — trust less"),
        global_rmse=0.068,
        is_corpus_spec=True,
    )
    assert char == "± Characterized — checked against a few logs — trust less"
    place = _calibration_chip_label(
        _badge("placeholder", "not checked against real logs yet"),
        global_rmse=0.068,
        is_corpus_spec=True,
    )
    assert place == "! Placeholder — not checked against real logs yet"


def test_chip_label_corpus_spec_without_rmse_falls_back_to_clause():
    """Corpus spec but no RMSE data → no fabricated number; fall back to the
    tier word + trailing clause."""
    from simf.ui.load import _calibration_chip_label

    label = _calibration_chip_label(
        _badge("calibrated", "matches real combat logs closely"),
        global_rmse=None,
        is_corpus_spec=True,
    )
    assert label == "✓ Calibrated — matches real combat logs closely"


def test_details_notes_unverified_dungeons_omitted_from_the_table():
    """A `null` residual (no usable calibration replay yet) is correctly
    filtered out of the ranked table — but that used to happen silently,
    leaving a reader on the popover with no way to know some of their prog
    dungeons weren't shown at all (round-2 review, 2026-07-05)."""
    cfg = RunConfig(
        k_active=3430,
        global_rmse=0.068,
        log_count=16,
        worst_residual_dungeon="Algeth'ar Academy",
        worst_residual_pct=-0.06,
        iterations=1000,
        seed=42,
        constants_version="12.0.5",
        simf_sha="abc1234",
        item_db_date="2026-05-12",
        policy_version="v3",
        dungeons=["Windrunner Spire", "Skyreach"],
        healer_profile="m+_high_key_healer",
        talent_hash="brutoh-actual",
        per_dungeon_residuals={
            "Windrunner Spire": 0.038,
            "Algeth'ar Academy": -0.06,
            "Skyreach": None,
            "Seat of the Triumvirate": None,
        },
    )
    out = format_run_config_details(cfg)
    assert "2 dungeons not shown" in out
    assert "Skyreach" in out and "Seat of the Triumvirate" in out
    assert "see the Vault-tab note" in out.lower() or "Vault-tab note" in out


def test_details_omits_unverified_note_when_every_dungeon_is_measured(baseline_config):
    """No `null` residuals in the corpus → no unverified-count clause."""
    cfg = replace(
        baseline_config,
        per_dungeon_residuals={"Windrunner Spire": 0.038, "Pit of Saron": 0.04},
    )
    out = format_run_config_details(cfg)
    assert "not shown" not in out


def test_details_surfaces_per_event_accuracy_honesty(baseline_config):
    """Per-event ±10pp variability disclaimer must appear whenever the
    calibration block renders. Found via the 2026-05-24 audit-script
    policy.tick measurement
    (docs/validation/per_school_gap_policy_tick_2026_05_24.md): per-run
    RMSE is the calibration number but per-event physical gap is ±10pp.
    Surfaces in the trust popover so users don't read a single-pull
    prediction as if it were the calibration number."""
    out = format_run_config_details(baseline_config)
    # R2 (2026-07-17) F-004 reorder renamed this line "Any one hit" and framed
    # it as the finest grain of the same ladder — the ±10pp per-event honesty
    # it carries must survive the rename (regression guard).
    assert "Any one hit" in out, (
        "Per-event honesty line missing from trust popover. Without it, "
        "users may over-trust single-pull predictions."
    )
    assert "10 percentage points" in out, "10pp figure must appear so the magnitude is visible"


def test_details_omits_per_event_honesty_when_no_calibration():
    """If global_rmse is None the calibration block doesn't render — the
    per-event honesty line piggybacks on it and must also be absent.
    Otherwise an uncalibrated build would surface a magnitude that came
    from a different corpus."""
    cfg = RunConfig(
        k_active=2700,
        global_rmse=None,
        log_count=0,
        worst_residual_dungeon=None,
        worst_residual_pct=None,
        iterations=2000,
        seed=42,
        constants_version="v0.7",
        simf_sha="abc1234",
        item_db_date="unknown",
        policy_version="v3",
        dungeons=[],
        healer_profile="default",
        talent_hash="",
    )
    out = format_run_config_details(cfg)
    assert "Single-hit accuracy" not in out


def test_details_omits_per_dungeon_block_when_none():
    """No per-dungeon dict → no per-dungeon section. Backward compat."""
    cfg = RunConfig(
        k_active=2700,
        global_rmse=0.079,
        log_count=6,
        worst_residual_dungeon=None,
        worst_residual_pct=None,
        iterations=2000,
        seed=42,
        constants_version="v0.7",
        simf_sha="abc1234",
        item_db_date="2026-05-15",
        policy_version="v3",
        dungeons=[],
        healer_profile="m+_high_key_healer",
        talent_hash="abcd",
        per_dungeon_residuals=None,
    )
    out = format_run_config_details(cfg)
    assert "per-dungeon error" not in out


def test_iter_formatted_with_thousands_separator():
    """10000 iter must render as '10,000' or '10000' — humans count zeros."""
    s = format_run_config(
        RunConfig(
            k_active=2700,
            global_rmse=0.08,
            log_count=6,
            worst_residual_dungeon=None,
            worst_residual_pct=None,
            iterations=10_000,
            seed=42,
            constants_version="v0.7",
            simf_sha="abc",
            item_db_date="2026-05-12",
            policy_version="v3",
            dungeons=["WR+18"],
            healer_profile="standard",
            talent_hash="x",
        )
    )
    assert "10,000" in s


def test_worst_residual_omitted_when_unset():
    """When no per-dungeon residual data is available, the strip omits that
    clause rather than emitting 'worst None'."""
    s = format_run_config(
        RunConfig(
            k_active=2700,
            global_rmse=0.08,
            log_count=6,
            worst_residual_dungeon=None,
            worst_residual_pct=None,
            iterations=1000,
            seed=42,
            constants_version="v0.7",
            simf_sha="abc",
            item_db_date="2026-05-12",
            policy_version="v3",
            dungeons=["WR+18"],
            healer_profile="standard",
            talent_hash="x",
        )
    )
    assert "worst" not in s.lower()
    assert "None" not in s


# ─── collapsed summary + details popover ───────────────────────────────────────


def test_details_omits_item_db_line_when_unknown():
    """No cached item DB snapshot → don't surface `item db: unknown` in the
    Run-details popover. Surfacing it confuses users about whether the sim
    used the right stats — Wowhead/Blizzard live lookups are the default
    path and an absent snapshot is the normal state for most users.

    engaged_tank Round 2 (2026-05-16): `item db: unknown` in yellow with
    no link/tooltip lost trust on the trinket/weapon stat lines."""
    from simf.ui.helpers.run_config import format_run_config_details

    cfg = RunConfig(
        k_active=2700,
        global_rmse=0.08,
        log_count=6,
        worst_residual_dungeon=None,
        worst_residual_pct=None,
        iterations=1000,
        seed=42,
        constants_version="v0.7",
        simf_sha="abc",
        item_db_date="unknown",
        policy_version="v3",
        dungeons=[],
        healer_profile="standard",
        talent_hash="x",
    )
    body = format_run_config_details(cfg)
    assert "item db" not in body.lower()
    assert "unknown" not in body

    # When the snapshot is present, the line should show.
    cfg_present = RunConfig(
        k_active=2700,
        global_rmse=0.08,
        log_count=6,
        worst_residual_dungeon=None,
        worst_residual_pct=None,
        iterations=1000,
        seed=42,
        constants_version="v0.7",
        simf_sha="abc",
        item_db_date="2026-05-12",
        policy_version="v3",
        dungeons=[],
        healer_profile="standard",
        talent_hash="x",
    )
    body_present = format_run_config_details(cfg_present)
    assert "item db" in body_present.lower()
    assert "2026-05-12" in body_present


# ─── deploy-provenance footer ──────────────────────────────────────────────


def test_build_footer_happy_path():
    """A known sha + a recent commit timestamp renders both the sha and a
    coarse '<age> ago' clause."""
    commit_iso = (datetime.now(UTC) - timedelta(hours=14)).isoformat()
    s = format_build_footer("abc1234", commit_iso)
    assert "build abc1234" in s
    assert "14h ago" in s
    assert "UTC" in s


def test_build_footer_unknown_sha_short_circuits():
    """`git rev-parse` can fail (tarball install) — 'unknown' sha collapses
    the whole caption rather than emitting a bogus commit clause next to it."""
    s = format_build_footer("unknown", "2026-07-06T21:14:00+00:00")
    assert s == "build unknown"


def test_build_footer_unknown_commit_time_omits_age_clause():
    """A known sha but an unavailable commit timestamp still shows the
    sha — just without a fabricated 'committed ...' clause."""
    s = format_build_footer("abc1234", "unknown")
    assert s == "build abc1234"
    assert "committed" not in s


def test_build_footer_unparseable_commit_time_omits_age_clause():
    """A commit_iso that doesn't parse as ISO-8601 must not raise — falls
    back to the bare build clause, same as the 'unknown' sentinel."""
    s = format_build_footer("abc1234", "not-a-timestamp")
    assert s == "build abc1234"
    assert "committed" not in s


def test_details_includes_fields_not_in_summary(baseline_config):
    """The popover surfaces everything the summary drops."""
    d = format_run_config_details(baseline_config)
    assert "iter" in d.lower() and "1,000" in d
    assert "seed" in d.lower() and "42" in d
    assert "v0.7" in d  # constants version
    assert "2026-05-12" in d  # item-db date
    assert "v3" in d  # policy version
    assert "WR+18" in d and "NW+18" in d
    assert "standard" in d  # healer profile
    assert "def5678" in d  # talent hash
    assert "Algeth'ar" in d and "-12.5%" in d


def test_details_does_not_duplicate_summary_fields(baseline_config):
    """The summary already shows K, RMSE, sha — the popover would just be
    visual noise if it repeated them."""
    d = format_run_config_details(baseline_config)
    # The summary's own "K=2700" format must not repeat in the details
    # popover — NOT a blanket ban on the substring "K=", which legitimately
    # appears in the round-2 "measured at K=3200, not this run's K=3430"
    # explanatory clause (a different sentence, not the summary's K field).
    assert f"K={baseline_config.k_active}" not in d
    assert "RMSE=" not in d
    assert "sha=" not in d


def test_reproduction_hash_none_without_character(baseline_config):
    """Nothing to reproduce yet — no character loaded."""
    assert compute_reproduction_hash(baseline_config, None) is None


def test_reproduction_hash_deterministic_for_identical_inputs(baseline_config):
    h1 = compute_reproduction_hash(baseline_config, _stub_char())
    h2 = compute_reproduction_hash(baseline_config, _stub_char())
    assert h1 is not None
    assert h1 == h2


def test_reproduction_hash_changes_with_character_stat(baseline_config):
    h1 = compute_reproduction_hash(baseline_config, _stub_char())
    h2 = compute_reproduction_hash(baseline_config, _stub_char(stamina=25001))
    assert h1 != h2


def test_reproduction_hash_changes_with_talents(baseline_config):
    h1 = compute_reproduction_hash(baseline_config, _stub_char())
    h2 = compute_reproduction_hash(baseline_config, _stub_char(talents="wowhead-prot"))
    assert h1 != h2


def test_reproduction_hash_changes_with_shield_armor(baseline_config):
    """Batch F review item 4: shield_armor was missing from the hashed
    field list — two configs differing only in shield armor (e.g. a
    shield-tank vs. a mis-hydrated shield_armor=0 char) must not collide."""
    h1 = compute_reproduction_hash(baseline_config, _stub_char())
    h2 = compute_reproduction_hash(baseline_config, _stub_char(shield_armor=0))
    assert h1 != h2


def test_reproduction_hash_changes_with_max_hp_override(baseline_config):
    h1 = compute_reproduction_hash(baseline_config, _stub_char())
    h2 = compute_reproduction_hash(baseline_config, _stub_char(max_hp_override=250000))
    assert h1 != h2


def test_reproduction_hash_changes_with_stamina_in_caster_form(baseline_config):
    h1 = compute_reproduction_hash(baseline_config, _stub_char())
    h2 = compute_reproduction_hash(baseline_config, _stub_char(stamina_in_caster_form=True))
    assert h1 != h2


def test_reproduction_hash_changes_with_active_buff_spell_ids(baseline_config):
    """A buff-gated difference (e.g. Metamorphosis up vs. not) must move
    the hash — this is the exact class of collision the item named."""
    h1 = compute_reproduction_hash(baseline_config, _stub_char())
    h2 = compute_reproduction_hash(
        baseline_config, _stub_char(active_buff_spell_ids=frozenset({202770}))
    )
    assert h1 != h2


def test_reproduction_hash_changes_with_detected_talent_spell_ids(baseline_config):
    h1 = compute_reproduction_hash(baseline_config, _stub_char())
    h2 = compute_reproduction_hash(
        baseline_config, _stub_char(detected_talent_spell_ids=frozenset())
    )
    assert h1 != h2


def test_reproduction_hash_ignores_frozenset_iteration_order(baseline_config):
    """Same buff-id set, constructed in a different literal order, is the
    same run — the hash must sort frozenset fields before hashing rather
    than leaning on `default=str`'s unstable set repr."""
    char_a = _stub_char(active_buff_spell_ids=frozenset({1, 2, 3}))
    char_b = _stub_char(active_buff_spell_ids=frozenset({3, 2, 1}))
    assert compute_reproduction_hash(baseline_config, char_a) == compute_reproduction_hash(
        baseline_config, char_b
    )


def test_reproduction_hash_changes_with_integer_constants_version(baseline_config, monkeypatch):
    """The patch LABEL (`cfg.constants_version`, e.g. "12.0.5") doesn't bump
    on a mechanical constants.yaml refit — the integer `constants_version`
    counter does, and an uncommitted/experimental refit (exactly how
    calibration experiments run on this Pi) must not silently share a hash
    with the committed constants."""
    from simf.ui.helpers import run_config as run_config_module

    char = _stub_char()
    monkeypatch.setattr(run_config_module, "load_constants", lambda: {"constants_version": 40})
    h1 = compute_reproduction_hash(baseline_config, char)
    monkeypatch.setattr(run_config_module, "load_constants", lambda: {"constants_version": 41})
    h2 = compute_reproduction_hash(baseline_config, char)
    assert h1 != h2


def test_reproduction_hash_changes_with_seed(baseline_config):
    h1 = compute_reproduction_hash(baseline_config, _stub_char())
    h2 = compute_reproduction_hash(replace(baseline_config, seed=43), _stub_char())
    assert h1 != h2


def test_reproduction_hash_ignores_calibration_only_fields(baseline_config):
    """A calibration refit (new RMSE/log_count) doesn't change what inputs
    produced this run's numbers — the hash must not move under it."""
    char = _stub_char()
    h1 = compute_reproduction_hash(baseline_config, char)
    refit = replace(baseline_config, global_rmse=0.05, log_count=20)
    h2 = compute_reproduction_hash(refit, char)
    assert h1 == h2


def test_reproduction_hash_ignores_dungeon_order(baseline_config):
    """Same prog-dungeon selection in a different order is the same run."""
    reordered = replace(baseline_config, dungeons=list(reversed(baseline_config.dungeons)))
    char = _stub_char()
    assert compute_reproduction_hash(baseline_config, char) == compute_reproduction_hash(
        reordered, char
    )


def test_details_renders_reproduction_hash_when_present(baseline_config):
    cfg = replace(baseline_config, repro_hash="a1b2c3d4e5f6")
    d = format_run_config_details(cfg)
    assert "a1b2c3d4e5f6" in d
    assert "reproduction hash" in d.lower()


def test_details_omits_reproduction_hash_when_absent(baseline_config):
    d = format_run_config_details(baseline_config)
    assert "reproduction hash" not in d.lower()


def test_details_renders_marginal_noise_summary_when_present(baseline_config):
    cfg = replace(baseline_config, marginal_noise_summary="crit ±64%, haste ±24%")
    d = format_run_config_details(cfg)
    assert "crit ±64%, haste ±24%" in d
    assert "Monte Carlo noise only, not model error" in d


def test_details_omits_marginal_noise_summary_when_absent(baseline_config):
    d = format_run_config_details(baseline_config)
    assert "stat-weight noise" not in d.lower()


# ─── patch-currency caveat (12.1.0 ships 2026-08-11) ──────────────────────────


class _FakeDatetime:
    """Stand-in for `datetime` with `.now(tz)` frozen to a fixed instant —
    lets a test simulate "before/after ship day" without real wall-clock
    dependence."""

    def __init__(self, fixed):
        self._fixed = fixed

    def now(self, tz=None):
        return self._fixed


def _freeze_run_config_now(monkeypatch, iso: str) -> None:
    import simf.ui.helpers.run_config as run_config_mod

    monkeypatch.setattr(run_config_mod, "datetime", _FakeDatetime(datetime.fromisoformat(iso)))


def test_patch_currency_caveat_silent_before_ship_date(baseline_config, monkeypatch):
    _freeze_run_config_now(monkeypatch, "2026-08-10T23:59:00+00:00")
    cfg = replace(baseline_config, constants_version="12.0.5")
    d = format_run_config_details(cfg)
    assert "12.1.0" not in d


def test_patch_currency_caveat_fires_on_ship_day(baseline_config, monkeypatch):
    _freeze_run_config_now(monkeypatch, "2026-08-11T00:00:01+00:00")
    cfg = replace(baseline_config, constants_version="12.0.5")
    d = format_run_config_details(cfg)
    assert "12.1.0 has shipped" in d
    assert "Game patch:** 12.0.5" in d


def test_patch_currency_caveat_silent_once_label_is_bumped(baseline_config, monkeypatch):
    """Self-clears the moment `last_verified_patch` moves past 12.0.5 — no
    second manual step to remember alongside the constants.yaml bump."""
    _freeze_run_config_now(monkeypatch, "2026-09-01T00:00:00+00:00")
    cfg = replace(baseline_config, constants_version="12.1.0")
    d = format_run_config_details(cfg)
    assert "12.1.0 has shipped" not in d

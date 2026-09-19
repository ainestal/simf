"""Surface 2 — "Why did I die?" — thin re-export facade.

The implementation was decomposed into role-named sibling modules
(``log_data`` / ``log_formatters`` / ``log_death`` / ``log_segment_risk`` /
``log_cd_plan`` / ``log_coaching`` / ``log_analysis`` / ``log_wcl`` /
``log_surface``). This module now only re-exports their public + test-referenced
symbols so ``from simf.ui.log_view import ...`` keeps resolving for app.py and
the test suite. ``app.py`` imports ``render_surface_log`` from here.
"""

from __future__ import annotations

from simf.ui.log_analysis import render_log_analysis  # noqa: F401
from simf.ui.log_cd_plan import (  # noqa: F401
    _PESSIMISTIC_REGIME_THRESHOLD,
    _cached_cd_plan,
    _cd_plan_caption_for_spec,
    _cd_prescription_card,
    _character_cache_key,
    _death_rate_stderr_pp,
    _fmt_anchor_when,
    _fmt_candidate_label,
    _fmt_press_row,
    _fmt_spike_damage,
    _press_rationale,
    _segment_for_time,
    _segment_position_phrase,
    render_cd_plan_panel,
)
from simf.ui.log_coaching import (  # noqa: F401
    _render_cross_log_threats,
    _render_rage_flow,
    render_defensive_coverage,
)
from simf.ui.log_data import (  # noqa: F401
    _LOG_PICKER_LABEL_CAP,
    EXAMPLES_DIR,
    REPO_ROOT,
    UPLOADS_DIR,
    _cached_all_deaths_for_target,
    _cached_coverage_report,
    _cached_death_analysis,
    _cached_hydrate_character,
    _cached_log_summary,
    _cached_mitigation_audit,
    _cached_party_roles,
    _cached_rage_events,
    _cached_run_death_count,
    _cached_run_events,
    _cached_runs,
    _format_death_badge,
    _log_picker_label,
    _resolve_equipped_stats,
    _resolve_log_path,
    list_logs,
)
from simf.ui.log_death import (  # noqa: F401
    _badge_for_event,
    _render_death_attribution,
    _segment_top_abilities,
)
from simf.ui.log_formatters import (  # noqa: F401
    _OTHER_SENTINEL,
    _ROLE_ICONS,
    _ROLE_LABELS,
    _fmt_mmss,
    _format_party_member,
    _format_run_date,
    _format_run_identity,
    _format_run_outcome,
    _format_run_outcome_short,
    _format_wcl_fight,
    _format_wcl_player,
    _link_md,
    _render_run_identity_header,
    _verdict_card,
)
from simf.ui.log_segment_risk import (  # noqa: F401
    _PER_PULL_MIN_PULLS,
    _PER_PULL_RUN_FRACTION_THRESHOLD,
    _render_pull_card,
    _should_flip_to_per_pull_view,
    render_per_segment_risk,
)
from simf.ui.log_surface import (  # noqa: F401
    _handle_log_upload,
    _pick_target_name,
    _render_local_log_flow,
    render_surface_log,
)
from simf.ui.log_wcl import (  # noqa: F401
    _PUBLIC_MAX_FIGHT_MINUTES,
    _pick_wcl_target,
    _public_wcl_analyze_gate,
    _render_wcl_url_flow,
    _resolve_wcl_target_info,
    _run_wcl_analysis,
    _wcl_coverage_for_render,
    _wcl_help_banner,
)

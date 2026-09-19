"""Regression tests for the Why-did-I-die local-log flow's progress signals.

Four verified frictions, fixed together:
  - the narrated analysis sequence is really 5 cached scans, not 4 — a
    5th, unnumbered spinner used to follow "step 4 of 4" with no number
    at all, silently understating the remaining work
  - the opt-in multi-log scan wrapped its whole per-file loop in one
    static spinner with no per-file progress, even though it iterates
    files one at a time and can sit for 100s+ of seconds
  - the multi-log scan's real per-log cost (~30s+ first time) was only
    ever a button tooltip, never a visible caption stated before the click

These are pinned via source inspection rather than driving the full
local-log flow end-to-end (which needs a real combat log fixture with
COMBATANT_INFO + party detection) — consistent with this codebase's own
`test_render_surface_log_has_file_uploader_widget` convention.
"""

from __future__ import annotations

import inspect
import re

from simf.ui import log_surface


def test_analysis_steps_constant_is_five():
    """The narrated sequence covers 5 cached scans: damage summary,
    mitigation audit, death reconstruction, run segmentation, and the
    cross-run aggregation that used to be an unnumbered 5th spinner."""
    assert log_surface._ANALYSIS_STEPS == 5


def test_all_five_step_labels_reference_the_shared_constant():
    """Every narrated step must read '(step N of {_ANALYSIS_STEPS})' —
    interpolating the constant (not a literal '4') means the count can't
    desync from the real number of steps again."""
    src = inspect.getsource(log_surface._render_local_log_flow)
    found = sorted(int(n) for n in re.findall(r"step (\d) of \{_ANALYSIS_STEPS\}", src))
    assert found == [1, 2, 3, 4, 5], (
        f"expected exactly steps 1-5 wired to _ANALYSIS_STEPS, found {found}"
    )
    # No stale hardcoded "of 4" left over from before the fix.
    assert "of 4)" not in src


def test_fifth_step_is_named_in_the_users_terms():
    """The old 5th spinner ('Aggregating other runs in this log…') gave no
    step number and read like internal jargon — renamed to describe the
    work the way the 4 numbered siblings do."""
    src = inspect.getsource(log_surface._render_local_log_flow)
    assert "Checking your other runs in this log (step 5 of {_ANALYSIS_STEPS})" in src


def test_multi_log_scan_uses_status_with_per_log_updates():
    """The opt-in 'scan every other log' loop must report per-file
    progress via `st.status(...).update(label=...)` inside the loop —
    a single static spinner around the whole loop left a reviewer sat at
    137s with zero change, even though the loop is one file at a time."""
    src = inspect.getsource(log_surface._render_local_log_flow)
    assert "st.status(" in src
    # The per-iteration label must be built inside the loop, cite the
    # 1-based counter, the total, and the current file's basename.
    assert re.search(
        r'status\.update\(\s*label=f"Scanning log \{i\} of \{extra_n\} — '
        r'\{os\.path\.basename\(other_log\)\}…"',
        src,
    ), "expected a per-log status.update(label=...) inside the scan loop"
    assert 'status.update(label=f"Scanned {extra_n} other {extra_word}.", state="complete")' in src


def test_scan_button_states_cost_up_front_as_a_visible_caption():
    """The ~30s-per-log cost used to live only in the button's hover
    tooltip — promoted to a visible caption so it's stated before the
    click, not only discoverable by hovering."""
    src = inspect.getsource(log_surface._render_local_log_flow)
    assert re.search(r'st\.caption\(\s*"Each log takes ~30s or more the first time', src)

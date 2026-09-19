"""Pin docstring drift: CONTRIBUTING.md's ``## Current state`` version must match pyproject.toml.

Iteration-5 (PR #95) refit CONTRIBUTING.md to match pyproject after a silent drift, but no
test pinned the relationship. This file adds the tripwire so CONTRIBUTING.md and pyproject
can never disagree on the project version again — the next drift is caught at
``make test`` time instead of slipping into a release.

Scope is intentionally narrow:
- Pin the **version** strictly (assert).
- Soft-warn on test-count drift in CONTRIBUTING.md (>100 from actual). Iteration-34
  refit "~985" → "~1175" after accumulating ~190 of silent drift; pinning
  strictly would just turn every iteration into a doc churn PR, but a passive
  warning surfaces the next ~190 of drift before it gets embarrassing again.
- Do not pin the date — drifts every iteration, no signal value.
"""

from __future__ import annotations

import ast
import re
import tomllib
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
CONTRIBUTING_MD = REPO_ROOT / "CONTRIBUTING.md"
ROADMAP_MD = REPO_ROOT / "ROADMAP.md"
CHANGELOG_MD = REPO_ROOT / "docs" / "CHANGELOG.md"
TESTS_DIR = REPO_ROOT / "tests"
CONSTANTS_YAML = REPO_ROOT / "src" / "simf" / "data" / "constants.yaml"
APPTEST_FLAKINESS_AUDIT = (
    REPO_ROOT / "docs" / "validation" / "apptest_flakiness_audit_2026_05_27.md"
)

# Soft tripwire: warn when CONTRIBUTING.md's stated test count drifts by more than this
# many tests from the actual count. Wide enough that a typical iteration's
# +5-to-+20-tests PR doesn't trip it; narrow enough that a multi-iteration silent
# drift (the ~190 that accumulated through iteration 34) does.
TEST_COUNT_DRIFT_WARN_THRESHOLD = 100


def _pyproject_version() -> str:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return data["project"]["version"]


def _contributing_md_current_state_version() -> str:
    """Extract the version from the ``## Current state (vX.Y.Z, ...)`` header."""

    text = CONTRIBUTING_MD.read_text(encoding="utf-8")
    match = re.search(
        r"^## Current state \(v(?P<version>\d+\.\d+\.\d+)[,)\s]",
        text,
        flags=re.MULTILINE,
    )
    assert match is not None, (
        "CONTRIBUTING.md must contain a `## Current state (vX.Y.Z, ...` header — "
        "rename or reformat broke the tripwire."
    )
    return match.group("version")


def _roadmap_current_state_version() -> str:
    """Extract the version from ROADMAP's top-of-file "Last updated" stamp.

    2026-07-28 doc cleanup: ROADMAP.md's old ``## Current State (vX.Y.Z ...)``
    section was a stale, session-by-session snapshot duplicating CONTRIBUTING.md's
    own (also stale) version of the same claim — archived verbatim to
    ``docs/CHANGELOG.md`` along with the rest of that drift-prone history.
    ROADMAP.md's version stamp now lives in its top-of-file comment header
    instead (``# Written: May 2026 · Last updated: YYYY-MM-DD (vX.Y.Z)``),
    which is what this now reads. This is the SAME tripwire, re-anchored to
    where the version claim actually lives post-restructuring — not a relaxed
    check.
    """

    text = ROADMAP_MD.read_text(encoding="utf-8")
    match = re.search(
        r"Last updated: [\d-]+ \(v(?P<version>\d+\.\d+\.\d+)\)",
        text,
    )
    assert match is not None, (
        "ROADMAP.md must contain a `Last updated: YYYY-MM-DD (vX.Y.Z)` "
        "top-of-file stamp — rename or reformat broke the tripwire."
    )
    return match.group("version")


def _contributing_md_stated_test_count() -> int | None:
    """Extract the integer from a "~N tests" mention in CONTRIBUTING.md, or None."""

    text = CONTRIBUTING_MD.read_text(encoding="utf-8")
    # Prefer the ``## Current state`` line so we don't accidentally match the
    # ``make test`` block if the two ever disagree.
    match = re.search(
        r"^## Current state \([^)]*~(?P<count>\d+)\s+tests\)",
        text,
        flags=re.MULTILINE,
    )
    if match is None:
        return None
    return int(match.group("count"))


def _contributing_md_calibration_rmse() -> str | None:
    """Extract the RMSE from the ``## Calibration (K=..., RMSE=X, ...)`` header."""

    text = CONTRIBUTING_MD.read_text(encoding="utf-8")
    match = re.search(
        r"^## Calibration \([^)]*RMSE=(?P<rmse>\d+\.\d+)",
        text,
        flags=re.MULTILINE,
    )
    return match.group("rmse") if match else None


def _constants_global_rmse() -> str | None:
    """Extract the authoritative ``global_rmse`` value from constants.yaml.

    Read by regex (not yaml.load) to keep this tripwire dependency-light and to
    match the rest of this file's text-scan style. Anchored on ``global_rmse:``
    so it never matches the historical ``# ... RMSE ...`` narrative comments
    elsewhere in the file.
    """

    text = CONSTANTS_YAML.read_text(encoding="utf-8")
    match = re.search(
        r"^\s*global_rmse:\s*(?P<rmse>\d+\.\d+)",
        text,
        flags=re.MULTILINE,
    )
    return match.group("rmse") if match else None


def _count_test_functions() -> int:
    """Count ``def test_*`` functions across the ``tests/`` tree via AST parse.

    Subprocess-fork-free so the test stays fast and deterministic — pytest's
    ``--collect-only`` would require shelling out and parsing stdout. AST
    walking matches pytest's default collection rule (functions whose name
    starts with ``test_``), parametrize expansion is intentionally NOT counted
    (the soft-tripwire threshold of ``TEST_COUNT_DRIFT_WARN_THRESHOLD`` accounts
    for that systemic undercount — see test docstring below).
    """

    count = 0
    for path in TESTS_DIR.rglob("test_*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            # Skip unparseable files rather than failing the tripwire — a real
            # syntax error in a test file gets caught by pytest collection.
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test_"
            ):
                count += 1
    return count


def test_contributing_md_current_state_version_matches_pyproject() -> None:
    """CONTRIBUTING.md ``## Current state`` version must equal pyproject's project.version.

    A mismatch here means either:
    - pyproject was bumped without updating CONTRIBUTING.md (forgot to refit the doc), OR
    - CONTRIBUTING.md was aspirationally bumped without a release (the doc-only drift class).

    Fix by aligning CONTRIBUTING.md to pyproject — a version bump implies a release event
    and should never be doc-only.
    """

    pyproject_version = _pyproject_version()
    contributing_version = _contributing_md_current_state_version()

    assert contributing_version == pyproject_version, (
        f"CONTRIBUTING.md ## Current state declares v{contributing_version} but pyproject "
        f"declares v{pyproject_version}. Update CONTRIBUTING.md to match pyproject "
        f"(or, if this is a real release, bump pyproject in the same PR)."
    )


def test_roadmap_current_state_version_matches_pyproject() -> None:
    """ROADMAP's top-of-file version stamp must equal pyproject's project.version.

    Added 2026-06-07: ROADMAP labelled a shipped ``v0.11.9`` milestone while
    pyproject stayed at ``0.11.8`` — the two drifted because nothing pinned the
    ROADMAP snapshot to the package version (the CONTRIBUTING.md tripwire above only
    covered CONTRIBUTING.md). This closes that gap: CONTRIBUTING.md, ROADMAP.md, and
    pyproject can no longer disagree on the current version. Fix a failure by
    aligning ROADMAP's version stamp to pyproject (a real release bumps
    pyproject + both docs in the same PR).

    Re-anchored 2026-07-28: the old ``## Current State (vX.Y.Z ...)`` section
    this test used to read was archived to ``docs/CHANGELOG.md`` (a stale,
    duplicated snapshot — see ``_roadmap_current_state_version``'s docstring).
    The version claim now lives in the file's top-of-file comment header.
    """

    pyproject_version = _pyproject_version()
    roadmap_version = _roadmap_current_state_version()

    assert roadmap_version == pyproject_version, (
        f"ROADMAP.md ## Current State declares v{roadmap_version} but pyproject "
        f"declares v{pyproject_version}. Align the ROADMAP snapshot to pyproject "
        f"(or bump pyproject + CONTRIBUTING.md + ROADMAP together for a real release)."
    )


def test_contributing_md_test_count_within_soft_drift_threshold() -> None:
    """Soft tripwire: warn (don't fail) when CONTRIBUTING.md's "~N tests" drifts > 100.

    Iteration-34 refit "~985" → "~1175" after ~190 of silent drift accumulated.
    The version tripwire above catches pyproject drift but didn't catch this
    class. Refit-on-warning lets the next iteration's PR notice and fix without
    every iteration blocking on doc-only churn — strict pinning would just
    generate noise (test count moves on virtually every PR that adds a test).

    The actual count here is the AST-walked "def test_*" count across tests/,
    not pytest's ``--collect-only`` total — AST counts the function definitions
    and skips parametrize expansion. For this codebase that's ~37 lower than
    the parametrize-expanded count (1138 vs 1175 at iteration 34), and CONTRIBUTING.md
    historically tracks the ``--collect-only`` number, so the threshold of 100
    swallows both the parametrize gap and any single iteration's worth of new
    tests without firing.

    Mutation behavior: setting CONTRIBUTING.md to "~500 tests" or "~9999 tests" raises
    a ``UserWarning`` instead of an ``AssertionError`` so CI stays green — the
    intent is to surface drift to the human reading the test output, not to
    block the merge train. To verify locally:

        # In CONTRIBUTING.md, edit "~1175 tests" → "~500 tests"
        # Then: pytest tests/test_docs_consistency.py -W error::UserWarning
        # Should fail with the drift warning promoted to an error.
    """

    stated = _contributing_md_stated_test_count()
    assert stated is not None, (
        "CONTRIBUTING.md ``## Current state`` line must contain a ``~N tests`` "
        "fragment so this tripwire can read the doc's stated count. The "
        "fuzzy ``~`` form is deliberate — strict pinning would block every "
        "PR until manually refit."
    )

    actual = _count_test_functions()
    drift = abs(actual - stated)

    if drift > TEST_COUNT_DRIFT_WARN_THRESHOLD:
        warnings.warn(
            f"CONTRIBUTING.md declares ~{stated} tests, AST-walk finds {actual} "
            f"``def test_*`` functions (drift {drift} > "
            f"{TEST_COUNT_DRIFT_WARN_THRESHOLD}). Refit the ``~N tests`` "
            f"figure in CONTRIBUTING.md (both the ``make test`` block and the "
            f"``## Current state`` header) to roughly match the current "
            f"count. This is a soft tripwire — CI stays green — but the "
            f"doc is drifting from reality and should be refit in the next "
            f"PR.",
            UserWarning,
            stacklevel=2,
        )


def test_apptest_flakiness_audit_exists_and_references_named_tests() -> None:
    """Audit doc must exist + cite the two flaky AppTest tests by name.

    Iteration-18 shipped a research-only audit at
    ``docs/validation/apptest_flakiness_audit_2026_05_27.md`` capturing the
    pre-push retry friction (xdist parallelism, non-deterministic). The doc is
    the only carrier of that context until the actual fix lands — pin both its
    existence and that it still names the flaky tests, so a future cleanup pass
    cannot silently drop the doc and lose the trail of evidence.
    """

    assert APPTEST_FLAKINESS_AUDIT.is_file(), (
        f"AppTest flakiness audit missing: {APPTEST_FLAKINESS_AUDIT.relative_to(REPO_ROOT)} "
        "was added in iteration 18 (loop) to capture the xdist pre-push retry pattern. "
        "Do not delete without first landing the underlying fix + a follow-up that "
        "supersedes this doc."
    )

    body = APPTEST_FLAKINESS_AUDIT.read_text(encoding="utf-8")
    for needle in (
        "test_cold_load_view_log_routes_to_log_surface",
        "test_log_surface_link_navigates_from_gear",
    ):
        assert needle in body, (
            f"AppTest flakiness audit must still name the flaky test '{needle}'. "
            "If the test was renamed, update both the test and this audit doc."
        )


def test_roadmap_session_10_cd_plan_queue_marked_shipped() -> None:
    """Iteration-32 annotated the session-10 CD-plan queue as closed.

    The queue (annotation > read-only guard > HRPS column) closed across PRs #112,
    #113, and #114 on 2026-05-27/28. This tripwire fails if a future edit
    silently un-strikes the queue (mutation-verified — flipping the ``~~`` off or
    removing the PR references makes the assertion fire).

    Re-anchored 2026-07-28: this text used to live directly in ROADMAP.md
    (line ~182 of the old file); the 2026-07-28 doc cleanup archived
    ROADMAP.md's whole stale "Release log" (this line's home) to
    ``docs/CHANGELOG.md`` verbatim, so the same content — and the same
    tripwire purpose — now lives there instead.
    """

    text = CHANGELOG_MD.read_text(encoding="utf-8")
    assert "~~The CD-plan queue from session 10" in text, (
        "docs/CHANGELOG.md must keep the strike-through annotation on the "
        "session-10 CD-plan queue line — that queue closed via PRs #112/#113/#114."
    )
    assert "PRs #112, #113, #114" in text, (
        "docs/CHANGELOG.md's session-10 CD-plan queue closure must cite PRs "
        "#112/#113/#114 — the three PRs that closed annotation, read-only "
        "guard, and HRPS column respectively."
    )


def test_contributing_md_next_priorities_does_not_reclaim_fixed_cheat_death_gate_bug() -> None:
    """CONTRIBUTING.md's ``## Next priorities`` list must not re-claim the AD
    cheat-death gate bug is open — it was fixed 2026-07-06 (PR #288,
    ``runner.py``'s Ardent Defender save now gates on the buff window being
    active, not on cooldown-readiness; see ``constants.yaml``'s
    ``ardent_defender_cheat_death_heal_pct`` comment and ``## Current state``
    item 8).

    Caught 2026-07-08 (docs-drift review): item 2's "Also still open" sentence
    named two things — the reactive-AD-press-policy gap (still genuinely open)
    and the cheat-death gate bug's stale mechanism description (already fixed
    the day before this sentence's own doc's dateline) — bundled as if both
    were still live. The reactive-AD clause is allowed to stay, and it's fine
    to still *mention* the (now-fixed) cheat-death gate bug in a "this was
    fixed" note; what's pinned absent here is the specific stale description
    of the bug's mechanism as a *currently-true* fact, which only made sense
    while the bug was open.

    Mutation: reinserting the phrase "checks cooldown-readiness only, never
    buff-window activity" into CONTRIBUTING.md's ``## Next priorities`` section
    makes this assertion fire.
    """

    text = CONTRIBUTING_MD.read_text(encoding="utf-8")
    next_priorities_match = re.search(
        r"^## Next priorities.*?(?=^## )",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert next_priorities_match is not None, (
        "CONTRIBUTING.md must contain a `## Next priorities` section — a rename or "
        "reformat broke this tripwire."
    )
    next_priorities = next_priorities_match.group(0)

    stale_mechanism_description = "checks cooldown-readiness only, never buff-window activity"
    assert stale_mechanism_description not in next_priorities, (
        "CONTRIBUTING.md's `## Next priorities` section re-states the `runner.py` "
        "Ardent Defender cheat-death gate bug's mechanism as a currently-true "
        "fact — it was fixed 2026-07-06 (PR #288). Remove the stale clause "
        "(the sibling reactive-AD-press-policy gap is still genuinely open "
        "and should stay; a brief 'this was fixed in PR #288' note is fine)."
    )


def test_constants_yaml_warrior_calibration_tier_rmse_is_not_a_tbd_placeholder() -> None:
    """The `protection_warrior` `calibration_tier` inline comment must not say
    "RMSE TBD" — the real value (0.068 from 2026-05-18 to 2026-07-17, 0.138
    since the 2026-07-18 Demo Shout/Phalanx double-count fix, 16 logs either
    way) has been known for months, and must always be legible from the
    comment regardless of which tier the spec currently sits at.

    Caught 2026-07-08 (docs-drift review): the comment read "RMSE TBD
    post-refit (2026-05-18 structural fix)" while `global_rmse: 0.068` sat
    ~450 lines below it in the same file, unchanged since that same refit.

    The regex captures whatever `calibration_tier` value is actually there
    (not hardcoded to `calibrated`) — protection_warrior was itself
    downgraded to `characterized` 2026-07-18, and this test's job is to keep
    checking the comment stays accurate through tier changes, not to gate on
    a specific tier.

    Mutation: reverting the inline comment on the `protection_warrior`
    `calibration_tier` line back to "RMSE TBD ..." makes this assertion fire.
    """

    text = CONSTANTS_YAML.read_text(encoding="utf-8")
    match = re.search(
        r"^\s*protection_warrior:\s*\n\s*calibration_tier:\s*\w+\s*#\s*(?P<comment>.*)$",
        text,
        flags=re.MULTILINE,
    )
    assert match is not None, (
        "constants.yaml must contain a `protection_warrior:` block whose "
        "`calibration_tier:` line carries an inline `#` comment — a reformat "
        "broke this tripwire."
    )
    comment = match.group("comment")

    assert "RMSE TBD" not in comment, (
        f"protection_warrior's calibration_tier comment still says 'RMSE TBD': "
        f"{comment!r}. The real value has been known since 2026-05-18 — state "
        f"it (see the authoritative `global_rmse` field further down this file)."
    )

    constants_rmse = _constants_global_rmse()
    assert constants_rmse is not None
    assert constants_rmse in comment, (
        f"protection_warrior's calibration_tier comment {comment!r} does not "
        f"cite the authoritative global_rmse value ({constants_rmse}) — align "
        f"the narrative comment to the real number."
    )


def test_contributing_md_calibration_rmse_matches_constants_global_rmse() -> None:
    """CONTRIBUTING.md's ``## Calibration (..., RMSE=X, ...)`` header must equal the
    authoritative ``global_rmse`` in constants.yaml.

    Caught 2026-06-07 (doc-consistency audit): the header advertised RMSE=0.065
    — a stale value carried over from the session-17 K-sweep narrative — while
    constants.yaml's authoritative ``global_rmse`` field was 0.068 (the value
    the engine actually reports, K=3430 SimC-DBC anchor). The number in the
    context doc and the number the code uses must agree. Unlike the ``~N tests``
    soft-drift tripwire, this is a single canonical value with no parametrize
    gap, so it's pinned strictly.

    Mutation: edit either side's number and this assertion fires. To verify:
        # In CONTRIBUTING.md, edit "RMSE=0.068" → "RMSE=0.065"; pytest this file.
    """

    contributing_rmse = _contributing_md_calibration_rmse()
    assert contributing_rmse is not None, (
        "CONTRIBUTING.md must contain a `## Calibration (K=..., RMSE=X.XXX, ...)` "
        "header — a rename or reformat broke this tripwire."
    )

    constants_rmse = _constants_global_rmse()
    assert constants_rmse is not None, (
        "constants.yaml must contain a `global_rmse: X.XXX` field under "
        "`calibration:` — a rename or reformat broke this tripwire."
    )

    assert float(contributing_rmse) == float(constants_rmse), (
        f"CONTRIBUTING.md Calibration header declares RMSE={contributing_rmse} but "
        f"constants.yaml global_rmse is {constants_rmse}. Align the CONTRIBUTING.md "
        f"header to the authoritative constants.yaml value (a recalibration "
        f"changes constants.yaml first; the doc follows)."
    )

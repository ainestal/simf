"""Unit tests for ``scripts/prune_merged_branches.py`` — pure-logic only.

Network paths (``_http_get`` / ``_http_delete``) are not exercised; we
test the classifier so the dangerous decision (delete vs keep) is pinned
without needing live API calls. The script lives outside the ``src/``
package, so we import it via a path-based loader (no install step).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "prune_merged_branches.py"


def _load_module():
    """Import the script as a module without modifying the repo install."""
    spec = importlib.util.spec_from_file_location("prune_merged_branches", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None, "spec_from_file_location failed"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["prune_merged_branches"] = mod
    spec.loader.exec_module(mod)
    return mod


pmb = _load_module()


def _pr(number: int, head: str, *, merged: bool, state: str = "closed") -> dict:
    return {
        "number": number,
        "state": state,
        "merged_at": "2026-05-27T00:00:00Z" if merged else None,
        "head": {"ref": head},
    }


def test_master_is_never_deletable_even_if_a_pr_targets_it():
    """Belt-and-braces: a PR opened FROM master against master would
    otherwise look like a merged-PR-head and trip a delete. Protect."""
    prs = {"master": [_pr(1, "master", merged=True)]}
    status = pmb.classify_branch("master", prs)
    assert status.safe_to_delete is False
    assert status.name in pmb.PROTECTED_BRANCHES


def test_branch_with_no_pr_is_kept():
    status = pmb.classify_branch("wip/scratch", {})
    assert status.latest_pr_number is None
    assert status.latest_pr_merged is None
    assert status.safe_to_delete is False


def test_branch_with_open_pr_is_kept():
    prs = {"feat/foo": [_pr(42, "feat/foo", merged=False, state="open")]}
    status = pmb.classify_branch("feat/foo", prs)
    assert status.latest_pr_number == 42
    assert status.latest_pr_merged is False
    assert status.latest_pr_state == "open"
    assert status.safe_to_delete is False


def test_branch_with_merged_pr_is_safe_to_delete():
    prs = {"feat/foo": [_pr(42, "feat/foo", merged=True)]}
    status = pmb.classify_branch("feat/foo", prs)
    assert status.latest_pr_merged is True
    assert status.safe_to_delete is True


def test_branch_with_closed_unmerged_pr_is_kept():
    """A PR can be closed without merging (rejected). Don't delete the
    branch — the work was rejected, not landed. Reopening that PR later
    requires the branch to still exist."""
    prs = {"feat/rejected": [_pr(99, "feat/rejected", merged=False, state="closed")]}
    status = pmb.classify_branch("feat/rejected", prs)
    assert status.latest_pr_merged is False
    assert status.safe_to_delete is False


def test_newest_pr_wins_when_branch_was_reused():
    """If a branch was used for two PRs (rare but possible), the
    decision must follow the NEWEST PR's state, not the oldest.

    Scenario: PR #1 was merged from feat/x, then somebody recreated
    feat/x for a new attempt PR #2 which is now open. The branch is NOT
    safe to delete because PR #2 is still alive.
    """
    prs = {
        "feat/x": [
            _pr(1, "feat/x", merged=True),
            _pr(2, "feat/x", merged=False, state="open"),
        ]
    }
    status = pmb.classify_branch("feat/x", prs)
    assert status.latest_pr_number == 2  # newest by number
    assert status.safe_to_delete is False


def test_newest_pr_wins_reverse_order_too():
    """Same as above but with API-default newest-first ordering. The
    classifier should not depend on input order; pinning that."""
    prs = {
        "feat/y": [
            _pr(7, "feat/y", merged=True),  # newest, merged → delete
            _pr(3, "feat/y", merged=False, state="closed"),  # older, rejected
        ]
    }
    status = pmb.classify_branch("feat/y", prs)
    assert status.latest_pr_number == 7
    assert status.safe_to_delete is True


def test_main_branch_protected_in_addition_to_master():
    """``main`` joins ``master`` in the protected set so this script
    is reusable on repos that renamed the default branch."""
    assert "main" in pmb.PROTECTED_BRANCHES
    prs = {"main": [_pr(1, "main", merged=True)]}
    status = pmb.classify_branch("main", prs)
    assert status.safe_to_delete is False


@pytest.mark.parametrize("name", ["master", "main"])
def test_protected_branches_never_safe_regardless_of_pr_state(name):
    for merged_state in (True, False, None):
        # No PR
        prs = {} if merged_state is None else {name: [_pr(1, name, merged=merged_state)]}
        status = pmb.classify_branch(name, prs)
        assert status.safe_to_delete is False, (
            f"{name} marked safe_to_delete with merged_state={merged_state}"
        )


# --- pagination tests --------------------------------------------------------
# `_fetch_all_prs` walks GitHub's /pulls endpoint page-by-page (100 entries
# per page) for both ``state=open`` and ``state=closed``. The pagination
# loop terminates on a short page (<100 entries) — the long-tail simf repo
# already crosses one page, so getting this wrong silently drops PRs and
# could mark a branch as "no PR" when its merged PR was on page 2.
# We mock ``_http_get`` rather than hit the live API so the test is
# hermetic.


def test_fetch_all_prs_groups_by_head_ref(monkeypatch):
    """A single page of mixed-state PRs is grouped by ``head.ref``."""
    calls: list[str] = []

    def fake_get(url: str, token: str) -> object:
        calls.append(url)
        if "state=open" in url:
            return [
                {"number": 10, "head": {"ref": "feat/a"}, "merged_at": None, "state": "open"},
            ]
        if "state=closed" in url:
            return [
                {
                    "number": 9,
                    "head": {"ref": "feat/b"},
                    "merged_at": "2026-05-01T00:00:00Z",
                    "state": "closed",
                },
            ]
        return []

    monkeypatch.setattr(pmb, "_http_get", fake_get)
    out = pmb._fetch_all_prs("fake-token")
    assert set(out.keys()) == {"feat/a", "feat/b"}
    assert out["feat/a"][0]["number"] == 10
    assert out["feat/b"][0]["number"] == 9
    # Sanity: both states must have been queried (or merged PRs from
    # state=closed would be silently absent from the grouping).
    assert any("state=open" in c for c in calls)
    assert any("state=closed" in c for c in calls)


def test_fetch_all_prs_paginates_until_short_page(monkeypatch):
    """When state=closed returns a full page (100 entries) we MUST fetch
    page 2 — otherwise merged PRs beyond the first 100 are silently
    invisible and their branches misclassify as ``KEEP (no PR)``.

    The loop's termination condition is ``len(data) < 100``. We seed
    page 1 with exactly 100 entries to force the loop to ask for page 2,
    and page 2 with fewer than 100 to terminate."""
    pages_seen: list[int] = []

    def fake_get(url: str, token: str) -> object:
        # Crude page parser: the script builds `&page=N` query strings.
        import re

        # Match `&page=N` — anchor on the `&` to avoid the `per_page=100`
        # token, which also contains the substring "page=".
        m = re.search(r"&page=(\d+)", url)
        page = int(m.group(1)) if m else 1
        pages_seen.append(page)
        if "state=open" in url:
            return []  # no open PRs in this scenario
        # state=closed: page 1 returns 100 entries, page 2 returns 1.
        if page == 1:
            return [
                {
                    "number": i,
                    "head": {"ref": f"feat/closed-{i}"},
                    "merged_at": "2026-05-01T00:00:00Z",
                    "state": "closed",
                }
                for i in range(1, 101)
            ]
        if page == 2:
            return [
                {
                    "number": 999,
                    "head": {"ref": "feat/page-two"},
                    "merged_at": "2026-05-01T00:00:00Z",
                    "state": "closed",
                }
            ]
        return []

    monkeypatch.setattr(pmb, "_http_get", fake_get)
    out = pmb._fetch_all_prs("fake-token")
    # Page 2 PR must be present — this is the regression guard.
    assert "feat/page-two" in out, (
        "Pagination broke — PR on page 2 of closed-state results "
        "was not merged into the grouped dict. Branches whose merged "
        "PR is past entry 100 would misclassify as 'no PR'."
    )
    assert len(out) == 101  # 100 from page 1 + 1 from page 2
    # The closed-state walk must have hit at least pages 1 and 2.
    assert 1 in pages_seen and 2 in pages_seen


def test_fetch_all_prs_stops_paginating_on_empty_page(monkeypatch):
    """If a page comes back empty (state with zero PRs at all), the loop
    must terminate immediately — not loop forever."""
    pages_seen: list[int] = []

    def fake_get(url: str, token: str) -> object:
        import re

        # Match `&page=N` — anchor on the `&` to avoid the `per_page=100`
        # token, which also contains the substring "page=".
        m = re.search(r"&page=(\d+)", url)
        page = int(m.group(1)) if m else 1
        pages_seen.append(page)
        return []  # always empty

    monkeypatch.setattr(pmb, "_http_get", fake_get)
    out = pmb._fetch_all_prs("fake-token")
    assert out == {}
    # Both states queried, each terminated on the first empty response.
    # If the loop didn't terminate we'd see many pages here.
    assert len(pages_seen) == 2, f"Expected one call per state, got pages: {pages_seen}"

"""List or delete remote branches whose head PR has been merged.

Hygiene tool for the simf repo. After landing PRs, the remote keeps the
branch ref around forever unless somebody manually deletes it. Over time
the branch list accumulates stale `feat/` and `docs/` refs that no longer
correspond to active work. This script:

  1. Lists every remote branch (excluding ``master``).
  2. For each branch, finds the most recent PR whose ``head.ref`` matched
     it and checks whether that PR was merged.
  3. Reports branches whose latest PR is merged → safe to delete.
  4. With ``--delete``, removes those refs via the GitHub REST API.

Auth: reads the token from ``~/.config/gh/token`` (same flow as
``docs/gh_pat_via_curl.md``). The token must have ``repo`` scope; the
fine-grained PAT the loop uses already does.

Invocation:

  .venv/bin/python scripts/prune_merged_branches.py            # dry-run list
  .venv/bin/python scripts/prune_merged_branches.py --delete   # actually delete

Branches with an OPEN PR or no PR at all are left untouched — only
"merged-and-stale" gets pruned. The protected list at module scope
hard-blocks ``master`` (and adds room to grow if other long-lived
branches ever appear).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_OWNER = "ainestal"
REPO_NAME = "simf"
API_ROOT = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"

# Refs we will never delete, regardless of PR state. Belt-and-braces against
# a future PR being opened from master against master or a similar foot-gun.
PROTECTED_BRANCHES: frozenset[str] = frozenset({"master", "main"})

DEFAULT_TOKEN_PATH = Path.home() / ".config" / "gh" / "token"


@dataclass(frozen=True)
class BranchPRStatus:
    """One remote branch + the disposition of its most recent PR.

    ``latest_pr_number`` and ``latest_pr_merged`` are ``None`` if no PR
    ever pointed at this branch. ``safe_to_delete`` is the single source
    of truth for the prune decision and folds in the protected-list
    check, so callers don't have to re-derive it.
    """

    name: str
    latest_pr_number: int | None
    latest_pr_merged: bool | None
    latest_pr_state: str | None  # "open" / "closed" / None

    @property
    def safe_to_delete(self) -> bool:
        if self.name in PROTECTED_BRANCHES:
            return False
        if self.latest_pr_number is None:
            # No PR ever opened from this branch — leave it (might be WIP
            # or a long-lived integration branch we don't know about).
            return False
        return bool(self.latest_pr_merged)


def classify_branch(
    name: str,
    prs_by_head: dict[str, list[dict]],
) -> BranchPRStatus:
    """Classify a single remote branch by its newest matching PR.

    ``prs_by_head`` maps ``head.ref`` → list of PR dicts (in API order,
    newest-first when the call uses ``sort=created&direction=desc`` —
    which is the GitHub default for the pulls endpoint).
    """
    if name in PROTECTED_BRANCHES:
        return BranchPRStatus(
            name=name, latest_pr_number=None, latest_pr_merged=None, latest_pr_state=None
        )
    matching = prs_by_head.get(name, [])
    if not matching:
        return BranchPRStatus(
            name=name, latest_pr_number=None, latest_pr_merged=None, latest_pr_state=None
        )
    # Pick the newest PR (largest number — PR numbers are monotonic).
    newest = max(matching, key=lambda p: p["number"])
    return BranchPRStatus(
        name=name,
        latest_pr_number=newest["number"],
        latest_pr_merged=newest.get("merged_at") is not None,
        latest_pr_state=newest.get("state"),
    )


def _read_token(token_path: Path = DEFAULT_TOKEN_PATH) -> str:
    if not token_path.exists():
        raise FileNotFoundError(
            f"GitHub token not found at {token_path}. See docs/gh_pat_via_curl.md for setup."
        )
    return token_path.read_text().strip()


def _http_get(url: str, token: str) -> object:
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_delete(url: str, token: str) -> int:
    req = urllib.request.Request(url, method="DELETE")
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def _fetch_all_branches(token: str) -> list[str]:
    names: list[str] = []
    page = 1
    while True:
        data = _http_get(f"{API_ROOT}/branches?per_page=100&page={page}", token)
        if not data:
            break
        names.extend(b["name"] for b in data)
        if len(data) < 100:
            break
        page += 1
    return names


def _fetch_all_prs(token: str) -> dict[str, list[dict]]:
    """Fetch closed + open PRs and return them grouped by ``head.ref``.

    We need closed PRs because the branches we want to prune are
    precisely the ones whose PR has already merged (= closed).
    """
    out: dict[str, list[dict]] = {}
    for state in ("open", "closed"):
        page = 1
        while True:
            url = f"{API_ROOT}/pulls?state={state}&per_page=100&page={page}"
            data = _http_get(url, token)
            if not data:
                break
            for pr in data:
                head = pr.get("head", {}).get("ref")
                if head:
                    out.setdefault(head, []).append(pr)
            if len(data) < 100:
                break
            page += 1
    return out


def run(delete: bool, *, token: str | None = None) -> list[BranchPRStatus]:
    """Main entry point. Returns the full per-branch status list.

    Side effect: when ``delete=True``, every entry with
    ``safe_to_delete=True`` has its ref removed via the API. Prints a
    one-line-per-branch report to stdout either way so the dry-run case
    is informative.
    """
    if token is None:
        token = _read_token()
    branches = _fetch_all_branches(token)
    prs_by_head = _fetch_all_prs(token)
    statuses = [classify_branch(b, prs_by_head) for b in branches]

    deletable = [s for s in statuses if s.safe_to_delete]
    kept = [s for s in statuses if not s.safe_to_delete]

    print(f"# remote branches: {len(statuses)}")
    print(f"# safe to delete (merged PR head): {len(deletable)}")
    print(f"# kept: {len(kept)}\n")

    for s in statuses:
        if s.name in PROTECTED_BRANCHES:
            tag = "PROTECTED"
        elif s.safe_to_delete:
            tag = f"DELETE (PR #{s.latest_pr_number} merged)"
        elif s.latest_pr_number is None:
            tag = "KEEP (no PR)"
        else:
            tag = f"KEEP (PR #{s.latest_pr_number} {s.latest_pr_state})"
        print(f"  {s.name:<48} {tag}")

    if not delete:
        print("\n(dry-run; pass --delete to actually remove the DELETE refs)")
        return statuses

    print("\nDeleting...")
    for s in deletable:
        url = f"{API_ROOT}/git/refs/heads/{s.name}"
        code = _http_delete(url, token)
        ok = "OK" if code in (204, 200) else f"ERROR {code}"
        print(f"  {s.name:<48} {ok}")
    return statuses


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Actually delete the safe-to-delete refs (default: dry-run).",
    )
    parser.add_argument(
        "--token-file",
        default=str(DEFAULT_TOKEN_PATH),
        help=f"Path to PAT file (default: {DEFAULT_TOKEN_PATH}).",
    )
    args = parser.parse_args(argv)
    token = Path(args.token_file).read_text().strip()
    run(delete=args.delete, token=token)
    return 0


if __name__ == "__main__":
    sys.exit(main())

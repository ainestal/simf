"""Cold-share URL handler — pure parser for `st.query_params`.

A Discord link like `simf.app/?demo=brutoh&view=log` should land a reader
on a populated simf state without re-pasting SimC. The parser stays in a
pure helper (no streamlit imports) so we can unit-test the directive in
isolation; the app layer just dispatches on the resulting fields.

Permissive on bad input — a malformed `?view=foo` should yield an
empty-on-that-axis directive rather than raising, because the caller is a
third-party-shared link that we don't control.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlencode

_VALID_VIEWS = {"gear", "log"}
_TRUTHY = {"1", "true", "yes", "on"}
# Demo names hit `data/characters/<name>.yaml` — restrict to simple slugs to
# block path traversal (`../`) and absolute paths from a hostile share link.
_DEMO_SLUG_RE = re.compile(r"^[a-z0-9_\-]+$")


@dataclass(frozen=True)
class ShareDirective:
    load_demo: str | None = None
    view: str | None = None
    advanced: bool = False
    read_only: bool = False  # ?ro=1 — disable trial swaps for cold-share viewers

    def is_empty(self) -> bool:
        return (
            self.load_demo is None
            and self.view is None
            and not self.advanced
            and not self.read_only
        )


def _first(value: object) -> str:
    """`st.query_params` returns str for single values and list[str] for
    repeated keys (`?foo=a&foo=b`). Normalize to the first string."""
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value) if value is not None else ""


def parse_share_params(params: dict) -> ShareDirective:
    """Parse a query-params dict into a ShareDirective. Unknown keys are
    ignored; invalid values for known keys are dropped silently."""
    raw_demo = _first(params.get("demo")).strip().lower()
    demo: str | None = raw_demo if raw_demo and _DEMO_SLUG_RE.match(raw_demo) else None

    raw_view = _first(params.get("view")).strip().lower()
    view: str | None = raw_view if raw_view in _VALID_VIEWS else None

    raw_adv = _first(params.get("adv")).strip().lower()
    advanced = raw_adv in _TRUTHY

    raw_ro = _first(params.get("ro")).strip().lower()
    read_only = raw_ro in _TRUTHY

    return ShareDirective(
        load_demo=demo,
        view=view,
        advanced=advanced,
        read_only=read_only,
    )


def build_share_url(
    *,
    base: str = "",
    demo: str | None = None,
    view: str | None = None,
    advanced: bool = False,
    read_only: bool = False,
) -> str:
    """Build a cold-share URL (query string) from directive fields — the
    inverse of `parse_share_params`. Only set params are emitted, in a stable
    order, so `parse_share_params(parse_qs(build_share_url(...)))` round-trips.
    Returns ``{base}?k=v&…`` (or just ``base`` when nothing is set)."""
    params: list[tuple[str, str]] = []
    if demo:
        params.append(("demo", demo))
    if view:
        params.append(("view", view))
    if advanced:
        params.append(("adv", "1"))
    if read_only:
        params.append(("ro", "1"))
    qs = urlencode(params)
    return f"{base}?{qs}" if qs else base

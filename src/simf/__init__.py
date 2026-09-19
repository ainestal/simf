"""simf — WoW retail tank damage mitigation simulator."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth = the installed package metadata (pyproject
    # version). Avoids the stale hardcoded constant that read "0.1.0" while
    # pyproject was at 0.12.0. Refreshes to the current version on `make install`.
    __version__ = version("simf")
except PackageNotFoundError:  # pragma: no cover - raw source checkout, not installed
    __version__ = "0.0.0+unknown"

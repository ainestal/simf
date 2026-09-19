"""Data-bound loaders that sit alongside the YAML they read.

Most data loaders live in `src/simf/core/constants.py` (engine
constants, dungeons, scaling tables). The modules under this package
are lightweight, surface-only loaders that don't pull in the engine —
e.g. `world_ceilings` reads season tank-spec completion-ceiling data
for the verdict caption layer.
"""

# simf — multi-stage container build.
#
# Two runtime targets exposed: `ui` (Streamlit on 8501) and `api`
# (FastAPI/uvicorn on 8000). Both share the same build stage.
#
# Phase 5.3 of the ROADMAP. The runtime image overrides the listening
# port via STREAMLIT_SERVER_PORT / UVICORN_PORT env vars for cloud
# deploys.
#
# Validator Phase 5 audit (2026-05-16) caught the previous build copying
# only `__init__.py` before `pip install`, which made Hatch build a wheel
# with empty packages. Fixed by copying the full source tree BEFORE the
# install step.

FROM python:3.13-slim AS build

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

# Copy the entire source tree first so Hatch can build a complete wheel.
# `pyproject.toml` declares `packages = ["src/simf"]` — without the full
# tree, the install would land an empty package even though the layer
# cache would still work.
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install with BOTH [ui] and [api] extras so a single image can serve
# either the Streamlit UI or the FastAPI surface. The runtime CMD picks
# which one runs.
RUN pip install --upgrade pip && \
    pip install .[ui,api]

COPY Makefile ROADMAP.md ./

# ----------------------------------------------------------------------
# UI target — Streamlit on 8501
# ----------------------------------------------------------------------

FROM python:3.13-slim AS ui

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

COPY --from=build /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=build /usr/local/bin/streamlit /usr/local/bin/streamlit
COPY --from=build /app/src ./src

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health').status==200 else 1)"

CMD ["streamlit", "run", "src/simf/ui/app.py", \
     "--server.port", "8501", \
     "--server.address", "0.0.0.0", \
     "--server.headless", "true", \
     "--server.fileWatcherType", "none"]

# ----------------------------------------------------------------------
# API target — FastAPI/uvicorn on 8000
# ----------------------------------------------------------------------

FROM python:3.13-slim AS api

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UVICORN_HOST=0.0.0.0 \
    UVICORN_PORT=8000

WORKDIR /app

COPY --from=build /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=build /usr/local/bin/uvicorn /usr/local/bin/uvicorn
COPY --from=build /app/src ./src

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status==200 else 1)"

CMD ["uvicorn", "simf.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

# Default target — preserves backward compat (older `docker build .`
# expectations land on the UI image).
FROM ui AS final

# =============================================================================
# Tahlamus Brain System - Dockerfile  (Phase A: parent build context)
# =============================================================================
# Build context is vibemind-os/ (NOT the_brain/) so the local monorepo package
# `vibemind-shared` (vibemind-os/shared/) is reachable. requirements.txt:10
# lists `vibemind-shared>=0.1.0` which is NOT on PyPI — it must be pip-installed
# from the local source. Pattern mirrors coding-engine/infra (context: ..).
#
# /app stays == the_brain/ contents so the code's relative paths (data/,
# production/api_server.py, start_server.py) keep working unchanged.
#
# Build (from vibemind-os/):
#   docker build -f brain/the_brain/Dockerfile -t vibemind-brain-core:latest .
# (driven by docker-compose.brain-core.yml which sets context: ../..)
# =============================================================================

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# System deps for numpy/scipy/torch wheels
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        curl \
    && rm -rf /var/lib/apt/lists/*

# 1. Install the local shared package FIRST (its own deps: openai/pyyaml/
#    python-dotenv). Kept as a separate layer so it caches independently of
#    the brain requirements.
COPY shared/ /opt/vibemind-shared/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir /opt/vibemind-shared/

# 2. ML stack — torch is boot-critical (web/routers/routing.py:7 hard-imports
#    it inside create_app) but requirements.txt:53 has it COMMENTED OUT, and
#    sentence-transformers/transformers are in NO requirements file (the native
#    venv had them installed manually). Install the CPU-only torch wheel: the
#    container has no GPU passthrough, so the +cu121 wheel (~2GB) would be dead
#    weight. Version line tracks requirements-tahlamus.txt (torch>=2.0).
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch>=2.0,<2.6" && \
    pip install --no-cache-dir \
        "sentence-transformers>=2.2,<6" \
        "transformers>=4.30"

# 3. Brain requirements. vibemind-shared is already satisfied by step 1, so
#    pip treats the requirements.txt line as met (no PyPI lookup).
COPY brain/the_brain/requirements.txt \
     brain/the_brain/requirements-minimal.txt \
     brain/the_brain/requirements-tahlamus.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 4. Full brain source into /app (so relative paths are unchanged vs. native).
COPY brain/the_brain/ /app/

# Persistent volume mount point (EventRoutingHead/SpaceRoutingHead .pt,
# ctm_checkpoints, snapshots).
RUN mkdir -p /app/data

EXPOSE 5000 5003

# No default CMD — docker-compose.brain-core.yml sets `python start_server.py 5000`.

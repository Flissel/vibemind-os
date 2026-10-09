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

# 2. torch: kept for web/routers/routing.py's RadialNetwork/SpaceRoutingHead.
#    sentence-transformers/transformers: ALSO kept, deliberately — brain-core
#    has two independent local-embedding consumers, not one. qdrant_kg.py's
#    Qwen KG-embedder has moved to embedding-service (see docs/superpowers/
#    specs/2026-07-13-brain-embedder-external-api-design.md), but
#    EventRoutingHead (web/brain_server.py, core/event_routing_head.py)
#    still needs local sentence-transformers for its <100ms real-time
#    intent-classification path (web/routers/classification.py) — a network
#    hop to embedding-service would blow that latency budget. See the
#    design spec's "Addendum" section for the full reasoning.
#    Default build-arg flips from cu121 to cpu: Qwen was the only reason
#    this defaulted to a CUDA wheel; neither the routing heads nor
#    EventRoutingHead's MiniLM model have a documented GPU requirement.
ARG BRAIN_TORCH_VARIANT=cpu
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
        --index-url "https://download.pytorch.org/whl/${BRAIN_TORCH_VARIANT}" \
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

# 4. Full brain source into /app.
#    ACHTUNG: relative Pfade sind hier NICHT wie nativ. Im Repo liegt ein Modul
#    als brain/the_brain/core/x.py (vier Eltern bis zur Wurzel), hier als
#    /app/core/x.py (drei). Vier Stellen im Kern rechneten mit parents[3] und
#    warfen darum in der Ausbringung IndexError: 3 - gemessen am 15.09.2026,
#    jeder Hop durch die kanonische Space-Routing-Schicht scheiterte daran.
#    Die Aufloesung laeuft seither ueber space_contract.resolve_registry_path.
COPY brain/the_brain/ /app/

# 4b. Die Space-Registry. Sie liegt im Repo NEBEN brain/the_brain und faellt
#     deshalb nicht unter das COPY oben - ohne diese Zeile fehlte sie im Image
#     ganz, und die Routing-Schicht fand nichts zum Laden. Gleiches Muster wie
#     Dockerfile.deterministic-gateway, das es laengst richtig macht.
COPY config/space_agent_registry.yml /app/config/space_agent_registry.yml
ENV SPACE_AGENT_REGISTRY_PATH=/app/config/space_agent_registry.yml

# 4c. Die Spaces. capabilities.yaml fuehrt `direct:spaces.<x>`-Targets (minibook,
#     schedule, video, research) und die Registry-Pruefung importiert
#     spaces._navigator - ohne dieses COPY warf jeder dieser Pfade im Container
#     ModuleNotFoundError (gemessen 2026-10-08, Vibemind_V1/docs/spaces/README.md
#     Befund 1). Submodule und vendorierte Apps haelt vibemind-os/.dockerignore draussen.
COPY spaces/ /app/spaces/

# Persistent volume mount point (EventRoutingHead/SpaceRoutingHead .pt,
# ctm_checkpoints, snapshots).
RUN mkdir -p /app/data

EXPOSE 5000 5003

# No default CMD — docker-compose.brain-core.yml sets `python start_server.py 5000`.

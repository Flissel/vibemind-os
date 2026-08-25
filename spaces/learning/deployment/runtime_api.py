from __future__ import annotations

import os
import socket
from collections.abc import Callable
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import inspect, text

from spaces.learning.deployment.health import HealthService
from spaces.learning.mcp.server import handle_message
from spaces.learning.services.db.models import Base
from spaces.learning.services.db.session import create_learning_engine


def _tcp_probe(url: str, default_port: int) -> Callable[[], bool]:
    def probe() -> bool:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or default_port
        if not host:
            return False
        with socket.create_connection((host, port), timeout=2):
            return True

    return probe


def _database_probe(database_url: str) -> bool:
    engine = create_learning_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    finally:
        engine.dispose()


def _migration_probe(database_url: str) -> bool:
    engine = create_learning_engine(database_url)
    try:
        present = set(inspect(engine).get_table_names())
        expected = set(Base.metadata.tables)
        return expected <= present
    finally:
        engine.dispose()


def build_health_service() -> HealthService:
    database_url = os.environ.get("LEARNING_DATABASE_URL", "").strip()
    redis_url = os.environ.get("LEARNING_REDIS_URL", "").strip()
    qdrant_url = os.environ.get("LEARNING_QDRANT_URL", "").strip()
    return HealthService(
        dependency_probes={
            "postgres": lambda: _database_probe(database_url),
            "redis": _tcp_probe(redis_url, 6379),
            "qdrant": _tcp_probe(qdrant_url, 6333),
        },
        migration_probe=lambda: _migration_probe(database_url),
        structural_probe=lambda: [],
        golden_path_probe=lambda: None,
    )


app = FastAPI(title="VibeMind Learning Runtime", version="1.0.0")


@app.get("/health/live")
def live() -> dict:
    return build_health_service().liveness()


@app.get("/health/ready")
def ready() -> JSONResponse:
    report = build_health_service().readiness()
    return JSONResponse(report, status_code=200 if report["status"] == "ok" else 503)


@app.get("/health/structural")
def structural() -> dict:
    return build_health_service().structural()


@app.get("/health/golden-path")
def golden_path() -> dict:
    return build_health_service().golden_path()


@app.post("/mcp")
async def mcp(request: Request) -> JSONResponse:
    response = handle_message(await request.json())
    if response is None:
        return JSONResponse({}, status_code=202)
    return JSONResponse(response)


def main() -> int:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8090)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

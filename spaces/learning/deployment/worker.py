from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from threading import Thread
import urllib.error
import urllib.request
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from spaces.learning.deployment.migrate import migrate
from spaces.learning.services.db.session import create_learning_engine
from spaces.learning.services.ingestion.outbox_worker import QdrantOutboxWorker
from spaces.learning.services.ingestion.qdrant_index import QdrantIndex
from spaces.learning.services.ingestion.retrieval import OpenFangEmbeddingGateway
from spaces.learning.services.course_factory.artifact_store import (
    CourseFactoryArtifactStore,
)
from spaces.learning.services.course_factory.catalog import SourceCatalogLoader
from spaces.learning.services.course_factory.learnhouse_gateway import (
    build_learnhouse_http_gateway,
)
from spaces.learning.services.course_factory.model_gateway import (
    build_openfang_model_gateway,
)
from spaces.learning.services.course_factory.publisher import CourseDraftPublisher
from spaces.learning.services.course_factory.quality_gate import QualityGate
from spaces.learning.services.course_factory.repository import CourseFactoryRepository
from spaces.learning.services.course_factory.runner import CourseFactoryRunner
from spaces.learning.services.course_factory.team import CourseAgentTeam
from spaces.learning.deployment.evaluation_api import run as run_evaluation_api


EMBEDDING_DIMENSION = 3072
logger = logging.getLogger(__name__)


def _heartbeat_path() -> Path:
    return Path(
        os.environ.get("LEARNING_WORKER_HEARTBEAT", "/tmp/learning-worker-heartbeat")
    )


def _healthy(max_age_seconds: float = 30.0) -> bool:
    path = _heartbeat_path()
    return (
        path.is_file()
        and time.time() - path.stat().st_mtime <= max_age_seconds
        and _evaluation_api_healthy()
    )


def _evaluation_api_healthy() -> bool:
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:8092/health/ready", timeout=2
        ) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def run() -> None:
    database_url = os.environ.get("LEARNING_DATABASE_URL", "").strip()
    qdrant_url = os.environ.get("LEARNING_QDRANT_URL", "").strip()
    embedding_url = os.environ.get("LEARNING_EMBEDDING_URL", "").strip()
    artifact_root_value = os.environ.get("LEARNING_ARTIFACT_ROOT", "").strip()
    if not database_url or not qdrant_url or not embedding_url or not artifact_root_value:
        raise RuntimeError(
            "Learning worker requires database, Qdrant, and embedding service URLs"
        )
    artifact_root = Path(artifact_root_value)
    migrate(database_url)
    engine = create_learning_engine(database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    index = QdrantIndex(qdrant_url, vector_size=EMBEDDING_DIMENSION)
    projection = QdrantOutboxWorker(
        factory,
        index=index,
        embedder=OpenFangEmbeddingGateway(embedding_url),
    )
    repository = CourseFactoryRepository(factory)
    artifact_store = CourseFactoryArtifactStore(
        factory, artifact_root=artifact_root
    )
    model_gateway = build_openfang_model_gateway()
    course_runner = CourseFactoryRunner(
        repository=repository,
        artifact_store=artifact_store,
        catalog=SourceCatalogLoader(factory),
        team=CourseAgentTeam(repository, model_gateway, artifact_store),
        quality_gate=QualityGate(repository),
        publisher=CourseDraftPublisher(
            repository=repository,
            artifact_store=artifact_store,
            learnhouse=build_learnhouse_http_gateway(),
        ),
    )
    evaluation_api = Thread(
        target=run_evaluation_api,
        name="learning-evaluation-api",
        daemon=True,
    )
    evaluation_api.start()
    try:
        while True:
            projected = projection.run_once()
            queued = repository.claim_next_queued()
            generated = queued is not None
            if queued is not None:
                try:
                    asyncio.run(course_runner.run_job(queued.id))
                except Exception:
                    logger.exception(
                        "learning_course_factory_job_failed",
                        extra={"job_id": queued.id},
                    )
            _heartbeat_path().touch()
            time.sleep(0.1 if projected or generated else 2.0)
    finally:
        index.close()
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args()
    if args.healthcheck:
        return 0 if _healthy() else 1
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

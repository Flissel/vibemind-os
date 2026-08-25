from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from spaces.learning.deployment.migrate import migrate
from spaces.learning.services.db.session import create_learning_engine
from spaces.learning.services.ingestion.outbox_worker import QdrantOutboxWorker
from spaces.learning.services.ingestion.qdrant_index import QdrantIndex
from spaces.learning.services.ingestion.retrieval import OpenFangEmbeddingGateway


EMBEDDING_DIMENSION = 3072


def _heartbeat_path() -> Path:
    return Path(
        os.environ.get("LEARNING_WORKER_HEARTBEAT", "/tmp/learning-worker-heartbeat")
    )


def _healthy(max_age_seconds: float = 30.0) -> bool:
    path = _heartbeat_path()
    return path.is_file() and time.time() - path.stat().st_mtime <= max_age_seconds


def run() -> None:
    database_url = os.environ.get("LEARNING_DATABASE_URL", "").strip()
    qdrant_url = os.environ.get("LEARNING_QDRANT_URL", "").strip()
    embedding_url = os.environ.get("LEARNING_EMBEDDING_URL", "").strip()
    if not database_url or not qdrant_url or not embedding_url:
        raise RuntimeError(
            "Learning worker requires database, Qdrant, and embedding service URLs"
        )
    migrate(database_url)
    engine = create_learning_engine(database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    index = QdrantIndex(qdrant_url, vector_size=EMBEDDING_DIMENSION)
    projection = QdrantOutboxWorker(
        factory,
        index=index,
        embedder=OpenFangEmbeddingGateway(embedding_url),
    )
    try:
        while True:
            processed = projection.run_once()
            _heartbeat_path().touch()
            time.sleep(0.1 if processed else 2.0)
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

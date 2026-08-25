from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from sqlalchemy import text

from spaces.learning.services.db.session import create_learning_engine


def _heartbeat_path() -> Path:
    return Path(
        os.environ.get("LEARNING_WORKER_HEARTBEAT", "/tmp/learning-worker-heartbeat")
    )


def _healthy(max_age_seconds: float = 30.0) -> bool:
    path = _heartbeat_path()
    return path.is_file() and time.time() - path.stat().st_mtime <= max_age_seconds


def run() -> None:
    database_url = os.environ.get("LEARNING_DATABASE_URL", "").strip()
    engine = create_learning_engine(database_url)
    try:
        while True:
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                _heartbeat_path().touch()
            except Exception:
                pass
            time.sleep(5)
    finally:
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

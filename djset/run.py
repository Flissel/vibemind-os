"""Run-Ordner: jede Ausfuehrung bekommt ihr eigenes Verzeichnis.

Weil ein Lauf unbeaufsichtigt stattfindet, sind die abgelegten Artefakte das
einzige Fenster in seine Entscheidungen.
"""

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)

RUNS_DIRNAME = "runs"
FINISHED_MARKER = "set.wav"


class RunNotFound(FileNotFoundError):
    """Der angeforderte Run-Ordner existiert nicht."""


def slugify(text: str) -> str:
    lowered = text.strip().lower()
    collapsed = re.sub(r"[^a-z0-9]+", "-", lowered)
    return collapsed.strip("-")


@dataclass(frozen=True)
class Run:
    base: Path
    run_id: str

    @property
    def directory(self) -> Path:
        return self.base / RUNS_DIRNAME / self.run_id

    @classmethod
    def create(cls, base: Path, genre: str, today: date) -> "Run":
        parent = base / RUNS_DIRNAME
        parent.mkdir(parents=True, exist_ok=True)
        prefix = f"{today.isoformat()}-{slugify(genre)}"

        taken = {path.name for path in parent.iterdir() if path.is_dir()}
        for number in range(1, 100):
            candidate = f"{prefix}-{number:02d}"
            if candidate not in taken:
                run = cls(base=base, run_id=candidate)
                run.directory.mkdir(parents=True)
                return run
        raise RuntimeError(f"mehr als 99 Laeufe fuer {prefix} an einem Tag")

    @classmethod
    def load(cls, base: Path, run_id: str) -> "Run":
        run = cls(base=base, run_id=run_id)
        if not run.directory.is_dir():
            raise RunNotFound(f"kein Run-Ordner fuer {run_id}")
        return run

    @classmethod
    def latest_unfinished(cls, base: Path) -> "Run | None":
        parent = base / RUNS_DIRNAME
        if not parent.is_dir():
            return None
        candidates = sorted(
            (path for path in parent.iterdir() if path.is_dir()),
            key=lambda path: path.name,
            reverse=True,
        )
        for path in candidates:
            if not (path / FINISHED_MARKER).exists():
                return cls(base=base, run_id=path.name)
        return None

    def path(self, name: str) -> Path:
        return self.directory / name if name else self.directory

    def has(self, name: str) -> bool:
        return self.path(name).exists()

    def write_text(self, name: str, text: str) -> Path:
        """Schreibt ein Artefakt unteilbar.

        Erst in eine Nachbardatei, dann os.replace auf das Ziel - unter POSIX
        und unter Windows (ab Python 3.3) ein atomarer Rename innerhalb
        desselben Dateisystems. Ein Abbruch mitten im Schreiben hinterlaesst
        damit entweder das alte Artefakt oder das neue, nie ein halbes.

        Genau die halben Artefakte sind es, die djset.analysis._read_existing
        beim Wiederaufnehmen abfangen muss. Die Nachsicht dort bleibt trotzdem
        stehen: sie gilt auch fuer Dateien, die aus einem aelteren Lauf oder
        von aussen kommen.

        Der Temporaername kommt von mkstemp und ist damit eindeutig - die
        Trackanalysen laufen aus vier Threads in denselben Ordner.
        """
        target = self.path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            dir=target.parent, prefix=f"{target.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(text)
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return target

    def write_model(self, name: str, model: BaseModel) -> Path:
        return self.write_text(name, model.model_dump_json(indent=2))

    def read_model(self, name: str, model_type: type[M]) -> M:
        target = self.path(name)
        if not target.exists():
            raise RunNotFound(f"Artefakt fehlt: {target}")
        return model_type.model_validate_json(target.read_text(encoding="utf-8"))

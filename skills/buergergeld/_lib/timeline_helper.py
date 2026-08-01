"""Geteilte Helper für timeline.yaml und todo.yaml.

Alle Bürgergeld-Skills schreiben Ereignisse hierüber, damit die Akte konsistent
bleibt. Atomisches Schreiben via Temp-File + Rename, Backup vor jeder Änderung.
"""

from __future__ import annotations

import os
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml


def buergergeld_root() -> Path:
    return Path(os.environ.get(
        "BUERGERGELD_ROOT",
        str(Path.home() / "Documents" / "Buergergeld")
    ))


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _atomic_dump(path: Path, data: dict[str, Any]) -> None:
    backup_dir = buergergeld_root() / ".backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(path, backup_dir / f"{path.name}.{stamp}.bak")

    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            data,
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=120,
        )
    tmp.replace(path)


def append_event(
    typ: str,
    titel: str,
    quelle: str,
    datum: date | str | None = None,
    **extra: Any,
) -> int:
    """Hängt ein Event an timeline.yaml an. Gibt die neue Event-ID zurück."""
    timeline_path = buergergeld_root() / "timeline.yaml"
    data = _load_yaml(timeline_path)
    events = data.setdefault("events", [])

    next_id = max((e.get("id", 0) for e in events), default=0) + 1
    if datum is None:
        datum = date.today()
    if isinstance(datum, datetime):
        datum = datum.date()

    event = {
        "id": next_id,
        "datum": datum,
        "typ": typ,
        "titel": titel,
        "quelle": quelle,
    }
    event.update(extra)
    events.append(event)

    data.setdefault("meta", {})
    data["meta"]["updated"] = date.today()
    data["meta"]["updated_by"] = quelle

    _atomic_dump(timeline_path, data)
    return next_id


def todo_set_status(
    item_id: str,
    new_status: str,
    notiz: str | None = None,
    erledigt_am: date | str | None = None,
) -> bool:
    """Setzt Status eines todo.yaml-Items. Gibt True bei Erfolg zurück."""
    valid = {"open", "in_progress", "submitted", "confirmed", "na"}
    if new_status not in valid:
        raise ValueError(f"status must be one of {valid}, got {new_status!r}")

    todo_path = buergergeld_root() / "todo.yaml"
    data = _load_yaml(todo_path)
    items = data.get("items", [])

    for item in items:
        if item.get("id") == item_id:
            item["status"] = new_status
            if notiz:
                item["notiz"] = notiz
            if erledigt_am:
                item["erledigt_am"] = erledigt_am
            data.setdefault("meta", {})
            data["meta"]["updated"] = date.today()
            _atomic_dump(todo_path, data)
            return True
    return False


def get_stammdaten() -> dict[str, Any]:
    return _load_yaml(buergergeld_root() / "stammdaten.yaml")


def get_timeline() -> dict[str, Any]:
    return _load_yaml(buergergeld_root() / "timeline.yaml")


def get_todo() -> dict[str, Any]:
    return _load_yaml(buergergeld_root() / "todo.yaml")

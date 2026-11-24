"""Auftragsablage des Musik-Dienstes: eine Datei je Auftrag, atomar geschrieben.

Ein Auftrag liegt in `<daten>/auftraege/<id>/auftrag.json`. Agenten sehen nur die ID,
nie einen Pfad; `hole` prueft die ID streng, damit aus ihr kein Pfad werden kann.
"""
from __future__ import annotations

import os
import re
import secrets
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ART = Literal["video", "set", "aufnehmen", "laura_render"]
ZUSTAND = Literal["wartet", "laeuft", "fertig", "fertig_mit_befund", "gescheitert", "abgebrochen"]

AUFTRAEGE_DIR = "auftraege"
AUFTRAG_DATEI = "auftrag.json"
_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z_]+-[0-9a-f]{4}$")
_VORRANG = {"laura_render": 0, "video": 1}


def _jetzt() -> datetime:
    return datetime.now(UTC)


class Auftrag(BaseModel):
    id: str
    art: ART
    zustand: ZUSTAND = "wartet"
    grund: str = ""
    eingabe: dict = Field(default_factory=dict)
    kanal: str = "mcp"
    gemeldet: bool = False
    erstellt: datetime = Field(default_factory=_jetzt)
    gestartet: datetime | None = None
    beendet: datetime | None = None
    lauf: str | None = None
    pid: int | None = None
    ergebnis: dict = Field(default_factory=dict)


class Ablage:
    def __init__(self, daten: Path) -> None:
        self.daten = Path(daten)
        self.wurzel = self.daten / AUFTRAEGE_DIR
        self.wurzel.mkdir(parents=True, exist_ok=True)

    def ordner(self, auftrag_id: str) -> Path:
        """Ordner eines Auftrags; wirft KeyError bei einer ID, die nicht der Form entspricht."""
        if not isinstance(auftrag_id, str) or not _ID.match(auftrag_id):
            raise KeyError(auftrag_id)
        return self.wurzel / auftrag_id

    def anlegen(self, art: str, eingabe: dict, kanal: str) -> Auftrag:
        jetzt = _jetzt()
        for _ in range(100):
            auftrag_id = f"{jetzt:%Y%m%d-%H%M%S}-{art}-{secrets.token_hex(2)}"
            if not (self.wurzel / auftrag_id).exists():
                break
        else:
            raise RuntimeError("keine freie Auftrags-ID gefunden")
        auftrag = Auftrag(
            id=auftrag_id, art=art, eingabe=dict(eingabe), kanal=kanal, erstellt=jetzt
        )
        self.speichern(auftrag)
        return auftrag

    def hole(self, auftrag_id: str) -> Auftrag:
        datei = self.ordner(auftrag_id) / AUFTRAG_DATEI
        if not datei.is_file():
            raise KeyError(auftrag_id)
        for versuch in range(50):
            try:
                text = datei.read_text(encoding="utf-8")
                break
            except PermissionError:  # Windows: gerade wird ersetzt
                if versuch == 49:
                    raise
                time.sleep(0.02)
            except FileNotFoundError:
                raise KeyError(auftrag_id) from None
        return Auftrag.model_validate_json(text)

    def speichern(self, auftrag: Auftrag) -> None:
        ziel_ordner = self.ordner(auftrag.id)
        ziel_ordner.mkdir(parents=True, exist_ok=True)
        ziel = ziel_ordner / AUFTRAG_DATEI
        handle, temporaer = tempfile.mkstemp(dir=ziel_ordner, prefix="auftrag.", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as strom:
                strom.write(auftrag.model_dump_json(indent=2))
            # Unter Windows scheitert replace kurz, solange ein Leser die Datei offen hat.
            for versuch in range(50):
                try:
                    os.replace(temporaer, ziel)
                    break
                except PermissionError:
                    if versuch == 49:
                        raise
                    time.sleep(0.02)
        except BaseException:
            Path(temporaer).unlink(missing_ok=True)
            raise

    def alle(self) -> list[Auftrag]:
        gefunden: list[Auftrag] = []
        for ordner in sorted(self.wurzel.iterdir()):
            if not ordner.is_dir() or not _ID.match(ordner.name):
                continue
            try:
                gefunden.append(self.hole(ordner.name))
            except (KeyError, ValueError):
                continue
        return gefunden

    def naechster(self) -> Auftrag | None:
        wartende = [a for a in self.alle() if a.zustand == "wartet"]
        if not wartende:
            return None
        return min(wartende, key=lambda a: (_VORRANG.get(a.art, 2), a.erstellt, a.id))

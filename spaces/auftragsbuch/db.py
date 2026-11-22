"""Zugriff auf das Auftragsbuch: nur die drei Funktionen des Gehirns (Schema `auftrag`).

`PgBuch` ruft ausschliesslich `auftrag.brain_anlegen`, `auftrag.brain_lesen` und
`auftrag.brain_probe_erledigen` auf, immer mit Abfrage-Parametern (nie per Zeichenketten-
Formatierung). Laden und Mandant kommen aus der Konfiguration des Dienstes, nie vom Aufrufer.
"""
from __future__ import annotations

from typing import Any, Protocol

import psycopg.errors
from psycopg.types.json import Jsonb

# Die festen Ablehnungstexte aus spaces/marketing/db/100_auftragsbuch_brain.sql (RAISE EXCEPTION).
# Ein Test haelt diese Liste und die SQL-Datei im Gleichstand.
ABLEHNUNGSTEXTE = (
    "Unbekannte Auftragsart",
    "Auftrag braucht Laden und Mandant",
    "Frist ausserhalb 1 bis 120 Minuten",
    "Plan-Kennung ungueltig",
    "Tagesgrenze erreicht: hoechstens 50 Proben pro Tag",
    "Tagesgrenze erreicht: hoechstens 60 Auftraege des Gehirns pro Tag",
)


class Abgelehnt(ValueError):
    """Die Datenbank hat den Auftrag mit einem der festen Texte abgelehnt."""


class Buch(Protocol):
    def anlegen(self, an: str, art: str, version: int, eingabe: dict,
                plan_id: str | None, frist_min: int) -> str: ...

    def lesen(self, auftrag_id: str) -> dict | None: ...

    def probe_erledigen(self, auftrag_id: str) -> bool: ...


class PgBuch:
    """`Buch` ueber einen `psycopg_pool.ConnectionPool`."""

    def __init__(self, pool: Any, laden: str, mandant: str) -> None:
        self._pool = pool
        self._laden = laden
        self._mandant = mandant

    def anlegen(self, an: str, art: str, version: int, eingabe: dict,
                plan_id: str | None, frist_min: int) -> str:
        try:
            with self._pool.connection() as conn:
                zeile = conn.execute(
                    "select auftrag.brain_anlegen(%s::text, %s::text, %s::int, %s::text, %s::text,"
                    " %s::jsonb, %s::text, %s::int)",
                    (an, art, version, self._laden, self._mandant, Jsonb(eingabe), plan_id, frist_min),
                ).fetchone()
        except psycopg.errors.RaiseException as e:
            text = e.diag.message_primary
            if text in ABLEHNUNGSTEXTE:
                raise Abgelehnt(text) from None
            raise
        return str(zeile[0])

    def lesen(self, auftrag_id: str) -> dict | None:
        with self._pool.connection() as conn:
            zeile = conn.execute("select auftrag.brain_lesen(%s::uuid)", (auftrag_id,)).fetchone()
        wert = zeile[0] if zeile else None
        return wert if isinstance(wert, dict) else None

    def probe_erledigen(self, auftrag_id: str) -> bool:
        with self._pool.connection() as conn:
            zeile = conn.execute("select auftrag.brain_probe_erledigen(%s::uuid)", (auftrag_id,)).fetchone()
        return bool(zeile and zeile[0])

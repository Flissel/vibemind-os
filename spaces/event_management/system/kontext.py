"""Aufrufkontext und Transaktion, die den RLS-Kontext je Transaktion setzt."""
from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger(__name__)


class KeinRecht(PermissionError):
    """Rolle zu schwach fuer die Aktion. Die Meldung nennt nie Daten."""


class NichtGefunden(LookupError):
    """Zeile oder Firma ist fuer den Kontext nicht sichtbar (API: 404)."""


@dataclass(frozen=True)
class Kontext:
    person_id: UUID | None
    betreiber: bool
    rollen: Mapping[UUID, str] = field(default_factory=dict)
    event_id: UUID | None = None
    actor: str = ""
    quelle: str = "oberflaeche"

    def __post_init__(self) -> None:
        object.__setattr__(self, "rollen", MappingProxyType(dict(self.rollen)))

    @property
    def firmen(self) -> frozenset[UUID]:
        return frozenset(self.rollen)

    @classmethod
    def system(cls) -> Kontext:
        """Nur fuer Migration und Login-Lookup: keine Firmen, kein Betreiber."""
        return cls(person_id=None, betreiber=False, rollen={}, actor="system", quelle="system")


@contextmanager
def transaktion(conn: psycopg.Connection[Any], kontext: Kontext) -> Iterator[psycopg.Cursor[Any]]:
    """Transaktion mit lokal gesetztem RLS-Kontext (verfaellt mit Commit/Rollback)."""
    werte = (
        ",".join(sorted(str(f) for f in kontext.firmen)),
        "an" if kontext.betreiber else "",
        str(kontext.person_id) if kontext.person_id else "",
        str(kontext.event_id) if kontext.event_id else "",
    )
    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "select set_config('event.firmen', %s, true),"
                " set_config('event.betreiber', %s, true),"
                " set_config('event.person', %s, true),"
                " set_config('event.event', %s, true)",
                werte,
            )
            yield cur

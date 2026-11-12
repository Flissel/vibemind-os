"""Aenderungsprotokoll (append-only) je Event."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg

from spaces.event_management.system import rechte
from spaces.event_management.system.kontext import (
    Kontext, NichtGefunden, event_bindung_pruefen, transaktion,
)

log = logging.getLogger(__name__)


def schreiben(
    cur: psycopg.Cursor[Any], k: Kontext, event_id: UUID, firma_id: UUID,
    baustein: str, aktion: str, ziel_id: UUID | None,
) -> None:
    """Haengt einen Eintrag an; laeuft auf dem Cursor der Aenderung (gleiche Transaktion)."""
    actor = k.actor
    if not actor and k.person_id is not None:
        cur.execute("select name from event.person where id = %s", (k.person_id,))
        row = cur.fetchone()
        if row is not None:
            actor = row["name"]
    if not actor:
        actor = "System"
    cur.execute(
        "insert into event.verlauf (event_id, firma_id, actor, quelle, baustein, aktion, ziel_id)"
        " values (%s, %s, %s, %s, %s, %s, %s)",
        (event_id, firma_id, actor, k.quelle, baustein, aktion, ziel_id),
    )


def lesen(
    conn: psycopg.Connection[Any], k: Kontext, event_id: UUID,
    seit: datetime | None = None, actor: str | None = None, limit: int = 100,
) -> list[dict[str, Any]]:
    """Eintraege eines Events, neueste zuerst."""
    event_bindung_pruefen(k, event_id)
    with transaktion(conn, k) as cur:
        cur.execute("select firma_id from event.event where id = %s", (event_id,))
        ev = cur.fetchone()
        if ev is None:
            raise NichtGefunden("Event nicht gefunden")
        rechte.pruefe(k, "lesen", ev["firma_id"])
        sql = ("select id, event_id, firma_id, zeitpunkt, actor, quelle, baustein, aktion, ziel_id"
               " from event.verlauf where event_id = %s")
        params: list[Any] = [event_id]
        if seit is not None:
            sql += " and zeitpunkt >= %s"
            params.append(seit)
        if actor is not None:
            sql += " and actor = %s"
            params.append(actor)
        sql += " order by zeitpunkt desc, id desc limit %s"
        params.append(limit)
        cur.execute(sql, params)
        return cur.fetchall()

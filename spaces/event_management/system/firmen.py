"""Firmen, Personen und Mitgliedschaften."""
from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID

import psycopg
from psycopg import errors

from spaces.event_management.system import pruefung, rechte
from spaces.event_management.system.kontext import Kontext, NichtGefunden, transaktion

log = logging.getLogger(__name__)

_KURZ = re.compile(r"[a-z0-9-]{2,32}")
ROLLEN = ("leitung", "mitglied")


def firma_anlegen(conn: psycopg.Connection[Any], k: Kontext, name: str, kurz: str) -> dict[str, Any]:
    rechte.pruefe(k, "firma_anlegen")
    name = pruefung.text("name", name).strip()
    kurz = pruefung.text("kurz", kurz)
    if not name:
        raise ValueError("Name fehlt")
    if not _KURZ.fullmatch(kurz):  # fullmatch: "$" liesse ein "\n" am Ende durch
        raise ValueError("Kurzname ungueltig")
    try:
        with transaktion(conn, k) as cur:
            cur.execute(
                "insert into event.firma (name, kurz) values (%s, %s) returning id, name, kurz",
                (name, kurz),
            )
            row = cur.fetchone()
    except errors.UniqueViolation:
        # Ohne psycopg-DETAIL (nennt den Wert); einzige Unique-Spalte neben der id ist kurz.
        raise ValueError("Kürzel bereits vergeben") from None
    assert row is not None
    return row


def firmen(conn: psycopg.Connection[Any], k: Kontext) -> list[dict[str, Any]]:
    with transaktion(conn, k) as cur:
        if k.betreiber:
            cur.execute("select id, name, kurz from event.firma order by name, id")
        else:
            cur.execute(
                "select id, name, kurz from event.firma where id = any(%s) order by name, id",
                (list(k.firmen),),
            )
        return cur.fetchall()


def person_finden_oder_anlegen(
    conn: psycopg.Connection[Any], mail: str, name: str
) -> dict[str, Any]:
    mail = pruefung.text("mail", mail).strip().lower()
    name = pruefung.text("name", name).strip()
    if not mail or not name:
        raise ValueError("Mail und Name sind Pflicht")
    with transaktion(conn, Kontext.system()) as cur:
        cur.execute(
            "insert into event.person (mail, name) values (%s, %s)"
            " on conflict (lower(mail)) do nothing",
            (mail, name),
        )
        cur.execute("select id, mail, name from event.person where lower(mail) = %s", (mail,))
        row = cur.fetchone()
    assert row is not None
    return row


def mitgliedschaft_setzen(
    conn: psycopg.Connection[Any], k: Kontext, person_id: UUID, firma_id: UUID, rolle: str
) -> None:
    """Legt eine NEUE Mitgliedschaft an (Recht `einladen`). Eine bestehende bleibt
    unveraendert (ValueError), auch bei parallelem Anlegen; Rollen aendert nur
    `rolle_aendern`."""
    if rolle not in ROLLEN:
        raise ValueError("Rolle ungueltig")
    rechte.pruefe(k, "einladen", firma_id)
    with transaktion(conn, k) as cur:
        cur.execute(
            "insert into event.mitgliedschaft (person_id, firma_id, rolle) values (%s, %s, %s)"
            " on conflict (person_id, firma_id) do nothing",
            (person_id, firma_id, rolle),
        )
        if cur.rowcount != 1:
            raise ValueError("Mitgliedschaft existiert bereits")


def rolle_aendern(
    conn: psycopg.Connection[Any], k: Kontext, person_id: UUID, firma_id: UUID, rolle: str
) -> None:
    """Aendert die Rolle einer BESTEHENDEN Mitgliedschaft (Recht `rolle_aendern`), in einer
    Transaktion. Unbekannte Mitgliedschaft: NichtGefunden. Die letzte Leitung bleibt."""
    if rolle not in ROLLEN:
        raise ValueError("Rolle ungueltig")
    rechte.pruefe(k, "rolle_aendern", firma_id)
    with transaktion(conn, k) as cur:
        cur.execute(
            "select rolle from event.mitgliedschaft where person_id = %s and firma_id = %s"
            " for update",
            (person_id, firma_id),
        )
        alt = cur.fetchone()
        if alt is None:
            raise NichtGefunden("Mitgliedschaft nicht gefunden")
        if alt["rolle"] == "leitung" and rolle != "leitung":
            _letzte_leitung_pruefen(cur, firma_id)
        cur.execute(
            "update event.mitgliedschaft set rolle = %s where person_id = %s and firma_id = %s",
            (rolle, person_id, firma_id),
        )
        if cur.rowcount != 1:
            raise NichtGefunden("Mitgliedschaft nicht gefunden")


def _letzte_leitung_pruefen(cur: psycopg.Cursor[Any], firma_id: UUID) -> None:
    """Sperrt die Leitungszeilen der Firma und wirft ValueError bei nur einer."""
    cur.execute(
        "select count(*) as n from (select 1 from event.mitgliedschaft"
        " where firma_id = %s and rolle = 'leitung' for update) l",
        (firma_id,),
    )
    row = cur.fetchone()
    if row is not None and row["n"] <= 1:
        raise ValueError("Die letzte Leitung einer Firma bleibt bestehen")


def mitglieder(conn: psycopg.Connection[Any], k: Kontext, firma_id: UUID) -> list[dict[str, Any]]:
    rechte.pruefe(k, "lesen", firma_id)
    with transaktion(conn, k) as cur:
        cur.execute(
            "select m.person_id, p.name, p.mail, m.rolle from event.mitgliedschaft m"
            " join event.person p on p.id = m.person_id"
            " where m.firma_id = %s order by p.name, p.mail",
            (firma_id,),
        )
        return cur.fetchall()


def mitglied_entfernen(
    conn: psycopg.Connection[Any], k: Kontext, person_id: UUID, firma_id: UUID
) -> None:
    rechte.pruefe(k, "mitglied_entfernen", firma_id)
    with transaktion(conn, k) as cur:
        cur.execute(
            "select rolle from event.mitgliedschaft where person_id = %s and firma_id = %s"
            " for update",
            (person_id, firma_id),
        )
        alt = cur.fetchone()
        if alt is None:
            raise NichtGefunden("Mitgliedschaft nicht gefunden")
        if alt["rolle"] == "leitung":
            _letzte_leitung_pruefen(cur, firma_id)
        cur.execute(
            "delete from event.mitgliedschaft where person_id = %s and firma_id = %s",
            (person_id, firma_id),
        )


def kontext_fuer_person(conn: psycopg.Connection[Any], person_id: UUID) -> Kontext:
    with transaktion(conn, Kontext(person_id=person_id, betreiber=False, rollen={})) as cur:
        cur.execute("select betreiber, aktiv from event.person where id = %s", (person_id,))
        p = cur.fetchone()
        if p is None or not p["aktiv"]:
            raise NichtGefunden("Person nicht gefunden")
        cur.execute(
            "select firma_id, rolle from event.mitgliedschaft where person_id = %s", (person_id,)
        )
        rollen = {r["firma_id"]: r["rolle"] for r in cur.fetchall()}
    return Kontext(person_id=person_id, betreiber=bool(p["betreiber"]), rollen=rollen)

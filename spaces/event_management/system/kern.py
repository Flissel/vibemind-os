"""Generische Fachschicht der Kern-Bausteine (Aufgaben, Orga-Team, Teilnehmende, Ablauf,
Budget) plus Kontakte. SQL wird nur aus Registry-Namen (`sql.Identifier`) gebaut."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg import errors, sql

from spaces.event_management.system import events, rechte, verlauf
from spaces.event_management.system.bausteine import REGISTRY, BausteinTabelle
from spaces.event_management.system.kategorien import Kategorie
from spaces.event_management.system.kontext import Kontext, NichtGefunden, transaktion

log = logging.getLogger(__name__)

_NIE_SCHREIBBAR = frozenset({"id", "event_id", "firma_id", "angelegt"})


def _tabelle(baustein: str) -> BausteinTabelle:
    t = REGISTRY.get(baustein)
    if t is None:
        raise ValueError("Unbekannter Baustein")
    return t


def _im_schema(
    event: Mapping[str, Any], t: BausteinTabelle, kategorien: Mapping[str, Kategorie] | None,
) -> None:
    schema = events.schema_fuer(event["kategorie"], kategorien)
    name = t.baustein or t.name
    if name not in schema.bausteine:
        raise ValueError(f"Baustein {name} gehört nicht zu Kategorie {event['kategorie']}")


def _uuid(feld: str, wert: Any) -> UUID:
    if isinstance(wert, UUID):
        return wert
    if isinstance(wert, str):
        try:
            return UUID(wert)
        except ValueError:
            pass
    raise ValueError(f"Feld '{feld}' muss eine UUID sein")


def _wert(feld: str, typ: str, wert: Any) -> Any:
    if typ == "text":
        if not isinstance(wert, str):
            raise ValueError(f"Feld '{feld}' muss Text sein")
        return wert
    if typ == "uuid":
        return _uuid(feld, wert)
    if typ == "datumzeit":
        if isinstance(wert, str):
            try:
                wert = datetime.fromisoformat(wert)
            except ValueError:
                raise ValueError(f"Feld '{feld}' ist keine gültige Zeit") from None
        if not isinstance(wert, datetime):
            raise ValueError(f"Feld '{feld}' ist keine gültige Zeit")
        return events.zeit(wert, feld)
    if typ == "ganzzahl":
        if isinstance(wert, bool) or not isinstance(wert, int) or wert < 0:
            raise ValueError(f"Feld '{feld}' muss eine ganze Zahl >= 0 sein")
        return wert
    if typ.startswith("auswahl:"):
        if not isinstance(wert, str) or wert not in typ[len("auswahl:"):].split(","):
            raise ValueError(f"Feld '{feld}' hat keinen erlaubten Wert")
        return wert
    raise ValueError(f"Feld '{feld}' hat einen unbekannten Typ")


def _pruefen(t: BausteinTabelle, werte: Mapping[str, Any], *, anlegen: bool) -> dict[str, Any]:
    if not isinstance(werte, Mapping):
        raise ValueError("Werte müssen ein Objekt sein")
    for name in werte:
        if not isinstance(name, str) or name in _NIE_SCHREIBBAR or name not in t.felder:
            raise ValueError("Unbekanntes oder nicht schreibbares Feld")
    if not anlegen and not werte:
        raise ValueError("Keine Werte zum Ändern")
    if anlegen:
        fehlt = sorted(f for f in t.pflicht if werte.get(f) is None)
        if fehlt:
            raise ValueError(f"Pflichtfeld '{fehlt[0]}' fehlt")
    ergebnis: dict[str, Any] = {}
    for name, roh in werte.items():
        if roh is None:
            if name in t.pflicht:
                raise ValueError(f"Pflichtfeld '{name}' darf nicht leer sein")
            ergebnis[name] = None
            continue
        v = _wert(name, t.felder[name], roh)
        if name in t.pflicht and t.felder[name] == "text" and not v.strip():
            raise ValueError(f"Pflichtfeld '{name}' darf nicht leer sein")
        ergebnis[name] = v
    beginn, ende = ergebnis.get("beginn"), ergebnis.get("ende")
    if beginn is not None and ende is not None and ende < beginn:
        raise ValueError("Ende liegt vor dem Beginn")
    return ergebnis


def _db_fehler(t: BausteinTabelle, e: psycopg.Error, werte: Mapping[str, Any]) -> ValueError:
    """Meldung ohne Daten; der Aufrufer verwirft die psycopg-Details mit `from None`."""
    if isinstance(e, errors.ForeignKeyViolation):
        name = e.diag.constraint_name or ""
        refs = [f for f in t.felder if f.endswith("_id") and f in werte]
        feld = next((f for f in refs if f in name), refs[0] if refs else "verweis")
        return ValueError(f"Verweis ungültig: {feld}")
    if isinstance(e, errors.UniqueViolation):
        return ValueError("Eintrag existiert bereits")
    return ValueError("Wert ungültig")


def _vorspann(
    cur: psycopg.Cursor[Any], k: Kontext, event_id: UUID, t: BausteinTabelle,
    kategorien: Mapping[str, Kategorie] | None, *, schreiben: bool,
) -> dict[str, Any]:
    ev = events.event_laden(cur, k, event_id)
    rechte.pruefe(k, "baustein_schreiben" if schreiben else "lesen", ev["firma_id"])
    if schreiben and ev["quelle"] == "legacy":
        raise ValueError("Legacy-Events sind schreibgeschützt")
    _im_schema(ev, t, kategorien)
    return ev


def liste(
    conn: psycopg.Connection[Any], k: Kontext, event_id: UUID, baustein: str,
    *, kategorien: Mapping[str, Kategorie] | None = None,
) -> list[dict[str, Any]]:
    t = _tabelle(baustein)
    order = sql.SQL(", ").join(sql.Identifier(c.strip()) for c in t.sortierung.split(","))
    with transaktion(conn, k) as cur:
        _vorspann(cur, k, event_id, t, kategorien, schreiben=False)
        cur.execute(
            sql.SQL("select * from event.{} where event_id = %s order by {}").format(
                sql.Identifier(t.tabelle), order),
            (event_id,),
        )
        return cur.fetchall()


def anlegen(
    conn: psycopg.Connection[Any], k: Kontext, event_id: UUID, baustein: str,
    werte: dict[str, Any], *, kategorien: Mapping[str, Kategorie] | None = None,
) -> dict[str, Any]:
    t = _tabelle(baustein)
    with transaktion(conn, k) as cur:
        ev = _vorspann(cur, k, event_id, t, kategorien, schreiben=True)
        v = _pruefen(t, werte, anlegen=True)
        spalten = ["event_id", "firma_id", *v]
        try:
            cur.execute(
                sql.SQL("insert into event.{} ({}) values ({}) returning *").format(
                    sql.Identifier(t.tabelle),
                    sql.SQL(", ").join(map(sql.Identifier, spalten)),
                    sql.SQL(", ").join(sql.Placeholder() * len(spalten))),
                [event_id, ev["firma_id"], *v.values()],
            )
        except (errors.IntegrityError, errors.DataError) as e:
            raise _db_fehler(t, e, v) from None
        row = cur.fetchone()
        assert row is not None
        verlauf.schreiben(cur, k, event_id, ev["firma_id"], baustein, "angelegt", row["id"])
    return row


def aendern(
    conn: psycopg.Connection[Any], k: Kontext, event_id: UUID, baustein: str, zeile_id: UUID,
    werte: dict[str, Any], *, kategorien: Mapping[str, Kategorie] | None = None,
) -> dict[str, Any]:
    t = _tabelle(baustein)
    with transaktion(conn, k) as cur:
        ev = _vorspann(cur, k, event_id, t, kategorien, schreiben=True)
        v = _pruefen(t, werte, anlegen=False)
        try:
            cur.execute(
                sql.SQL("update event.{} set {} where id = %s and event_id = %s returning *")
                .format(
                    sql.Identifier(t.tabelle),
                    sql.SQL(", ").join(
                        sql.SQL("{} = %s").format(sql.Identifier(c)) for c in v)),
                [*v.values(), zeile_id, event_id],
            )
        except (errors.IntegrityError, errors.DataError) as e:
            raise _db_fehler(t, e, v) from None
        row = cur.fetchone()
        if row is None:
            raise NichtGefunden("Eintrag nicht gefunden")
        verlauf.schreiben(cur, k, event_id, ev["firma_id"], baustein, "geaendert", zeile_id)
    return row


def loeschen(
    conn: psycopg.Connection[Any], k: Kontext, event_id: UUID, baustein: str, zeile_id: UUID,
    *, kategorien: Mapping[str, Kategorie] | None = None,
) -> None:
    t = _tabelle(baustein)
    with transaktion(conn, k) as cur:
        ev = _vorspann(cur, k, event_id, t, kategorien, schreiben=True)
        cur.execute(
            sql.SQL("delete from event.{} where id = %s and event_id = %s returning id").format(
                sql.Identifier(t.tabelle)),
            (zeile_id, event_id),
        )
        if cur.fetchone() is None:
            raise NichtGefunden("Eintrag nicht gefunden")
        verlauf.schreiben(cur, k, event_id, ev["firma_id"], baustein, "geloescht", zeile_id)


def kontakt_anlegen(
    conn: psycopg.Connection[Any], k: Kontext, firma_id: UUID, name: str,
    mail: str | None = None, telegram: str | None = None,
) -> dict[str, Any]:
    rechte.pruefe(k, "baustein_schreiben", firma_id)
    for feld, wert in (("name", name), ("mail", mail), ("telegram", telegram)):
        if wert is not None and not isinstance(wert, str):
            raise ValueError(f"Feld '{feld}' muss Text sein")
    if not name.strip():
        raise ValueError("Pflichtfeld 'name' fehlt")
    with transaktion(conn, k) as cur:
        cur.execute(
            "insert into event.kontakt (firma_id, name, mail, telegram) values (%s,%s,%s,%s)"
            " returning *",
            (firma_id, name.strip(), mail, telegram),
        )
        row = cur.fetchone()
        assert row is not None
    return row


def kontakte(
    conn: psycopg.Connection[Any], k: Kontext, firma_id: UUID, suche: str | None = None,
) -> list[dict[str, Any]]:
    rechte.pruefe(k, "lesen", firma_id)
    query = "select * from event.kontakt where firma_id = %s"
    params: list[Any] = [firma_id]
    if suche:
        query += (" and (name ilike %s escape '\\' or mail ilike %s escape '\\'"
                  " or telegram ilike %s escape '\\')")
        params += [events.like_muster(suche)] * 3
    query += " order by name, id"
    with transaktion(conn, k) as cur:
        cur.execute(query, params)
        return cur.fetchall()

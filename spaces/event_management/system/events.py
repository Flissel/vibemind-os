"""Events: Anlegen, Aendern, Status, Katalog mit Phase, Schema-Pruefung der Metadaten."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import asdict
from datetime import date, datetime, timezone
from functools import cache
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from spaces.event_management.system import rechte, verlauf
from spaces.event_management.system.kategorien import (
    Feld, Kategorie, WirksamesSchema, lade_kategorien, wirksames_schema,
)
from spaces.event_management.system.kontext import Kontext, NichtGefunden, transaktion

log = logging.getLogger(__name__)

ZEITZONE = ZoneInfo("Europe/Berlin")
_STATUS = ("entwurf", "geplant", "laeuft", "abgeschlossen", "abgesagt", "archiviert")
_AENDERBAR = frozenset({"name", "beginn", "ende", "ort_adresse", "ort_link", "beschreibung",
                        "metadaten"})


@cache
def _standard_kategorien() -> dict[str, Kategorie]:
    return lade_kategorien()


def _schema(kategorie: str, kategorien: Mapping[str, Kategorie] | None) -> WirksamesSchema:
    kats = _standard_kategorien() if kategorien is None else kategorien
    if kategorie not in kats:
        raise ValueError("Unbekannte Kategorie")
    if kats[kategorie].abstrakt:
        raise ValueError("Abstrakte Kategorie nicht waehlbar")
    return wirksames_schema(kats, kategorie)


def _zeit(wert: datetime, name: str) -> datetime:
    if not isinstance(wert, datetime):
        raise ValueError(f"Zeit '{name}' ungueltig")
    return wert.replace(tzinfo=ZEITZONE) if wert.tzinfo is None else wert


def _typ_ok(f: Feld, v: Any) -> bool:
    if f.typ in ("text", "langtext"):
        return isinstance(v, str)
    if f.typ == "url":
        return isinstance(v, str) and v.startswith(("http://", "https://"))
    if f.typ == "zahl":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if f.typ == "ja_nein":
        return isinstance(v, bool)
    if f.typ in ("datum", "datumzeit"):
        if not isinstance(v, str):
            return False
        try:
            (date if f.typ == "datum" else datetime).fromisoformat(v)
        except ValueError:
            return False
        return True
    if f.typ == "auswahl":
        return isinstance(v, str) and v in f.optionen
    return False


def _metadaten_pruefen(schema: WirksamesSchema, roh: Mapping[str, Any] | None) -> dict[str, Any]:
    roh = dict(roh or {})
    felder = {f.id: f for f in schema.felder}
    for name in roh:
        if name not in felder:
            raise ValueError(f"Unbekanntes Metadatenfeld '{name}'")
    ergebnis: dict[str, Any] = {}
    for f in schema.felder:
        if roh.get(f.id) is not None:
            if not _typ_ok(f, roh[f.id]):
                raise ValueError(f"Metadatenfeld '{f.id}' hat einen falschen Typ oder Wert")
            ergebnis[f.id] = roh[f.id]
        elif f.vorgabe is not None:
            ergebnis[f.id] = f.vorgabe
        elif f.pflicht:
            raise ValueError(f"Pflichtfeld '{f.id}' fehlt")
    return ergebnis


def _eckdaten_pruefen(schema: WirksamesSchema, werte: Mapping[str, Any]) -> None:
    for name in sorted(schema.eckdaten_pflicht):
        v = werte.get(name)
        if v is None or (isinstance(v, str) and not v.strip()):
            raise ValueError(f"Eckdatum '{name}' ist Pflicht")


def _phase(row: Mapping[str, Any], jetzt: datetime) -> str:
    if row["status"] in ("abgesagt", "archiviert"):
        return "vergangen"
    if row["beginn"] <= jetzt <= row["ende"]:
        return "laeuft"
    if jetzt < row["beginn"]:
        return "kommend"
    return "vergangen"


def _laden(cur: psycopg.Cursor[Any], event_id: UUID, sperren: bool = False) -> dict[str, Any]:
    cur.execute("select * from event.event where id = %s" + (" for update" if sperren else ""),
                (event_id,))
    row = cur.fetchone()
    if row is None:
        raise NichtGefunden("Event nicht gefunden")
    return row


def event_anlegen(
    conn: psycopg.Connection[Any], k: Kontext, firma_id: UUID, kategorie: str, name: str,
    beginn: datetime, ende: datetime, ort_adresse: str | None = None,
    ort_link: str | None = None, beschreibung: str | None = None,
    metadaten: dict[str, Any] | None = None,
    *, kategorien: Mapping[str, Kategorie] | None = None,
) -> dict[str, Any]:
    rechte.pruefe(k, "event_anlegen", firma_id)
    schema = _schema(kategorie, kategorien)
    name = name.strip()
    if not name:
        raise ValueError("Name fehlt")
    beginn, ende = _zeit(beginn, "beginn"), _zeit(ende, "ende")
    if ende < beginn:
        raise ValueError("Ende liegt vor dem Beginn")
    _eckdaten_pruefen(schema, {"ort_adresse": ort_adresse, "ort_link": ort_link,
                               "ende": ende, "beschreibung": beschreibung})
    meta = _metadaten_pruefen(schema, metadaten)
    with transaktion(conn, k) as cur:
        cur.execute(
            "insert into event.event (firma_id, kategorie, name, beginn, ende, ort_adresse,"
            " ort_link, beschreibung, metadaten) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " returning *",
            (firma_id, kategorie, name, beginn, ende, ort_adresse, ort_link, beschreibung,
             Jsonb(meta)),
        )
        row = cur.fetchone()
        assert row is not None
        verlauf.schreiben(cur, k, row["id"], firma_id, "event", "angelegt", row["id"])
    return row


def event_aendern(
    conn: psycopg.Connection[Any], k: Kontext, event_id: UUID,
    *, kategorien: Mapping[str, Kategorie] | None = None, **felder: Any,
) -> dict[str, Any]:
    falsch = sorted(set(felder) - _AENDERBAR)
    if falsch:
        raise ValueError(f"Feld '{falsch[0]}' ist nicht aenderbar")
    with transaktion(conn, k) as cur:
        alt = _laden(cur, event_id, sperren=True)
        rechte.pruefe(k, "event_aendern", alt["firma_id"])
        schema = _schema(alt["kategorie"], kategorien)
        neu = dict(alt)
        for key, wert in felder.items():
            neu[key] = _zeit(wert, key) if key in ("beginn", "ende") else wert
        if "name" in felder:
            neu["name"] = str(neu["name"]).strip()
            if not neu["name"]:
                raise ValueError("Name fehlt")
        if neu["ende"] < neu["beginn"]:
            raise ValueError("Ende liegt vor dem Beginn")
        _eckdaten_pruefen(schema, neu)
        if "metadaten" in felder:
            neu["metadaten"] = _metadaten_pruefen(schema, felder["metadaten"])
        cur.execute(
            "update event.event set name=%s, beginn=%s, ende=%s, ort_adresse=%s, ort_link=%s,"
            " beschreibung=%s, metadaten=%s, geaendert=now() where id=%s returning *",
            (neu["name"], neu["beginn"], neu["ende"], neu["ort_adresse"], neu["ort_link"],
             neu["beschreibung"], Jsonb(neu["metadaten"]), event_id),
        )
        row = cur.fetchone()
        assert row is not None
        verlauf.schreiben(cur, k, event_id, row["firma_id"], "event", "geaendert", event_id)
    return row


def event_status(
    conn: psycopg.Connection[Any], k: Kontext, event_id: UUID, status: str,
) -> dict[str, Any]:
    if status not in _STATUS:
        raise ValueError("Unbekannter Status")
    aktion = "event_absagen" if status in ("abgesagt", "archiviert") else "event_aendern"
    with transaktion(conn, k) as cur:
        alt = _laden(cur, event_id, sperren=True)
        rechte.pruefe(k, aktion, alt["firma_id"])
        cur.execute("update event.event set status=%s, geaendert=now() where id=%s returning *",
                    (status, event_id))
        row = cur.fetchone()
        assert row is not None
        verlauf.schreiben(cur, k, event_id, row["firma_id"], "event", f"status_{status}", event_id)
    return row


def event_loeschen(conn: psycopg.Connection[Any], k: Kontext, event_id: UUID) -> None:
    with transaktion(conn, k) as cur:
        alt = _laden(cur, event_id, sperren=True)
        rechte.pruefe(k, "event_loeschen", alt["firma_id"])
        cur.execute("delete from event.event where id = %s", (event_id,))


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def katalog(
    conn: psycopg.Connection[Any], k: Kontext, firma_id: UUID | None = None,
    kategorie: str | None = None, suche: str | None = None, jetzt: datetime | None = None,
    *, kategorien: Mapping[str, Kategorie] | None = None,
) -> list[dict[str, Any]]:
    if firma_id is not None:
        rechte.pruefe(k, "lesen", firma_id)
    kats = _standard_kategorien() if kategorien is None else kategorien
    jetzt_ = datetime.now(timezone.utc) if jetzt is None else _zeit(jetzt, "jetzt")
    sql = (
        "select e.*, f.name as firma_name,"
        " (select count(*) from event.aufgabe a where a.event_id = e.id and a.status <> 'erledigt')"
        "   as aufgaben_offen,"
        " (select count(*) from event.aufgabe a where a.event_id = e.id) as aufgaben_gesamt,"
        " (select count(*) from event.teilnehmende t where t.event_id = e.id"
        "   and t.zusage = 'zugesagt') as teilnehmende_zugesagt"
        " from event.event e join event.firma f on f.id = e.firma_id where true"
    )
    params: list[Any] = []
    if firma_id is not None:
        sql += " and e.firma_id = %s"
        params.append(firma_id)
    elif not k.betreiber:
        sql += " and e.firma_id = any(%s)"
        params.append(list(k.firmen))
    if kategorie is not None:
        sql += " and e.kategorie = %s"
        params.append(kategorie)
    if suche:
        sql += (" and (e.name ilike %s escape '\\' or e.ort_adresse ilike %s escape '\\'"
                " or f.name ilike %s escape '\\')")
        params += [_like(suche)] * 3
    with transaktion(conn, k) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    for r in rows:
        r["kategorie_name"] = kats[r["kategorie"]].name if r["kategorie"] in kats else r["kategorie"]
        r["phase"] = _phase(r, jetzt_)
        r["kennzahlen"] = {
            "aufgaben_offen": r.pop("aufgaben_offen"),
            "aufgaben_gesamt": r.pop("aufgaben_gesamt"),
            "teilnehmende_zugesagt": r.pop("teilnehmende_zugesagt"),
        }
    rang = {"laeuft": 0, "kommend": 1, "vergangen": 2}
    rows.sort(key=lambda r: (
        rang[r["phase"]],
        -r["ende"].timestamp() if r["phase"] == "vergangen" else r["beginn"].timestamp(),
    ))
    return rows


def event(
    conn: psycopg.Connection[Any], k: Kontext, event_id: UUID,
    *, kategorien: Mapping[str, Kategorie] | None = None,
) -> dict[str, Any]:
    with transaktion(conn, k) as cur:
        row = _laden(cur, event_id)
        rechte.pruefe(k, "lesen", row["firma_id"])
    s = _schema(row["kategorie"], kategorien)
    row["schema"] = {
        "kategorie": s.kategorie, "name": s.name, "bausteine": list(s.bausteine),
        "felder": [asdict(f) for f in s.felder], "begriffe": dict(s.begriffe),
    }
    return row

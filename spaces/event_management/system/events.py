"""Events: Anlegen, Aendern, Status, Katalog mit Phase, Schema-Pruefung der Metadaten."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime, timezone
from functools import cache
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from spaces.event_management.system import pruefung, rechte, verlauf
from spaces.event_management.system.kategorien import (
    Kategorie, WirksamesSchema, lade_kategorien, wirksames_schema,
)
from spaces.event_management.system.kontext import (
    KeinRecht, Kontext, NichtGefunden, event_bindung_pruefen, transaktion,
)

log = logging.getLogger(__name__)

ZEITZONE = ZoneInfo("Europe/Berlin")
_STATUS = ("entwurf", "geplant", "laeuft", "abgeschlossen", "abgesagt", "archiviert")
_AENDERBAR = frozenset({"name", "beginn", "ende", "ort_adresse", "ort_link", "beschreibung",
                        "metadaten"})


@cache
def _standard_kategorien() -> dict[str, Kategorie]:
    return lade_kategorien()


def schema_fuer(kategorie: str, kategorien: Mapping[str, Kategorie] | None) -> WirksamesSchema:
    kats = _standard_kategorien() if kategorien is None else kategorien
    if kategorie not in kats:
        raise ValueError("Unbekannte Kategorie")
    if kats[kategorie].abstrakt:
        raise ValueError("Abstrakte Kategorie nicht waehlbar")
    return wirksames_schema(kats, kategorie)


def zeit(wert: datetime, name: str) -> datetime:
    if not isinstance(wert, datetime):
        raise ValueError(f"Zeit '{name}' ungueltig")
    return wert.replace(tzinfo=ZEITZONE) if wert.tzinfo is None else wert


def _ort_link(wert: Any) -> str | None:
    """Nur http(s)-Links (kein javascript:, data: usw. in einem spaeter klickbaren Link)."""
    link = pruefung.text_oder_none("ort_link", wert)
    if link is not None and not link.startswith(("http://", "https://")):
        raise ValueError("Feld 'ort_link' muss mit http:// oder https:// beginnen")
    return link


def _metadaten_pruefen(schema: WirksamesSchema, roh: Mapping[str, Any] | None) -> dict[str, Any]:
    if roh is not None and not isinstance(roh, Mapping):
        raise ValueError("Metadaten müssen ein Objekt sein")
    roh = dict(roh or {})
    felder = {f.id: f for f in schema.felder}
    for name in roh:
        if name not in felder:
            raise ValueError(f"Unbekanntes Metadatenfeld '{name}'")
    ergebnis: dict[str, Any] = {}
    for f in schema.felder:
        if roh.get(f.id) is not None:
            if not pruefung.metadatum_ok(f.typ, f.optionen, roh[f.id]):
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


def event_laden(
    cur: psycopg.Cursor[Any], k: Kontext, event_id: UUID, *, sperren: bool = False,
) -> dict[str, Any]:
    """Laedt ein Event; NichtGefunden, wenn es unsichtbar ist oder der Kontext auf ein
    anderes Event gebunden ist (`k.event_id`, Gegenstueck zu `event.event_ok`)."""
    event_bindung_pruefen(k, event_id)
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
    if k.event_id is not None:
        raise KeinRecht("Keine Berechtigung fuer event_anlegen")
    rechte.pruefe(k, "event_anlegen", firma_id)
    schema = schema_fuer(pruefung.text("kategorie", kategorie), kategorien)
    name = pruefung.text("name", name).strip()
    if not name:
        raise ValueError("Name fehlt")
    beginn, ende = zeit(beginn, "beginn"), zeit(ende, "ende")
    ort_adresse = pruefung.text_oder_none("ort_adresse", ort_adresse)
    ort_link = _ort_link(ort_link)
    beschreibung = pruefung.text_oder_none("beschreibung", beschreibung)
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
        alt = event_laden(cur, k, event_id, sperren=True)
        rechte.pruefe(k, "event_aendern", alt["firma_id"])
        schema = schema_fuer(alt["kategorie"], kategorien)
        neu = dict(alt)
        for key, wert in felder.items():
            if wert is None and key in ("name", "beginn", "ende"):
                raise ValueError(f"Feld '{key}' darf nicht leer sein")
            if key in ("beginn", "ende"):
                neu[key] = zeit(wert, key)
            elif key == "ort_link":
                neu[key] = _ort_link(wert)
            elif key != "metadaten":
                neu[key] = pruefung.text_oder_none(key, wert)
        if "name" in felder:
            neu["name"] = neu["name"].strip()
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


def _gruppe_abbauen(cur: psycopg.Cursor[Any], k: Kontext, event_id: UUID) -> None:
    """Legt fuer die bestaetigte Telegram-Gruppe des Events einen `abbauen`-Auftrag an
    (Spec §4: beim Archivieren werden Agent, Server und Binding entfernt). Muss VOR einem
    Loeschen laufen: die Kaskade nimmt die Verbindung mit, danach liesse RLS
    (`bereitstellung_chat`) den Auftrag nicht mehr zu.

    Lehnt RLS den Auftrag ab (Kontext ohne passende Person), entfaellt er nur fuer diesen
    Aufruf; der Bereitstellungs-Arbeiter raeumt den Agenten dann als verwaist ab (ein
    archiviertes oder geloeschtes Event hat keinen Soll-Agenten mehr)."""
    cur.execute("select chat_id from event.verbindung where art = 'gruppe' and event_id = %s"
                " and status = 'bestaetigt'", (event_id,))
    gruppe = cur.fetchone()
    if gruppe is None:
        return
    try:
        with cur.connection.transaction():  # Savepoint: Ablehnung nur fuer den Auftrag
            cur.execute(
                "insert into event.bereitstellung (art, ziel, event_id, person_id, chat_id,"
                " agent_id) values ('abbauen', 'gruppe', %s, %s, %s, %s)",
                (event_id, k.person_id, gruppe["chat_id"], f"ev-{UUID(str(event_id)).hex[:8]}"))
    except psycopg.errors.InsufficientPrivilege:
        log.warning("Abbau-Auftrag uebersprungen (keine Berechtigung); der Arbeiter raeumt"
                    " den Agenten als verwaist ab")


def event_status(
    conn: psycopg.Connection[Any], k: Kontext, event_id: UUID, status: str,
) -> dict[str, Any]:
    if status not in _STATUS:
        raise ValueError("Unbekannter Status")
    with transaktion(conn, k) as cur:
        alt = event_laden(cur, k, event_id, sperren=True)
        # Absagen/Archivieren und deren Ruecknahme brauchen dasselbe Recht.
        ende = ("abgesagt", "archiviert")
        aktion = "event_absagen" if status in ende or alt["status"] in ende else "event_aendern"
        rechte.pruefe(k, aktion, alt["firma_id"])
        cur.execute("update event.event set status=%s, geaendert=now() where id=%s returning *",
                    (status, event_id))
        row = cur.fetchone()
        assert row is not None
        if status == "archiviert" and alt["status"] != "archiviert":
            _gruppe_abbauen(cur, k, event_id)
        verlauf.schreiben(cur, k, event_id, row["firma_id"], "event", f"status_{status}", event_id)
    return row


def event_loeschen(conn: psycopg.Connection[Any], k: Kontext, event_id: UUID) -> None:
    with transaktion(conn, k) as cur:
        alt = event_laden(cur, k, event_id, sperren=True)
        rechte.pruefe(k, "event_loeschen", alt["firma_id"])
        _gruppe_abbauen(cur, k, event_id)
        cur.execute("delete from event.event where id = %s", (event_id,))


def like_muster(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def katalog(
    conn: psycopg.Connection[Any], k: Kontext, firma_id: UUID | None = None,
    kategorie: str | None = None, suche: str | None = None, jetzt: datetime | None = None,
    *, kategorien: Mapping[str, Kategorie] | None = None,
) -> list[dict[str, Any]]:
    if firma_id is not None:
        rechte.pruefe(k, "lesen", firma_id)
    kategorie = pruefung.text_oder_none("kategorie", kategorie)
    suche = pruefung.text_oder_none("suche", suche)
    kats = _standard_kategorien() if kategorien is None else kategorien
    jetzt_ = datetime.now(timezone.utc) if jetzt is None else zeit(jetzt, "jetzt")
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
    if k.event_id is not None:
        sql += " and e.id = %s"
        params.append(k.event_id)
    if kategorie is not None:
        sql += " and e.kategorie = %s"
        params.append(kategorie)
    if suche:
        sql += (" and (e.name ilike %s escape '\\' or e.ort_adresse ilike %s escape '\\'"
                " or f.name ilike %s escape '\\')")
        params += [like_muster(suche)] * 3
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
        row = event_laden(cur, k, event_id)
        rechte.pruefe(k, "lesen", row["firma_id"])
    s = schema_fuer(row["kategorie"], kategorien)
    row["schema"] = {
        "kategorie": s.kategorie, "name": s.name, "bausteine": list(s.bausteine),
        "felder": [asdict(f) for f in s.felder], "begriffe": dict(s.begriffe),
    }
    return row

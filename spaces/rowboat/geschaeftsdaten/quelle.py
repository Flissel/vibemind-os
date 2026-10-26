"""Datenquelle des Geschaeftsdaten-Laufs: Datentypen, Protokoll und `PsqlQuelle`.

`PsqlQuelle` fragt ueber `docker exec <container> psql -h 127.0.0.1 -U rowboat_leser` ab (trust nur
fuer 127.0.0.1 im Container, kein Passwort). Die SQL geht ueber stdin, die Antwort ist eine
JSON-Zeile (`coalesce(json_agg(t), '[]')`). Es wird ausschliesslich `select` gesendet; Schema-Namen
und IDs werden vor jeder Interpolation gegen feste Muster geprueft. Fehlermeldungen enthalten nie
den stderr von psql (er kann Daten enthalten), nur den Exit-Code.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Protocol

SCHEMA_MUSTER = re.compile(r"^sales(_[a-z][a-z0-9_]{0,30})?$")
UUID_MUSTER = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TEST_SCHEMA = "sales_test"
ZEITLIMIT_S = 120


@dataclass(frozen=True)
class Kontakt:
    id: str
    gesperrt: bool
    archiviert: bool
    name: str | None
    company: str | None
    title: str | None
    status: str | None
    consent_status: str | None
    source: str | None
    email_klein: str | None
    art: str | None
    bereiche: tuple[str, ...]
    created_at: datetime
    updated_at: datetime | None


@dataclass(frozen=True)
class Ereignis:
    lead_id: str
    typ: str
    zeit: datetime
    termin_uid: str | None
    termin_datum: str | None
    termin_uhrzeit: str | None
    empfohlen_von_name: str | None
    empfohlen_von_lead: str | None


@dataclass(frozen=True)
class Versand:
    newsletter: str
    erster_versand: datetime
    empfaenger: int


@dataclass(frozen=True)
class NewsletterInhalt:
    id: str
    mandant: str
    titel: str
    format: str
    felder: dict
    bloecke: dict | None
    seite: dict | None


@dataclass(frozen=True)
class Medium:
    art: Literal["reel", "podcast"]
    mandant: str
    titel: str
    dauer_s: float | None
    fertig_am: datetime


class DbFehler(Exception):
    """Abfrage gescheitert (psql-Fehler, Zeitueberschreitung, unlesbare Antwort). Ohne Daten im Text."""


class Quelle(Protocol):
    def marken(self) -> dict[str, str]: ...
    def hat_sichten(self, schema: str) -> bool: ...
    def kontakte(self, schema: str) -> list[Kontakt]: ...
    def ereignisse(self, schema: str) -> list[Ereignis]: ...
    def versand(self, schema: str) -> list[Versand]: ...
    def newsletter_inhalte(self, ids: list[str]) -> list[NewsletterInhalt]: ...
    def medien(self, von: datetime, bis: datetime) -> list[Medium]: ...


def _schema(schema: str) -> str:
    if not isinstance(schema, str) or not SCHEMA_MUSTER.match(schema) or schema == TEST_SCHEMA:
        raise ValueError("ungueltiges Laden-Schema")
    return schema


def _zeitpunkt(wert: datetime) -> str:
    if not isinstance(wert, datetime) or wert.tzinfo is None:
        raise ValueError("Zeitpunkt muss tz-aware sein")
    return wert.isoformat()


def _zeit(roh: Any) -> datetime:
    t = datetime.fromisoformat(roh)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _zeit_oder_none(roh: Any) -> datetime | None:
    return None if roh is None else _zeit(roh)


def _text(roh: Any) -> str | None:
    return None if roh is None else str(roh)


def _kontakt(z: dict) -> Kontakt:
    bereiche = z.get("bereiche")
    return Kontakt(
        id=str(z["id"]), gesperrt=bool(z["gesperrt"]), archiviert=bool(z["archiviert"]),
        name=_text(z.get("name")), company=_text(z.get("company")), title=_text(z.get("title")),
        status=_text(z.get("status")), consent_status=_text(z.get("consent_status")),
        source=_text(z.get("source")), email_klein=_text(z.get("email_klein")), art=_text(z.get("art")),
        bereiche=tuple(b for b in bereiche if isinstance(b, str)) if isinstance(bereiche, list) else (),
        created_at=_zeit(z["created_at"]), updated_at=_zeit_oder_none(z.get("updated_at")))


def _ereignis(z: dict) -> Ereignis:
    return Ereignis(
        lead_id=str(z["lead_id"]), typ=str(z["typ"]), zeit=_zeit(z["zeit"]),
        termin_uid=_text(z.get("termin_uid")), termin_datum=_text(z.get("termin_datum")),
        termin_uhrzeit=_text(z.get("termin_uhrzeit")), empfohlen_von_name=_text(z.get("empfohlen_von_name")),
        empfohlen_von_lead=_text(z.get("empfohlen_von_lead")))


def _objekt_oder_none(roh: Any) -> dict | None:
    return roh if isinstance(roh, dict) else None


class PsqlQuelle:
    """Quelle ueber `docker exec ... psql` als `rowboat_leser`."""

    def __init__(self, container: str = "debian-supabase-db-1",
                 laeufer: Callable[..., Any] = subprocess.run) -> None:
        self._container = container
        self._laeufer = laeufer

    def _argv(self) -> list[str]:
        return ["docker", "exec", "-i", self._container, "psql", "-h", "127.0.0.1", "-U", "rowboat_leser",
                "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-At", "-f", "-"]

    def _zeilen(self, sql: str) -> list[dict]:
        abfrage = f"select coalesce(json_agg(t), '[]'::json) from ({sql}) t;\n"
        try:
            r = self._laeufer(self._argv(), input=abfrage, capture_output=True, text=True,
                              encoding="utf-8", timeout=ZEITLIMIT_S, check=False)
        except subprocess.TimeoutExpired:
            raise DbFehler("Zeitueberschreitung") from None
        except OSError as e:
            raise DbFehler(f"Aufruf gescheitert ({type(e).__name__})") from None
        if r.returncode != 0:
            raise DbFehler(f"psql Exit {r.returncode}")
        try:
            daten = json.loads((r.stdout or "").strip())
        except ValueError:
            raise DbFehler("Antwort ist kein JSON") from None
        if not isinstance(daten, list) or not all(isinstance(z, dict) for z in daten):
            raise DbFehler("Antwort hat falsche Form")
        return daten

    def _umwandeln(self, sql: str, funktion: Callable[[dict], Any]) -> list:
        zeilen = self._zeilen(sql)
        try:
            return [funktion(z) for z in zeilen]
        except (KeyError, TypeError, ValueError) as e:
            raise DbFehler(f"Antwort unlesbar ({type(e).__name__})") from None

    def marken(self) -> dict[str, str]:
        return {str(z["id"]): str(z["name"])
                for z in self._umwandeln("select id, name from marketing.mandanten", dict)}

    def hat_sichten(self, schema: str) -> bool:
        s = _schema(schema)
        # Ohne USAGE auf dem Schema (neuer Laden, Rollen-Skript noch nicht eingespielt) wuerde to_regclass
        # mit "permission denied for schema" scheitern; dann gilt der Laden als "ohne Sichten". Getrennte
        # WHEN-Zweige, weil SQL bei AND keine Auswertungsreihenfolge zusichert (has_schema_privilege wirft
        # bei fehlendem Schema).
        zeilen = self._zeilen(
            f"select case when not exists (select 1 from pg_namespace where nspname = '{s}') then false "
            f"when not has_schema_privilege('{s}', 'usage') then false "
            f"else to_regclass('{s}.rowboat_kontakte') is not null end as da")
        return bool(zeilen and zeilen[0].get("da"))

    def kontakte(self, schema: str) -> list[Kontakt]:
        s = _schema(schema)
        return self._umwandeln(
            "select id, gesperrt, archiviert, name, company, title, status, consent_status, source, "
            "email_klein, art, bereiche, created_at, updated_at "
            f"from {s}.rowboat_kontakte order by created_at, id", _kontakt)

    def ereignisse(self, schema: str) -> list[Ereignis]:
        s = _schema(schema)
        return self._umwandeln(
            "select lead_id, typ, zeit, termin_uid, termin_datum, termin_uhrzeit, empfohlen_von_name, "
            f"empfohlen_von_lead from {s}.rowboat_ereignisse order by zeit, lead_id", _ereignis)

    def versand(self, schema: str) -> list[Versand]:
        s = _schema(schema)
        return self._umwandeln(
            f"select newsletter, erster_versand, empfaenger from {s}.rowboat_newsletter order by newsletter",
            lambda z: Versand(str(z["newsletter"]), _zeit(z["erster_versand"]), int(z["empfaenger"])))

    def newsletter_inhalte(self, ids: list[str]) -> list[NewsletterInhalt]:
        ids = sorted(set(ids))
        if not all(isinstance(i, str) and UUID_MUSTER.match(i) for i in ids):
            raise ValueError("ungueltige Newsletter-ID")
        if not ids:
            return []
        liste = ", ".join(f"'{i}'" for i in ids)
        return self._umwandeln(
            "select i.id, i.mandant, i.titel, f.format, f.felder, f.bloecke, f.seite "
            "from marketing.inhalte i join marketing.inhalt_fassungen f "
            "on f.inhalt = i.id and f.fassung = i.freigegebene_fassung "
            f"where i.status = 'freigegeben' and i.art = 'newsletter' and i.id in ({liste}) order by i.id",
            lambda z: NewsletterInhalt(
                id=str(z["id"]), mandant=str(z["mandant"]), titel=str(z["titel"]), format=str(z["format"]),
                felder=z["felder"] if isinstance(z["felder"], dict) else {},
                bloecke=_objekt_oder_none(z.get("bloecke")), seite=_objekt_oder_none(z.get("seite"))))

    def medien(self, von: datetime, bis: datetime) -> list[Medium]:
        a, b = _zeitpunkt(von), _zeitpunkt(bis)
        return self._umwandeln(
            "select 'reel' as art, mandant, titel, dauer_s, geaendert_am as fertig_am "
            "from marketing.video_auftraege where status = 'fertig' and not archiviert "
            f"and geaendert_am >= '{a}'::timestamptz and geaendert_am < '{b}'::timestamptz "
            "union all "
            "select 'podcast' as art, mandant, titel, dauer_s, geaendert_am as fertig_am "
            "from marketing.ton_auftraege where status = 'fertig' "
            f"and geaendert_am >= '{a}'::timestamptz and geaendert_am < '{b}'::timestamptz "
            "order by fertig_am",
            lambda z: Medium(art=z["art"], mandant=str(z["mandant"]), titel=str(z["titel"]),
                             dauer_s=None if z.get("dauer_s") is None else float(z["dauer_s"]),
                             fertig_am=_zeit(z["fertig_am"])))

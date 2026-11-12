"""event-api: duenne FastAPI-Schale ueber der Fachschicht.

Je Anfrage eine Verbindung aus dem Pool (autocommit, also frei); die Fachfunktionen
oeffnen ihre Transaktionen selbst, die API umschliesst sie nie mit einer weiteren.
Fehlerabbildung: NichtGefunden 404, KeinRecht 403, ValueError 422 (Meldung der
Fachschicht, datenfrei), alles andere 500 "interner Fehler". Unerwartete Fehler werden
nur mit Typname und Routenmuster geloggt (nie der echte Pfad mit Token, nie str(e)).
"""
# Ohne `from __future__ import annotations`: FastAPI muss die lokalen
# Annotated-Aliase (Conn, Lesen, Schreiben) zur Laufzeit aufloesen.
import logging
import math
import os
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from spaces.event_management.system import events, firmen, kern, verlauf, zugang
from spaces.event_management.system.bausteine import REGISTRY
from spaces.event_management.system.kategorien import Kategorie, lade_kategorien, waehlbare
from spaces.event_management.system.kontext import KeinRecht, Kontext, NichtGefunden, transaktion
from spaces.event_management.system.mailer import Mailer, SmtpMailer

log = logging.getLogger(__name__)

COOKIE = "event_sitzung"
COOKIE_SEKUNDEN = 12 * 3600
GEHEIMNIS_MIN_BYTES = 32
PORT_VORGABE = 8170
_ANMELDUNG_FEHLGESCHLAGEN = "Anmeldung fehlgeschlagen"
_NICHT_ANGEMELDET = "Nicht angemeldet"
_ZEICHEN_UNGUELTIG = "Ungültige Zeichen"

Verbindung = psycopg.Connection[Any]


# --- Eingaben ------------------------------------------------------------------------


def _sauber(wert: Any) -> None:
    """Wirft ValueError bei NUL, einzelnen Surrogaten (beide machen in psycopg einen
    Datenbankfehler, also 500) und nicht endlichen Zahlen (NaN/Infinity)."""
    if isinstance(wert, str):
        if "\x00" in wert:
            raise ValueError(_ZEICHEN_UNGUELTIG)
        try:
            wert.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError(_ZEICHEN_UNGUELTIG) from None
    elif isinstance(wert, float):
        if not math.isfinite(wert):
            raise ValueError("Zahl ungültig")
    elif isinstance(wert, Mapping):
        for k, v in wert.items():
            _sauber(k)
            _sauber(v)
    elif isinstance(wert, (list, tuple)):
        for v in wert:
            _sauber(v)


class _Streng(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Eingabe(_Streng):
    @model_validator(mode="before")
    @classmethod
    def _eingabe_sauber(cls, daten: Any) -> Any:
        _sauber(daten)
        return daten


class AnmeldenDaten(_Streng):
    # Bewusst ohne _sauber: auch NUL/Surrogate in der Mail ergeben das einheitliche 401.
    mail: str = Field(max_length=320)
    passwort: str = Field(max_length=1024)


class EinloesenDaten(_Eingabe):
    passwort: str | None = Field(default=None, max_length=1024)
    name: str | None = Field(default=None, max_length=200)


class FirmaDaten(_Eingabe):
    name: str = Field(max_length=200)
    kurz: str = Field(max_length=32)


class EinladungDaten(_Eingabe):
    mail: str = Field(max_length=320)
    name: str = Field(max_length=200)
    rolle: str


class RolleDaten(_Eingabe):
    rolle: str


class EventNeu(_Eingabe):
    firma_id: UUID
    kategorie: str
    name: str
    beginn: datetime
    ende: datetime
    ort_adresse: str | None = None
    ort_link: str | None = None
    beschreibung: str | None = None
    metadaten: dict[str, Any] | None = None


class EventAenderung(_Eingabe):
    name: str | None = None
    beginn: datetime | None = None
    ende: datetime | None = None
    ort_adresse: str | None = None
    ort_link: str | None = None
    beschreibung: str | None = None
    metadaten: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _pflicht_nicht_leer(self) -> "EventAenderung":
        for feld in ("name", "beginn", "ende"):
            if feld in self.model_fields_set and getattr(self, feld) is None:
                raise ValueError(f"Feld '{feld}' darf nicht leer sein")
        return self


class StatusDaten(_Eingabe):
    status: str


class KontaktDaten(_Eingabe):
    name: str = Field(max_length=200)
    mail: str | None = Field(default=None, max_length=320)
    telegram: str | None = Field(default=None, max_length=200)


def _text_param(wert: str | None) -> str | None:
    if wert is not None:
        _sauber(wert)
    return wert


# --- Fehlerabbildung -------------------------------------------------------------------


class _Fehlerfang:
    """Letzte Wache: unerwartete Fehler werden 500 ohne Details, nichts geht an den Server.

    Geloggt werden nur Typname, Methode und Routenmuster (``/api/einladung/{token}``,
    nicht der echte Pfad), kein exc_info: psycopg-DETAIL oder SMTP-Fehler koennen
    personenbezogene Daten tragen.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        begonnen = False

        async def senden(nachricht: Message) -> None:
            nonlocal begonnen
            if nachricht["type"] == "http.response.start":
                begonnen = True
            await send(nachricht)

        try:
            await self.app(scope, receive, senden)
        except Exception as e:  # noqa: BLE001 - genau dafuer ist die Wache da
            muster = getattr(scope.get("route"), "path", "(ohne Route)")
            log.error("Unerwarteter Fehler (%s) bei %s %s",
                      type(e).__name__, scope.get("method", ""), muster)
            if not begonnen:
                antwort = JSONResponse({"detail": "interner Fehler"}, status_code=500)
                await antwort(scope, receive, send)


async def _nicht_gefunden(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": "Nicht gefunden"}, status_code=404)


async def _kein_recht(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=403)


async def _zeichen(request: Request, exc: Exception) -> JSONResponse:
    # str(UnicodeError) zitiert das Zeichen und die Stelle der Eingabe.
    return JSONResponse({"detail": _ZEICHEN_UNGUELTIG}, status_code=422)


async def _wertfehler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=422)


async def _validierung(request: Request, exc: Exception) -> JSONResponse:
    """Wie FastAPI, aber ohne ``input``/``ctx``: die Antwort wiederholt keine Eingaben."""
    fehler = exc.errors() if isinstance(exc, RequestValidationError) else []
    return JSONResponse(
        {"detail": [{"loc": list(f.get("loc", ())), "msg": f.get("msg", ""),
                     "type": f.get("type", "")} for f in fehler]},
        status_code=422,
    )


# --- App ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Sitzung:
    conn: Verbindung
    k: Kontext
    person_id: UUID
    cookie: str


def erstelle_app(
    dsn: str, geheimnis: bytes, mailer: Mailer, basis_url: str,
    kategorien_ordner: Path | None = None, *, cookie_sicher: bool = True,
) -> FastAPI:
    """Baut die App. Kategorien werden hier einmal geladen (KategorieFehler bricht ab).

    `basis_url` (fuer Einladungslinks) kommt nur aus der Konfiguration, nie aus der
    Anfrage. Der Pool oeffnet sich im Lifespan (uvicorn, ``with TestClient(app)``).
    """
    if not isinstance(geheimnis, bytes) or len(geheimnis) < GEHEIMNIS_MIN_BYTES:
        raise ValueError(f"Geheimnis braucht mindestens {GEHEIMNIS_MIN_BYTES} Bytes")
    if not isinstance(basis_url, str) or not basis_url.startswith(("https://", "http://")):
        raise ValueError("basis_url muss mit http:// oder https:// beginnen")
    kats: dict[str, Kategorie] = lade_kategorien(kategorien_ordner)
    pool: ConnectionPool[Any] = ConnectionPool(
        dsn, min_size=1, max_size=10, open=False, kwargs={"autocommit": True},
        name="event-api",
    )

    @asynccontextmanager
    async def lebenszyklus(app: FastAPI) -> AsyncIterator[None]:
        pool.open(wait=True, timeout=30)
        try:
            yield
        finally:
            pool.close()

    app = FastAPI(title="event-api", lifespan=lebenszyklus,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.pool = pool
    app.add_middleware(_Fehlerfang)
    app.add_exception_handler(NichtGefunden, _nicht_gefunden)
    app.add_exception_handler(KeinRecht, _kein_recht)
    app.add_exception_handler(UnicodeError, _zeichen)
    app.add_exception_handler(ValueError, _wertfehler)
    app.add_exception_handler(RequestValidationError, _validierung)

    # --- Abhaengigkeiten ------------------------------------------------------------

    def verbindung() -> Iterator[Verbindung]:
        with pool.connection() as conn:
            yield conn

    Conn = Annotated[Verbindung, Depends(verbindung)]

    def sitzung(request: Request, conn: Conn) -> Sitzung:
        wert = request.cookies.get(COOKIE, "")
        person_id = zugang.cookie_pruefen(wert, geheimnis, conn) if wert else None
        if person_id is None:
            raise HTTPException(401, _NICHT_ANGEMELDET)
        try:
            k = firmen.kontext_fuer_person(conn, person_id)
        except NichtGefunden:
            raise HTTPException(401, _NICHT_ANGEMELDET) from None
        return Sitzung(conn, k, person_id, wert)

    def schreibend(
        s: Annotated[Sitzung, Depends(sitzung)],
        x_csrf: Annotated[str | None, Header(alias="X-CSRF")] = None,
    ) -> Sitzung:
        if not x_csrf or not zugang.csrf_ok(s.cookie, x_csrf, geheimnis):
            raise HTTPException(403, "CSRF-Token fehlt oder ist falsch")
        return s

    Lesen = Annotated[Sitzung, Depends(sitzung)]
    Schreiben = Annotated[Sitzung, Depends(schreibend)]

    def cookie_setzen(response: Response, wert: str) -> None:
        response.set_cookie(COOKIE, wert, max_age=COOKIE_SEKUNDEN, path="/",
                            secure=cookie_sicher, httponly=True, samesite="lax")

    def firmen_mit_rolle(conn: Verbindung, k: Kontext) -> list[dict[str, Any]]:
        return [{**f, "rolle": k.rollen.get(f["id"])} for f in firmen.firmen(conn, k)]

    def ich_daten(conn: Verbindung, k: Kontext, person_id: UUID, cookie: str) -> dict[str, Any]:
        with transaktion(conn, k) as cur:
            cur.execute("select id, name, mail from event.person where id = %s", (person_id,))
            person = cur.fetchone()
        if person is None:
            raise NichtGefunden("Person nicht gefunden")
        return {"person": person, "betreiber": k.betreiber,
                "firmen": firmen_mit_rolle(conn, k), "csrf": zugang.csrf_token(cookie, geheimnis)}

    def baustein_name(baustein: str) -> str:
        if baustein not in REGISTRY:
            raise NichtGefunden("Baustein nicht gefunden")
        return baustein

    # --- Zugang ---------------------------------------------------------------------

    @app.post("/api/anmelden")
    def anmelden(daten: AnmeldenDaten, response: Response, conn: Conn) -> dict[str, Any]:
        try:
            person_id = zugang.anmelden(conn, daten.mail, daten.passwort)
        except (psycopg.DataError, UnicodeError):
            person_id = None  # NUL/Surrogat in der Mail: wie jede andere Fehlanmeldung
        if person_id is None:
            raise HTTPException(401, _ANMELDUNG_FEHLGESCHLAGEN)
        try:
            with transaktion(conn, Kontext.system()) as cur:
                cur.execute("select sitzungs_version from event.person where id = %s",
                            (person_id,))
                row = cur.fetchone()
            if row is None:
                raise NichtGefunden("Person nicht gefunden")
            k = firmen.kontext_fuer_person(conn, person_id)
        except NichtGefunden:
            raise HTTPException(401, _ANMELDUNG_FEHLGESCHLAGEN) from None
        wert = zugang.cookie_ausstellen(person_id, int(row["sitzungs_version"]), geheimnis)
        cookie_setzen(response, wert)
        return ich_daten(conn, k, person_id, wert)

    @app.post("/api/abmelden")
    def abmelden(response: Response, s: Schreiben) -> dict[str, Any]:
        zugang.abmelden_ueberall(s.conn, s.person_id)
        response.delete_cookie(COOKIE, path="/", secure=cookie_sicher, httponly=True,
                               samesite="lax")
        return {"ok": True}

    @app.get("/api/ich")
    def ich(s: Lesen) -> dict[str, Any]:
        return ich_daten(s.conn, s.k, s.person_id, s.cookie)

    @app.get("/api/einladung/{token}")
    def einladung_ansehen(token: str, conn: Conn) -> dict[str, Any]:
        return zugang.einladung_info(conn, token)

    @app.post("/api/einladung/{token}")
    def einladung_einloesen(token: str, daten: EinloesenDaten, conn: Conn) -> dict[str, Any]:
        # Einloesen meldet nicht an: der Token ersetzt kein Passwort (bestehende Konten).
        zugang.einladung_einloesen(conn, token, daten.passwort or "", daten.name)
        return {"eingeloest": True}

    # --- Firmen und Mitglieder ------------------------------------------------------

    @app.get("/api/firmen")
    def firmen_liste(s: Lesen) -> list[dict[str, Any]]:
        return firmen_mit_rolle(s.conn, s.k)

    @app.post("/api/firmen", status_code=201)
    def firma_anlegen(daten: FirmaDaten, s: Schreiben) -> dict[str, Any]:
        return firmen.firma_anlegen(s.conn, s.k, daten.name, daten.kurz)

    @app.get("/api/firmen/{firma_id}/mitglieder")
    def mitglieder(firma_id: UUID, s: Lesen) -> list[dict[str, Any]]:
        return firmen.mitglieder(s.conn, s.k, firma_id)

    @app.post("/api/firmen/{firma_id}/einladungen", status_code=201)
    def einladen(firma_id: UUID, daten: EinladungDaten, s: Schreiben) -> dict[str, Any]:
        zugang.einladen(s.conn, s.k, mailer, daten.mail, daten.name, firma_id, daten.rolle,
                        basis_url)
        return {"eingeladen": True}

    @app.patch("/api/firmen/{firma_id}/mitglieder/{person_id}")
    def rolle_aendern(
        firma_id: UUID, person_id: UUID, daten: RolleDaten, s: Schreiben,
    ) -> dict[str, Any]:
        # PATCH aendert nur bestehende Mitgliedschaften; Aufnehmen geht nur per Einladung.
        if not any(m["person_id"] == person_id
                   for m in firmen.mitglieder(s.conn, s.k, firma_id)):
            raise NichtGefunden("Mitgliedschaft nicht gefunden")
        firmen.mitgliedschaft_setzen(s.conn, s.k, person_id, firma_id, daten.rolle)
        return {"person_id": person_id, "firma_id": firma_id, "rolle": daten.rolle}

    @app.delete("/api/firmen/{firma_id}/mitglieder/{person_id}")
    def mitglied_entfernen(firma_id: UUID, person_id: UUID, s: Schreiben) -> dict[str, Any]:
        firmen.mitglied_entfernen(s.conn, s.k, person_id, firma_id)
        return {"ok": True}

    # --- Kategorien -----------------------------------------------------------------

    @app.get("/api/kategorien")
    def kategorien_liste(s: Lesen) -> list[dict[str, Any]]:
        return [{"id": k.id, "name": k.name, "eltern": k.eltern} for k in waehlbare(kats)]

    @app.get("/api/kategorien/{kategorie_id}/schema")
    def kategorie_schema(kategorie_id: str, s: Lesen) -> dict[str, Any]:
        kat = kats.get(kategorie_id)
        if kat is None or kat.abstrakt:
            raise NichtGefunden("Kategorie nicht gefunden")
        sch = events.schema_fuer(kategorie_id, kats)
        return {"kategorie": sch.kategorie, "name": sch.name, "kette": list(sch.kette),
                "bausteine": list(sch.bausteine), "felder": [asdict(f) for f in sch.felder],
                "begriffe": dict(sch.begriffe),
                "eckdaten_pflicht": sorted(sch.eckdaten_pflicht)}

    # --- Events ---------------------------------------------------------------------

    @app.get("/api/events")
    def katalog(
        s: Lesen, firma: UUID | None = None, kategorie: str | None = None,
        suche: str | None = None,
    ) -> list[dict[str, Any]]:
        return events.katalog(s.conn, s.k, firma_id=firma, kategorie=_text_param(kategorie),
                              suche=_text_param(suche), kategorien=kats)

    @app.post("/api/events", status_code=201)
    def event_anlegen(daten: EventNeu, s: Schreiben) -> dict[str, Any]:
        return events.event_anlegen(
            s.conn, s.k, daten.firma_id, daten.kategorie, daten.name, daten.beginn, daten.ende,
            daten.ort_adresse, daten.ort_link, daten.beschreibung, daten.metadaten,
            kategorien=kats,
        )

    @app.get("/api/events/{event_id}")
    def event_lesen(event_id: UUID, s: Lesen) -> dict[str, Any]:
        return events.event(s.conn, s.k, event_id, kategorien=kats)

    @app.patch("/api/events/{event_id}")
    def event_aendern(event_id: UUID, daten: EventAenderung, s: Schreiben) -> dict[str, Any]:
        felder = daten.model_dump(exclude_unset=True)
        if not felder:
            raise ValueError("Keine Werte zum Ändern")
        return events.event_aendern(s.conn, s.k, event_id, kategorien=kats, **felder)

    @app.post("/api/events/{event_id}/status")
    def event_status(event_id: UUID, daten: StatusDaten, s: Schreiben) -> dict[str, Any]:
        return events.event_status(s.conn, s.k, event_id, daten.status)

    @app.delete("/api/events/{event_id}")
    def event_loeschen(event_id: UUID, s: Schreiben) -> dict[str, Any]:
        events.event_loeschen(s.conn, s.k, event_id)
        return {"ok": True}

    # --- Bausteine und Verlauf ------------------------------------------------------

    @app.get("/api/events/{event_id}/b/{baustein}")
    def baustein_liste(event_id: UUID, baustein: str, s: Lesen) -> list[dict[str, Any]]:
        return kern.liste(s.conn, s.k, event_id, baustein_name(baustein), kategorien=kats)

    @app.post("/api/events/{event_id}/b/{baustein}", status_code=201)
    def baustein_anlegen(
        event_id: UUID, baustein: str, werte: Annotated[dict[str, Any], Body()], s: Schreiben,
    ) -> dict[str, Any]:
        name = baustein_name(baustein)
        _sauber(werte)
        return kern.anlegen(s.conn, s.k, event_id, name, werte, kategorien=kats)

    @app.patch("/api/events/{event_id}/b/{baustein}/{zeile_id}")
    def baustein_aendern(
        event_id: UUID, baustein: str, zeile_id: UUID,
        werte: Annotated[dict[str, Any], Body()], s: Schreiben,
    ) -> dict[str, Any]:
        name = baustein_name(baustein)
        _sauber(werte)
        return kern.aendern(s.conn, s.k, event_id, name, zeile_id, werte, kategorien=kats)

    @app.delete("/api/events/{event_id}/b/{baustein}/{zeile_id}")
    def baustein_loeschen(
        event_id: UUID, baustein: str, zeile_id: UUID, s: Schreiben,
    ) -> dict[str, Any]:
        kern.loeschen(s.conn, s.k, event_id, baustein_name(baustein), zeile_id, kategorien=kats)
        return {"ok": True}

    @app.get("/api/events/{event_id}/verlauf")
    def verlauf_lesen(
        event_id: UUID, s: Lesen, seit: datetime | None = None, actor: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[dict[str, Any]]:
        return verlauf.lesen(s.conn, s.k, event_id,
                             seit=events.zeit(seit, "seit") if seit is not None else None,
                             actor=_text_param(actor), limit=limit)

    # --- Kontakte -------------------------------------------------------------------

    @app.get("/api/firmen/{firma_id}/kontakte")
    def kontakte(firma_id: UUID, s: Lesen, suche: str | None = None) -> list[dict[str, Any]]:
        return kern.kontakte(s.conn, s.k, firma_id, suche=_text_param(suche))

    @app.post("/api/firmen/{firma_id}/kontakte", status_code=201)
    def kontakt_anlegen(firma_id: UUID, daten: KontaktDaten, s: Schreiben) -> dict[str, Any]:
        return kern.kontakt_anlegen(s.conn, s.k, firma_id, daten.name, daten.mail,
                                    daten.telegram)

    return app


# --- Kommandozeile -------------------------------------------------------------------


@dataclass(frozen=True)
class Konfiguration:
    dsn: str
    geheimnis: bytes
    basis_url: str
    port: int
    cookie_sicher: bool

    def __repr__(self) -> str:  # DSN und Geheimnis nie ausgeben
        return f"Konfiguration(basis_url={self.basis_url!r}, port={self.port})"


def _datei_lesen(pfad: str, name: str) -> str:
    try:
        # strip(): Dateien von Windows enden oft mit CRLF.
        wert = Path(pfad).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise ValueError(f"{name} nicht lesbar") from None
    if not wert:
        raise ValueError(f"{name} ist leer")
    return wert


def konfiguration(umgebung: Mapping[str, str] | None = None) -> Konfiguration:
    """Liest die EVENT_*-Werte; Fehlermeldungen nennen nur den Variablennamen."""
    env = os.environ if umgebung is None else umgebung
    dsn_datei = env.get("EVENT_APP_DSN_FILE", "").strip()
    if dsn_datei:
        dsn = _datei_lesen(dsn_datei, "EVENT_APP_DSN_FILE")
    else:
        dsn = env.get("EVENT_APP_DSN", "").strip()
        if not dsn:
            raise ValueError("EVENT_APP_DSN_FILE oder EVENT_APP_DSN fehlt")
    geheim_datei = env.get("EVENT_SITZUNG_GEHEIMNIS_FILE", "").strip()
    if not geheim_datei:
        raise ValueError("EVENT_SITZUNG_GEHEIMNIS_FILE fehlt")
    geheimnis = _datei_lesen(geheim_datei, "EVENT_SITZUNG_GEHEIMNIS_FILE").encode("utf-8")
    basis_url = env.get("EVENT_BASIS_URL", "").strip()
    if not basis_url.startswith(("https://", "http://")):
        raise ValueError("EVENT_BASIS_URL fehlt oder ist ungueltig")
    try:
        port = int(env.get("EVENT_API_PORT", "").strip() or PORT_VORGABE)
    except ValueError:
        raise ValueError("EVENT_API_PORT ungueltig") from None
    if not 0 < port < 65536:
        raise ValueError("EVENT_API_PORT ungueltig")
    cookie_sicher = env.get("EVENT_COOKIE_UNSICHER", "").strip() != "1"
    return Konfiguration(dsn, geheimnis, basis_url, port, cookie_sicher)


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    k = konfiguration()
    app = erstelle_app(k.dsn, k.geheimnis, SmtpMailer.aus_umgebung(), k.basis_url,
                       cookie_sicher=k.cookie_sicher)
    # Kein Access-Log: der Pfad von /api/einladung/{token} enthaelt das Token.
    uvicorn.run(app, host="0.0.0.0", port=k.port, access_log=False)


if __name__ == "__main__":
    main()

"""event-mcp ueber den echten HTTP-Weg (Starlette-TestClient gegen erstelle_mcp_app).

Rohe JSON-RPC-POSTs an /mcp (stateless, JSON-Antworten); der Bearer-Schluessel wird je
Anfrage gelesen. Die App laeuft mit der App-DSN (`app_dsn`), Testdaten per `db_admin`.
"""
from __future__ import annotations

import itertools
import json
import logging
import threading
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest
from starlette.testclient import TestClient

from spaces.event_management.system import marke, mcp_server, schluessel, werkzeuge
from spaces.event_management.system.kontext import KeinRecht, NichtGefunden
from spaces.event_management.system.mailer import SmtpMailer, SpeicherMailer

BASIS = "http://127.0.0.1:8171"
BEGINN = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)
_IDS = itertools.count(1)


def _firma(admin: Any, kurz: str) -> UUID:
    return admin.execute(
        "insert into event.firma (name, kurz) values (%s, %s) returning id",
        ("Beispiel " + kurz, kurz),
    ).fetchone()["id"]


def _event(admin: Any, firma: UUID, kategorie: str, name: str) -> UUID:
    return admin.execute(
        "insert into event.event (firma_id, kategorie, name, beginn, ende)"
        " values (%s, %s, %s, %s, %s) returning id",
        (firma, kategorie, name, BEGINN, BEGINN + timedelta(hours=3)),
    ).fetchone()["id"]


def _person(admin: Any, mail: str, *, betreiber: bool = False) -> UUID:
    return admin.execute(
        "insert into event.person (mail, name, betreiber) values (%s, 'Erika Beispiel', %s)"
        " returning id",
        (mail, betreiber),
    ).fetchone()["id"]


def _schluessel(admin: Any, art: str, **argumente: Any) -> str:
    _, klartext = schluessel.ausstellen(admin, art=art, bezeichnung="Test", **argumente)
    return klartext


def _rpc(client: TestClient, key: str | None, methode: str, params: dict[str, Any] | None = None,
         *, header: str | None = None) -> Any:
    kopf = {"Accept": "application/json, text/event-stream",
            "Content-Type": "application/json"}
    if header is not None:
        kopf["Authorization"] = header
    elif key is not None:
        kopf["Authorization"] = f"Bearer {key}"
    nachricht: dict[str, Any] = {"jsonrpc": "2.0", "id": next(_IDS), "method": methode}
    if params is not None:
        nachricht["params"] = params
    return client.post("/mcp", headers=kopf, content=json.dumps(nachricht))


def _werkzeuge(client: TestClient, key: str) -> dict[str, Any]:
    antwort = _rpc(client, key, "tools/list", {})
    assert antwort.status_code == 200, antwort.text
    return {t["name"]: t for t in antwort.json()["result"]["tools"]}


def _aufruf(client: TestClient, key: str, name: str, argumente: dict[str, Any]) -> dict[str, Any]:
    antwort = _rpc(client, key, "tools/call", {"name": name, "arguments": argumente})
    assert antwort.status_code == 200, antwort.text
    ergebnis: dict[str, Any] = antwort.json()["result"]
    return ergebnis


def _text(ergebnis: dict[str, Any]) -> str:
    assert [c["type"] for c in ergebnis["content"]] == ["text"]
    text: str = ergebnis["content"][0]["text"]
    return text


@pytest.fixture
def welt(db_admin: Any) -> dict[str, Any]:
    a = _firma(db_admin, "firma-a")
    b = _firma(db_admin, "firma-b")
    lea = _person(db_admin, "lea@example.org")
    bea = _person(db_admin, "bea@example.org", betreiber=True)
    db_admin.execute("insert into event.mitgliedschaft values (%s, %s, 'leitung')", (lea, b))
    return {
        "a": a, "b": b, "lea": lea, "bea": bea,
        "meeting": _event(db_admin, a, "team_meeting", "Teamrunde Beispiel"),
        "festival": _event(db_admin, b, "festival", "Testfestival"),
    }


@pytest.fixture
def client(app_dsn: str) -> Iterator[TestClient]:
    app = mcp_server.erstelle_mcp_app(app_dsn)
    with TestClient(app, base_url=BASIS) as c:
        yield c


# --- Zugang -------------------------------------------------------------------------


def test_gesund_ohne_schluessel(client):
    antwort = client.get("/gesund")
    assert antwort.status_code == 200 and antwort.json() == {"ok": True}


def test_ohne_schluessel_401(client, welt):
    for header in (None, "", "Bearer", "Bearer ", "Basic abc", "evk_ohne_schema"):
        antwort = _rpc(client, None, "tools/list", {}, header=header)
        assert antwort.status_code == 401, header
        assert antwort.headers["www-authenticate"].startswith("Bearer")
        assert "result" not in antwort.text


def test_falscher_schluessel_401(client, db_admin, welt):
    falsch = "evk_" + "A" * 43
    antwort = _rpc(client, falsch, "tools/list", {})
    assert antwort.status_code == 401 and falsch not in antwort.text

    key = _schluessel(db_admin, "event", event_id=welt["meeting"])
    assert _rpc(client, key, "tools/list", {}).status_code == 200
    sid = schluessel.pruefen(db_admin, key).id
    schluessel.widerrufen(db_admin, sid)
    antwort = _rpc(client, key, "tools/call",
                   {"name": "aufgaben_anlegen", "arguments": {"titel": "Nach Widerruf"}})
    assert antwort.status_code == 401 and key not in antwort.text
    assert db_admin.execute("select count(*) as n from event.aufgabe").fetchone()["n"] == 0


def test_fremder_host_abgelehnt(app_dsn, db_admin, welt):
    key = _schluessel(db_admin, "event", event_id=welt["meeting"])
    app = mcp_server.erstelle_mcp_app(app_dsn)
    with TestClient(app, base_url="http://boese.example.org") as c:
        assert _rpc(c, key, "tools/list", {}).status_code == 421
    with TestClient(mcp_server.erstelle_mcp_app(app_dsn), base_url="http://event-mcp:8171") as c:
        assert _rpc(c, key, "tools/list", {}).status_code == 200


# --- Werkzeuge ----------------------------------------------------------------------


def test_initialize_und_tools_list(client, db_admin, welt):
    key = _schluessel(db_admin, "event", event_id=welt["meeting"])
    antwort = _rpc(client, key, "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"}})
    assert antwort.status_code == 200, antwort.text
    ergebnis = antwort.json()["result"]
    assert ergebnis["serverInfo"]["name"] == "event-mcp"
    assert "tools" in ergebnis["capabilities"]


def test_tools_list_je_schluessel(client, db_admin, welt):
    event_key = _schluessel(db_admin, "event", event_id=welt["meeting"])
    leitung_key = _schluessel(db_admin, "leitung", person_id=welt["lea"])
    lesen_key = _schluessel(db_admin, "betreiber_lesen", person_id=welt["bea"])
    melden_key = _schluessel(db_admin, "melden")

    ev = _werkzeuge(client, event_key)
    assert "protokoll_anlegen" in ev and "budget_liste" not in ev
    assert "marke" not in ev["protokoll_anlegen"]["inputSchema"]["properties"]
    assert ev["protokoll_anlegen"]["inputSchema"]["additionalProperties"] is False
    assert ev["protokoll_liste"]["annotations"]["readOnlyHint"] is True
    assert ev["protokoll_anlegen"]["annotations"]["readOnlyHint"] is False
    assert ev["ablauf_liste"]["description"] == "Tagesordnung des Events anzeigen"

    ld = _werkzeuge(client, leitung_key)
    assert "budget_liste" in ld and "buehnen_anlegen" in ld and "event_waehlen" in ld
    assert "marke" in ld["aufgaben_anlegen"]["inputSchema"]["required"]

    lesen = _werkzeuge(client, lesen_key)
    assert "aufgaben_liste" in lesen
    assert not [n for n in lesen if n.endswith(("_anlegen", "_aendern", "_loeschen"))]

    assert _werkzeuge(client, melden_key) == {}


def test_tool_call_schreibt_und_nennt_event(client, db_admin, db_app, welt):
    key = _schluessel(db_admin, "event", event_id=welt["meeting"])
    ergebnis = _aufruf(client, key, "aufgaben_anlegen", {"titel": "Raum buchen"})
    assert ergebnis["isError"] is False
    daten = json.loads(_text(ergebnis))
    assert daten["event"] == {"name": "Teamrunde Beispiel", "firma": "Beispiel firma-a"}
    assert daten["zeile"]["titel"] == "Raum buchen"
    zeile = db_admin.execute("select event_id from event.aufgabe where id = %s",
                             (daten["zeile"]["id"],)).fetchone()
    assert zeile["event_id"] == welt["meeting"]
    verlauf = db_admin.execute("select quelle, actor from event.verlauf where event_id = %s",
                               (welt["meeting"],)).fetchone()
    assert verlauf == {"quelle": "bot", "actor": "bot:event"}

    # Leitung waehlt per Werkzeug und schreibt mit der Marke ins richtige Event.
    leitung_key = _schluessel(db_admin, "leitung", person_id=welt["lea"])
    gewaehlt = json.loads(_text(_aufruf(client, leitung_key, "event_waehlen",
                                        {"suche": "Testfestival"})))
    neu = _aufruf(client, leitung_key, "buehnen_anlegen",
                  {"marke": gewaehlt["marke"], "name": "Hauptbuehne"})
    assert json.loads(_text(neu))["event"] == {"name": "Testfestival",
                                                "firma": "Beispiel firma-b"}


def test_fehler_als_werkzeug_ergebnis(client, db_admin, welt):
    key = _schluessel(db_admin, "event", event_id=welt["meeting"])
    unbekannt = _aufruf(client, key, "budget_liste", {})
    assert unbekannt["isError"] is True and _text(unbekannt) == "nicht gefunden"

    wert = _aufruf(client, key, "aufgaben_anlegen", {"titel": "x", "event_id": str(welt["b"])})
    assert wert["isError"] is True and str(welt["b"]) not in _text(wert)

    leitung_key = _schluessel(db_admin, "leitung", person_id=welt["lea"])
    marke_falsch = _aufruf(client, leitung_key, "aufgaben_liste", {"marke": "mk_aaaaaaaaaaaa"})
    assert marke_falsch["isError"] is True and _text(marke_falsch) == marke.MARKE_UNGUELTIG

    # NichtGefunden der Fachschicht: nur "nicht gefunden".
    fehlt = _aufruf(client, key, "aufgaben_aendern", {"zeile": str(welt["b"]), "titel": "x"})
    assert fehlt["isError"] is True and _text(fehlt) == "nicht gefunden"
    # ValueError: eigener Text.
    leer = _aufruf(client, key, "aufgaben_anlegen", {})
    assert leer["isError"] is True and _text(leer) == "Pflichtargument 'titel' fehlt"


def test_kein_recht_als_keine_berechtigung(client, db_admin, welt, monkeypatch):
    key = _schluessel(db_admin, "event", event_id=welt["meeting"])

    def verboten(*_a: Any, **_k: Any) -> Any:
        raise KeinRecht("Keine Berechtigung fuer baustein_schreiben")

    monkeypatch.setattr(werkzeuge, "ausfuehren", verboten)
    ergebnis = _aufruf(client, key, "aufgaben_liste", {})
    assert ergebnis["isError"] is True and _text(ergebnis) == "keine Berechtigung"

    monkeypatch.setattr(werkzeuge, "katalog", verboten)
    antwort = _rpc(client, key, "tools/list", {})
    assert antwort.status_code == 200
    assert antwort.json()["error"]["message"] == "keine Berechtigung"


def test_tools_list_fehlertexte(client, db_admin, welt, monkeypatch):
    key = _schluessel(db_admin, "event", event_id=welt["meeting"])
    for fehler, erwartet in ((NichtGefunden("Event nicht gefunden"), "nicht gefunden"),
                             (RuntimeError("geheim"), "interner Fehler")):
        def kaputt(*_a: Any, _f: Exception = fehler, **_k: Any) -> Any:
            raise _f

        monkeypatch.setattr(werkzeuge, "katalog", kaputt)
        antwort = _rpc(client, key, "tools/list", {})
        assert antwort.status_code == 200
        assert antwort.json()["error"]["message"] == erwartet
    # Ein geloeschtes Event macht seinen Schluessel ungueltig: schon das Tor sagt 401.
    db_admin.execute("delete from event.event where id = %s", (welt["meeting"],))
    assert _rpc(client, key, "tools/list", {}).status_code == 401


def test_interner_fehler_ohne_details(client, db_admin, welt, monkeypatch, caplog):
    key = _schluessel(db_admin, "event", event_id=welt["meeting"])

    def kaputt(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise RuntimeError("geheimes Detail " + key)

    monkeypatch.setattr(werkzeuge, "ausfuehren", kaputt)
    with caplog.at_level(logging.DEBUG, logger="spaces.event_management"):
        ergebnis = _aufruf(client, key, "aufgaben_liste", {})
    assert ergebnis["isError"] is True and _text(ergebnis) == "interner Fehler"
    assert "geheimes Detail" not in caplog.text and key not in caplog.text
    assert "RuntimeError" in caplog.text


def test_logs_ohne_schluessel_und_kundentext(client, db_admin, welt, caplog):
    key = _schluessel(db_admin, "event", event_id=welt["meeting"])
    with caplog.at_level(logging.INFO):
        _werkzeuge(client, key)
        _aufruf(client, key, "aufgaben_anlegen", {"titel": "Vertrauliche Notiz"})
        _rpc(client, "evk_" + "B" * 43, "tools/list", {})
    assert key not in caplog.text and key[4:] not in caplog.text
    assert "B" * 43 not in caplog.text
    assert "Vertrauliche Notiz" not in caplog.text


def test_parallel_zwei_schluessel_getrennt(client, db_admin, welt, monkeypatch):
    """Review Focus 1: zwei Gespraeche mit verschiedenen Schluesseln gleichzeitig."""
    event_key = _schluessel(db_admin, "event", event_id=welt["meeting"])
    leitung_key = _schluessel(db_admin, "leitung", person_id=welt["lea"])
    m = marke.marke_ausstellen(db_admin, schluessel.pruefen(db_admin, leitung_key),
                               welt["festival"])
    fehler: list[str] = []
    start = threading.Barrier(2)

    katalog = {key: set(_werkzeuge(client, key)) for key in (event_key, leitung_key)}
    assert katalog[event_key] != katalog[leitung_key]

    # Die ersten beiden Werkzeugaufrufe warten im Server-Thread aufeinander: sie laufen
    # also nachweislich gleichzeitig (Timeout statt Haengen, wenn nicht).
    original = werkzeuge.ausfuehren
    treffpunkt = threading.Barrier(2)
    sperre = threading.Lock()
    zaehler = itertools.count()
    getroffen: list[bool] = []

    def mit_barriere(*a: Any, **k: Any) -> Any:
        with sperre:
            warten = next(zaehler) < 2
        if warten:
            treffpunkt.wait(timeout=30)
            getroffen.append(True)
        return original(*a, **k)

    monkeypatch.setattr(werkzeuge, "ausfuehren", mit_barriere)

    def gespraech(key: str, kennung: str, extra: dict[str, Any], erwartet: dict[str, str]) -> None:
        try:
            start.wait(timeout=30)
            for i in range(20):
                if set(_werkzeuge(client, key)) != katalog[key]:
                    fehler.append(f"{kennung}: falscher Katalog in Runde {i}")
                ergebnis = _aufruf(client, key, "aufgaben_anlegen",
                                   {**extra, "titel": f"{kennung}-{i}"})
                daten = json.loads(_text(ergebnis))
                if ergebnis["isError"] or daten["event"] != erwartet:
                    fehler.append(f"{kennung}: falsches Event in Runde {i}")
        except Exception as e:  # noqa: BLE001 - im Hauptthread melden
            fehler.append(f"{kennung}: {type(e).__name__}: {e}")

    threads = [
        threading.Thread(target=gespraech, args=(
            event_key, "A", {}, {"name": "Teamrunde Beispiel", "firma": "Beispiel firma-a"})),
        threading.Thread(target=gespraech, args=(
            leitung_key, "B", {"marke": m}, {"name": "Testfestival", "firma": "Beispiel firma-b"})),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)
    assert fehler == []
    assert getroffen == [True, True]

    zeilen = db_admin.execute("select event_id, titel from event.aufgabe").fetchall()
    assert len(zeilen) == 40
    for z in zeilen:
        erwartet = welt["meeting"] if z["titel"].startswith("A-") else welt["festival"]
        assert z["event_id"] == erwartet, z["titel"]


# --- Kommandozeile ------------------------------------------------------------------


def test_konfiguration(tmp_path):
    datei = tmp_path / "dsn"
    datei.write_text("host=127.0.0.1 dbname=x\r\n", encoding="utf-8")
    k = mcp_server.konfiguration({"EVENT_APP_DSN_FILE": str(datei)})
    assert (k.dsn, k.host, k.port) == ("host=127.0.0.1 dbname=x", "127.0.0.1", 8171)
    assert "dbname" not in repr(k)
    k = mcp_server.konfiguration({"EVENT_APP_DSN": "host=h", "EVENT_MCP_PORT": "9000",
                                  "EVENT_MCP_HOST": "0.0.0.0"})
    assert (k.dsn, k.host, k.port) == ("host=h", "0.0.0.0", 9000)
    for env in ({}, {"EVENT_APP_DSN": "x", "EVENT_MCP_PORT": "abc"},
                {"EVENT_APP_DSN": "x", "EVENT_MCP_PORT": "70000"}):
        with pytest.raises(ValueError):
            mcp_server.konfiguration(env)


# --- Einladen mit dem Mailer der App (Ruling S6) ------------------------------------

EINLADUNGS_BASIS = "https://events.example.org"
_SMTP_UMGEBUNG = ("EVENT_SMTP_HOST", "EVENT_SMTP_PORT", "EVENT_SMTP_USER",
                  "EVENT_SMTP_ABSENDER", "EVENT_SMTP_PASSWORT_FILE")


def _einladung(firma: str) -> dict[str, Any]:
    return {"firma": firma, "mail": "neu@example.org", "name": "Nora Neu", "rolle": "mitglied"}


def test_einladen_ueber_mcp_mit_mailer(app_dsn, db_admin, welt):
    mailer = SpeicherMailer()
    app = mcp_server.erstelle_mcp_app(app_dsn, mailer=mailer, basis_url=EINLADUNGS_BASIS)
    key = _schluessel(db_admin, "leitung", person_id=welt["lea"])  # Leitung von firma-b
    with TestClient(app, base_url=BASIS) as c:
        ok = _aufruf(c, key, "einladen", _einladung("firma-b"))
        assert ok["isError"] is False
        assert json.loads(_text(ok)) == {"firma": {"name": "Beispiel firma-b", "kurz": "firma-b"},
                                         "eingeladen": True}
        fremd = _aufruf(c, key, "einladen", _einladung("firma-a"))
        assert fremd["isError"] is True and _text(fremd) == "nicht gefunden"
    assert [m.an for m in mailer.gesendet] == ["neu@example.org"]
    assert f"{EINLADUNGS_BASIS}/einladung/" in mailer.gesendet[0].text
    assert db_admin.execute("select count(*) as n from event.einladung").fetchone()["n"] == 1


def test_einladen_ohne_mailer_sauber_abgelehnt(client, db_admin, welt):
    key = _schluessel(db_admin, "leitung", person_id=welt["lea"])
    ergebnis = _aufruf(client, key, "einladen", _einladung("firma-b"))
    assert ergebnis["isError"] is True
    assert _text(ergebnis) == "Einladen ist nicht eingerichtet"
    assert db_admin.execute("select count(*) as n from event.einladung").fetchone()["n"] == 0
    assert db_admin.execute("select count(*) as n from event.person where mail = %s",
                            ("neu@example.org",)).fetchone()["n"] == 0


def test_basis_url_geprueft(app_dsn):
    for falsch in ("ftp://events.example.org", "events.example.org", ""):
        with pytest.raises(ValueError):
            mcp_server.erstelle_mcp_app(app_dsn, basis_url=falsch)


def test_main_verdrahtet_mailer_und_basis_url(monkeypatch, tmp_path):
    import uvicorn

    erhalten: list[dict[str, Any]] = []

    def app_bauen(dsn: str, **kw: Any) -> object:
        erhalten.append({"dsn": dsn, **kw})
        return object()

    monkeypatch.setattr(mcp_server, "erstelle_mcp_app", app_bauen)
    monkeypatch.setattr(uvicorn, "run", lambda *_a, **_k: None)
    passwort = tmp_path / "smtp"
    passwort.write_text("geheim\r\n", encoding="utf-8")
    monkeypatch.delenv("EVENT_APP_DSN_FILE", raising=False)
    for name, wert in (("EVENT_APP_DSN", "host=h"), ("EVENT_BASIS_URL", EINLADUNGS_BASIS),
                       ("EVENT_SMTP_HOST", "smtp.example.org"), ("EVENT_SMTP_PORT", "587"),
                       ("EVENT_SMTP_USER", "bot"), ("EVENT_SMTP_ABSENDER", "bot@example.org"),
                       ("EVENT_SMTP_PASSWORT_FILE", str(passwort))):
        monkeypatch.setenv(name, wert)

    mcp_server.main()
    assert isinstance(erhalten[-1]["mailer"], SmtpMailer)
    assert erhalten[-1]["mailer"].host == "smtp.example.org"
    assert erhalten[-1]["basis_url"] == EINLADUNGS_BASIS

    # Ohne SMTP-Konfiguration startet der Dienst trotzdem; Einladen ist dann aus.
    for name in _SMTP_UMGEBUNG:
        monkeypatch.delenv(name)
    mcp_server.main()
    assert erhalten[-1]["mailer"] is None and erhalten[-1]["basis_url"] == EINLADUNGS_BASIS
    monkeypatch.delenv("EVENT_BASIS_URL")
    mcp_server.main()
    assert erhalten[-1]["basis_url"] is None


def test_konfiguration_basis_url():
    k = mcp_server.konfiguration({"EVENT_APP_DSN": "x", "EVENT_BASIS_URL": " " + EINLADUNGS_BASIS})
    assert k.basis_url == EINLADUNGS_BASIS
    assert mcp_server.konfiguration({"EVENT_APP_DSN": "x"}).basis_url is None
    with pytest.raises(ValueError):
        mcp_server.konfiguration({"EVENT_APP_DSN": "x", "EVENT_BASIS_URL": "ftp://x"})

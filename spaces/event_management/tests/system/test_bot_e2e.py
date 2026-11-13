"""Ende-zu-Ende-Probe der Bot-Kette (Etappe 3, Task 9), lokal und ohne Netz.

Alles laeuft gegen den Wegwerf-Postgres: die Leitung arbeitet ueber die event-api
(TestClient, Login, CSRF), der Empfangs- und die bereitgestellten Agenten ueber event-mcp
per HTTP (TestClient, Streamable HTTP), der Bereitstellungs-Arbeiter mit der Admin-DSN.
Telegram nur ueber `SpeicherTelegram`, OpenClaw nur ueber `SpeicherAusfuehrer`: kein
Docker, kein Container, keine VM. Den Schluessel eines Agenten liest der Test aus der
Config des SpeicherAusfuehrers, so wie der Gateway ihn im Header schicken wuerde.

Schluessel stehen in keinem Assert-Ausdruck und haben ein maskiertes repr (`_Schluessel`):
pytest zeigt Fixture-Werte und Teilausdruecke bei einem Fehler.
Chat-IDs sind Beispielwerte.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from spaces.event_management.system import (
    api, bereitstellung, events, mcp_server, schluessel, verbinden, zugang,
)
from spaces.event_management.system.bausteine import REGISTRY
from spaces.event_management.system.bereitstellung import SpeicherAusfuehrer
from spaces.event_management.system.mailer import SpeicherMailer
from spaces.event_management.system.telegram import SpeicherTelegram

BEGINN = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)
CHAT = -1001234567890
CHAT_ZWEI = -1001234567891
DIREKT = 123456789
PW = "ein-langes-passwort-1"
GEHEIM = b"api-testgeheimnis-0123456789abcdef"
BASIS = "https://events.example.org"
# So sieht der Claw-Container event-mcp im Compose-Netz (Dienstname, Port der CLI).
MCP_URL = f"http://event-mcp:{mcp_server.PORT_VORGABE}/mcp"
WS = "/home/node/.openclaw/workspace-"
# Was schon vor dem Event-System in der Config steht und nie angefasst werden darf.
GRUND_CONFIG: dict[str, Any] = {
    "agents": {"list": [{"id": "main", "tools": {"allow": ["melden__gruppe_melden",
                                                           "melden__chat_melden"]}}]},
    "bindings": [],
    "channels": {"telegram": {"groupPolicy": "allowlist",
                              "groups": {"*": {"requireMention": True}}}},
    "mcp": {"servers": {"melden": {"url": MCP_URL, "transport": "streamable-http"}}},
}
MELDEN = {"gruppe_melden", "chat_melden"}
WAHL_UND_VERWALTUNG = {
    "events_suchen", "event_waehlen", "event_anlegen", "einladen", "event_absagen",
    "event_archivieren", "event_loeschen", "kontakt_entfernen", "rolle_aendern",
    "gruppe_verbinden", "firma_anlegen",
}
_KOPF = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def _sperren_leeren() -> Iterator[None]:
    zugang._sperre_zuruecksetzen()
    verbinden._sperre_zuruecksetzen()
    yield
    zugang._sperre_zuruecksetzen()
    verbinden._sperre_zuruecksetzen()


# --- Welt ----------------------------------------------------------------------------


def _id(admin: Any, abfrage: str, werte: tuple[object, ...]) -> Any:
    return admin.execute(abfrage, werte).fetchone()["id"]


@pytest.fixture
def welt(db_admin: Any) -> dict[str, Any]:
    """Eine Firma mit einer Leitung (Passwort gesetzt, Login ueber die API)."""
    firma = _id(db_admin, "insert into event.firma (name, kurz) values (%s, %s) returning id",
                ("Beispiel GmbH", "beispiel"))
    lea = _id(db_admin, "insert into event.person (mail, name, passwort_hash) values"
                        " (%s, %s, %s) returning id",
              ("lea@example.org", "Lea Beispiel", zugang.passwort_hash(PW)))
    db_admin.execute("insert into event.mitgliedschaft values (%s, %s, 'leitung')", (lea, firma))
    return {"firma": firma, "lea": lea}


@pytest.fixture
def telegram() -> SpeicherTelegram:
    t = SpeicherTelegram()
    t.chats[CHAT] = {"titel": "Beispielgruppe", "art": "supergroup", "bot_ist_mitglied": True}
    t.chats[CHAT_ZWEI] = {"titel": "Zweite Gruppe", "art": "group", "bot_ist_mitglied": True}
    t.chats[DIREKT] = {"titel": "Lea (@lea_beispiel)", "art": "private",
                       "bot_ist_mitglied": True}
    return t


@pytest.fixture
def leitung(app_dsn: str, welt: dict[str, Any], telegram: SpeicherTelegram,
            ) -> Iterator[TestClient]:
    """event-api, angemeldet als Leitung (Sitzungs-Cookie + CSRF-Kopf)."""
    app = api.erstelle_app(app_dsn, GEHEIM, SpeicherMailer(), BASIS, cookie_sicher=False,
                           telegram=telegram)
    with TestClient(app) as c:
        r = c.post("/api/anmelden", json={"mail": "lea@example.org", "passwort": PW})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF"] = r.json()["csrf"]
        yield c


@pytest.fixture
def mcp(app_dsn: str, telegram: SpeicherTelegram) -> Iterator[TestClient]:
    """event-mcp unter dem Namen, unter dem der Claw ihn erreicht (Host-Pruefung inklusive)."""
    teile = urlsplit(MCP_URL)
    app = mcp_server.erstelle_mcp_app(app_dsn, telegram=telegram)
    with TestClient(app, base_url=f"{teile.scheme}://{teile.netloc}") as c:
        yield c


class _Schluessel(str):
    """Klartext-Schluessel, dessen repr maskiert ist: pytest zeigt Fixture-Werte und
    Assert-Teilausdruecke per repr, der Schluessel erscheint so in keiner Testausgabe."""

    def __repr__(self) -> str:
        return "'evk_***'"


@pytest.fixture
def melden_schluessel(db_admin: Any) -> str:
    """Schluessel des Empfangs-Agenten `main` (wie von anbinden.sh Zeile 5 erwartet)."""
    _, klartext = schluessel.ausstellen(db_admin, art="melden", bezeichnung="Empfang")
    return _Schluessel(klartext)


# --- MCP ueber HTTP ------------------------------------------------------------------

_nummer = iter(range(1, 1_000_000))


def _mcp(c: TestClient, schluessel_klartext: str, methode: str,
         params: dict[str, Any]) -> httpx.Response:
    return c.post("/mcp", headers={**_KOPF, "Authorization": f"Bearer {schluessel_klartext}"},
                  json={"jsonrpc": "2.0", "id": next(_nummer), "method": methode,
                        "params": params})


def _werkzeuge(c: TestClient, schluessel_klartext: str) -> dict[str, dict[str, Any]]:
    r = _mcp(c, schluessel_klartext, "tools/list", {})
    assert r.status_code == 200, r.text
    return {t["name"]: t for t in r.json()["result"]["tools"]}


def _aufruf(c: TestClient, schluessel_klartext: str, name: str,
            argumente: dict[str, Any]) -> dict[str, Any]:
    """Werkzeug aufrufen; Rueckgabe ist das JSON der Antwort (Fehler -> AssertionError)."""
    r = _mcp(c, schluessel_klartext, "tools/call", {"name": name, "arguments": argumente})
    assert r.status_code == 200, r.text
    ergebnis = r.json()["result"]
    text = ergebnis["content"][0]["text"]
    assert ergebnis["isError"] is False, text
    antwort: dict[str, Any] = json.loads(text)
    return antwort


def _fehler(c: TestClient, schluessel_klartext: str, name: str,
            argumente: dict[str, Any]) -> str:
    r = _mcp(c, schluessel_klartext, "tools/call", {"name": name, "arguments": argumente})
    assert r.status_code == 200, r.text
    ergebnis = r.json()["result"]
    assert ergebnis["isError"] is True
    text: str = ergebnis["content"][0]["text"]
    return text


# --- Kette: Code, Melden, Bestaetigen, Bereitstellen ---------------------------------


def _event_anlegen(leitung: TestClient, welt: dict[str, Any], name: str,
                   kategorie: str = "fest") -> UUID:
    r = leitung.post("/api/events", json={
        "firma_id": str(welt["firma"]), "kategorie": kategorie, "name": name,
        "beginn": BEGINN.isoformat(), "ende": (BEGINN + timedelta(hours=4)).isoformat(),
        "ort_adresse": "Musterstraße 1, Musterdorf"})
    assert r.status_code == 201, r.text
    return UUID(r.json()["id"])


def _bestaetigen(leitung: TestClient, offen_url: str, titel: str) -> None:
    gemeldet = [v for v in leitung.get(offen_url).json() if v["status"] == "gemeldet"]
    assert [v["chat_titel"] for v in gemeldet] == [titel]
    r = leitung.post(f"/api/verbindungen/{gemeldet[0]['id']}/bestaetigen")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "bestaetigt" and r.json()["bereitstellung"]


def _gruppe_verbinden(leitung: TestClient, mcp: TestClient, melden_schluessel: str,
                      event_id: UUID, chat: int, titel: str) -> None:
    """Leitung erzeugt den Code (API), der Empfang meldet ihn (MCP), die Leitung bestaetigt
    den angezeigten Gruppentitel (API)."""
    url = f"/api/events/{event_id}/verbinden"
    r = leitung.post(url)
    assert r.status_code == 201, r.text
    antwort = _aufruf(mcp, melden_schluessel, "gruppe_melden",
                      {"code": r.json()["code"], "chat_id": chat})
    assert antwort == {"antwort": verbinden.DANKE}
    _bestaetigen(leitung, url, titel)


def _agent(config: dict[str, Any], agent: str) -> dict[str, Any] | None:
    return next((a for a in config["agents"]["list"] if a.get("id") == agent), None)


def _bindungen(config: dict[str, Any], agent: str) -> list[dict[str, Any]]:
    return [b for b in config.get("bindings", []) if b.get("agentId") == agent]


def _schluessel_des_agenten(config: dict[str, Any], agent: str) -> str:
    """Bearer aus dem Server-Eintrag des Agenten; der Wert erscheint in keiner Meldung."""
    server = config["mcp"]["servers"][agent]
    wert = server["headers"]["Authorization"]
    if not (isinstance(wert, str) and wert.startswith("Bearer evk_")):
        raise AssertionError("Server-Eintrag traegt keinen Bearer-Schluessel")
    return _Schluessel(wert.removeprefix("Bearer "))


def _lauf(admin: Any, aus: SpeicherAusfuehrer) -> list[str]:
    erledigt = bereitstellung.lauf(admin, aus, mcp_url=MCP_URL)
    zeilen = admin.execute("select art, status, fehler from event.bereitstellung"
                           " where id = any(%s) order by angelegt, id", (erledigt,)).fetchall()
    return [f"{z['art']}:{z['status']}:{z['fehler']}" for z in zeilen]


def _ev(event_id: UUID) -> str:
    return f"ev-{event_id.hex[:8]}"


# --- Gruppe: vom Code bis zum Abbau --------------------------------------------------


def test_gruppe_von_code_bis_abbau(db_admin, welt, leitung, mcp, melden_schluessel, telegram):
    event_id = _event_anlegen(leitung, welt, "Sommerfest")
    _gruppe_verbinden(leitung, mcp, melden_schluessel, event_id, CHAT, "Beispielgruppe")

    aus = SpeicherAusfuehrer(config=GRUND_CONFIG)
    assert _lauf(db_admin, aus) == ["anlegen:erfolg:None"]
    agent = _ev(event_id)
    config = aus.config_lesen()
    server = config["mcp"]["servers"][agent]
    assert (server["url"], server["transport"]) == (MCP_URL, "streamable-http")
    assert _agent(config, agent) == {"id": agent, "workspace": WS + agent,
                                     "tools": {"allow": [f"{agent}__*"]}}
    assert _bindungen(config, agent) == [{"agentId": agent, "match": {
        "channel": "telegram", "peer": {"kind": "group", "id": str(CHAT)}}}]
    assert config["channels"]["telegram"]["groups"][str(CHAT)] == {"requireMention": False}
    assert "Sommerfest" in aus.dateien[f"{WS}{agent}/SOUL.md"]
    # Der Empfang bleibt unberuehrt.
    assert _agent(config, "main") == GRUND_CONFIG["agents"]["list"][0]
    assert config["mcp"]["servers"]["melden"] == GRUND_CONFIG["mcp"]["servers"]["melden"]
    key = _schluessel_des_agenten(config, agent)

    # tools/list mit dem Schluessel des Gruppen-Agenten: nur Bausteine dieses Events.
    werkzeuge = _werkzeuge(mcp, key)
    namen = set(werkzeuge)
    assert not namen & (WAHL_UND_VERWALTUNG | MELDEN)
    bausteine = set(events.schema_fuer("fest", None).bausteine)
    assert {"aufgaben_anlegen", "helferschichten_liste", "programm_liste"} <= namen
    for fremd in ("jury_liste", "abgaben_anlegen", "aussteller_liste", "protokoll_liste"):
        assert fremd not in namen
    for name in namen:
        eintrag, _, op = name.rpartition("_")
        if eintrag in REGISTRY and op in ("liste", "anlegen", "aendern", "loeschen"):
            assert (REGISTRY[eintrag].baustein or eintrag) in bausteine, name
    assert all("marke" not in w["inputSchema"].get("properties", {})
               for w in werkzeuge.values())

    # Schreiben ueber den Bot landet im Event und im Verlauf mit quelle=bot.
    neu = _aufruf(mcp, key, "aufgaben_anlegen", {"titel": "Bühne aufbauen"})
    assert neu["event"] == {"name": "Sommerfest", "firma": "Beispiel GmbH"}
    assert neu["zeile"]["titel"] == "Bühne aufbauen"
    zeilen = leitung.get(f"/api/events/{event_id}/b/aufgaben").json()
    assert [z["titel"] for z in zeilen] == ["Bühne aufbauen"]
    eintraege = leitung.get(f"/api/events/{event_id}/verlauf").json()
    bot = [e for e in eintraege if e["quelle"] == "bot"]
    assert [(e["actor"], e["baustein"], e["aktion"]) for e in bot] == [
        ("bot:event", "aufgaben", "angelegt")]
    assert all(e["quelle"] == "oberflaeche" for e in eintraege if e not in bot)

    # Archivieren (Oberflaeche) legt den Abbau an; der naechste Lauf raeumt alles ab.
    r = leitung.post(f"/api/events/{event_id}/status", json={"status": "archiviert"})
    assert r.status_code == 200, r.text
    assert _lauf(db_admin, aus) == ["abbauen:erfolg:None"]
    config = aus.config_lesen()
    assert _agent(config, agent) is None
    assert agent not in config["mcp"]["servers"]
    assert _bindungen(config, agent) == []
    assert str(CHAT) not in config["channels"]["telegram"]["groups"]
    assert f"{WS}{agent}/SOUL.md" not in aus.dateien  # umbenannt, nicht geloescht
    assert any(p.startswith(f"{WS}{agent}-abgebaut-") for p in aus.dateien)
    assert _agent(config, "main") == GRUND_CONFIG["agents"]["list"][0]
    assert config["mcp"]["servers"]["melden"] == GRUND_CONFIG["mcp"]["servers"]["melden"]

    # Der alte Schluessel ist widerrufen: event-mcp lehnt schon vor dem MCP ab.
    r = _mcp(mcp, key, "tools/list", {})
    assert r.status_code == 401
    assert _mcp(mcp, key, "tools/call", {"name": "aufgaben_liste",
                                         "arguments": {}}).status_code == 401
    # Ein weiterer Lauf hat nichts mehr zu tun.
    assert _lauf(db_admin, aus) == []


# --- Leitung im Direktchat -----------------------------------------------------------


def test_leitung_direktchat_waehlt_und_sagt_zweistufig_ab(
        db_admin, welt, leitung, mcp, melden_schluessel, telegram):
    event_id = _event_anlegen(leitung, welt, "Sommerfest")
    r = leitung.post("/api/ich/verbinden")
    assert r.status_code == 201, r.text
    antwort = _aufruf(mcp, melden_schluessel, "chat_melden",
                      {"code": r.json()["code"], "chat_id": DIREKT})
    assert antwort == {"antwort": verbinden.DANKE}
    _bestaetigen(leitung, "/api/ich/verbinden", "Lea (@lea_beispiel)")

    aus = SpeicherAusfuehrer(config=GRUND_CONFIG)
    assert _lauf(db_admin, aus) == ["anlegen:erfolg:None"]
    agent = f"ld-{welt['lea'].hex[:8]}"
    config = aus.config_lesen()
    assert _bindungen(config, agent) == [{
        "agentId": agent, "match": {"channel": "telegram",
                                    "peer": {"kind": "direct", "id": str(DIREKT)}},
        "session": {"dmScope": "per-peer"}}]
    assert str(DIREKT) not in config["channels"]["telegram"]["groups"]
    key = _schluessel_des_agenten(config, agent)

    namen = set(_werkzeuge(mcp, key))
    assert {"event_waehlen", "event_absagen", "gruppe_verbinden", "aufgaben_anlegen"} <= namen
    assert not namen & (MELDEN | {"firma_anlegen"})

    gewaehlt = _aufruf(mcp, key, "event_waehlen", {"suche": "sommer"})
    assert gewaehlt["event"]["name"] == "Sommerfest"
    m = gewaehlt["marke"]
    status_vorher = leitung.get(f"/api/events/{event_id}").json()["status"]
    assert status_vorher != "abgesagt"

    # Stufe 1: nur Zusammenfassung und Code, nichts geaendert.
    frage = _aufruf(mcp, key, "event_absagen", {"marke": m})
    assert frage["event"] == {"name": "Sommerfest", "firma": "Beispiel GmbH"}
    assert "Sommerfest" in frage["zusammenfassung"] and frage["bestaetigung"]
    assert leitung.get(f"/api/events/{event_id}").json()["status"] == status_vorher
    # Stufe 2 mit falschem Code aendert ebenfalls nichts.
    assert _fehler(mcp, key, "event_absagen", {"marke": m, "bestaetigung": "falsch"})
    assert leitung.get(f"/api/events/{event_id}").json()["status"] == status_vorher
    # Stufe 2 mit dem Code fuehrt aus.
    erg = _aufruf(mcp, key, "event_absagen", {"marke": m, "bestaetigung": frage["bestaetigung"]})
    assert erg == {"event": {"name": "Sommerfest", "firma": "Beispiel GmbH"},
                   "status": "abgesagt"}
    assert leitung.get(f"/api/events/{event_id}").json()["status"] == "abgesagt"
    # Der Code gilt genau einmal.
    assert _fehler(mcp, key, "event_absagen",
                   {"marke": m, "bestaetigung": frage["bestaetigung"]})
    bot = [e for e in leitung.get(f"/api/events/{event_id}/verlauf").json()
           if e["quelle"] == "bot"]
    assert [(e["actor"], e["actor_person_id"], e["aktion"]) for e in bot] == [
        ("bot:leitung", str(welt["lea"]), "status_abgesagt")]


# --- Trennung der Schluessel ---------------------------------------------------------


def test_schluessel_zweier_events_und_des_empfangs_getrennt(
        db_admin, welt, leitung, mcp, melden_schluessel, telegram):
    fest = _event_anlegen(leitung, welt, "Sommerfest", "fest")
    hack = _event_anlegen(leitung, welt, "Hackathon Nord", "hackathon")
    _gruppe_verbinden(leitung, mcp, melden_schluessel, fest, CHAT, "Beispielgruppe")
    _gruppe_verbinden(leitung, mcp, melden_schluessel, hack, CHAT_ZWEI, "Zweite Gruppe")
    aus = SpeicherAusfuehrer(config=GRUND_CONFIG)
    assert _lauf(db_admin, aus) == ["anlegen:erfolg:None", "anlegen:erfolg:None"]
    config = aus.config_lesen()
    key_fest = _schluessel_des_agenten(config, _ev(fest))
    key_hack = _schluessel_des_agenten(config, _ev(hack))
    if key_fest == key_hack:
        raise AssertionError("zwei Agenten tragen denselben Schluessel")

    namen_fest, namen_hack = set(_werkzeuge(mcp, key_fest)), set(_werkzeuge(mcp, key_hack))
    assert namen_fest != namen_hack
    assert "helferschichten_liste" in namen_fest - namen_hack
    assert {"jury_liste", "abgaben_anlegen"} <= namen_hack - namen_fest

    _aufruf(mcp, key_fest, "aufgaben_anlegen", {"titel": "Zelt"})
    fest_liste = _aufruf(mcp, key_fest, "aufgaben_liste", {})
    hack_liste = _aufruf(mcp, key_hack, "aufgaben_liste", {})
    assert fest_liste["event"]["name"] == "Sommerfest"
    assert [z["titel"] for z in fest_liste["zeilen"]] == ["Zelt"]
    assert hack_liste["event"]["name"] == "Hackathon Nord" and hack_liste["zeilen"] == []
    # Ein Werkzeug, das nur das andere Event hat, gibt es fuer diesen Schluessel nicht.
    assert _fehler(mcp, key_fest, "jury_liste", {}) == "nicht gefunden"

    # Der Empfangs-Schluessel sieht genau die zwei Melde-Werkzeuge und kommt an nichts sonst.
    assert set(_werkzeuge(mcp, melden_schluessel)) == MELDEN
    for name, argumente in (("aufgaben_liste", {}), ("event_waehlen", {"suche": "Sommer"}),
                            ("event_info", {})):
        assert _fehler(mcp, melden_schluessel, name, argumente) == "nicht gefunden"

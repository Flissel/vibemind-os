"""Die Formular-Routen (`/fenster/{token}`) -- die einzige Stelle, an der
`server.py` selbst Netzwerkverhalten zeigt, das ein Test treffen kann.

Review Runde 3 (C-Kritisch #1, #2, Important "Route-Test"): vorher stand
"der Wert reist per POST, nie als Query-Parameter" nur als Kommentar in
`fenster.py` -- kein Test schickte je eine echte HTTP-artige Anfrage durch
die Routen selbst. Diese Datei benutzt Starlettes `TestClient` gegen genau
die Funktionen, die `server._baue_server()` registriert (`server.
_fenster_zeigen`/`_fenster_annehmen`), verkabelt in einer eigenen,
minimalen `Starlette`-App -- ohne FastMCPs Session-Manager, der fuer diese
Routen ohnehin nichts beitraegt.

Vier Eigenschaften, alle strukturell, keine per Konvention:
  1. Loopback-Wache: eine Anfrage von einer NICHT-Loopback-Adresse (Stand-
     in fuer "aus dem Container") wird abgelehnt, BEVOR das Token
     angefasst wird. GEMESSENE GRENZE (s. `server.py`-Moduldoku): auf einem
     Docker-Desktop/WSL-Mirrored-Host trennt diese Pruefung den echten
     Container NICHT vom Host -- dieser Test simuliert nur eine Adresse,
     die nicht ueber Docker Desktops `host.docker.internal`-Bruecke kommt.
  2. oauth-Zwischenstand: ein POST auf eine `art=oauth`-Anfrage verbraucht
     das Token NICHT (der echte Provisioner fehlt noch, Aufgabe 6) -- auch
     wenn der Body einen `wert` enthaelt, damit die Mutation "oauth-Zweig
     entfernen" nicht zufaellig von der Leer-Wert-Wache mitgefangen wird.
  3. Der Wert kommt nur aus dem POST-Body, nie aus der Query -- und ein
     leerer oder Nur-Leerzeichen-Wert wird abgelehnt, OHNE das Token zu
     verbrauchen; ein Wert mit Rand-Leerzeichen erreicht den Schreiber
     gestrippt.
"""
from __future__ import annotations

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

import anfragen
import server

_FAKE_WERT = "offensichtlich-erfunden-kein-echtes-secret-route"

_APP = Starlette(routes=[
    Route("/fenster/{token}", endpoint=server._fenster_zeigen, methods=["GET"]),
    Route("/fenster/{token}", endpoint=server._fenster_annehmen, methods=["POST"]),
])

# `client=` setzt scope["client"] direkt -- s. Starlette-TestClient-Doku.
# "203.0.113.5" ist TEST-NET-3 (RFC 5737): niemals eine echte Loopback- oder
# sonst zuweisbare Adresse, hier als Stellvertreter fuer "irgendwo aus dem
# Container/Netz, nicht der Host selbst".
_HOST_CLIENT = TestClient(_APP, client=("127.0.0.1", 51000))
_FREMDER_CLIENT = TestClient(_APP, client=("203.0.113.5", 51000))


def test_get_von_fremder_adresse_wird_abgelehnt():
    a = anfragen.anlegen("proj", "demo", "ROUTE_FREMD_GET", "bearer", "")
    r = _FREMDER_CLIENT.get(f"/fenster/{a.token}")
    assert r.status_code == 403
    assert anfragen.holen(a.token) is not None, "eine abgelehnte Anfrage darf das Token nicht anfassen"


def test_post_von_fremder_adresse_wird_abgelehnt_und_verbraucht_kein_token():
    a = anfragen.anlegen("proj", "demo", "ROUTE_FREMD_POST", "bearer", "")
    r = _FREMDER_CLIENT.post(f"/fenster/{a.token}", data={"wert": _FAKE_WERT})
    assert r.status_code == 403
    assert anfragen.holen(a.token) is not None, "die Loopback-Wache muss VOR verbrauchen() greifen"


def test_get_von_host_verbraucht_kein_token():
    a = anfragen.anlegen("proj", "demo", "ROUTE_GET_OK", "bearer", "")
    r = _HOST_CLIENT.get(f"/fenster/{a.token}")
    assert r.status_code == 200
    assert anfragen.holen(a.token) is not None, "GET liest nur, verbraucht nie"


def test_unbekanntes_token_gibt_404_vom_host_aus():
    r = _HOST_CLIENT.get("/fenster/gibt-es-nicht")
    assert r.status_code == 404


def test_oauth_post_verbraucht_das_token_nicht(monkeypatch):
    """Der Provisioner fuer den echten Consent-Flow fehlt noch (Aufgabe 6).
    Ohne die Abfrage in `_fenster_annehmen` wuerde dieser POST das Token
    verbrauchen und einen LEEREN Wert an den Schreiber reichen.

    Review Runde 3 (Mutation-Check-Fund): eine fruehere Fassung postete
    ohne Body -- die oauth-Seite hat kein `wert`-Feld, also war der Body
    ohnehin leer, und der Leer-Wert-Wache (die es unabhaengig gibt) haette
    den Schreibweg allein schon verhindert. Die Mutation "oauth-Zweig
    entfernen" haette dann NICHT rot geschlagen, sie haette nur den
    Seitentext geaendert -- der Test haette also Wortlaut geprueft, nicht
    das Verhalten. Ein NICHT-leerer `wert` im Body (Stand-in fuer irgendeine
    kuenftige oauth-Seite mit einem versteckten Feld, oder einen Betreiber,
    der trotzdem einen Wert mitschickt) isoliert die oauth-Wache: entfernt
    man NUR sie, faellt dieser Wert durch die Leer-Pruefung (er ist ja
    nicht leer) und erreicht den Schreiber -- HIER, nicht in
    test_leerer_wert_wird_abgelehnt_ohne_token_zu_verbrauchen, faellt der
    Unterschied auf."""
    gerufen = []
    monkeypatch.setattr(server.werkzeuge, "schluessel_entgegennehmen",
                        lambda **k: gerufen.append(k) or {"ok": True})
    a = anfragen.anlegen("proj", "demo", "ROUTE_OAUTH", "oauth", "https://mcp.example.com/mcp")
    r = _HOST_CLIENT.post(f"/fenster/{a.token}", data={"wert": _FAKE_WERT})
    assert r.status_code == 200
    assert "erfuegbar" in r.text  # "Noch nicht verfuegbar"
    assert gerufen == [], "der Schreibweg darf fuer oauth heute gar nicht erst aufgerufen werden, auch mit einem Wert im Body"
    assert anfragen.holen(a.token) is not None, "das Token bleibt gueltig fuer einen spaeteren Versuch"


def test_leerer_wert_wird_abgelehnt_ohne_token_zu_verbrauchen(monkeypatch):
    gerufen = []
    monkeypatch.setattr(server.werkzeuge, "schluessel_entgegennehmen",
                        lambda **k: gerufen.append(k) or {"ok": True})
    a = anfragen.anlegen("proj", "demo", "ROUTE_LEER", "bearer", "")
    # Leerer Body, aber ein `wert` in der QUERY -- der darf nicht zaehlen,
    # s. test_wert_kommt_nur_aus_dem_post_body_nie_aus_der_query unten.
    r = _HOST_CLIENT.post(f"/fenster/{a.token}?wert=aus-der-query")
    assert r.status_code == 400
    assert "fehlt" in r.text
    assert gerufen == [], "ein leerer Wert darf den Schreibweg nie erreichen"
    assert anfragen.holen(a.token) is not None, "ein abgelehntes leeres Formular darf das Token nicht verbrauchen"


def test_wert_kommt_nur_aus_dem_post_body_nie_aus_der_query(monkeypatch):
    """Aendert eine kuenftige Bearbeitung `_fenster_annehmen` so, dass der
    Wert (auch nur zusaetzlich) aus `request.query_params` gelesen wird,
    faellt dieser Test um -- der Schreiber saehe `aus-der-query` statt
    `aus-dem-body`."""
    gerufen = []
    monkeypatch.setattr(server.werkzeuge, "schluessel_entgegennehmen",
                        lambda **k: gerufen.append(k) or {"ok": True})
    a = anfragen.anlegen("proj", "demo", "ROUTE_BODY_VS_QUERY", "bearer", "")
    r = _HOST_CLIENT.post(f"/fenster/{a.token}?wert=aus-der-query",
                          data={"wert": "aus-dem-body"})
    assert r.status_code == 200
    assert len(gerufen) == 1
    assert gerufen[0]["wert"] == "aus-dem-body"
    assert anfragen.holen(a.token) is None, "ein angenommener Wert verbraucht das Token"


def test_nur_leerzeichen_wird_wie_ein_leerer_wert_abgelehnt(monkeypatch):
    """Review Runde 3, Fix-Runde 2 (Important): ein Copy-Paste-Artefakt aus
    nur Leerzeichen ist in Python `truthy` -- ohne `.strip()` VOR der
    Leer-Pruefung waere das kein 'Wert fehlt', sondern ein Wert, der beim
    Tresor und beim Anbieter landet und dort scheitert, schwerer zu
    diagnostizieren als eine ehrliche Ablehnung hier."""
    gerufen = []
    monkeypatch.setattr(server.werkzeuge, "schluessel_entgegennehmen",
                        lambda **k: gerufen.append(k) or {"ok": True})
    a = anfragen.anlegen("proj", "demo", "ROUTE_NUR_LEERZEICHEN", "bearer", "")
    r = _HOST_CLIENT.post(f"/fenster/{a.token}", data={"wert": "   "})
    assert r.status_code == 400
    assert "fehlt" in r.text
    assert gerufen == [], "nur Leerzeichen darf den Schreibweg nie erreichen"
    assert anfragen.holen(a.token) is not None, "eine abgelehnte Nur-Leerzeichen-Eingabe darf das Token nicht verbrauchen"


def test_wert_wird_gestrippt_bevor_er_den_schreiber_erreicht(monkeypatch):
    gerufen = []
    monkeypatch.setattr(server.werkzeuge, "schluessel_entgegennehmen",
                        lambda **k: gerufen.append(k) or {"ok": True})
    a = anfragen.anlegen("proj", "demo", "ROUTE_STRIP", "bearer", "")
    r = _HOST_CLIENT.post(f"/fenster/{a.token}", data={"wert": "  echt-mit-randweiss  "})
    assert r.status_code == 200
    assert len(gerufen) == 1
    assert gerufen[0]["wert"] == "echt-mit-randweiss"

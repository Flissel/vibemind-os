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
  2. oauth-Weiche (Aufgabe 6 -- der Provisioner ist jetzt angebunden): ein
     POST auf eine `art=oauth`-Anfrage nimmt den Wert vom injizierten
     `beschaffer`, NIE aus dem Formular-Body, auch wenn der Body einen
     `wert` enthaelt (Mutation-Check: entfernt man die Weiche, faellt die
     Anfrage auf den Formular-Pfad durch und der Body-Wert erreicht den
     Schreiber -- HIER faellt das auf, nicht als geaenderter Seitentext).
     Ein Fehlschlag des `beschaffer` (der echte Provisioner meldet ueber
     `SystemExit`, kein `Exception`) zeigt eine gewoehnliche Antwort ohne
     die Ausnahme im Klartext, statt unbehandelt durchzuschlagen.
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


def test_oauth_post_ruft_den_echten_provisioner_statt_den_formular_wert(monkeypatch):
    """Aufgabe 6: der Provisioner ist jetzt angebunden. Ein POST auf eine
    `art=oauth`-Anfrage muss `fenster.oauth_entgegennehmen` durchlaufen, die
    den Wert vom injizierten `beschaffer` holt -- NIE aus `request.form()`.
    Kein Netzwerk hier: `server.provision_oauth_token.token_holen` ist
    monkeypatched, exakt die Naht, die `fenster.py` dafuer vorsieht.

    Mutation-Check (Review Runde 3, Fix-Runde weiter oben, jetzt fuer den
    echten Fluss wiederholt): der Body traegt trotzdem einen `wert`
    (`aus-dem-formular-koerper`), der vom PROVISIONER-Wert
    (`aus-dem-provisioner`) verschieden ist. Entfernt eine kuenftige
    Aenderung die `vorschau.art == "oauth"`-Weiche in `_fenster_annehmen`,
    faellt die Anfrage auf den normalen Formular-Pfad durch: der
    Leer-Wert-Wache haette dann NICHTS entgegenzusetzen (der Body-Wert ist
    ja nicht leer), und der Schreiber saehe `aus-dem-formular-koerper` --
    einen Wert aus dem POST-Body statt aus dem Provisioner, fuer eine
    `art=oauth`-Anfrage. Genau DIESER Unterschied (welcher Wert den
    Schreiber erreicht), nicht bloss ein geaenderter Seitentext, ist die
    Eigenschaft, die dieser Test haelt."""
    gerufen = []
    monkeypatch.setattr(server.werkzeuge, "schluessel_entgegennehmen",
                        lambda **k: gerufen.append(k) or {"ok": True})
    beschafft = []

    def _beschaffer(mcp_url):
        beschafft.append(mcp_url)
        return "ROUTE_OAUTH", "aus-dem-provisioner"

    monkeypatch.setattr(server.provision_oauth_token, "token_holen", _beschaffer)
    a = anfragen.anlegen("proj", "demo", "ROUTE_OAUTH", "oauth", "https://mcp.example.com/mcp")
    r = _HOST_CLIENT.post(f"/fenster/{a.token}", data={"wert": "aus-dem-formular-koerper"})
    assert r.status_code == 200
    assert beschafft == ["https://mcp.example.com/mcp"], "die oauth-Weiche muss den Provisioner mit `ziel` rufen"
    assert len(gerufen) == 1
    assert gerufen[0]["wert"] == "aus-dem-provisioner", "der Schreiber muss den Provisioner-Wert sehen, nicht den Formular-Body"
    assert "aus-dem-provisioner" not in r.text
    assert "aus-dem-formular-koerper" not in r.text
    assert anfragen.holen(a.token) is None, "ein erfolgreich beschaffter oauth-Token verbraucht das Token"


def test_oauth_post_bei_beschaffer_fehlschlag_zeigt_keine_ausnahme_und_keinen_token(monkeypatch):
    """Der echte Provisioner meldet erwartete Fehlschlaege (Discovery,
    Registrierung, Callback-Timeout, fehlender access_token) per
    `raise SystemExit(...)`, NICHT per `Exception` -- und `SystemExit` ist
    kein `Exception`-Subtyp. Ein `except Exception` in `oauth_entgegennehmen`
    wuerde das durchfallen lassen und die Ausnahme unbehandelt bis in den
    ASGI-Stack durchschlagen (gemessen: genau das geschah hier, bevor der
    Catch auf `except BaseException` erweitert wurde, s. Task-6-Bericht).
    Dieser Test haelt fest, dass die Route stattdessen eine gewoehnliche
    200-Antwort ohne den Token/die Fehlermeldung im Klartext zeigt -- fuer
    SystemExit UND fuer jede andere Ausnahme gleich."""
    monkeypatch.setattr(server.werkzeuge, "schluessel_entgegennehmen",
                        lambda **k: (_ for _ in ()).throw(AssertionError("darf fuer einen Fehlschlag nie gerufen werden")))

    def _beschaffer(mcp_url):
        raise SystemExit("offensichtlich-erfunden-oauth-fehlertext-koennte-den-token-tragen")

    monkeypatch.setattr(server.provision_oauth_token, "token_holen", _beschaffer)
    a = anfragen.anlegen("proj", "demo", "ROUTE_OAUTH_FEHL", "oauth", "https://mcp.example.com/mcp")
    r = _HOST_CLIENT.post(f"/fenster/{a.token}", data={})
    assert r.status_code == 200
    assert "offensichtlich-erfunden-oauth-fehlertext-koennte-den-token-tragen" not in r.text
    assert anfragen.holen(a.token) is None, "ein Fehlschlag verbraucht das Token trotzdem (kein Retry auf demselben Link)"


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

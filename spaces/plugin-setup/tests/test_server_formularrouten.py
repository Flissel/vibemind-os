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

Fuenf Eigenschaften, alle strukturell, keine per Konvention:
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
  4. Fix-Runde 1, Befund 1: beide Schreibwege in `_fenster_annehmen`
     laufen per `run_in_threadpool`, damit ein blockierender Beschaffer
     (Stand-in fuer `wait_for_callback`s `threading.Event.wait()`, bis
     zu 300s im echten Provisioner) die EINE Event-Loop des Prozesses
     nicht fuer jede andere Anfrage sperrt. Geprueft per `asyncio.gather`
     gegen die echte ASGI-App (httpx `ASGITransport`) -- NICHT per
     Starlettes synchronem `TestClient`, der keine zwei Anfragen
     gleichzeitig lostreten kann.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest
from httpx import ASGITransport, AsyncClient
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


@pytest.mark.asyncio
async def test_oauth_beschaffung_blockiert_ein_gleichzeitiges_get_nicht(monkeypatch):
    """Mutationsprobe zu Fix-Runde 1, Befund 1: `fenster.oauth_entgegennehmen`
    haengt im echten Betrieb blockierend auf `threading.Event.wait()`
    (`wait_for_callback`, bis zu `timeout_seconds=300`) -- OHNE
    `run_in_threadpool` um den Aufruf in `_fenster_annehmen` wuerde das die
    EINE Event-Loop des FastMCP-Prozesses fuer JEDE andere Anfrage sperren.

    Aufbau: ein `beschaffer` blockiert SYNCHRON auf einem `threading.Event`,
    das dieser Test erst setzt, NACHDEM ein zweites, unabhaengiges GET
    geantwortet hat. POST (oauth, blockiert) und GET (ein normales Formular)
    laufen NEBENLAEUFIG per `asyncio.gather` gegen die echte ASGI-App
    (httpx `ASGITransport`, dieselbe `_APP`-Verkabelung wie oben) --
    Starlettes synchroner `TestClient` kann zwei Anfragen nicht gleichzeitig
    lostreten, darum hier `httpx.AsyncClient` statt `_HOST_CLIENT`.

    Der Beweis ist NICHT der Statuscode (der ist in beiden Faellen 200),
    sondern die REIHENFOLGE der Fertigstellung: laeuft der oauth-Zweig im
    Threadpool, blockiert er nur einen Worker-Thread, die Event-Loop bleibt
    frei, und das GET antwortet SOFORT -- lange bevor der Beschaffer
    zurueckkehrt. Faellt `run_in_threadpool` weg, blockiert der synchrone
    Aufruf die Event-Loop selbst; das GET kann dann erst verarbeitet werden,
    NACHDEM der Beschaffer zurueckgekehrt ist (hier: nach dem
    `freigegeben.wait(timeout=5)`-Timeout, da niemand das Event mehr setzt)
    -- die Reihenfolge kippt zu `["post", "get"]`, und das GET braucht
    knapp 5s statt Millisekunden. Beides haelt dieser Test fest.
    """
    freigegeben = threading.Event()
    reihenfolge: list[str] = []
    zeiten: dict[str, float] = {}

    def _blockierender_beschaffer(mcp_url):
        # Stellvertreter fuer `wait_for_callback`s `threading.Event.wait()`.
        # `timeout=5` ist nur eine Sicherung gegen einen echten Haenger
        # dieses Tests, kein Teil der geprueften Eigenschaft.
        freigegeben.wait(timeout=5)
        return "ROUTE_NEBENLAEUFIG_OAUTH", "aus-dem-provisioner"

    monkeypatch.setattr(server.werkzeuge, "schluessel_entgegennehmen",
                        lambda **k: {"ok": True})
    monkeypatch.setattr(server.provision_oauth_token, "token_holen", _blockierender_beschaffer)

    a_oauth = anfragen.anlegen("proj", "demo", "ROUTE_NEBENLAEUFIG_OAUTH", "oauth",
                              "https://mcp.example.com/mcp")
    a_get = anfragen.anlegen("proj", "demo", "ROUTE_NEBENLAEUFIG_GET", "bearer", "")

    start = time.monotonic()

    async with AsyncClient(transport=ASGITransport(app=_APP),
                           base_url="http://testserver") as client:

        async def _post():
            r = await client.post(f"/fenster/{a_oauth.token}")
            reihenfolge.append("post")
            zeiten["post"] = time.monotonic() - start
            return r

        async def _get():
            r = await client.get(f"/fenster/{a_get.token}")
            reihenfolge.append("get")
            zeiten["get"] = time.monotonic() - start
            # Erst JETZT den Beschaffer entriegeln: das POST kann nur dann
            # vor dem GET fertig werden, wenn die Auslagerung fehlt UND das
            # GET seinerseits erst nach dem `freigegeben.wait`-Timeout an
            # die Reihe kommt.
            freigegeben.set()
            return r

        post_antwort, get_antwort = await asyncio.gather(_post(), _get())

    assert get_antwort.status_code == 200
    assert post_antwort.status_code == 200
    assert reihenfolge == ["get", "post"], (
        "das GET muss abgeschlossen sein, WAEHREND das blockierende POST "
        f"noch laeuft -- tatsaechliche Reihenfolge: {reihenfolge}, "
        f"Zeiten: {zeiten}")
    assert zeiten["get"] < 1.0, (
        "das GET durfte durch das laufende, blockierende POST nicht "
        f"verzoegert werden, brauchte aber {zeiten['get']:.3f}s")


@pytest.mark.asyncio
async def test_bearer_schreiber_blockiert_ein_gleichzeitiges_get_nicht(monkeypatch):
    """N8: `run_in_threadpool` war bisher nur auf dem OAUTH-Pfad
    festgenagelt -- eine Mutation, die es NUR um den bearer-Schreibweg in
    `_fenster_annehmen` herum entfernt, liess bis hierher ALLE Tests gruen,
    weil kein Test je einen blockierenden `schreiber` fuer den bearer-Pfad
    simuliert hat (die anderen bearer-Tests oben patchen `schluessel_
    entgegennehmen` immer auf einen sofort zurueckkehrenden Ersatz).

    Aufbau identisch zum oauth-Pendant oben, nur fuer den bearer-Pfad: ein
    `schluessel_entgegennehmen`-Ersatz blockiert SYNCHRON auf einem
    `threading.Event`, das erst gesetzt wird, NACHDEM ein zweites,
    unabhaengiges GET geantwortet hat. POST (bearer, blockiert) und GET
    laufen NEBENLAEUFIG per `asyncio.gather` gegen die echte ASGI-App.
    Beweis ist wieder die REIHENFOLGE der Fertigstellung, nicht der
    Statuscode: laeuft der bearer-Zweig im Threadpool, blockiert er nur
    einen Worker-Thread, die Event-Loop bleibt frei, das GET antwortet
    SOFORT. Faellt `run_in_threadpool` fuer den bearer-Pfad weg, blockiert
    der synchrone Aufruf die Event-Loop selbst, und das GET kann erst nach
    dem `freigegeben.wait(timeout=5)`-Timeout verarbeitet werden."""
    freigegeben = threading.Event()
    reihenfolge: list[str] = []
    zeiten: dict[str, float] = {}

    def _blockierender_schreiber(**kwargs):
        freigegeben.wait(timeout=5)
        return {"ok": True}

    monkeypatch.setattr(server.werkzeuge, "schluessel_entgegennehmen", _blockierender_schreiber)

    a_post = anfragen.anlegen("proj", "demo", "ROUTE_NEBENLAEUFIG_BEARER", "bearer", "")
    a_get = anfragen.anlegen("proj", "demo", "ROUTE_NEBENLAEUFIG_GET_BEARER", "bearer", "")

    start = time.monotonic()

    async with AsyncClient(transport=ASGITransport(app=_APP),
                           base_url="http://testserver") as client:

        async def _post():
            r = await client.post(f"/fenster/{a_post.token}", data={"wert": _FAKE_WERT})
            reihenfolge.append("post")
            zeiten["post"] = time.monotonic() - start
            return r

        async def _get():
            r = await client.get(f"/fenster/{a_get.token}")
            reihenfolge.append("get")
            zeiten["get"] = time.monotonic() - start
            # Erst JETZT den Schreiber entriegeln, aus demselben Grund wie
            # beim oauth-Pendant oben.
            freigegeben.set()
            return r

        post_antwort, get_antwort = await asyncio.gather(_post(), _get())

    assert get_antwort.status_code == 200
    assert post_antwort.status_code == 200
    assert reihenfolge == ["get", "post"], (
        "das GET muss abgeschlossen sein, WAEHREND das blockierende bearer-"
        f"POST noch laeuft -- tatsaechliche Reihenfolge: {reihenfolge}, "
        f"Zeiten: {zeiten}")
    assert zeiten["get"] < 1.0, (
        "das GET durfte durch das laufende, blockierende bearer-POST nicht "
        f"verzoegert werden, brauchte aber {zeiten['get']:.3f}s")

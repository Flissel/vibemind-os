"""Server des Musik-Dienstes: Werkzeugliste, Kanal aus dem Kopf, JSON-Routen. In-Prozess ueber
den Starlette-TestClient, ohne Port; die Werkzeuge sind ein aufzeichnendes Ersatzobjekt."""
import asyncio
import json
import time

import pytest
from starlette.testclient import TestClient

from djset.dienst import server as dienst_server
from djset.dienst.server import bauen, dj_set_befehl, env_nachladen, kanal_aus_kopf

GUELTIG = "20261124-101500-video-ab12"
ERWARTET = {
    "bestand_zeigen", "song_aufnehmen", "set_bestellen", "videomusik_bestellen",
    "auftrag_zeigen", "auftrag_abbrechen", "in_laura_legen", "laura_videos_suchen",
    "laura_rendern",
}


class FakeWerkzeuge:
    """Schreibt jeden Aufruf (Name, Argumente) mit und gibt ein festes dict zurueck."""

    def __init__(self):
        self.aufrufe: list[tuple] = []
        self.schlaf_s = 0.0

    def _ruf(self, name, *args, **kwargs):
        self.aufrufe.append((name, args, kwargs))
        if self.schlaf_s:
            time.sleep(self.schlaf_s)
        return {"ok": True, "wer": name}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a, **k: self._ruf(name, *a, **k)

    def namen(self):
        return [a[0] for a in self.aufrufe]


@pytest.fixture
def fake():
    return FakeWerkzeuge()


@pytest.fixture
def client(fake):
    server = bauen(fake)
    app = server.streamable_http_app(host="127.0.0.1")
    with TestClient(app, base_url="http://127.0.0.1:8148") as c:
        yield c


MCP_KOPF = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _mcp(client, methode, params, kopf=None, id_=1):
    antwort = client.post(
        "/mcp", headers={**MCP_KOPF, **(kopf or {})},
        json={"jsonrpc": "2.0", "id": id_, "method": methode, "params": params},
    )
    text = antwort.text
    if antwort.headers.get("content-type", "").startswith("text/event-stream"):
        daten = [z[5:].strip() for z in text.splitlines() if z.startswith("data:")]
        return antwort, json.loads(daten[-1]) if daten else None
    return antwort, antwort.json() if text else None


def _werkzeug_rufen(client, name, argumente, kopf=None):
    """tools/call mit Sitzung (initialize zuerst), liefert die JSON-RPC-Antwort."""
    init, _ = _mcp(client, "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"}}, kopf)
    sitzung = {"mcp-session-id": init.headers["mcp-session-id"]} if "mcp-session-id" in init.headers else {}
    client.post("/mcp", headers={**MCP_KOPF, **(kopf or {}), **sitzung},
                json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    _, antwort = _mcp(client, "tools/call", {"name": name, "arguments": argumente},
                      {**(kopf or {}), **sitzung}, id_=2)
    return antwort


def test_werkzeugliste_genau(fake):
    server = bauen(fake)
    namen = {t.name for t in asyncio.run(server.list_tools())}
    assert namen == ERWARTET


def test_kein_werkzeug_hat_einen_kanal_parameter(fake):
    for t in asyncio.run(bauen(fake).list_tools()):
        eigenschaften = (t.input_schema or {}).get("properties", {})
        assert "kanal" not in eigenschaften and "ctx" not in eigenschaften, t.name


def test_kanal_aus_header(client, fake):
    antwort = _werkzeug_rufen(client, "set_bestellen", {"wunsch": "x", "minuten": 5},
                              {"X-Musik-Kanal": "telegram"})
    assert "error" not in antwort and antwort["result"]["isError"] is False
    assert fake.aufrufe[-1][0] == "set_bestellen"
    assert fake.aufrufe[-1][2]["kanal"] == "telegram"

    _werkzeug_rufen(client, "set_bestellen", {"wunsch": "x", "minuten": 5})
    assert fake.aufrufe[-1][2]["kanal"] == "mcp"

    _werkzeug_rufen(client, "set_bestellen", {"wunsch": "x", "minuten": 5},
                    {"X-Musik-Kanal": "root"})
    assert fake.aufrufe[-1][2]["kanal"] == "mcp"


def test_kanal_kopf_erlaubte_werte():
    assert kanal_aus_kopf({"X-Musik-Kanal": "app"}) == "app"
    assert kanal_aus_kopf({"x-musik-kanal": "video-claw"}) == "video-claw"
    assert kanal_aus_kopf({"X-Musik-Kanal": "mcp"}) == "mcp"
    assert kanal_aus_kopf({"X-Musik-Kanal": "Telegram"}) == "mcp"
    assert kanal_aus_kopf({}) == "mcp"
    assert kanal_aus_kopf(None) == "mcp"


def test_json_route_video(client, fake):
    antwort = client.post("/auftraege/video", json={
        "wunsch": "ruhig", "sekunden": 30, "kanal": "video-claw"})
    assert antwort.status_code == 200
    assert antwort.json() == {"ok": True, "wer": "videomusik_bestellen"}
    name, args, kwargs = fake.aufrufe[-1]
    assert name == "videomusik_bestellen" and args[0] == "ruhig" and args[1] == 30
    assert kwargs["kanal"] == "video-claw"


def test_json_route_kanal_unbekannt_ist_mcp(client, fake):
    client.post("/auftraege/set", json={"wunsch": "x", "minuten": 5, "kanal": "../x"})
    assert fake.aufrufe[-1][2]["kanal"] == "mcp"
    client.post("/auftraege/set", json={"wunsch": "x", "minuten": 5})
    assert fake.aufrufe[-1][2]["kanal"] == "mcp"


def test_json_route_kaputter_koerper_400(client, fake):
    antwort = client.post("/auftraege/set", content=b"{kaputt", headers={"Content-Type": "application/json"})
    assert antwort.status_code == 400 and antwort.json()["ok"] is False
    antwort = client.post("/auftraege/set", json=[1, 2])
    assert antwort.status_code == 400
    assert fake.aufrufe == []


def test_json_routen_rest(client, fake):
    assert client.get(f"/auftraege/{GUELTIG}").json()["wer"] == "auftrag_zeigen"
    assert fake.aufrufe[-1][1] == (GUELTIG,)

    assert client.get("/auftraege?kanal=telegram&offen=1").json()["wer"] == "auftraege_offen"
    assert fake.aufrufe[-1][1] == ("telegram",)
    client.get("/auftraege?kanal=quatsch&offen=1")
    assert fake.aufrufe[-1][1] == ("mcp",)

    assert client.post(f"/auftraege/{GUELTIG}/gemeldet").json()["wer"] == "gemeldet"
    assert fake.aufrufe[-1][1] == (GUELTIG,)

    r = client.post(f"/auftraege/{GUELTIG}/in-laura", json={"timeline_id": "t1", "gain_percent": 80})
    assert r.json()["wer"] == "in_laura_legen"
    assert fake.aufrufe[-1][1] == (GUELTIG, "t1", 80)

    r = client.post("/laura/rendern", json={"timeline_id": "t1", "kanal": "app"})
    assert r.json()["wer"] == "laura_rendern"
    assert fake.aufrufe[-1][2]["kanal"] == "app"

    assert client.get("/laura/suchen?q=reel").json()["wer"] == "laura_videos_suchen"
    assert fake.aufrufe[-1][1] == ("reel",)


def test_health_antwortet_waehrend_werkzeug_laeuft(fake):
    import threading

    fake.schlaf_s = 2.0
    server = bauen(fake)
    app = server.streamable_http_app(host="127.0.0.1")
    with TestClient(app, base_url="http://127.0.0.1:8148") as c:
        langsam = threading.Thread(target=lambda: c.get(f"/auftraege/{GUELTIG}"))
        langsam.start()
        time.sleep(0.3)
        t0 = time.monotonic()
        antwort = c.get("/health")
        dauer = time.monotonic() - t0
        langsam.join()
    assert antwort.status_code == 200 and antwort.json() == {"status": "ok"}
    assert dauer < 0.5
    assert fake.namen() == ["auftrag_zeigen"]  # das lange Werkzeug lief wirklich


@pytest.mark.parametrize("pfad", [
    "/auftraege/..%5Cx", "/auftraege/%2e%2e", "/auftraege/..%2fx", "/auftraege/abc",
    f"/auftraege/{GUELTIG}x", f"/auftraege/{GUELTIG}%0a",
])
def test_json_route_boese_id_404(client, fake, pfad):
    antwort = client.get(pfad)
    assert antwort.status_code == 404
    assert fake.aufrufe == []


def test_boese_id_404_auch_fuer_post_routen(client, fake):
    assert client.post("/auftraege/..%5Cx/gemeldet").status_code == 404
    assert client.post("/auftraege/%2e%2e/in-laura", json={"timeline_id": "t"}).status_code == 404
    assert fake.aufrufe == []


def test_env_nachladen_nur_fehlende(tmp_path, monkeypatch):
    datei = tmp_path / ".env"
    datei.write_text('MUSIK_LINK_HOST="10.0.0.1"\nMUSIK_LINK_PORT=9\nANDERES=1\n', encoding="utf-8")
    monkeypatch.delenv("MUSIK_LINK_HOST", raising=False)
    monkeypatch.setenv("MUSIK_LINK_PORT", "8146")
    monkeypatch.delenv("ANDERES", raising=False)
    env_nachladen(datei, ("MUSIK_LINK_HOST", "MUSIK_LINK_PORT"))
    import os
    assert os.environ["MUSIK_LINK_HOST"] == "10.0.0.1"
    assert os.environ["MUSIK_LINK_PORT"] == "8146"  # nicht ueberschrieben
    assert "ANDERES" not in os.environ  # nicht in der Liste
    monkeypatch.delenv("MUSIK_LINK_HOST", raising=False)


def test_dj_set_befehl_nutzt_den_interpreter():
    befehl = dj_set_befehl()
    assert befehl[0].endswith(".exe") or befehl[1:] == ["-m", "djset.cli"]


def test_main_ohne_daten_endet_mit_meldung(monkeypatch, capsys):
    monkeypatch.delenv("MUSIK_DATEN", raising=False)
    monkeypatch.setattr(dienst_server, "_aeusseres_env", lambda: None)
    assert dienst_server.main([]) == 2
    assert "MUSIK_DATEN" in capsys.readouterr().err

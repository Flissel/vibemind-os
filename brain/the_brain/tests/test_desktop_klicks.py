"""Klickmarken (GET /api/desktop/klicks) und Klick durchreichen (POST /api/desktop/hand-klick).

Ohne Bildschirm und ohne Netz: das Aufgabenbuch liegt in tmp_path, ``klicken`` ist ersetzt.
Der Weg ueber den echten Executor und den Werkzeug-Dienst steht in tests/test_desktop_schleife.py.
Alle Auftragstexte sind erfunden.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import desktop_klicks as dk  # noqa: E402
from web.routers import desktop_klicks as route  # noqa: E402

JETZT = 1_800_000_000.0


def _aufgabe(ordner: Path, kennung: str, text: str, schritte: list, *, trocken: bool = False) -> None:
    pfad = ordner / f"{kennung}.json"
    pfad.write_text(json.dumps(
        {"id": kennung, "aufgabe": text, "zustand": "fertig", "trocken": trocken, "schritte": schritte}),
        encoding="utf-8")
    os.utime(pfad, (JETZT, JETZT))  # zuletzt geschrieben "jetzt" (die Testzeit)


def _schritt(art="click", x=10, y=20, zeit=JETZT - 5, ok=True, echt=True, meldung="ausgeführt", **mehr):
    angaben = {"x": x, "y": y, **mehr} if art == "click" else {"text": "geheim"}
    return {"nr": 1, "zeit": zeit, "art": art, "angaben": angaben, "ok": ok, "echt": echt, "meldung": meldung}


@pytest.fixture
def buch(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBEMIND_DESKTOP_AUFGABEN_DIR", str(tmp_path))
    return tmp_path


def test_klicks_aus_dem_buch_ohne_text_mit_quelle(buch):
    _aufgabe(buch, "dt-20270115-080000-aaaaaa", "Schreibe Einkaufsliste in den Editor",
             [_schritt(x=1, y=2, zeit=JETZT - 30), _schritt(art="type"), _schritt(x=3, y=4, zeit=JETZT - 20)])
    _aufgabe(buch, "dt-20270115-080100-bbbbbb", "Hand: Klick", [_schritt(x=5, y=6, zeit=JETZT - 10, quelle="hand")])
    _aufgabe(buch, "dt-20270115-080200-cccccc", "Hand: Klick",
             [_schritt(x=7, y=8, zeit=JETZT - 8, ok=False, echt=False,
                       meldung="gesperrt: das Fenster unter dem Punkt steht auf der Sperrliste")])
    _aufgabe(buch, "dt-20270115-080300-dddddd", "Klick im Trockenlauf",
             [_schritt(x=9, y=9, zeit=JETZT - 4, echt=False, meldung="trocken: gebucht")], trocken=True)
    stand = dk.klicks(JETZT - 60, jetzt=JETZT)
    assert stand["success"] is True and stand["stopped"] is False
    assert [(k["x"], k["y"], k["quelle"], k["ok"], k["echt"], k.get("grund"), k["hand"]) for k in stand["klicks"]] == [
        (1, 2, "agent", True, True, None, False), (3, 4, "agent", True, True, None, False),
        (5, 6, "hand", True, True, None, True), (7, 8, "hand", False, False, "gesperrt", True),
        (9, 9, "trocken", True, False, None, False)]
    text = json.dumps(stand)
    for geheim in ("Einkaufsliste", "geheim", "Sperrliste", "Trockenlauf"):
        assert geheim not in text


def test_nur_seit_und_hoechstens_ein_tag(buch):
    _aufgabe(buch, "dt-20270115-080000-aaaaaa", "x", [_schritt(x=1, zeit=JETZT - 300), _schritt(x=2, zeit=JETZT - 30)])
    assert [k["x"] for k in dk.klicks(JETZT - 60, jetzt=JETZT)["klicks"]] == [2]
    assert [k["x"] for k in dk.klicks(None, jetzt=JETZT)["klicks"]] == [2]  # Vorgabe: zwei Minuten
    assert [k["x"] for k in dk.klicks(0, jetzt=JETZT)["klicks"]] == [1, 2]


def test_fremde_dateien_und_kaputte_eintraege_fallen_weg(buch):
    (buch / "dt-kaputt.json").write_text("{", encoding="utf-8")
    (buch / "notiz.json").write_text("[]", encoding="utf-8")
    _aufgabe(buch, "dt-20270115-080000-aaaaaa", "x", [
        _schritt(x="1"), _schritt(x=True), {"art": "click", "zeit": "gestern"}, "unsinn", _schritt(x=4, y=5)])
    (buch / "STOP").write_text("", encoding="utf-8")
    stand = dk.klicks(JETZT - 60, jetzt=JETZT)
    assert [(k["x"], k["y"]) for k in stand["klicks"]] == [(4, 5)] and stand["stopped"] is True


def test_ohne_ordner_ehrlich(monkeypatch):
    monkeypatch.delenv("VIBEMIND_DESKTOP_AUFGABEN_DIR", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(dk, "_CONTAINER_ORDNER", Path("/gibt/es/nicht"))
    assert dk.klicks(0, jetzt=JETZT) == {"success": False, "error": "aufgabenbuch_fehlt", "klicks": [], "stopped": False}


# ─── Routen ──────────────────────────────────────────────────────────────────

SCHLUESSEL = "beispiel-schluessel-nur-fuer-tests-0123456789"


def gezeichnet(x, y, *, zeit=None, schluessel=SCHLUESSEL):
    import time
    from core.desktop_tagesrest import eingabe_zeichen
    return {"X-Vibemind-Eingabe": eingabe_zeichen(schluessel, f"hand-klick {x} {y}", int(time.time() if zeit is None else zeit))}


@pytest.fixture
def client(buch, monkeypatch):
    app = FastAPI()
    app.include_router(route.router)
    aufrufe: list = []

    def klicken(x, y):
        aufrufe.append((x, y))
        return {"x": x, "y": y, "ok": True, "echt": True, "trocken": False, "grund": None, "task_id": "dt-x", "plan_id": "p"}

    import core.desktop_hand as hand
    monkeypatch.setattr(hand, "klicken", klicken)
    monkeypatch.setenv("VIBEMIND_EINGABE_SCHLUESSEL", SCHLUESSEL)
    monkeypatch.setattr(hand, "_VERBRAUCHT", {})
    c = TestClient(app, base_url="http://127.0.0.1:5000")
    c.aufrufe = aufrufe
    return c


def test_marken_fuer_programme_und_den_eigenen_ursprung(client, buch):
    _aufgabe(buch, "dt-20270115-080000-aaaaaa", "x", [_schritt(x=1)])
    assert client.get("/api/desktop/klicks", params={"seit": 0}).json()["success"] is True
    assert client.get("/api/desktop/klicks", params={"seit": "nan"}).status_code == 400
    assert client.get("/api/desktop/klicks", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


@pytest.mark.parametrize("koepfe", [
    {"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://fremd.example"}, {"Origin": "null"},
    {"Host": "boese.example:5000"}])
def test_marken_nicht_fuer_fremde_seiten(client, koepfe):
    antwort = client.get("/api/desktop/klicks", headers=koepfe)
    assert antwort.status_code == 403 and antwort.json()["grund"] == "nur_lokal"


def test_hand_klick_vom_hauptprozess(client):
    antwort = client.post("/api/desktop/hand-klick", json={"x": 100, "y": -20}, headers=gezeichnet(100, -20))
    assert antwort.status_code == 200 and antwort.json()["ok"] is True
    assert client.aufrufe == [(100, -20)]


@pytest.mark.parametrize("koepfe", [
    {"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "cross-site"}, {"Origin": "null"},
    {"Origin": "http://127.0.0.1:5000"}, {"Host": "boese.example"}])
def test_hand_klick_nie_aus_einer_webseite(client, koepfe):
    antwort = client.post("/api/desktop/hand-klick", json={"x": 1, "y": 2}, headers={**gezeichnet(1, 2), **koepfe})
    assert antwort.status_code == 403 and client.aufrufe == []


@pytest.mark.parametrize("body", [{"x": 1}, {"x": 1, "y": 2, "taste": "rechts"}, [1, 2], "x"])
def test_hand_klick_nur_x_und_y(client, body):
    antwort = client.post("/api/desktop/hand-klick", json=body, headers=gezeichnet(1, 2))
    assert antwort.status_code == 400 and client.aufrufe == []


def test_hand_klick_nur_mit_dem_zeichen_der_app(client, monkeypatch):
    import time
    def post(koepfe):
        return client.post("/api/desktop/hand-klick", json={"x": 5, "y": 6}, headers=koepfe)
    # Host-Kopf und fehlendes Origin allein genuegen nicht (das kann jeder im Netz vortaeuschen).
    for koepfe in ({}, {"X-Vibemind-Eingabe": "kaputt"}, gezeichnet(5, 7), gezeichnet(5, 6, schluessel="x" * 40),
                   gezeichnet(5, 6, zeit=time.time() - 120)):
        antwort = post(koepfe)
        assert (antwort.status_code, antwort.json()["grund"]) == (403, "nicht_belegt")
    zeichen = gezeichnet(5, 6)
    assert post(zeichen).status_code == 200
    assert post(zeichen).json()["grund"] == "nicht_belegt"  # jedes Zeichen nur einmal
    monkeypatch.delenv("VIBEMIND_EINGABE_SCHLUESSEL")
    assert post(gezeichnet(5, 6)).status_code == 403  # ohne Schluessel im Gehirn nie
    assert client.aufrufe == [(5, 6)]


def test_klicken_prueft_den_punkt_vor_jedem_aufruf(monkeypatch):
    from core import desktop_hand
    monkeypatch.setattr(desktop_hand, "fester_aufruf", lambda *a, **k: pytest.fail("kein Aufruf"))
    for x, y in (("1", 2), (1.5, 2), (True, 2), (None, 2), (1, 20001)):
        assert desktop_hand.klicken(x, y)["grund"] == "eingabe_ungueltig"

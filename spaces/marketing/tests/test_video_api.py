"""Reel-Auftraege (Video-Claw, 17.10.2026, Migration 072) ohne echte DB - FalscheDB wie test_ton_api."""
import time

import pytest
from fastapi.testclient import TestClient

from spaces.marketing.api import medien_mandant, server
from spaces.marketing.api import video
from spaces.marketing.sync import _db
from spaces.marketing.tests.test_bilder_api import FalschePsql
from spaces.marketing.tests.test_pult_api import FalscheDB

AID = "0123abcd-3333-3333-3333-333333333333"
BK, AK, PK = "bild-k", "api-k", "pult-k"
A, B, P = {"X-API-Key": AK}, {"X-Bild-Key": BK}, {"X-Pult-Key": PK}
MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 3000
BILDER = {"thema": "bAV in 30 Sekunden", "sprechertext": "Drei Fehler, die fast jede Firma macht.",
          "bilder": ["team.png", "buero.jpg"], "logo": "logo-vibemind-0123456789.png",
          "farben": {"hintergrund": "#0f2422", "text": "#e9fbf6"}}


@pytest.fixture
def db(monkeypatch, tmp_path):
    f = FalscheDB()
    f.psql = FalschePsql(f)
    monkeypatch.setattr(_db, "query_via_docker", f.query)
    monkeypatch.setattr(_db, "query_one", f.one)
    monkeypatch.setattr(_db, "_run_psql", f.psql)
    monkeypatch.setenv("MARKETING_BILD_KEY", BK)
    monkeypatch.setenv("MARKETING_PULT_KEY", PK)
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    monkeypatch.delenv("MARKETING_MEDIEN_ORDNER", raising=False)
    monkeypatch.delenv("MARKETING_VIDEO_MEDIEN_BASIS", raising=False)
    monkeypatch.setattr(server, "API_KEY", AK)
    monkeypatch.setattr(medien_mandant, "sicht", lambda m: {"name": m, "eigene": set(), "gemeinsam": set(),
                                                            "fremde": {"fremd.png"}})
    f.ordner = tmp_path
    return f


@pytest.fixture
def c():
    return TestClient(server.app)


# --- Briefing ----------------------------------------------------------------

def test_briefing_bilder_wird_gekuerzt():
    b = video.briefing_pruefen("bilder", {**BILDER, "unbekannt": "weg"})
    assert "unbekannt" not in b and b["dauer_s"] == 30 and b["stimme"] == "standard"
    assert b["bilder"] == ["team.png", "buero.jpg"] and b["hook"] == ""


@pytest.mark.parametrize("art, b, grund", [
    ("film", BILDER, "art"),
    ("bilder", {**BILDER, "bilder": []}, "bilder"),
    ("bilder", {**BILDER, "bilder": ["../x.png"]}, "bilder"),
    ("bilder", {**BILDER, "bilder": ["a.gif"]}, "bilder"),
    ("bilder", {**BILDER, "farben": {"hintergrund": "red"}}, "farben"),
    ("bilder", {**BILDER, "dauer_s": 90}, "dauer_s"),
    ("bilder", {**BILDER, "dauer_s": True}, "dauer_s"),
    ("bilder", {**BILDER, "stimme": "../anna"}, "stimme"),
    ("bilder", {**BILDER, "sprechertext": "x" * 901}, "sprechertext"),
    ("bilder", {**BILDER, "thema": ""}, "thema"),
    ("material", {"thema": "t", "sprechertext": "s", "videos": ["nicht hex!"]}, "videos"),
    ("kurzfassung", {"thema": "t"}, "video"),
])
def test_briefing_formen(art, b, grund):
    with pytest.raises(Exception) as e:
        video.briefing_pruefen(art, b)
    assert getattr(e.value, "status_code", None) == 422 and grund in e.value.detail


def test_kurzfassung_ohne_sprechertext():
    b = video.briefing_pruefen("kurzfassung", {"thema": "t", "video": "a1b2c3d4e5f6"})
    assert b["sprechertext"] == "" and b["video"] == "a1b2c3d4e5f6"


# --- Anlegen -----------------------------------------------------------------

def test_agent_legt_an_ueber_funktion(db, c):
    db.antworten = [[{"id": AID}]]
    r = c.post("/api/video/auftraege", headers=A,
               json={"firma": "vibemind", "art": "bilder", "titel": "Herbst-Reel", "briefing": BILDER})
    assert r.status_code == 200 and r.json() == {"auftrag": AID}
    assert "marketing.video_auftrag_anlegen(" in db.sql[-1] and "'agent'" in db.sql[-1]
    assert "INSERT" not in db.sql[-1]


def test_fremdes_bild_abgelehnt(db, c):
    r = c.post("/api/video/auftraege", headers=A, json={
        "firma": "vibemind", "art": "bilder", "titel": "T", "briefing": {**BILDER, "bilder": ["fremd.png"]}})
    assert r.status_code == 422 and "anderen Firma" in r.json()["detail"] and not db.sql


def test_pult_legt_als_betreiber_an(db, c):
    db.antworten = [[{"id": AID}]]
    r = c.post("/api/pult/video", headers=P, json={"mandant": "vibemind", "art": "material", "titel": "T",
                                                   "briefing": {"thema": "t", "sprechertext": "s"}})
    assert r.status_code == 200 and "'betreiber'" in db.sql[-1]


def test_pult_abbrechen(db, c):
    db.antworten = [[{"s": "abgebrochen"}]]
    r = c.post(f"/api/pult/video/{AID}/abbrechen", headers=P)
    assert r.status_code == 200 and r.json() == {"status": "abgebrochen"}
    assert "marketing.video_abbrechen(" in db.sql[-1]


def test_schluessel_pflicht(db, c):
    assert c.get("/api/pult/video?mandant=vibemind").status_code == 401
    assert c.get("/api/video/auftraege?firma=vibemind").status_code == 401
    assert c.post("/api/video/arbeiter/naechster").status_code == 401


# --- Arbeiter ----------------------------------------------------------------

def test_naechster_und_weiter(db, c):
    db.antworten = [[{"a": {"id": AID, "art": "bilder"}}], [{"ok": True}]]
    assert c.post("/api/video/arbeiter/naechster", headers=B).json()["auftrag"]["id"] == AID
    assert c.post(f"/api/video/arbeiter/{AID}/weiter", headers=B).json() == {"ok": True}


def _in_arbeit(db, status="in_arbeit"):
    return [{"titel": "Herbst-Reel im Laden", "mandant": "vibemind", "status": status, "briefing": {}}]


def test_datei_ablegen(db, c):
    db.antworten = [_in_arbeit(db)]
    r = c.post(f"/api/video/arbeiter/{AID}/datei", headers=B, content=MP4)
    assert r.status_code == 200
    name = r.json()["name"]
    assert name == "vid-0123abcd-herbst-reel-im-laden.mp4" and (db.ordner / name).read_bytes() == MP4


def test_datei_nur_mp4(db, c):
    r = c.post(f"/api/video/arbeiter/{AID}/datei", headers=B, content=b"GIF89a" + b"\x00" * 100)
    assert r.status_code == 422


def test_datei_zu_gross(db, c):
    r = c.post(f"/api/video/arbeiter/{AID}/datei", headers=B, content=MP4 + b"\x00" * video.MP4_MAX)
    assert r.status_code == 413


def test_datei_nicht_in_arbeit(db, c):
    db.antworten = [_in_arbeit(db, "abgebrochen")]
    assert c.post(f"/api/video/arbeiter/{AID}/datei", headers=B, content=MP4).status_code == 409


def test_fertig(db, c):
    name = "vid-0123abcd-herbst.mp4"
    (db.ordner / name).write_bytes(MP4)
    db.antworten = [[{"v": "fertig"}]]
    r = c.post(f"/api/video/arbeiter/{AID}/fertig", headers=B,
               json={"datei": name, "dauer_s": 24.5, "laura_export_id": "a1b2c3d4e5f6"})
    assert r.status_code == 200 and r.json() == {"status": "fertig"}
    assert f"{len(MP4)}" in db.sql[-1] and "'a1b2c3d4e5f6'" in db.sql[-1]


@pytest.mark.parametrize("koerper", [
    {"datei": "vid-ffffffff-fremd.mp4", "dauer_s": 10},
    {"datei": "../x.mp4", "dauer_s": 10},
    {"datei": "vid-0123abcd-herbst.mp4", "dauer_s": 500},
    {"befund": 5},
    {"laura_export_id": "x y"},
])
def test_fertig_formen(db, c, koerper):
    (db.ordner / "vid-0123abcd-herbst.mp4").write_bytes(MP4)
    assert c.post(f"/api/video/arbeiter/{AID}/fertig", headers=B, json=koerper).status_code == 422


def test_fehler_ohne_datei(db, c):
    db.antworten = [[{"v": "fehler"}]]
    r = c.post(f"/api/video/arbeiter/{AID}/fertig", headers=B, json={"befund": "Laura 503"})
    assert r.json() == {"status": "fehler"} and "NULL" in db.sql[-1]


# --- signierter Medienlink fuer Laura ----------------------------------------

def test_medienlink_und_abruf(db, c, monkeypatch):
    (db.ordner / "team.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
    monkeypatch.setenv("MARKETING_VIDEO_MEDIEN_BASIS", "https://vm.example:8446")
    db.antworten = [_in_arbeit(db)]
    r = c.post(f"/api/video/arbeiter/{AID}/medien-link", headers=B, json={"name": "team.png"})
    assert r.status_code == 200
    url = r.json()["url"]
    assert url.startswith("https://vm.example:8446/api/video/medien/") and url.endswith("/team.png")
    pfad = url[len("https://vm.example:8446"):]
    abruf = c.get(pfad)        # ohne jeden Schluessel - die Signatur ist die Berechtigung
    assert abruf.status_code == 200 and abruf.content.startswith(b"\x89PNG")


def test_medienlink_fremdes_bild(db, c):
    (db.ordner / "fremd.png").write_bytes(b"\x89PNG")
    db.antworten = [_in_arbeit(db)]
    r = c.post(f"/api/video/arbeiter/{AID}/medien-link", headers=B, json={"name": "fremd.png"})
    assert r.status_code == 422


def test_abruf_gefaelscht_oder_abgelaufen(db, c):
    (db.ordner / "team.png").write_bytes(b"\x89PNG")
    ablauf = int(time.time()) + 600
    assert c.get(f"/api/video/medien/{ablauf}/{'0' * 40}/team.png").status_code == 403
    alt = int(time.time()) - 10
    assert c.get(f"/api/video/medien/{alt}/{video._signatur(alt, 'team.png')}/team.png").status_code == 403
    weit = int(time.time()) + 86400
    assert c.get(f"/api/video/medien/{weit}/{video._signatur(weit, 'team.png')}/team.png").status_code == 403
    gut = video._signatur(ablauf, "team.png")
    assert c.get(f"/api/video/medien/{ablauf}/{gut}/andere.png").status_code in (403, 404)

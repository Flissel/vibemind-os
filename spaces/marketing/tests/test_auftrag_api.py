"""Auftrags-Arbeiter-API (M8 Stufe A, Task 3) ohne echte DB - FalscheDB wie test_video_api."""
import pytest
from fastapi.testclient import TestClient

from spaces.marketing.api import medien_mandant, server, video
from spaces.marketing.sync import _db
from spaces.marketing.tests.test_bilder_api import FalschePsql
from spaces.marketing.tests.test_pult_api import FalscheDB

AID = "0123abcd-4444-4444-4444-444444444444"
VID = "0123abcd-5555-5555-5555-555555555555"
BK, AK = "bild-k", "api-k"
B = {"X-Bild-Key": BK}
REEL = {"titel": "bAV-Reel", "thema": "bAV in 30 Sekunden", "sprechertext": "Drei Fehler, die fast jede Firma macht.",
        "hook": "Stopp!", "dauer_s": 30}
ERZEUGT = {"titel": "bAV: drei Fehler", "text_lang": "Langer Text.", "text_kurz": "Kurzer Text."}
DATEI = "vid-0123abcd-reel.mp4"


@pytest.fixture
def db(monkeypatch, tmp_path):
    f = FalscheDB()
    f.psql = FalschePsql(f)
    monkeypatch.setattr(_db, "query_via_docker", f.query)
    monkeypatch.setattr(_db, "query_one", f.one)
    monkeypatch.setattr(_db, "_run_psql", f.psql)
    monkeypatch.setenv("MARKETING_BILD_KEY", BK)
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    monkeypatch.setattr(server, "API_KEY", AK)
    monkeypatch.setattr(medien_mandant, "sicht", lambda m: {"name": m, "eigene": set(), "gemeinsam": set(),
                                                            "fremde": set()})
    return f


@pytest.fixture
def c():
    return TestClient(server.app)


def test_ohne_bild_key_401(db, c):
    assert c.post("/api/auftrag/arbeiter/naechster").status_code == 401
    assert c.post("/api/auftrag/arbeiter/naechster", headers={"X-Bild-Key": "falsch"}).status_code == 401
    assert c.get("/api/auftrag/arbeiter/laufend").status_code == 401
    assert c.post(f"/api/auftrag/arbeiter/{AID}/fehler", json={"grund": "x"}).status_code == 401
    assert db.sql == []


def test_naechster_leer_gibt_null(db, c):
    db.antworten = [[{"a": None}]]
    r = c.post("/api/auftrag/arbeiter/naechster", headers=B)
    assert r.status_code == 200 and r.json() == {"auftrag": None}
    assert "auftrag.naechster('marketing'" in db.sql[0] and "10 minutes" in db.sql[0]


def test_laufend_listet_auftraege_mit_video(db, c):
    db.antworten = [[{"a": {"id": AID, "ergebnis": {"video": VID}}}]]
    r = c.get("/api/auftrag/arbeiter/laufend", headers=B)
    assert r.status_code == 200 and r.json() == {"auftraege": [{"id": AID, "ergebnis": {"video": VID}}]}
    assert "auftrag.laufende('marketing')" in db.sql[0]


def test_weiter(db, c):
    db.antworten = [[{"id": AID}], [{"ok": True}]]
    r = c.post(f"/api/auftrag/arbeiter/{AID}/weiter", headers=B)
    assert r.json() == {"ok": True} and "an = 'marketing'" in db.sql[0] and "auftrag.verlaengern" in db.sql[1]
    assert c.post("/api/auftrag/arbeiter/kein-uuid/weiter", headers=B).status_code == 404


def test_reel_legt_ki_video_mit_auftrag_im_briefing_an(db, c):
    db.antworten = [[{"mandant": "fin2gether", "stand": "in_arbeit", "ergebnis": {}}], [], [{"id": VID}], [{"ok": True}], [{"ok": True}]]
    r = c.post(f"/api/auftrag/arbeiter/{AID}/reel", json=REEL, headers=B)
    assert r.status_code == 200 and r.json() == {"video": VID}
    frist = db.sql[4]                                   # I6: eigenes Renderbudget ueber die SQL-Funktion
    assert frist == f"SELECT auftrag.renderfrist('{AID}'::uuid, 120) AS ok"
    assert "WITH" not in frist and "UPDATE" not in frist
    mischen = db.sql[3]
    assert "auftrag.ergebnis_mischen" in mischen and VID in mischen
    drehbuch = ('"drehbuch": {"thema": "bAV in 30 Sekunden", "sprechertext": "Drei Fehler, die fast jede Firma '
                'macht.", "hook": "Stopp!", "dauer_s": 30, "titel": "bAV-Reel"}')
    assert drehbuch in mischen


def test_reel_setzt_die_markenstimme_ins_briefing(db, c, monkeypatch):
    monkeypatch.setattr(video, "stimmen_liste", lambda: ["anna"])
    db.antworten = [[{"mandant": "fin2gether", "stand": "in_arbeit", "ergebnis": {}}], [{"s": "anna"}],
                    [{"id": VID}], [{"ok": True}], [{"ok": True}]]
    r = c.post(f"/api/auftrag/arbeiter/{AID}/reel", json={**REEL, "stimme": "standard"}, headers=B)
    assert r.status_code == 200
    assert "marken_spiegel" in db.sql[1] and "'fin2gether'" in db.sql[1]
    assert '"stimme": "anna"' in db.sql[2]


@pytest.mark.parametrize("methode, pfad, body", [
    ("post", "weiter", None), ("post", "reel", REEL), ("get", "reel", None),
    ("post", "erzeugt", ERZEUGT), ("post", "fehler", {"grund": "x"})])
def test_fremder_auftrag_404_ohne_schreiben(db, c, methode, pfad, body):
    db.antworten = [[]]                       # Zeile mit an = 'marketing' gibt es nicht (an = 'rowboat')
    r = getattr(c, methode)(f"/api/auftrag/arbeiter/{AID}/{pfad}", headers=B, **({"json": body} if body else {}))
    assert r.status_code == 404
    assert len(db.sql) == 1 and "an = 'marketing'" in db.sql[0] and "SELECT auftrag." not in db.sql[0]
    assert "video_auftrag_anlegen" not in db.sql[0]


def test_reel_zweimal_409(db, c):
    db.antworten = [[{"mandant": "fin2gether", "stand": "in_arbeit", "ergebnis": {"video": VID}}]]
    r = c.post(f"/api/auftrag/arbeiter/{AID}/reel", json=REEL, headers=B)
    assert r.status_code == 409 and len(db.sql) == 1


def test_reel_unbekannter_auftrag_404(db, c):
    db.antworten = [[]]
    assert c.post(f"/api/auftrag/arbeiter/{AID}/reel", json=REEL, headers=B).status_code == 404


def test_reel_briefing_ungueltig_422(db, c):
    r = c.post(f"/api/auftrag/arbeiter/{AID}/reel", json={**REEL, "dauer_s": 90}, headers=B)
    assert r.status_code == 422 and db.sql == []


def test_reel_lesen(db, c):
    db.antworten = [[{"status": "fertig", "datei": DATEI, "befund": ""}], []]
    r = c.get(f"/api/auftrag/arbeiter/{AID}/reel", headers=B)
    assert r.json() == {"status": "fertig", "datei": DATEI, "befund": ""}
    db.antworten = [[{"status": "fehler", "datei": None, "befund": "Render: Traceback secret-host:5432"}]]
    r = c.get(f"/api/auftrag/arbeiter/{AID}/reel", headers=B)
    assert r.json()["befund"] == "Laura konnte das Video nicht fertig rendern."     # I3: nie roh
    db.antworten = []
    assert c.get(f"/api/auftrag/arbeiter/{AID}/reel", headers=B).status_code == 404


@pytest.mark.parametrize("feld, wert", [("titel", "x" * 91), ("text_lang", "x" * 1301), ("text_kurz", "x" * 281),
                                        ("titel", " "), ("text_lang", ""), ("text_kurz", 5)])
def test_erzeugt_zu_langer_text_422(db, c, feld, wert):
    r = c.post(f"/api/auftrag/arbeiter/{AID}/erzeugt", json={**ERZEUGT, feld: wert}, headers=B)
    assert r.status_code == 422 and db.sql == []


def test_erzeugt_ohne_fertiges_video_409(db, c):
    db.antworten = [[{"stand": "in_arbeit", "status": "in_arbeit", "datei": None}]]
    r = c.post(f"/api/auftrag/arbeiter/{AID}/erzeugt", json=ERZEUGT, headers=B)
    assert r.status_code == 409 and len(db.sql) == 1


def test_erzeugt_nimmt_datei_aus_dem_video(db, c):
    db.antworten = [[{"stand": "in_arbeit", "status": "fertig", "datei": DATEI}], [{"ok": True}], [{"ok": True}]]
    r = c.post(f"/api/auftrag/arbeiter/{AID}/erzeugt", json={**ERZEUGT, "datei": "boese.mp4"}, headers=B)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert DATEI in db.sql[1] and "boese" not in db.sql[1]
    assert "stand_setzen" in db.sql[2] and "'in_arbeit'" in db.sql[2] and "'erzeugt'" in db.sql[2]


def test_fehler_kuerzt_grund_auf_300(db, c):
    db.antworten = [[{"id": AID}], [{"ok": True}]]
    r = c.post(f"/api/auftrag/arbeiter/{AID}/fehler", json={"grund": "g" * 500}, headers=B)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert "'" + "g" * 300 + "'" in db.sql[1] and "g" * 301 not in db.sql[1]
    assert "'in_arbeit'" in db.sql[1] and "'fehler'" in db.sql[1]

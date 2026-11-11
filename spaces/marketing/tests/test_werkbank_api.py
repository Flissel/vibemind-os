"""Werkbank-API (Arbeiter, Pult, Upload in Teilen) ohne echte DB - FalscheDB wie test_auftrag_api."""
import hashlib
import re
import types

import pytest
from fastapi.testclient import TestClient

from spaces.marketing.api import medien_mandant, server, video, werkbank
from spaces.marketing.sync import _db
from spaces.marketing.tests.test_bilder_api import FalschePsql
from spaces.marketing.tests.test_pult_api import FalscheDB

AID = "0123abcd-4444-4444-4444-444444444444"
SID = "0123abcd-6666-6666-6666-666666666666"
VID2 = "0123abcd-5555-5555-5555-555555555555"
BK, PK, AK = "bild-k", "pult-k", "api-k"
B, P = {"X-Bild-Key": BK}, {"X-Pult-Key": PK}
A = "/api/werkbank/arbeiter"
PW = "/api/pult/werkbank"
NAME = "wb-0123abcd-s01-f1.mp4"
KENNUNG = "wb-abcdefghij"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100
KOPF_OK = {"mandant": "fin2gether", "stand": "in_arbeit", "werkbank": None}
SZENE_OK = {"auftrag": AID, "nr": 1, "mandant": "fin2gether"}


def gliederung(n: int) -> dict:
    return {"titel": "T", "hook": "H", "stufen": [{"name": "Eins", "szenen": [{"kurz": f"s{i}"} for i in range(n)]}],
            "fehlt": []}


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
    monkeypatch.delenv("MARKETING_WERKBANK_MAX_BYTES", raising=False)
    monkeypatch.setattr(server, "API_KEY", AK)
    monkeypatch.setattr(werkbank.shutil, "disk_usage", lambda o: types.SimpleNamespace(free=10 * 1024 ** 3))
    f.ordner = tmp_path
    return f


@pytest.fixture
def c():
    return TestClient(server.app)


def beginnen(db, c, groesse, name=NAME):
    db.antworten = [[KOPF_OK]]
    r = c.post(f"{A}/{AID}/datei/{name}/beginnen", json={"bytes": groesse}, headers=B)
    assert r.status_code == 200, r.text


def teil(db, c, daten, offset, name=NAME):
    db.antworten = [[KOPF_OK]]
    return c.put(f"{A}/{AID}/datei/{name}/teil?offset={offset}", content=daten, headers=B)


# ─── Schluessel und Verknuepfung ─────────────────────────────────────────


def test_ohne_schluessel_401(db, c):
    assert c.get(f"{A}/arbeit").status_code == 401
    assert c.post(f"{A}/naechster", headers={"X-Bild-Key": "falsch"}).status_code == 401
    assert c.post(f"{A}/{AID}/datei/{NAME}/beginnen", json={"bytes": 1}).status_code == 401
    assert c.get(f"{A}/{AID}/datei/{NAME}").status_code == 401
    assert c.get(f"{PW}/{AID}?mandant=vibemind").status_code == 401
    assert c.get(f"{PW}/v/{KENNUNG}", headers={"X-Pult-Key": "falsch"}).status_code == 401
    assert c.post(f"{PW}/v/{KENNUNG}/urteil", json={}).status_code == 401 and db.sql == []


def test_naechster_beginnt_das_werk(db, c):
    db.antworten = [[{"a": {"id": AID, "art": "video"}}], [{"w": {"auftrag": AID}}]]
    r = c.post(f"{A}/naechster", headers=B)
    assert r.status_code == 200 and r.json() == {"auftrag": {"id": AID, "art": "video"}}
    assert "auftrag.video_naechster('10 minutes'" in db.sql[0]
    assert f"marketing.video_werk_beginnen('{AID}'::uuid)" in db.sql[1]


def test_naechster_leer(db, c):
    db.antworten = [[{"a": None}]]
    assert c.post(f"{A}/naechster", headers=B).json() == {"auftrag": None} and len(db.sql) == 1


def test_weiter_und_frist(db, c):
    db.antworten = [[KOPF_OK], [{"ok": True}], [KOPF_OK], [{"ok": True}]]
    assert c.post(f"{A}/{AID}/weiter", headers=B).json() == {"ok": True}
    assert "an = 'marketing'" in db.sql[0] and "art = 'video'" in db.sql[0]
    assert "auftrag.verlaengern" in db.sql[1]
    assert c.post(f"{A}/{AID}/frist", headers=B).json() == {"ok": True}
    assert f"auftrag.renderfrist('{AID}'::uuid, 1440)" in db.sql[3]


@pytest.mark.parametrize("methode, pfad, body", [
    ("post", "weiter", None), ("post", "frist", None), ("post", "gliederung", {"gliederung": gliederung(8)}),
    ("post", "szene-nehmen", None), ("post", "aufruf", None), ("post", "hinweis", {"key": "k", "satz": "s"}),
    ("post", f"datei/{NAME}/beginnen", {"bytes": 5}), ("post", f"datei/{NAME}/abschliessen", {"sha256": "0" * 64}),
    ("get", f"datei/{NAME}", None)])
def test_fremder_auftrag_404_ohne_schreiben(db, c, methode, pfad, body):
    db.antworten = [[]]
    r = getattr(c, methode)(f"{A}/{AID}/{pfad}", headers=B, **({"json": body} if body else {}))
    assert r.status_code == 404
    assert len(db.sql) == 1 and "an = 'marketing'" in db.sql[0] and "art = 'video'" in db.sql[0]
    assert "SELECT marketing.video_" not in db.sql[0] and "SELECT auftrag." not in db.sql[0]


def test_szenenroute_fremder_auftrag_404(db, c):
    db.antworten = [[]]
    r = c.post(f"{A}/szene/{SID}/fehler", json={"grund": "x"}, headers=B)
    assert r.status_code == 404 and len(db.sql) == 1 and "art = 'video'" in db.sql[0]


def test_arbeit_mit_markenstimme(db, c, monkeypatch):
    monkeypatch.setattr(video, "stimmen_liste", lambda: ["anna"])
    db.antworten = [[{"w": {"auftrag": AID, "mandant": "fin2gether", "stimme": None}},
                     {"w": {"auftrag": VID2, "mandant": "vibemind"}}],
                    [{"s": "anna"}], []]
    r = c.get(f"{A}/arbeit", headers=B)
    assert r.status_code == 200
    stimmen = [w["stimme"] for w in r.json()["werke"]]
    assert stimmen == ["anna", "standard"]
    assert "marketing.video_arbeit()" in db.sql[0]



# ─── Gliederung, Text, Bild ──────────────────────────────────────────────


def test_gliederung_ok_und_stufen_ueber_alle_szenen(db, c):
    g = {"stufen": [{"name": "A", "szenen": [{"kurz": "a"}] * 4}, {"name": "B", "szenen": ["b1", "b2", "b3", "b4"]}]}
    db.antworten = [[KOPF_OK], [{"k": KENNUNG}]]
    r = c.post(f"{A}/{AID}/gliederung", json={"gliederung": g, "quellen": [{"u": 1}]}, headers=B)
    assert r.status_code == 200 and r.json() == {"kennung": KENNUNG}
    assert "marketing.video_gliederung_setzen" in db.sql[1]


@pytest.mark.parametrize("n", [7, 16, 30])
def test_gliederung_falsche_szenenzahl_422(db, c, n):
    db.antworten = [[KOPF_OK]]
    r = c.post(f"{A}/{AID}/gliederung", json={"gliederung": gliederung(n), "quellen": []}, headers=B)
    assert r.status_code == 422 and len(db.sql) == 1


@pytest.mark.parametrize("muell", ["", "  ", None, 5, [], {}, {"kurz": ""}, {"kurz": 3}, {"bildart": "slide"}])
def test_gliederung_mit_muelleintrag_422(db, c, muell):
    g = gliederung(8)
    g["stufen"][0]["szenen"].append(muell)                   # 8 gueltige + Muell: SQL machte daraus 9 Szenen
    db.antworten = [[KOPF_OK]]
    assert c.post(f"{A}/{AID}/gliederung", json={"gliederung": g}, headers=B).status_code == 422
    assert len(db.sql) == 1


def test_gliederung_15_gueltige_plus_muell_422_und_16_gueltige_422(db, c):
    g = gliederung(15)
    g["stufen"].append({"name": "Z", "szenen": [None]})
    db.antworten = [[KOPF_OK]]
    assert c.post(f"{A}/{AID}/gliederung", json={"gliederung": g}, headers=B).status_code == 422
    db.antworten = [[KOPF_OK]]
    assert c.post(f"{A}/{AID}/gliederung", json={"gliederung": gliederung(16)}, headers=B).status_code == 422


def test_gliederung_stufe_ohne_szenenliste_422(db, c):
    db.antworten = [[KOPF_OK]]
    g = {"stufen": [{"name": "A", "szenen": "x"}]}
    assert c.post(f"{A}/{AID}/gliederung", json={"gliederung": g}, headers=B).status_code == 422


def test_gliederung_ohne_stufen_422(db, c):
    db.antworten = [[KOPF_OK]]
    assert c.post(f"{A}/{AID}/gliederung", json={"gliederung": {"stufen": []}}, headers=B).status_code == 422


def test_gliederung_nicht_dran_409(db, c):
    db.antworten = [[KOPF_OK], [{"k": None}]]
    assert c.post(f"{A}/{AID}/gliederung", json={"gliederung": gliederung(8)}, headers=B).status_code == 409


TEXT = {"sprechtext": "Hallo.", "bildart": "slide", "uebergang": "schnitt", "quellen": [],
        "bild": {"schritte": [{"vorlage": "titel"}, {"vorlage": "zahl"}]}}


def test_text_ok(db, c):
    db.antworten = [[SZENE_OK], [{"k": KENNUNG}]]
    r = c.post(f"{A}/szene/{SID}/text", json=TEXT, headers=B)
    assert r.status_code == 200 and r.json() == {"kennung": KENNUNG}
    assert "marketing.video_szene_text_setzen" in db.sql[1]


def test_text_unbekannte_vorlage_422(db, c):
    db.antworten = [[SZENE_OK]]
    bild = {"schritte": [{"vorlage": "titel"}, {"vorlage": "gibtsnicht"}]}
    assert c.post(f"{A}/szene/{SID}/text", json={**TEXT, "bild": bild}, headers=B).status_code == 422


@pytest.mark.parametrize("aenderung", [
    {"bildart": "foto"}, {"uebergang": "wisch"}, {"bild": {"schritte": [{"vorlage": "titel"}]}},
    {"bild": {}}, {"bild": {"vorlage": "titel"}}, {"bild": {"schritte": "x"}}, {"bild": None},
    {"bild": {"schritte": [{"vorlage": "titel"}, "text"]}}, {"bild": {"schritte": [{"vorlage": "titel"}, {}]}},
    {"bild": {"schritte": [{"vorlage": "titel"}] * 6}}, {"sprechtext": ""}])
def test_text_ungueltig_422(db, c, aenderung):
    db.antworten = [[SZENE_OK]]
    assert c.post(f"{A}/szene/{SID}/text", json={**TEXT, **aenderung}, headers=B).status_code == 422
    assert len(db.sql) == 1


@pytest.mark.parametrize("bildart", ["slide", "avatar_gross", "avatar_slide", "clip"])
@pytest.mark.parametrize("bild", [{}, {"schritte": []}, {"schritte": [{"vorlage": "titel"}]},
                                  {"schritte": [{"vorlage": "titel"}, {"vorlage": "nix"}]}])
def test_bild_ohne_gueltige_schritte_422_fuer_jede_bildart(db, c, bildart, bild):
    db.antworten = [[SZENE_OK]]
    assert c.post(f"{A}/szene/{SID}/bild", json={"bildart": bildart, "bild": bild}, headers=B).status_code == 422
    assert len(db.sql) == 1


def test_bild(db, c):
    db.antworten = [[SZENE_OK], [{"ok": True}]]
    r = c.post(f"{A}/szene/{SID}/bild", json={"bildart": "avatar_slide", "bild": TEXT["bild"]}, headers=B)
    assert r.status_code == 200 and r.json() == {"ok": True} and "video_szene_bild_setzen" in db.sql[1]
    db.antworten = [[SZENE_OK]]
    assert c.post(f"{A}/szene/{SID}/bild", json={"bildart": "x", "bild": TEXT["bild"]}, headers=B).status_code == 422


BELEGE = [{"aussage": "12 Teilnehmer pro Gruppe", "quelle": "auftrag"}, {"aussage": "30 % Zeit", "quelle": "Marke.md"}]


def test_text_traegt_belege(db, c):
    db.antworten = [[SZENE_OK], [{"k": KENNUNG}]]
    r = c.post(f"{A}/szene/{SID}/text", json={**TEXT, "belege": BELEGE}, headers=B)
    assert r.status_code == 200
    assert '"belege": [{"aussage": "12 Teilnehmer pro Gruppe", "quelle": "auftrag"}' in db.sql[1]
    db.antworten = [[SZENE_OK], [{"k": KENNUNG}]]
    c.post(f"{A}/szene/{SID}/text", json=TEXT, headers=B)              # ohne belege: leere Liste
    assert '"belege": []' in db.sql[3]


@pytest.mark.parametrize("belege", [
    "x", [1], [{"aussage": "a"}], [{"aussage": "", "quelle": "q"}], [{"aussage": "a", "quelle": "q", "x": 1}],
    [{"aussage": "a" * 501, "quelle": "q"}], [{"aussage": "a", "quelle": "q" * 201}], [{"aussage": "a", "quelle": "q"}] * 41])
def test_belege_grenzen_422(db, c, belege):
    db.antworten = [[SZENE_OK]]
    assert c.post(f"{A}/szene/{SID}/text", json={**TEXT, "belege": belege}, headers=B).status_code == 422
    assert len(db.sql) == 1


def test_bild_mit_quellen_und_belegen(db, c):
    db.antworten = [[SZENE_OK], [{"ok": True}]]
    r = c.post(f"{A}/szene/{SID}/bild", json={"bildart": "slide", "bild": TEXT["bild"], "quellen": ["Marke.md"],
                                              "belege": BELEGE[1:]}, headers=B)
    assert r.status_code == 200
    assert '\'["Marke.md"]\'::jsonb, \'[{"aussage": "30 % Zeit", "quelle": "Marke.md"}]\'::jsonb) AS ok' in db.sql[1]
    db.antworten = [[SZENE_OK], [{"ok": True}]]
    c.post(f"{A}/szene/{SID}/bild", json={"bildart": "slide", "bild": TEXT["bild"]}, headers=B)
    assert "NULL::jsonb, NULL::jsonb) AS ok" in db.sql[3]


def test_szene_nehmen(db, c):
    db.antworten = [[KOPF_OK], [{"s": {"id": SID, "nr": 1}}]]
    r = c.post(f"{A}/{AID}/szene-nehmen", headers=B)
    assert r.json() == {"szene": {"id": SID, "nr": 1}} and "'20 minutes'::interval" in db.sql[1]


# ─── Clip, Fehler, Hinweis, Aufruf, Fertig ───────────────────────────────

CLIP = {"variante": 1, "datei": NAME, "dauer_s": 21.5, "stimme": "eigene-c", "engine": "chatterbox"}


def test_clip_ok(db, c):
    (db.ordner / NAME).write_bytes(MP4)
    db.antworten = [[SZENE_OK], [{"k": KENNUNG}]]
    r = c.post(f"{A}/szene/{SID}/clip", json=CLIP, headers=B)
    assert r.status_code == 200 and r.json() == {"kennung": KENNUNG}
    assert "video_szene_clip_setzen" in db.sql[1] and NAME in db.sql[1]


def test_clip_variante_null_wird_angenommen(db, c):
    """Szenen beginnen bei Variante 0 (099); die erste Datei heisst f0."""
    (db.ordner / "wb-0123abcd-s01-f0.mp4").write_bytes(MP4)
    db.antworten = [[SZENE_OK], [{"k": KENNUNG}]]
    r = c.post(f"{A}/szene/{SID}/clip", json={**CLIP, "variante": 0, "datei": "wb-0123abcd-s01-f0.mp4"}, headers=B)
    assert r.status_code == 200 and "video_szene_clip_setzen" in db.sql[1] and ", 0, " in db.sql[1]


@pytest.mark.parametrize("variante", [-1, 1001, 1.5, "0", True])
def test_clip_variante_ausserhalb_422(db, c, variante):
    db.antworten = [[SZENE_OK]]
    assert c.post(f"{A}/szene/{SID}/clip", json={**CLIP, "variante": variante}, headers=B).status_code == 422


def test_clip_dateiversion_muss_zur_variante_passen_409(db, c):
    (db.ordner / NAME).write_bytes(MP4)                      # f1, Body sagt variante 2
    db.antworten = [[SZENE_OK]]
    assert c.post(f"{A}/szene/{SID}/clip", json={**CLIP, "variante": 2}, headers=B).status_code == 409
    assert len(db.sql) == 1


def test_clip_fremder_dateiname_409(db, c):
    (db.ordner / "wb-ffffffff-s01-f1.mp4").write_bytes(MP4)
    db.antworten = [[SZENE_OK]]
    r = c.post(f"{A}/szene/{SID}/clip", json={**CLIP, "datei": "wb-ffffffff-s01-f1.mp4"}, headers=B)
    assert r.status_code == 409 and len(db.sql) == 1


def test_clip_andere_szene_409(db, c):
    (db.ordner / "wb-0123abcd-s02-f1.mp4").write_bytes(MP4)
    db.antworten = [[SZENE_OK]]
    assert c.post(f"{A}/szene/{SID}/clip", json={**CLIP, "datei": "wb-0123abcd-s02-f1.mp4"}, headers=B).status_code == 409


def test_clip_datei_fehlt_409(db, c):
    db.antworten = [[SZENE_OK]]
    assert c.post(f"{A}/szene/{SID}/clip", json=CLIP, headers=B).status_code == 409 and len(db.sql) == 1


def test_fehler_hinweis_aufruf(db, c):
    db.antworten = [[SZENE_OK], [{"ok": True}]]
    r = c.post(f"{A}/szene/{SID}/fehler", json={"grund": "x" * 500}, headers=B)
    assert r.json() == {"ok": True} and "x" * 300 in db.sql[1] and "x" * 301 not in db.sql[1]
    db.antworten = [[KOPF_OK], [{"ok": False}]]
    assert c.post(f"{A}/{AID}/hinweis", json={"key": "stimme_fehlt", "satz": "Satz."}, headers=B).json() == {"ok": False}
    assert c.post(f"{A}/{AID}/hinweis", json={"key": "Gross Klein", "satz": "s"}, headers=B).status_code == 422
    db.antworten = [[KOPF_OK], [{"n": 3}]]
    assert c.post(f"{A}/{AID}/aufruf", headers=B).json() == {"aufrufe": 3}


def test_aufgegeben_melden_szene_und_gliederung(db, c):
    db.antworten = [[SZENE_OK], [{"ok": True}]]
    r = c.post(f"{A}/szene/{SID}/aufgegeben", json={"grund": "Szene 1: Quelle fehlt.", "wunsch": "mehr"}, headers=B)
    assert r.json() == {"ok": True}
    assert f"marketing.video_szene_aufgeben('{SID}'::uuid, 'Szene 1: Quelle fehlt.', 'mehr')" in db.sql[1]
    db.antworten = [[SZENE_OK], [{"ok": False}]]
    assert c.post(f"{A}/szene/{SID}/aufgegeben", json={"grund": "g" * 400}, headers=B).json() == {"ok": False}
    assert "'" + "g" * 300 + "', '')" in db.sql[3]
    db.antworten = [[KOPF_OK], [{"ok": True}]]
    r = c.post(f"{A}/{AID}/gliederung-aufgegeben", json={"grund": "G"}, headers=B)
    assert r.json() == {"ok": True} and f"marketing.video_gliederung_aufgeben('{AID}'::uuid, 'G', '')" in db.sql[5]
    assert c.post(f"{A}/szene/{SID}/aufgegeben", json={"grund": 3}, headers=B).status_code == 422


def test_aufgegeben_fremder_auftrag_404(db, c):
    db.antworten = [[]]
    assert c.post(f"{A}/szene/{SID}/aufgegeben", json={"grund": "g"}, headers=B).status_code == 404
    db.antworten = [[]]
    assert c.post(f"{A}/{AID}/gliederung-aufgegeben", json={"grund": "g"}, headers=B).status_code == 404
    assert not any("aufgeben" in s for s in db.sql)


def test_video_gescheitert_mit_versuch(db, c):
    db.antworten = [[KOPF_OK], [{"ok": True}]]
    r = c.post(f"{A}/{AID}/video-gescheitert", json={"grund": "Platz fehlt.", "versuch": 2}, headers=B)
    assert r.json() == {"ok": True} and f"marketing.video_bau_gescheitert('{AID}'::uuid, 2, 'Platz fehlt.')" in db.sql[1]
    for koerper in ({"grund": "g"}, {"grund": "g", "versuch": -1}, {"grund": "g", "versuch": "1"}):
        assert c.post(f"{A}/{AID}/video-gescheitert", json=koerper, headers=B).status_code == 422


FERTIG = {"datei": "wb-0123abcd-video-f1.mp4", "bytes": 5000, "dauer_s": 240.0, "titel": "T", "beschreibung": "B",
          "zeitmarken": [{"t": 0, "titel": "Start"}], "messnotiz": "M", "warnungen": []}


def test_fertig_gibt_datei_im_koerper_weiter(db, c):
    (db.ordner / FERTIG["datei"]).write_bytes(MP4)
    db.antworten = [[KOPF_OK], [{"ok": True}]]
    r = c.post(f"{A}/{AID}/fertig", json=FERTIG, headers=B)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert "video_fertig_setzen" in db.sql[1] and '"datei": "wb-0123abcd-video-f1.mp4"' in db.sql[1]


@pytest.mark.parametrize("aenderung", [{"titel": "x" * 101}, {"beschreibung": "x" * 5001}, {"messnotiz": "x" * 1501}])
def test_fertig_grenzen_422(db, c, aenderung):
    (db.ordner / FERTIG["datei"]).write_bytes(MP4)
    db.antworten = [[KOPF_OK]]
    assert c.post(f"{A}/{AID}/fertig", json={**FERTIG, **aenderung}, headers=B).status_code == 422


@pytest.mark.parametrize("aenderung", [
    {"zeitmarken": "x"}, {"zeitmarken": ["x"]}, {"zeitmarken": [{"t": 0}] * 101}, {"zeitmarken": [{"t": "x" * 600}]},
    {"warnungen": "x"}, {"warnungen": [1]}, {"warnungen": ["x"] * 51}, {"warnungen": ["x" * 501]}])
def test_fertig_zeitmarken_und_warnungen_422(db, c, aenderung):
    (db.ordner / FERTIG["datei"]).write_bytes(MP4)
    db.antworten = [[KOPF_OK]]
    r = c.post(f"{A}/{AID}/fertig", json={**FERTIG, **aenderung}, headers=B)
    assert r.status_code == 422 and len(db.sql) == 1


def test_quellen_grenzen_422(db, c):
    for quellen in ("x", [1], ["x"] * 201, ["x" * 2001]):
        db.antworten = [[KOPF_OK]]
        r = c.post(f"{A}/{AID}/gliederung", json={"gliederung": gliederung(8), "quellen": quellen}, headers=B)
        assert r.status_code == 422, quellen


def test_fertig_datei_fehlt_oder_fremd_409(db, c):
    db.antworten = [[KOPF_OK]]
    assert c.post(f"{A}/{AID}/fertig", json=FERTIG, headers=B).status_code == 409
    db.antworten = [[KOPF_OK]]
    assert c.post(f"{A}/{AID}/fertig", json={**FERTIG, "datei": "wb-ffffffff-video-f1.mp4"}, headers=B).status_code == 409


# ─── Upload in Teilen ────────────────────────────────────────────────────


def test_platz_reicht_nicht_507(db, c, monkeypatch):
    monkeypatch.setattr(werkbank.shutil, "disk_usage", lambda o: types.SimpleNamespace(free=10))
    db.antworten = [[KOPF_OK]]
    r = c.post(f"{A}/{AID}/datei/{NAME}/beginnen", json={"bytes": 1000}, headers=B)
    assert r.status_code == 507 and not (db.ordner / (NAME + ".teil")).exists()


def test_ueber_zwei_gb_413(db, c):
    db.antworten = [[KOPF_OK]]
    r = c.post(f"{A}/{AID}/datei/{NAME}/beginnen", json={"bytes": 2 * 1024 ** 3 + 1}, headers=B)
    assert r.status_code == 413 and not (db.ordner / (NAME + ".teil")).exists()


def test_max_bytes_aus_der_umgebung(db, c, monkeypatch):
    monkeypatch.setenv("MARKETING_WERKBANK_MAX_BYTES", "100")
    db.antworten = [[KOPF_OK]]
    assert c.post(f"{A}/{AID}/datei/{NAME}/beginnen", json={"bytes": 101}, headers=B).status_code == 413


@pytest.mark.parametrize("name", ["../x.mp4", "wb-0123abcd-s1-f1.mp4", "wb-0123abcd-s01-f1.mp4.teil", "wb-0123ABCD-s01-f1.mp4",
                                  "x.mp4", "wb-0123abcd-s01-f1000.mp4"])
def test_ungueltiger_name_422(db, c, name):
    # "../x.mp4" enthaelt "/": der Router findet die Route nicht (404); alle anderen scheitern am Muster (422).
    erwartet = 404 if "/" in name else 422
    r = c.post(f"{A}/{AID}/datei/{name}/beginnen", json={"bytes": 5}, headers=B)
    assert r.status_code == erwartet and db.sql == []
    assert not list(db.ordner.iterdir())


def test_name_eines_anderen_auftrags_422(db, c):
    r = c.post(f"{A}/{AID}/datei/wb-ffffffff-s01-f1.mp4/beginnen", json={"bytes": 5}, headers=B)
    assert r.status_code == 422 and db.sql == [] and not list(db.ordner.iterdir())


def test_beginnen_legt_leere_teil_datei_an(db, c):
    beginnen(db, c, 100)
    assert (db.ordner / (NAME + ".teil")).read_bytes() == b""


def test_teil_haengt_an_und_meldet_laenge(db, c):
    beginnen(db, c, 100)
    assert teil(db, c, b"a" * 40, 0).json() == {"laenge": 40}
    assert teil(db, c, b"b" * 10, 40).json() == {"laenge": 50}
    assert (db.ordner / (NAME + ".teil")).read_bytes() == b"a" * 40 + b"b" * 10


def test_teil_mit_falschem_offset_409_nennt_laenge(db, c):
    beginnen(db, c, 100)
    teil(db, c, b"a" * 40, 0)
    r = teil(db, c, b"b" * 10, 7)
    assert r.status_code == 409 and r.json()["laenge"] == 40
    assert (db.ordner / (NAME + ".teil")).stat().st_size == 40


def test_teil_ohne_beginnen_409(db, c):
    assert teil(db, c, b"a", 0).status_code == 409


def test_teil_ueber_angemeldete_groesse_413_und_aufgeraeumt(db, c):
    beginnen(db, c, 10)
    assert teil(db, c, b"a" * 11, 0).status_code == 413
    assert not list(db.ordner.iterdir())


def test_teil_ueber_8_mb_413_waehrend_des_lesens(db, c, monkeypatch):
    monkeypatch.setattr(werkbank, "TEIL_MAX", 1000)
    beginnen(db, c, 10_000)
    r = teil(db, c, b"a" * 1001, 0)
    assert r.status_code == 413 and not list(db.ordner.iterdir())


def test_teil_ueber_limit_ohne_content_length_413(db, c, monkeypatch):
    monkeypatch.setattr(werkbank, "TEIL_MAX", 1000)
    beginnen(db, c, 10_000)
    db.antworten = [[KOPF_OK]]
    r = c.put(f"{A}/{AID}/datei/{NAME}/teil?offset=0", content=(b"a" * 400 for _ in range(4)), headers=B)
    assert r.status_code == 413 and not list(db.ordner.iterdir())


def hochladen(db, c, daten, name=NAME, sha=None):
    beginnen(db, c, len(daten), name)
    assert teil(db, c, daten, 0, name).status_code == 200
    db.antworten = [[KOPF_OK]]
    return c.post(f"{A}/{AID}/datei/{name}/abschliessen",
                  json={"sha256": sha or hashlib.sha256(daten).hexdigest()}, headers=B)


def test_abschliessen_legt_datei_ab_und_ordnet_mandant_zu(db, c):
    r = hochladen(db, c, MP4)
    assert r.status_code == 200 and r.json()["datei"] == NAME
    assert (db.ordner / NAME).read_bytes() == MP4
    assert sorted(p.name for p in db.ordner.iterdir()) == [NAME]
    assert len(db.psql.sql) == 1 and NAME in db.psql.sql[0] and "fin2gether" in db.psql.sql[0]


def test_abgebrochener_upload_laesst_keine_zieldatei(db, c):
    r = hochladen(db, c, MP4, sha="0" * 64)
    assert r.status_code == 422
    assert not (db.ordner / NAME).exists() and not (db.ordner / (NAME + ".teil")).exists()
    assert not list(db.ordner.iterdir())


def test_abschliessen_ohne_anmeldedatei_laesst_kein_teil_zurueck(db, c):
    beginnen(db, c, 5)
    (db.ordner / (NAME + ".teil.groesse")).unlink()
    (db.ordner / (NAME + ".teil")).write_bytes(b"abc")
    db.antworten = [[KOPF_OK]]
    r = c.post(f"{A}/{AID}/datei/{NAME}/abschliessen", json={"sha256": "0" * 64}, headers=B)
    assert r.status_code == 409 and not list(db.ordner.iterdir())


def test_zu_kurzer_upload_422_und_aufgeraeumt(db, c):
    beginnen(db, c, len(MP4) + 5)
    teil(db, c, MP4, 0)
    db.antworten = [[KOPF_OK]]
    r = c.post(f"{A}/{AID}/datei/{NAME}/abschliessen", json={"sha256": "0" * 64}, headers=B)
    assert r.status_code == 422 and not list(db.ordner.iterdir())


def test_kein_mp4_422(db, c):
    daten = b"GIF89a" + b"\x00" * 100
    r = hochladen(db, c, daten)
    assert r.status_code == 422 and not list(db.ordner.iterdir()) and db.psql.sql == []


def test_zuordnung_scheitert_loescht_teil(db, c):
    db.psql.fehler = [RuntimeError("x")]
    r = hochladen(db, c, MP4)
    assert r.status_code == 503 and not list(db.ordner.iterdir())


def test_abschliessen_loescht_aeltere_fassung(db, c):
    (db.ordner / "wb-0123abcd-s01-f1.mp4").write_bytes(MP4)
    (db.ordner / "wb-0123abcd-s02-f1.mp4").write_bytes(MP4)          # andere Szene bleibt
    (db.ordner / "wb-0123abcd-s01-f3.mp4").write_bytes(MP4)          # neuere Fassung bleibt
    r = hochladen(db, c, MP4, name="wb-0123abcd-s01-f2.mp4")
    assert r.status_code == 200
    assert sorted(p.name for p in db.ordner.iterdir()) == [
        "wb-0123abcd-s01-f2.mp4", "wb-0123abcd-s01-f3.mp4", "wb-0123abcd-s02-f1.mp4"]


def test_datei_holen_nur_eigener_auftrag(db, c):
    (db.ordner / NAME).write_bytes(MP4)
    (db.ordner / "wb-ffffffff-s01-f1.mp4").write_bytes(MP4)
    db.antworten = [[KOPF_OK]]
    r = c.get(f"{A}/{AID}/datei/{NAME}", headers=B)
    assert r.status_code == 200 and r.content == MP4 and r.headers["content-type"] == "video/mp4"
    db.antworten = [[KOPF_OK]]
    assert c.get(f"{A}/{AID}/datei/wb-ffffffff-s01-f1.mp4", headers=B).status_code == 404
    assert c.get(f"{A}/{AID}/datei/..%2Fsecret.mp4", headers=B).status_code == 404   # Pfadtrenner: keine Route
    db.antworten = [[KOPF_OK]]
    assert c.get(f"{A}/{AID}/datei/wb-0123abcd-s09-f1.mp4", headers=B).status_code == 404


# ─── Pult ────────────────────────────────────────────────────────────────

VZ = {"auftrag": AID, "szene": SID, "tor": "text", "stand": "offen", "mandant": "fin2gether", "nr": 3}


def test_pult_vorschlag_nach_kennung(db, c):
    db.antworten = [[VZ]]
    r = c.get(f"{PW}/v/{KENNUNG}", headers=P)
    assert r.json() == {"auftrag": AID, "tor": "text", "nr": 3, "mandant": "fin2gether", "aktuell": True}
    db.antworten = [[{**VZ, "stand": "angenommen"}]]
    assert c.get(f"{PW}/v/{KENNUNG}", headers=P).json()["aktuell"] is False
    db.antworten = [[]]
    assert c.get(f"{PW}/v/{KENNUNG}", headers=P).status_code == 404
    assert c.get(f"{PW}/v/kaputt", headers=P).status_code == 404


def test_pult_werk_und_fremder_mandant(db, c):
    db.antworten = [[{**KOPF_OK, "werkbank": {"szenen": 8}}], [{"w": {"auftrag": AID, "szenen": []}}]]
    r = c.get(f"{PW}/{AID}?mandant=fin2gether", headers=P)
    assert r.status_code == 200 and r.json()["werkbank"] == {"szenen": 8} and r.json()["stand"] == "in_arbeit"
    db.antworten = [[KOPF_OK]]
    assert c.get(f"{PW}/{AID}?mandant=vibemind", headers=P).status_code == 404 and len(db.sql) == 3


def test_pult_werk_mit_lebender_gliederung_nennt_kennung(db, c):
    db.antworten = [[{**KOPF_OK, "werkbank": {"szenen": 8}}],
                    [{"w": {"auftrag": AID, "szenen": []}, "gliederung_kennung": KENNUNG}]]
    r = c.get(f"{PW}/{AID}?mandant=fin2gether", headers=P)
    assert r.status_code == 200 and r.json()["gliederung_kennung"] == KENNUNG
    assert set(r.json()) == {"werk", "stand", "werkbank", "gliederung_kennung"}   # bisherige Schluessel bleiben
    sql = db.sql[1]
    assert "szene IS NULL" in sql and "tor = 'gliederung'" in sql and "'wartend', 'offen'" in sql
    assert f"v.auftrag = '{AID}'::uuid" in sql


def test_pult_werk_ohne_lebende_gliederung_kennung_null(db, c):
    db.antworten = [[KOPF_OK], [{"w": {"auftrag": AID}, "gliederung_kennung": None}]]
    r = c.get(f"{PW}/{AID}?mandant=fin2gether", headers=P)
    assert r.status_code == 200 and r.json()["gliederung_kennung"] is None and r.json()["werk"] == {"auftrag": AID}


def test_pult_werk_fremder_mandant_404_ohne_kennung(db, c):
    db.antworten = [[KOPF_OK]]
    r = c.get(f"{PW}/{AID}?mandant=vibemind", headers=P)
    assert r.status_code == 404 and "kennung" not in r.text and len(db.sql) == 1


def test_szene_fremder_mandant_404(db, c):
    db.antworten = [[KOPF_OK]]
    assert c.get(f"{PW}/{AID}/szene/1?mandant=vibemind", headers=P).status_code == 404
    assert len(db.sql) == 1


def test_pult_szene(db, c):
    db.antworten = [[KOPF_OK], [{"szene": {"id": SID, "nr": 1}, "kennung": KENNUNG}]]
    r = c.get(f"{PW}/{AID}/szene/1?mandant=fin2gether", headers=P)
    assert r.json() == {"szene": {"id": SID, "nr": 1}, "kennung": KENNUNG}


def test_wunsch_mandant_vom_vorschlag(db, c):
    db.antworten = [[VZ], [{"ok": True}]]
    r = c.post(f"{PW}/v/{KENNUNG}/wunsch", json={"wunsch": "kuerzer", "mandant": "fin2gether", "von": "felix"}, headers=P)
    assert r.json() == {"ok": True} and "video_wunsch_setzen" in db.sql[1]
    db.antworten = [[VZ]]
    r = c.post(f"{PW}/v/{KENNUNG}/wunsch", json={"wunsch": "x", "mandant": "vibemind", "von": "felix"}, headers=P)
    assert r.status_code == 404 and len(db.sql) == 3


def test_urteil_setzt_wunsch_vor_dem_entscheiden(db, c):
    db.antworten = [[VZ], [{"ok": True}], [{"e": {"ok": True, "stand": "text_ok"}}]]
    r = c.post(f"{PW}/v/{KENNUNG}/urteil",
               json={"urteil": "nochmal", "wunsch": "mehr Zahlen", "mandant": "fin2gether", "von": "felix"}, headers=P)
    assert r.status_code == 200 and r.json() == {"ok": True, "stand": "text_ok"}
    assert "video_wunsch_setzen" in db.sql[1] and "mehr Zahlen" in db.sql[1]
    assert "video_entscheiden" in db.sql[2] and "'seite'" in db.sql[2]


def test_urteil_veraltet_409(db, c):
    db.antworten = [[VZ], [{"e": {"ok": False}}]]
    r = c.post(f"{PW}/v/{KENNUNG}/urteil", json={"urteil": "passt", "mandant": "fin2gether", "von": "felix"}, headers=P)
    assert r.status_code == 409
    assert "video_wunsch_setzen" not in " ".join(db.sql)


def test_urteil_ungueltig_422(db, c):
    r = c.post(f"{PW}/v/{KENNUNG}/urteil", json={"urteil": "vielleicht", "mandant": "fin2gether", "von": "f"}, headers=P)
    assert r.status_code == 422 and db.sql == []


def test_nochmal_versuchen_und_oeffnen(db, c):
    body = {"mandant": "fin2gether", "von": "felix"}
    db.antworten = [[KOPF_OK], [{"id": SID}], [{"ok": True}]]
    assert c.post(f"{PW}/{AID}/szene/2/nochmal-versuchen", json=body, headers=P).json() == {"ok": True}
    assert "video_szene_nochmal" in db.sql[2] and "nr = 2" in db.sql[1]
    db.antworten = [[KOPF_OK], [{"id": SID}], [{"ok": True}]]
    r = c.post(f"{PW}/{AID}/szene/2/oeffnen", json={**body, "wunsch": "anders"}, headers=P)
    assert r.json() == {"ok": True} and "video_szene_oeffnen" in db.sql[5] and "anders" in db.sql[5]
    db.antworten = [[KOPF_OK], [{"id": SID}], [{"ok": False}]]
    assert c.post(f"{PW}/{AID}/szene/2/oeffnen", json={**body, "wunsch": "a"}, headers=P).status_code == 409


def test_aufgegebene_szene_grund_sichtbar_dann_neuer_wunsch(db, c):
    """C1 an der API: der Arbeiter meldet die Aufgabe, die Szenenseite sieht den Grund, der Wunsch geht an die
    SQL-Funktion (die den Grund loescht; danach fragt der Arbeiter einmal neu)."""
    body = {"mandant": "fin2gether", "von": "felix"}
    db.antworten = [[SZENE_OK], [{"ok": True}]]
    c.post(f"{A}/szene/{SID}/aufgegeben", json={"grund": "Szene 2: Quelle fehlt – bitte Material ergänzen."}, headers=B)
    db.antworten = [[KOPF_OK], [{"szene": {"id": SID, "nr": 2, "stand": "geplant",
                                           "aufgegeben": "Szene 2: Quelle fehlt – bitte Material ergänzen."},
                                 "kennung": None}]]
    r = c.get(f"{PW}/{AID}/szene/2?mandant=fin2gether", headers=P)
    assert r.json()["szene"]["aufgegeben"] == "Szene 2: Quelle fehlt – bitte Material ergänzen."
    db.antworten = [[KOPF_OK], [{"id": SID}], [{"ok": True}]]
    r = c.post(f"{PW}/{AID}/szene/2/wunsch", json={**body, "wunsch": "Nimm die Zahl aus dem Angebot"}, headers=P)
    assert r.json() == {"ok": True}
    assert f"marketing.video_szene_wunsch('{SID}'::uuid, 'Nimm die Zahl aus dem Angebot')" in db.sql[-1]
    db.antworten = [[KOPF_OK], [{"id": SID}], [{"ok": False}]]
    assert c.post(f"{PW}/{AID}/szene/2/wunsch", json={**body, "wunsch": "x"}, headers=P).status_code == 409
    db.antworten = [[KOPF_OK]]
    assert c.post(f"{PW}/{AID}/szene/2/wunsch", json={**body, "wunsch": ""}, headers=P).status_code == 422


def test_gliederung_wunsch_und_neu_bauen(db, c):
    body = {"mandant": "fin2gether", "von": "felix"}
    db.antworten = [[KOPF_OK], [{"ok": True}]]
    assert c.post(f"{PW}/{AID}/gliederung/wunsch", json={**body, "wunsch": "kürzer"}, headers=P).json() == {"ok": True}
    assert f"marketing.video_gliederung_wunsch('{AID}'::uuid, 'kürzer')" in db.sql[1]
    db.antworten = [[KOPF_OK], [{"ok": False}]]
    assert c.post(f"{PW}/{AID}/gliederung/wunsch", json={**body, "wunsch": "x"}, headers=P).status_code == 409
    db.antworten = [[KOPF_OK], [{"ok": True}]]
    assert c.post(f"{PW}/{AID}/video/neu-bauen", json=body, headers=P).json() == {"ok": True}
    assert f"marketing.video_neu_bauen('{AID}'::uuid)" in db.sql[-1]
    db.antworten = [[KOPF_OK], [{"ok": False}]]
    assert c.post(f"{PW}/{AID}/video/neu-bauen", json=body, headers=P).status_code == 409


@pytest.mark.parametrize("pfad, extra", [("szene/2/wunsch", {"wunsch": "w"}), ("gliederung/wunsch", {"wunsch": "w"}),
                                         ("video/neu-bauen", {})])
def test_neue_pult_routen_fremder_mandant_404(db, c, pfad, extra):
    db.antworten = [[KOPF_OK]]
    r = c.post(f"{PW}/{AID}/{pfad}", json={"mandant": "vibemind", "von": "f", **extra}, headers=P)
    assert r.status_code == 404 and len(db.sql) == 1
    assert c.post(f"{PW}/{AID}/{pfad}", json={"mandant": "fin2gether", "von": "f", **extra}).status_code == 401


def test_nochmal_fremder_mandant_404(db, c):
    db.antworten = [[KOPF_OK]]
    r = c.post(f"{PW}/{AID}/szene/2/nochmal-versuchen", json={"mandant": "vibemind", "von": "f"}, headers=P)
    assert r.status_code == 404 and len(db.sql) == 1


# ─── Hartes Gesetz: nur SQL-Funktionen, nie datenmodifizierende Anweisungen ───


def test_nur_funktionsaufrufe_kein_insert_update_delete(db, c, monkeypatch):
    """Die Lektion aus dem Auftragsbuch: _schreiben verpackt jede Anweisung als Unterabfrage, ein
    WITH ... UPDATE faellt dort in Produktion durch. Hier laufen alle Schreibwege einmal; das aufgezeichnete
    SQL darf INSERT/UPDATE/DELETE nicht enthalten (Funktionsnamen wie video_... zaehlen nicht)."""
    monkeypatch.setattr(video, "stimmen_liste", lambda: [])
    (db.ordner / NAME).write_bytes(MP4)
    (db.ordner / FERTIG["datei"]).write_bytes(MP4)

    def lauf(antworten, f):
        db.antworten = list(antworten)
        f()

    ok = [{"ok": True}]
    lauf([[{"a": {"id": AID}}], [{"w": {}}]], lambda: c.post(f"{A}/naechster", headers=B))
    lauf([[{"w": {"mandant": "m"}}], []], lambda: c.get(f"{A}/arbeit", headers=B))
    lauf([[KOPF_OK], ok], lambda: c.post(f"{A}/{AID}/weiter", headers=B))
    lauf([[KOPF_OK], ok], lambda: c.post(f"{A}/{AID}/frist", headers=B))
    lauf([[KOPF_OK], [{"k": KENNUNG}]], lambda: c.post(f"{A}/{AID}/gliederung", json={"gliederung": gliederung(8)}, headers=B))
    lauf([[SZENE_OK], [{"k": KENNUNG}]], lambda: c.post(f"{A}/szene/{SID}/text", json=TEXT, headers=B))
    lauf([[SZENE_OK], ok], lambda: c.post(f"{A}/szene/{SID}/bild", json={"bildart": "slide", "bild": TEXT["bild"]}, headers=B))
    lauf([[KOPF_OK], [{"s": None}]], lambda: c.post(f"{A}/{AID}/szene-nehmen", headers=B))
    lauf([[SZENE_OK], [{"k": KENNUNG}]], lambda: c.post(f"{A}/szene/{SID}/clip", json=CLIP, headers=B))
    lauf([[SZENE_OK], ok], lambda: c.post(f"{A}/szene/{SID}/fehler", json={"grund": "g"}, headers=B))
    lauf([[KOPF_OK], ok], lambda: c.post(f"{A}/{AID}/hinweis", json={"key": "k", "satz": "s"}, headers=B))
    lauf([[KOPF_OK], [{"n": 1}]], lambda: c.post(f"{A}/{AID}/aufruf", headers=B))
    lauf([[KOPF_OK], ok], lambda: c.post(f"{A}/{AID}/fertig", json=FERTIG, headers=B))
    lauf([[VZ], ok], lambda: c.post(f"{PW}/v/{KENNUNG}/wunsch", json={"wunsch": "w", "mandant": "fin2gether", "von": "f"}, headers=P))
    lauf([[VZ], ok, [{"e": {"ok": True}}]], lambda: c.post(
        f"{PW}/v/{KENNUNG}/urteil", json={"urteil": "passt", "wunsch": "w", "mandant": "fin2gether", "von": "f"}, headers=P))
    lauf([[KOPF_OK], [{"id": SID}], ok], lambda: c.post(
        f"{PW}/{AID}/szene/1/nochmal-versuchen", json={"mandant": "fin2gether", "von": "f"}, headers=P))
    lauf([[KOPF_OK], [{"id": SID}], ok], lambda: c.post(
        f"{PW}/{AID}/szene/1/oeffnen", json={"mandant": "fin2gether", "von": "f", "wunsch": "w"}, headers=P))
    lauf([[KOPF_OK], [{"w": {}}]], lambda: c.get(f"{PW}/{AID}?mandant=fin2gether", headers=P))
    lauf([[KOPF_OK], [{"szene": {}}]], lambda: c.get(f"{PW}/{AID}/szene/1?mandant=fin2gether", headers=P))
    lauf([[VZ]], lambda: c.get(f"{PW}/v/{KENNUNG}", headers=P))
    lauf([[SZENE_OK], ok], lambda: c.post(f"{A}/szene/{SID}/aufgegeben", json={"grund": "g"}, headers=B))
    lauf([[KOPF_OK], ok], lambda: c.post(f"{A}/{AID}/gliederung-aufgegeben", json={"grund": "g"}, headers=B))
    lauf([[KOPF_OK], ok], lambda: c.post(f"{A}/{AID}/video-gescheitert", json={"grund": "g", "versuch": 0}, headers=B))
    lauf([[KOPF_OK], [{"id": SID}], ok], lambda: c.post(
        f"{PW}/{AID}/szene/1/wunsch", json={"mandant": "fin2gether", "von": "f", "wunsch": "w"}, headers=P))
    lauf([[KOPF_OK], ok], lambda: c.post(
        f"{PW}/{AID}/gliederung/wunsch", json={"mandant": "fin2gether", "von": "f", "wunsch": "w"}, headers=P))
    lauf([[KOPF_OK], ok], lambda: c.post(f"{PW}/{AID}/video/neu-bauen", json={"mandant": "fin2gether", "von": "f"}, headers=P))
    assert len(db.sql) > 40
    schreibend = [s for s in db.sql if re.search(r"\b(INSERT|UPDATE|DELETE)\b", s, re.IGNORECASE)]
    assert schreibend == []
    assert not any(re.search(r"\bWITH\b", s) for s in db.sql)
    funktionen = [s for s in db.sql if s.startswith("SELECT marketing.video_") or s.startswith("SELECT auftrag.")]
    assert len(funktionen) >= 14                       # die Schreibwege laufen wirklich ueber Funktionen

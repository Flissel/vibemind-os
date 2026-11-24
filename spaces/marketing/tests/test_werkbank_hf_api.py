"""Higgsfield-Wege der Werkbank-API (Migration 102) ohne echte DB: Form, Verknuepfung, Dateien und die Regel, dass
die Freigabe nur ueber die Sales-Seite geht. Die Geldregeln selbst prueft db/verify_102.sql."""
import types

import pytest
from fastapi.testclient import TestClient

from spaces.marketing.api import server, werkbank
from spaces.marketing.sync import _db
from spaces.marketing.tests.test_bilder_api import FalschePsql
from spaces.marketing.tests.test_pult_api import FalscheDB

AID = "0123abcd-4444-4444-4444-444444444444"
HID = "0123abcd-7777-7777-7777-777777777777"
SID = "0123abcd-6666-6666-6666-666666666666"
BK, PK, AK = "bild-k", "pult-k", "api-k"
B, P = {"X-Bild-Key": BK}, {"X-Pult-Key": PK}
A = "/api/werkbank/arbeiter"
PW = "/api/pult/werkbank"
KOPF_OK = {"mandant": "fin2gether", "stand": "werkbank", "werkbank": None}
CLIP_OK = {"id": HID, "auftrag": AID, "stand": "vorgeschlagen", "versuch": 2, "nach_nr": 3, "mandant": "fin2gether"}
START, ENDE = "wb-0123abcd-s02-f0-z.jpg", "wb-0123abcd-s03-f1-a.jpg"
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
WER = {"mandant": "fin2gether", "von": "felix"}


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
    monkeypatch.setattr(server, "API_KEY", AK)
    monkeypatch.setattr(werkbank.shutil, "disk_usage", lambda o: types.SimpleNamespace(free=10 * 1024 ** 3))
    f.ordner = tmp_path
    return f


@pytest.fixture
def c():
    return TestClient(server.app)


def vorschlag(**anders) -> dict:
    return {"nach_nr": 3, "von_variante": 0, "nach_variante": 1, "start_bild": START, "end_bild": ENDE,
            "prompt": "Ein Papierschneider teilt das Blatt", "dauer_s": 3, "preis": 5.25, "modell": "kling3_0", **anders}


# ─── Schluessel ──────────────────────────────────────────────────────────


def test_ohne_schluessel_401(db, c):
    assert c.post(f"{A}/hf/abgleich").status_code == 401
    assert c.post(f"{A}/hf/nehmen", headers={"X-Bild-Key": "falsch"}).status_code == 401
    assert c.post(f"{A}/{AID}/hf/vorschlag", json=vorschlag()).status_code == 401
    assert c.put(f"{A}/{AID}/bild/{START}", content=JPG).status_code == 401
    assert c.post(f"{A}/hf/{HID}/auftrag", json={"hf_auftrag": "job-123456", "preis": 5.25}).status_code == 401
    assert c.get(f"{PW}/{AID}/hf?mandant=fin2gether").status_code == 401
    assert c.post(f"{PW}/hf/{HID}/urteil", json={}, headers={"X-Pult-Key": "falsch"}).status_code == 401
    assert c.post(f"{PW}/hf/{HID}/nochmal", json={}).status_code == 401
    # der Arbeiter-Schluessel oeffnet die Freigabe nicht
    assert c.post(f"{PW}/hf/{HID}/urteil", json={**WER, "urteil": "erzeugen"}, headers=B).status_code == 401
    assert db.sql == []


# ─── Arbeiter: Abgleich, Bilder, Vorschlag ───────────────────────────────


def test_abgleich_nennt_nur_werke_mit_fehlenden_stellen(db, c):
    andere = "0123abcd-5555-5555-5555-555555555555"
    db.antworten = [[{"auftrag": AID, "mandant": "fin2gether", "titel": "Chunking", "thema": "x"},
                     {"auftrag": andere, "mandant": "vibemind", "titel": None, "thema": "y"}],
                    [{"e": {"fehlend": [{"nach_nr": 3, "von_nr": 2}]}}], [{"e": {"fehlend": []}}]]
    r = c.post(f"{A}/hf/abgleich", headers=B)
    assert r.status_code == 200
    assert r.json() == {"werke": [{"auftrag": AID, "mandant": "fin2gether", "titel": "Chunking", "thema": "x",
                                   "fehlend": [{"nach_nr": 3, "von_nr": 2}]}]}
    assert "a.stand = 'werkbank'" in db.sql[0] and "uebergang = 'higgsfield'" in db.sql[0]
    assert f"marketing.video_hf_abgleichen('{AID}'::uuid)" in db.sql[1]
    assert f"marketing.video_hf_abgleichen('{andere}'::uuid)" in db.sql[2]


def test_bild_ablegen_und_holen(db, c):
    db.antworten = [[KOPF_OK]]
    r = c.put(f"{A}/{AID}/bild/{START}", content=JPG, headers=B)
    assert r.status_code == 200 and r.json() == {"ok": True, "datei": START, "bytes": len(JPG)}
    assert (db.ordner / START).read_bytes() == JPG and not (db.ordner / (START + ".teil")).exists()
    assert "fin2gether" in db.psql.sql[0]                       # dem Mandanten der Auftragszeile zugeordnet
    db.antworten = [[KOPF_OK]]
    g = c.get(f"{A}/{AID}/bild/{START}", headers=B)
    assert g.status_code == 200 and g.content == JPG and g.headers["content-type"] == "image/jpeg"


@pytest.mark.parametrize("name, daten, code", [
    ("wb-0123abcd-s02-f0-z.png", JPG, 404),                    # nur JPEG-Namen
    ("wb-99999999-s02-f0-z.jpg", JPG, 404),                    # anderer Auftrag
    ("wb-0123abcd-s02-f0-m.jpg", JPG, 404),                    # nur a (erstes) und z (letztes)
    (START, b"\x89PNG\r\n\x1a\n" + b"\x00" * 200, 422),         # kein JPEG
    (START, b"\xff\xd8\xff", 422),                             # zu kurz
    (START, b"\xff\xd8\xff" + b"\x00" * (2 * 1024 * 1024), 413)],
    ids=["png-name", "fremder-auftrag", "falsche-seite", "kein-jpeg", "zu-kurz", "zu-gross"])
def test_bild_abgelehnt(db, c, name, daten, code):
    db.antworten = [[KOPF_OK]]
    r = c.put(f"{A}/{AID}/bild/{name}", content=daten, headers=B)
    assert r.status_code == code
    assert list(db.ordner.iterdir()) == [] and db.psql.sql == []


def test_bild_fremder_auftrag_404(db, c):
    db.antworten = [[]]
    assert c.put(f"{A}/{AID}/bild/{START}", content=JPG, headers=B).status_code == 404
    assert list(db.ordner.iterdir()) == []


def test_vorschlag_ok(db, c):
    (db.ordner / START).write_bytes(JPG)
    (db.ordner / ENDE).write_bytes(JPG)
    db.antworten = [[KOPF_OK], [{"id": SID}], [{"id": HID}]]
    r = c.post(f"{A}/{AID}/hf/vorschlag", json=vorschlag(prompt="Ein  Schnitt\n durch\tdas Blatt "), headers=B)
    assert r.status_code == 200 and r.json() == {"id": HID}
    assert "nr = 3" in db.sql[1]
    sql = db.sql[2]
    assert f"marketing.video_hf_vorschlagen('{AID}'::uuid, '{SID}'::uuid, " in sql
    assert '"prompt": "Ein Schnitt durch das Blatt"' in sql      # Umbrueche und Tabulatoren werden Leerzeichen
    assert f'"start_bild": "{START}"' in sql and f'"end_bild": "{ENDE}"' in sql
    assert '"preis": 5.25' in sql and '"von_variante": 0' in sql and '"nach_variante": 1' in sql


@pytest.mark.parametrize("anders", [
    {"prompt": ""}, {"prompt": "x" * 1001}, {"prompt": "mit\x00null"}, {"prompt": "mit\x1bescape"}, {"prompt": 5},
    {"dauer_s": 4}, {"dauer_s": "3"}, {"preis": -1}, {"preis": 101}, {"preis": True}, {"preis": "5"},
    {"modell": "sora"}, {"nach_nr": 1}, {"nach_nr": "3"}, {"von_variante": -1}, {"nach_variante": None}])
def test_vorschlag_falsche_form_422(db, c, anders):
    (db.ordner / START).write_bytes(JPG)
    (db.ordner / ENDE).write_bytes(JPG)
    db.antworten = [[KOPF_OK]]
    r = c.post(f"{A}/{AID}/hf/vorschlag", json=vorschlag(**anders), headers=B)
    assert r.status_code == 422, r.text
    assert not any("video_hf_vorschlagen" in s for s in db.sql)


@pytest.mark.parametrize("anders", [
    {"start_bild": "wb-0123abcd-s02-f0-a.jpg"},                # Startbild muss das LETZTE Bild der Szene davor sein
    {"start_bild": "wb-0123abcd-s01-f0-z.jpg"},                # falsche Szene
    {"start_bild": "wb-0123abcd-s02-f1-z.jpg"},                # falsche Variante
    {"end_bild": "wb-0123abcd-s03-f1-z.jpg"},                  # Endbild muss das ERSTE Bild der Zielszene sein
    {"end_bild": "wb-99999999-s03-f1-a.jpg"},                  # anderer Auftrag
    {"end_bild": "../geheim.jpg"}])
def test_vorschlag_bild_passt_nicht_409(db, c, anders):
    for n in (START, ENDE, "wb-0123abcd-s02-f0-a.jpg", "wb-0123abcd-s01-f0-z.jpg", "wb-0123abcd-s02-f1-z.jpg",
              "wb-0123abcd-s03-f1-z.jpg", "wb-99999999-s03-f1-a.jpg"):
        (db.ordner / n).write_bytes(JPG)
    db.antworten = [[KOPF_OK]]
    r = c.post(f"{A}/{AID}/hf/vorschlag", json=vorschlag(**anders), headers=B)
    assert r.status_code == 409 and not any("video_hf_vorschlagen" in s for s in db.sql)


def test_vorschlag_bild_fehlt_oder_stelle_zu_409(db, c):
    db.antworten = [[KOPF_OK]]
    assert c.post(f"{A}/{AID}/hf/vorschlag", json=vorschlag(), headers=B).status_code == 409   # Bilder liegen nicht da
    (db.ordner / START).write_bytes(JPG)
    (db.ordner / ENDE).write_bytes(JPG)
    db.antworten = [[KOPF_OK], [{"id": SID}], [{"id": None}]]
    r = c.post(f"{A}/{AID}/hf/vorschlag", json=vorschlag(), headers=B)
    assert r.status_code == 409 and "kein Vorschlag" in r.json()["detail"]


# ─── Arbeiter: Bestellen ─────────────────────────────────────────────────


def test_nehmen(db, c):
    db.antworten = [[{"c": {"id": HID, "auftrag": AID, "prompt": "p", "hf_auftrag": None}}]]
    r = c.post(f"{A}/hf/nehmen", headers=B)
    assert r.json() == {"clip": {"id": HID, "auftrag": AID, "prompt": "p", "hf_auftrag": None}}
    assert "marketing.video_hf_nehmen('15 minutes'::interval)" in db.sql[0]
    db.antworten = [[{"c": None}]]
    assert c.post(f"{A}/hf/nehmen", headers=B).json() == {"clip": None}


def test_preis_pruefen(db, c):
    db.antworten = [[CLIP_OK], [{"ok": True}], [CLIP_OK], [{"ok": False}]]
    assert c.post(f"{A}/hf/{HID}/preis", json={"preis": 5.25}, headers=B).json() == {"ok": True}
    assert f"marketing.video_hf_preis_pruefen('{HID}'::uuid, 5.25::numeric)" in db.sql[1]
    assert c.post(f"{A}/hf/{HID}/preis", json={"preis": 8.75}, headers=B).json() == {"ok": False}
    assert c.post(f"{A}/hf/{HID}/preis", json={"preis": "5.25"}, headers=B).status_code == 422
    assert c.post(f"{A}/hf/{HID}/preis", json={"preis": 1000}, headers=B).status_code == 422


def test_auftrag_merken(db, c):
    db.antworten = [[CLIP_OK], [{"ok": True}]]
    nummer = "8323a0b9-2ef6-4665-89a2-2d20ee770dfb"
    r = c.post(f"{A}/hf/{HID}/auftrag", json={"hf_auftrag": nummer, "preis": 5.25}, headers=B)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert f"marketing.video_hf_auftrag_merken('{HID}'::uuid, '{nummer}', 5.25::numeric)" in db.sql[1]
    db.antworten = [[CLIP_OK], [{"ok": False}]]
    assert c.post(f"{A}/hf/{HID}/auftrag", json={"hf_auftrag": nummer, "preis": 5.25}, headers=B).status_code == 409


@pytest.mark.parametrize("nummer", ["kurz", "mit leer zeichen", "x'; DROP TABLE y; --", "a" * 81, None, 123456])
def test_auftrag_merken_falsche_nummer_422(db, c, nummer):
    r = c.post(f"{A}/hf/{HID}/auftrag", json={"hf_auftrag": nummer, "preis": 5.25}, headers=B)
    assert r.status_code == 422 and db.sql == []


def test_fertig_prueft_namen_und_datei(db, c):
    name = "wb-0123abcd-h03-f2.mp4"
    db.antworten = [[CLIP_OK]]
    assert c.post(f"{A}/hf/{HID}/fertig", json={"datei": name, "dauer_s": 2.0}, headers=B).status_code == 409  # liegt nicht da
    (db.ordner / name).write_bytes(b"x")
    db.antworten = [[CLIP_OK], [{"ok": True}]]
    r = c.post(f"{A}/hf/{HID}/fertig", json={"datei": name, "dauer_s": 2.0}, headers=B)
    assert r.status_code == 200 and f"marketing.video_hf_fertig('{HID}'::uuid, '{name}', 2.0::numeric)" in db.sql[-1]
    for falsch in ("wb-0123abcd-h03-f1.mp4", "wb-0123abcd-h04-f2.mp4", "wb-99999999-h03-f2.mp4", "wb-0123abcd-s03-f2.mp4"):
        (db.ordner / falsch).write_bytes(b"x")
        db.antworten = [[CLIP_OK]]
        assert c.post(f"{A}/hf/{HID}/fertig", json={"datei": falsch, "dauer_s": 2.0}, headers=B).status_code == 409
    db.antworten = [[CLIP_OK], [{"ok": False}]]
    assert c.post(f"{A}/hf/{HID}/fertig", json={"datei": name, "dauer_s": 2.0}, headers=B).status_code == 409


def test_fehler_und_weiter(db, c):
    db.antworten = [[CLIP_OK], [{"ok": True}], [CLIP_OK], [{"ok": True}]]
    assert c.post(f"{A}/hf/{HID}/fehler", json={"grund": "  Die Anmeldung ist abgelaufen.  "}, headers=B).json() == {"ok": True}
    assert f"marketing.video_hf_fehler('{HID}'::uuid, 'Die Anmeldung ist abgelaufen.')" in db.sql[1]
    assert c.post(f"{A}/hf/{HID}/weiter", headers=B).json() == {"ok": True}
    assert f"marketing.video_hf_weiter('{HID}'::uuid, '15 minutes'::interval)" in db.sql[3]


@pytest.mark.parametrize("pfad, body", [
    ("preis", {"preis": 5.25}), ("auftrag", {"hf_auftrag": "job-123456", "preis": 5.25}), ("weiter", None),
    ("fertig", {"datei": "wb-0123abcd-h03-f2.mp4", "dauer_s": 2}), ("fehler", {"grund": "x"})])
def test_unbekannter_clip_404_ohne_schreiben(db, c, pfad, body):
    db.antworten = [[]]
    r = c.post(f"{A}/hf/{HID}/{pfad}", headers=B, **({"json": body} if body else {}))
    assert r.status_code == 404 and len(db.sql) == 1
    assert "an = 'marketing'" in db.sql[0] and "SELECT marketing.video_hf_" not in db.sql[0]


def test_uebergangsclip_laesst_sich_hochladen_und_holen(db, c):
    """Der Upload in Teilen und das Abholen nehmen auch wb-<aid8>-hNN-f<v>.mp4; als fertiges Video gilt er nie."""
    name = "wb-0123abcd-h03-f2.mp4"
    db.antworten = [[KOPF_OK]]
    assert c.post(f"{A}/{AID}/datei/{name}/beginnen", json={"bytes": 5}, headers=B).status_code == 200
    (db.ordner / name).write_bytes(b"x")
    db.antworten = [[KOPF_OK]]
    assert c.get(f"{A}/{AID}/datei/{name}", headers=B).status_code == 200
    db.antworten = [[KOPF_OK]]
    r = c.post(f"{A}/{AID}/fertig", headers=B, json={"datei": name, "bytes": 1, "dauer_s": 1, "titel": "T", "beschreibung": "B"})
    assert r.status_code == 409 and not any("video_fertig_setzen" in s for s in db.sql)


def test_szenentext_nimmt_higgsfield_als_uebergang(db, c):
    db.antworten = [[{"auftrag": AID, "nr": 3, "mandant": "fin2gether"}], [{"k": "wb-abcdefghij"}]]
    bild = {"schritte": [{"vorlage": "titel"}, {"vorlage": "titel"}]}
    r = c.post(f"{A}/szene/{SID}/text", headers=B,
               json={"sprechtext": "Text", "bildart": "slide", "uebergang": "higgsfield", "bild": bild})
    assert r.status_code == 200 and '"uebergang": "higgsfield"' in db.sql[1]


# ─── Pult: Freigabe je Clip ──────────────────────────────────────────────


def test_pult_liste(db, c):
    liste = {"clips": [{"id": HID, "stand": "vorgeschlagen", "preis": 5.25}], "credits": 0, "bestellt": 0, "vorbereitung": [5]}
    db.antworten = [[KOPF_OK], [{"l": liste}]]
    r = c.get(f"{PW}/{AID}/hf?mandant=fin2gether", headers=P)
    assert r.status_code == 200 and r.json() == liste
    assert f"marketing.video_hf_liste('{AID}'::uuid)" in db.sql[1]
    db.antworten = [[KOPF_OK]]
    assert c.get(f"{PW}/{AID}/hf?mandant=anderer", headers=P).status_code == 404


def test_pult_erzeugen_geht_nur_ueber_die_seite_mit_text_und_preis(db, c):
    db.antworten = [[CLIP_OK], [{"e": {"ok": True, "stand": "freigegeben"}}]]
    r = c.post(f"{PW}/hf/{HID}/urteil", headers=P,
               json={**WER, "urteil": "erzeugen", "prompt": "Mein\ngeänderter Text", "preis_gesehen": 5.25})
    assert r.status_code == 200 and r.json() == {"ok": True, "stand": "freigegeben"}
    assert db.sql[1].count("video_hf_entscheiden") == 1
    assert (f"marketing.video_hf_entscheiden('{HID}'::uuid, 'erzeugen', 'Mein geänderter Text', 5.25::numeric, "
            f"'felix', 'seite')") in db.sql[1]


def test_pult_weg_laesst_sich_nicht_uebergeben(db, c):
    """Ein `weg` im Koerper aendert nichts: die API schreibt immer 'seite'."""
    db.antworten = [[CLIP_OK], [{"e": {"ok": True, "stand": "freigegeben"}}]]
    c.post(f"{PW}/hf/{HID}/urteil", headers=P,
           json={**WER, "weg": "rowboat", "urteil": "erzeugen", "prompt": "Text", "preis_gesehen": 5.25})
    assert "'seite') AS e" in db.sql[1] and "rowboat" not in db.sql[1]


@pytest.mark.parametrize("body", [
    {"urteil": "erzeugen", "prompt": "Text"},                               # Preis fehlt
    {"urteil": "erzeugen", "prompt": "Text", "preis_gesehen": "5.25"},
    {"urteil": "erzeugen", "preis_gesehen": 5.25},                          # Text fehlt
    {"urteil": "erzeugen", "prompt": "x" * 1001, "preis_gesehen": 5.25},
    {"urteil": "erzeugen", "prompt": "mit\x07klingel", "preis_gesehen": 5.25},
    {"urteil": "passt", "prompt": "Text", "preis_gesehen": 5.25},
    {"prompt": "Text", "preis_gesehen": 5.25}])
def test_pult_urteil_falsche_form_422(db, c, body):
    r = c.post(f"{PW}/hf/{HID}/urteil", headers=P, json={**WER, **body})
    assert r.status_code == 422 and db.sql == []


def test_pult_urteil_ohne_namen_oder_mandant_422(db, c):
    body = {"urteil": "erzeugen", "prompt": "Text", "preis_gesehen": 5.25}
    assert c.post(f"{PW}/hf/{HID}/urteil", headers=P, json={"mandant": "fin2gether", **body}).status_code == 422
    assert c.post(f"{PW}/hf/{HID}/urteil", headers=P, json={"von": "felix", **body}).status_code == 422
    assert db.sql == []


def test_pult_fremder_mandant_404_ohne_schreiben(db, c):
    db.antworten = [[CLIP_OK]]
    r = c.post(f"{PW}/hf/{HID}/urteil", headers=P,
               json={"mandant": "anderer", "von": "x", "urteil": "erzeugen", "prompt": "Text", "preis_gesehen": 5.25})
    assert r.status_code == 404 and len(db.sql) == 1


@pytest.mark.parametrize("grund, wort", [("preis", "Preis"), ("stand", "Stand"), ("ueberholt", "neu vorgeschlagen"),
                                         ("weg", "Sales-Seite"), ("auftrag", "nicht mehr in Arbeit"), (None, "Stand")])
def test_pult_abgelehnte_freigabe_409_mit_satz(db, c, grund, wort):
    db.antworten = [[CLIP_OK], [{"e": {"ok": False, "grund": grund}}]]
    r = c.post(f"{PW}/hf/{HID}/urteil", headers=P,
               json={**WER, "urteil": "erzeugen", "prompt": "Text", "preis_gesehen": 5.25})
    assert r.status_code == 409 and wort in r.json()["detail"]


def test_pult_verwerfen_braucht_weder_text_noch_preis(db, c):
    db.antworten = [[CLIP_OK], [{"e": {"ok": True, "stand": "verworfen"}}]]
    r = c.post(f"{PW}/hf/{HID}/urteil", headers=P, json={**WER, "urteil": "verwerfen"})
    assert r.status_code == 200
    assert f"marketing.video_hf_entscheiden('{HID}'::uuid, 'verwerfen', NULL, NULL, 'felix', 'seite')" in db.sql[1]


def test_pult_nochmal_legt_nur_einen_vorschlag_an(db, c):
    neu = "0123abcd-8888-8888-8888-888888888888"
    db.antworten = [[CLIP_OK], [{"id": neu}]]
    r = c.post(f"{PW}/hf/{HID}/nochmal", headers=P, json={**WER, "prompt": "Neuer Text"})
    assert r.status_code == 200 and r.json() == {"id": neu}
    assert f"marketing.video_hf_nochmal('{HID}'::uuid, 'Neuer Text', 'felix', 'seite')" in db.sql[1]
    assert not any("video_hf_entscheiden" in s or "video_hf_nehmen" in s for s in db.sql)
    db.antworten = [[CLIP_OK], [{"id": None}]]
    assert c.post(f"{PW}/hf/{HID}/nochmal", headers=P, json={**WER, "prompt": "Neuer Text"}).status_code == 409
    assert c.post(f"{PW}/hf/{HID}/nochmal", headers=P, json={**WER, "prompt": ""}).status_code == 422


def test_pult_ohne_uebergang(db, c):
    db.antworten = [[KOPF_OK], [{"ok": True}]]
    assert c.post(f"{PW}/{AID}/hf/3/ohne", headers=P, json=WER).json() == {"ok": True}
    assert f"marketing.video_hf_ohne('{AID}'::uuid, 3, 'seite')" in db.sql[1]
    db.antworten = [[KOPF_OK], [{"ok": False}]]
    assert c.post(f"{PW}/{AID}/hf/3/ohne", headers=P, json=WER).status_code == 409

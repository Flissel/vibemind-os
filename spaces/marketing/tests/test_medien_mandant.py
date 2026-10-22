"""Sichtbarkeitsregel und Pult-Routen fuer Bilder je Mandant (FalscheDB, kein echtes Postgres)."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from spaces.marketing.api import medien_mandant as mm
from spaces.marketing.api import server
from spaces.marketing.sync import _db
from spaces.marketing.tests.test_pult_api import FalscheDB

PK = "pult-k"
H = {"X-Pult-Key": PK}
IID = "11111111-1111-1111-1111-111111111111"
LOGO_E = "logo-vibemind-0123456789.png"
LOGO_F = "logo-laura-0123456789.jpg"


class FalschePsql:
    """Faelschung von _db._run_psql (Schreibweg mit datenmodifizierender CTE)."""
    def __init__(self):
        self.sql = []
        self.ausgaben = []
        self.fehler = []

    def __call__(self, sql, container, streng=False):
        assert streng is True and container is None
        self.sql.append(sql)
        if self.fehler:
            e = self.fehler.pop(0)
            if e is not None:
                raise e
        return self.ausgaben.pop(0) if self.ausgaben else "1\n"


@pytest.fixture
def umg(monkeypatch):
    f, p = FalscheDB(), FalschePsql()
    monkeypatch.setattr(_db, "query_via_docker", f.query)
    monkeypatch.setattr(_db, "query_one", f.one)
    monkeypatch.setattr(_db, "_run_psql", p)
    monkeypatch.setenv("MARKETING_PULT_KEY", PK)
    return f, p, TestClient(server.app)


def _s(eigene=(), fremde=(), gemeinsam=()):
    return {"name": "Vibemind", "eigene": set(eigene), "fremde": set(fremde), "gemeinsam": set(gemeinsam)}


def _sicht_zeile(name="Vibemind", eigene=(), fremde=(), gemeinsam=()):
    return {"name": name, "eigene": list(eigene), "fremde": list(fremde), "gemeinsam": list(gemeinsam)}


# --- ist_fremd ---------------------------------------------------------


@pytest.mark.parametrize("name,s,erwartet", [
    ("a.jpg", _s(eigene=["a.jpg"]), False),
    ("a.jpg", _s(fremde=["a.jpg"]), True),
    ("a.jpg", _s(gemeinsam=["a.jpg"]), False),
    (LOGO_F, _s(gemeinsam=[LOGO_F]), False),      # Zeile (Gemeinsam) schlaegt Logo-Regel
    (LOGO_F, _s(eigene=[LOGO_F]), False),
    (LOGO_E, _s(fremde=[LOGO_E]), True),          # Zeile schlaegt auch eigenes Logo
    (LOGO_F, _s(), True),                         # Logo anderer Firma ohne Zeile
    (LOGO_E, _s(), False),                        # Logo eigener Firma ohne Zeile
    ("unbekannt.png", _s(), False),               # keine Zeile, kein Logo = Gemeinsam
    ("logo-laura-xyz.png", _s(), False),          # kein gueltiges Logo-Muster
])
def test_ist_fremd(name, s, erwartet):
    assert mm.ist_fremd(name, "vibemind", s) is erwartet


# Schluss-Fix I1: NTFS am PC oeffnet FOTO.JPG als foto.jpg - Vergleich ohne Gross/Klein, fremd zuerst.
@pytest.mark.parametrize("name,s,erwartet", [
    ("FOTO.JPG", _s(fremde=["foto.jpg"]), True),
    ("foto.jpg", _s(fremde=["FOTO.JPG"]), True),
    ("FOTO.JPG", _s(eigene=["FOTO.JPG"], fremde=["foto.jpg"]), True),        # fremd gewinnt (fail-closed)
    ("Foto.Jpg", _s(gemeinsam=["foto.jpg"], fremde=["FOTO.jpg"]), True),
    ("FOTO.JPG", _s(eigene=["foto.jpg"]), False),
    ("FOTO.JPG", _s(gemeinsam=["foto.jpg"]), False),
    ("LOGO-laura-0123456789.PNG", _s(), True),                               # wie logo-laura-...png
    ("logo-Laura-0123456789.Jpg", _s(), True),
    ("LOGO-VIBEMIND-0123456789.PNG", _s(), False),                           # eigenes Logo bleibt eigen
    ("LOGO-laura-0123456789.PNG", _s(gemeinsam=["logo-laura-0123456789.png"]), False),
])
def test_ist_fremd_ohne_gross_klein(name, s, erwartet):
    assert mm.ist_fremd(name, "vibemind", s) is erwartet


# --- sicht -------------------------------------------------------------


def test_sicht_liefert_mengen_in_einer_abfrage(umg):
    f, _, _ = umg
    f.antworten.append([_sicht_zeile(eigene=["a"], fremde=["b"], gemeinsam=["c"])])
    s = mm.sicht("vibemind")
    assert s == {"name": "Vibemind", "eigene": {"a"}, "fremde": {"b"}, "gemeinsam": {"c"}}
    assert len(f.sql) == 1 and "medien_mandant" in f.sql[0] and "'vibemind'" in f.sql[0]


def test_sicht_db_fehler_503(umg):
    f, _, _ = umg
    f.fehler.append(RuntimeError("psql failed: connection refused"))
    with pytest.raises(HTTPException) as e:
        mm.sicht("vibemind")
    assert e.value.status_code == 503


def test_sicht_unbekannter_mandant_422(umg):
    f, _, _ = umg
    f.antworten.append([_sicht_zeile(name=None)])
    with pytest.raises(HTTPException) as e:
        mm.sicht("nix")
    assert e.value.status_code == 422 and e.value.detail == "Unbekannter Mandant"


# --- /medien/sichtbar --------------------------------------------------


def test_sichtbar_filtert_fremde_und_behaelt_reihenfolge(umg):
    f, _, c = umg
    f.antworten.append([_sicht_zeile(eigene=["e.jpg"], fremde=["f.jpg"], gemeinsam=["g.jpg"])])
    f.antworten.append([{"id": "vibemind", "name": "Vibemind"}, {"id": "laura", "name": "Laura"}])
    namen = ["g.jpg", "f.jpg", LOGO_F, "../x.jpg", "e.jpg", "frei.png", LOGO_E, "a/b.jpg"]
    r = c.post("/api/pult/medien/sichtbar", json={"mandant": "vibemind", "namen": namen}, headers=H)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["sichtbar"] == ["g.jpg", "e.jpg", "frei.png", LOGO_E]
    assert j["zuordnung"] == {"g.jpg": None, "e.jpg": "vibemind", LOGO_E: "vibemind"}
    assert j["mandant"] == "vibemind" and j["name"] == "Vibemind"
    assert j["mandanten"] == [{"id": "vibemind", "name": "Vibemind"}, {"id": "laura", "name": "Laura"}]


def test_sichtbar_gross_klein_varianten(umg):
    """Schluss-Fix I1: die Gross/Klein-Variante eines fremden Bildes ist nicht sichtbar; LOGO.PNG zaehlt wie logo.png."""
    f, _, c = umg
    f.antworten.append([_sicht_zeile(eigene=["e.jpg"], fremde=["f.jpg"], gemeinsam=["g.jpg"])])
    f.antworten.append([])
    namen = ["F.JPG", "E.JPG", "G.Jpg", "LOGO-LAURA-0123456789.PNG", "LOGO-Vibemind-0123456789.PNG"]
    j = c.post("/api/pult/medien/sichtbar", json={"mandant": "vibemind", "namen": namen}, headers=H).json()
    assert j["sichtbar"] == ["E.JPG", "G.Jpg", "LOGO-Vibemind-0123456789.PNG"]
    assert j["zuordnung"] == {"E.JPG": "vibemind", "G.Jpg": None, "LOGO-Vibemind-0123456789.PNG": "vibemind"}


def test_sichtbar_zu_viele_namen_422(umg):
    f, _, c = umg
    r = c.post("/api/pult/medien/sichtbar", json={"mandant": "vibemind", "namen": ["a"] * 2001}, headers=H)
    assert r.status_code == 422 and f.sql == []


def test_sichtbar_exakt_2000_namen_ok(umg):
    f, _, c = umg
    f.antworten.append([_sicht_zeile()])
    f.antworten.append([])
    r = c.post("/api/pult/medien/sichtbar", json={"mandant": "vibemind", "namen": ["a.jpg"] * 2000}, headers=H)
    assert r.status_code == 200 and len(r.json()["sichtbar"]) == 2000


def test_sichtbar_db_fehler_503_nie_alles_sichtbar(umg):
    f, _, c = umg
    f.fehler.append(RuntimeError("weg"))
    r = c.post("/api/pult/medien/sichtbar", json={"mandant": "vibemind", "namen": ["a.jpg"]}, headers=H)
    assert r.status_code == 503


@pytest.mark.parametrize("body", [
    {"mandant": "", "namen": ["a.jpg"]},
    {"namen": ["a.jpg"]},
    {"mandant": None, "namen": ["a.jpg"]},
    {"mandant": 5, "namen": ["a.jpg"]},
    {"mandant": "Vibe Mind", "namen": ["a.jpg"]},
])
def test_sichtbar_ohne_gueltigen_mandant_422_kein_rueckfall(umg, body):
    f, _, c = umg
    r = c.post("/api/pult/medien/sichtbar", json=body, headers=H)
    assert r.status_code == 422 and f.sql == []


def test_dateiname_regel():
    assert not mm._gueltig("a\x7fb.jpg") and not mm._gueltig("a\x00.jpg") and not mm._gueltig("a..b.jpg")
    assert mm._gueltig("ä ö.jpg") and not mm._gueltig("x" * 201) and mm._gueltig("x" * 200)


# --- /medien/zuordnung -------------------------------------------------


def test_zuordnung_null_schreibt_null(umg):
    f, p, c = umg
    r = c.post("/api/pult/medien/zuordnung", json={"dateiname": "a.jpg", "mandant": None}, headers=H)
    assert r.status_code == 200 and r.json() == {"dateiname": "a.jpg", "mandant": None}
    assert f.sql == [] and "NULL::text" in p.sql[0] and "ARRAY['a.jpg']" in p.sql[0]


def test_zuordnung_mit_mandant(umg):
    f, p, c = umg
    f.antworten.append([_sicht_zeile(name="Laura")])
    r = c.post("/api/pult/medien/zuordnung", json={"dateiname": "a.jpg", "mandant": "laura"}, headers=H)
    assert r.status_code == 200 and r.json()["mandant"] == "laura"
    assert "SELECT n, 'laura' FROM unnest" in p.sql[0]


@pytest.mark.parametrize("body", [
    {"dateiname": "a.jpg", "mandant": ""},
    {"dateiname": "a.jpg"},
    {"dateiname": "a.jpg", "mandant": 5},
    {"dateiname": "a.jpg", "mandant": False},
    {"dateiname": "a.jpg", "mandant": ["laura"]},
])
def test_zuordnung_nur_null_ist_gemeinsam_sonst_422(umg, body):
    f, p, c = umg
    r = c.post("/api/pult/medien/zuordnung", json=body, headers=H)
    assert r.status_code == 422 and f.sql == [] and p.sql == []


def test_zuordnung_unbekannter_mandant_422(umg):
    f, p, c = umg
    f.antworten.append([_sicht_zeile(name=None)])
    r = c.post("/api/pult/medien/zuordnung", json={"dateiname": "a.jpg", "mandant": "nix"}, headers=H)
    assert r.status_code == 422 and p.sql == []


@pytest.mark.parametrize("name", ["a/b.jpg", "../x.jpg", "", "a\\b.jpg", 'a"b.jpg', "x" * 201, None, 5])
def test_zuordnung_ungueltiger_name_422_ohne_sql(umg, name):
    f, p, c = umg
    r = c.post("/api/pult/medien/zuordnung", json={"dateiname": name, "mandant": None}, headers=H)
    assert r.status_code == 422 and f.sql == [] and p.sql == []


def test_zuordnung_db_fehler_503(umg):
    f, p, c = umg
    p.fehler.append(RuntimeError("psql failed: ssh: connect refused"))
    r = c.post("/api/pult/medien/zuordnung", json={"dateiname": "a.jpg", "mandant": None}, headers=H)
    assert r.status_code == 503


def test_zuordnung_db_ablehnung_422_mit_grund(umg):
    _, p, c = umg
    p.fehler.append(RuntimeError("psql failed: ERROR:  Dateiname unzulaessig"))
    r = c.post("/api/pult/medien/zuordnung", json={"dateiname": "a.jpg", "mandant": None}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Dateiname unzulaessig"


# --- /inhalte/{iid}/medien/zuordnen ------------------------------------


def test_inhalt_zuordnen(umg):
    _, p, c = umg
    p.ausgaben.append("2\n")
    r = c.post(f"/api/pult/inhalte/{IID}/medien/zuordnen", json={"namen": ["a.jpg", "b.jpg"]}, headers=H)
    assert r.status_code == 200 and r.json() == {"zugeordnet": 2}
    assert "marketing.inhalte i" in p.sql[0] and IID in p.sql[0] and "i.mandant" in p.sql[0]


def test_inhalt_unbekannt_404(umg):
    _, p, c = umg
    p.ausgaben.append("0\n")
    r = c.post(f"/api/pult/inhalte/{IID}/medien/zuordnen", json={"namen": ["a.jpg"]}, headers=H)
    assert r.status_code == 404


def test_inhalt_zu_viele_namen_422(umg):
    _, p, c = umg
    r = c.post(f"/api/pult/inhalte/{IID}/medien/zuordnen", json={"namen": [f"{i}.jpg" for i in range(21)]}, headers=H)
    assert r.status_code == 422 and p.sql == []


def test_inhalt_leere_namen_und_ungueltige_422(umg):
    _, p, c = umg
    assert c.post(f"/api/pult/inhalte/{IID}/medien/zuordnen", json={"namen": []}, headers=H).status_code == 422
    assert c.post(f"/api/pult/inhalte/{IID}/medien/zuordnen", json={"namen": ["../x"]}, headers=H).status_code == 422
    assert p.sql == []


def test_inhalt_ungueltige_id_404(umg):
    _, p, c = umg
    assert c.post("/api/pult/inhalte/kaputt/medien/zuordnen", json={"namen": ["a.jpg"]}, headers=H).status_code == 404


def test_bildauftrag_ueber_inhalt_und_404(umg):
    _, p, _ = umg
    p.ausgaben += ["1\n", "0\n"]
    aid = "22222222-2222-2222-2222-222222222222"
    assert mm.zuordnen_fuer_bildauftrag(aid, ["a.jpg"]) == 1
    assert "marketing.bild_auftraege b ON b.inhalt = i.id" in p.sql[0] and aid in p.sql[0]
    with pytest.raises(HTTPException) as e:
        mm.zuordnen_fuer_bildauftrag(aid, ["a.jpg"])
    assert e.value.status_code == 404


def test_zuordnen_leer_ohne_sql_und_dedupliziert(umg):
    _, p, _ = umg
    assert mm.zuordnen_fuer_inhalt(IID, []) == 0 and mm.zuordnen([], "x") == 0 and p.sql == []
    mm.zuordnen(["a.jpg", "a.jpg", "b.jpg"], None)
    assert "ARRAY['a.jpg','b.jpg']" in p.sql[0]


def test_unlesbare_ausgabe_503(umg):
    _, p, c = umg
    p.ausgaben.append("")
    r = c.post(f"/api/pult/inhalte/{IID}/medien/zuordnen", json={"namen": ["a.jpg"]}, headers=H)
    assert r.status_code == 503


# --- /mandanten + Schluessel -------------------------------------------


def test_mandanten_liste(umg):
    f, _, c = umg
    f.antworten.append([{"id": "vibemind", "name": "Vibemind", "aktiv": True}])
    r = c.get("/api/pult/mandanten", headers=H)
    assert r.json() == {"mandanten": [{"id": "vibemind", "name": "Vibemind", "aktiv": True}]}
    assert "ORDER BY aktiv DESC, name" in f.sql[0]


def test_mandanten_liefert_einrichtung_und_filter(umg):
    f, _, c = umg
    zeile = {"id": "probe_gmbh", "name": "Probe GmbH", "aktiv": False, "einrichtung": "laeuft"}
    f.antworten.append([zeile])
    r = c.get("/api/pult/mandanten", headers=H)
    assert r.status_code == 200 and r.json() == {"mandanten": [zeile]}
    assert "einrichtung" in f.sql[0].split("FROM")[0] and "einrichtung = 'laeuft'" not in f.sql[0]
    f.antworten.append([zeile])
    r = c.get("/api/pult/mandanten?einrichtung=1", headers=H)
    assert r.status_code == 200 and r.json() == {"mandanten": [zeile]}
    assert "WHERE einrichtung = 'laeuft'" in f.sql[1]


@pytest.mark.parametrize("methode,pfad,body", [
    ("get", "/api/pult/mandanten", None),
    ("post", "/api/pult/medien/sichtbar", {"mandant": "vibemind", "namen": []}),
    ("post", "/api/pult/medien/zuordnung", {"dateiname": "a.jpg", "mandant": None}),
    ("post", f"/api/pult/inhalte/{IID}/medien/zuordnen", {"namen": ["a.jpg"]}),
])
def test_ohne_schluessel_401(umg, methode, pfad, body):
    f, p, c = umg
    for kopf in ({}, {"X-Pult-Key": "falsch"}):
        r = getattr(c, methode)(pfad, headers=kopf, **({"json": body} if body is not None else {}))
        assert r.status_code == 401
    assert f.sql == [] and p.sql == []


# --- Editor, Entwuerfe, Bilder (Plan 2026-10-19) Task 3: Platzhalter statt Bild, Zuordnung vergessen ---

def _bild_dok(url="medien:foto.jpg", extra=None):
    d = {"root": {"type": "EmailLayout", "data": {"childrenIds": ["k"]}},
         "k": {"type": "Image", "data": {"style": {}, "props": {"url": url, "width": 600, "height": 300}}}}
    d.update(extra or {})
    return d


def _vw(iid, titel, status="entwurf", fmt="bloecke", mandant="vibemind", wo="aktuell", fassung=3):
    return {"id": iid, "titel": titel, "status": status, "art": "newsletter", "wo": wo,
            "mandant": mandant, "format": fmt, "fassung": fassung}


A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _fassung(dok, fassung=3):
    return {"fassung": fassung, "format": "bloecke", "bloecke": dok,
            "felder": {"betreff": "Herbst-Betreff", "vorschautext": "Kurz"}}


def test_platzhalter_fremdes_bild_403_ohne_schreiben(umg):
    f, p, c = umg
    f.antworten.append([_sicht_zeile(fremde=["Foto.jpg"])])
    r = c.post("/api/pult/medien/platzhalter", json={"name": "foto.jpg", "mandant": "vibemind", "von": "f"},
               headers=H)
    assert r.status_code == 403 and r.json()["detail"] == "Das Bild gehört einer anderen Firma"
    assert not any("pult_bloecke_speichern" in s for s in f.sql) and len(f.sql) == 1 and p.sql == []


def test_platzhalter_stellt_nur_eigene_entwuerfe_um(umg):
    f, _, c = umg
    f.antworten.append([_sicht_zeile()])
    f.antworten.append([_vw(A, "Mein Entwurf"), _vw("b", "Eingereicht", status="eingereicht"),
                        _vw("c", "Raus", status="freigegeben", wo="freigegeben"),
                        _vw("d", "Geheim", mandant="fin2gether")])
    f.antworten.append([_fassung(_bild_dok())])
    f.antworten.append([{"fassung": 4}])
    r = c.post("/api/pult/medien/platzhalter", json={"name": "foto.jpg", "mandant": "vibemind", "von": "f"},
               headers=H)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["umgestellt"] == [{"id": A, "titel": "Mein Entwurf", "fassung": 4}]
    assert {g["id"] for g in j["gesperrt"]} == {"b", "c", "d"} and len(j["gesperrt"]) == 3
    assert all(g["grund"] for g in j["gesperrt"]) and "Geheim" not in r.text
    speichern = [s for s in f.sql if "pult_bloecke_speichern" in s]
    assert len(speichern) == 1
    s = speichern[0]
    assert f"'{A}'::uuid, 3," in s and "'Herbst-Betreff'" in s and "'Kurz'" in s and "'betreiber', false" in s
    assert "medien:platzhalter-2x1.png" in s and "medien:foto.jpg" not in s
    assert "'vibemind'" in f.sql[0]


def test_platzhalter_feldfassung_gesperrt(umg):
    f, _, c = umg
    f.antworten.append([_sicht_zeile()])
    f.antworten.append([_vw(A, "Alt", fmt="felder")])
    r = c.post("/api/pult/medien/platzhalter", json={"name": "foto.jpg", "mandant": "vibemind"}, headers=H)
    assert r.status_code == 200 and r.json()["umgestellt"] == [] and len(r.json()["gesperrt"]) == 1
    assert not any("pult_bloecke_speichern" in s for s in f.sql)


def test_platzhalter_db_ablehnung_wird_gesperrt(umg):
    f, _, c = umg
    f.antworten.append([_sicht_zeile()])
    f.antworten.append([_vw(A, "Mein Entwurf")])
    f.antworten.append([_fassung(_bild_dok())])
    f.fehler = [None, None, None, RuntimeError("psql: ERROR:  Der Assistent arbeitet gerade\nCONTEXT: x")]
    r = c.post("/api/pult/medien/platzhalter", json={"name": "foto.jpg", "mandant": "vibemind"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"umgestellt": [],
                        "gesperrt": [{"id": A, "titel": "Mein Entwurf", "grund": "Der Assistent arbeitet gerade"}]}


def test_platzhalter_db_weg_503(umg):
    f, _, c = umg
    f.antworten.append([_sicht_zeile()])
    f.antworten.append([_vw(A, "Mein Entwurf")])
    f.antworten.append([_fassung(_bild_dok())])
    f.fehler = [None, None, None, RuntimeError("ssh: connect to host offload-vm")]
    r = c.post("/api/pult/medien/platzhalter", json={"name": "foto.jpg", "mandant": "vibemind"}, headers=H)
    assert r.status_code == 503 and "offload" not in r.text


def test_platzhalter_rest_im_dokument_sperrt(umg):
    f, _, c = umg
    link = {"t": {"type": "Text", "data": {"style": {}, "props": {"text": "[Bild](medien:foto.jpg)",
                                                                  "markdown": True}}}}
    f.antworten.append([_sicht_zeile()])
    f.antworten.append([_vw(A, "Mein Entwurf")])
    f.antworten.append([_fassung(_bild_dok(extra=link))])
    r = c.post("/api/pult/medien/platzhalter", json={"name": "foto.jpg", "mandant": "vibemind"}, headers=H)
    assert r.status_code == 200
    assert r.json()["gesperrt"] == [{"id": A, "titel": "Mein Entwurf",
                                     "grund": "Das Bild steckt an einer Stelle, die kein Bildplatz ist"}]
    assert not any("pult_bloecke_speichern" in s for s in f.sql)


@pytest.mark.parametrize("body", [{"name": "foto.jpg"}, {"name": "foto.jpg", "mandant": ""},
                                  {"name": "../x.jpg", "mandant": "vibemind"}, {"mandant": "vibemind"}])
def test_platzhalter_eingaben_422_ohne_sql(umg, body):
    f, p, c = umg
    r = c.post("/api/pult/medien/platzhalter", json=body, headers=H)
    assert r.status_code == 422 and f.sql == [] and p.sql == []


@pytest.fixture
def medienordner(monkeypatch, tmp_path):
    medien, bilder = tmp_path / "medien", tmp_path / "bilder"
    medien.mkdir()
    bilder.mkdir()
    monkeypatch.setenv("MARKETING_MEDIEN_ORDNER", str(medien))
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(bilder))
    return medien, bilder


def test_vergessen_loescht_zuordnung(umg, medienordner):
    f, p, c = umg
    (medienordner[0] / "andere.jpg").write_bytes(b"x")
    p.ausgaben.append("1\n")
    r = c.post("/api/pult/medien/vergessen", json={"name": "Foto.jpg"}, headers=H)
    assert r.status_code == 200 and r.json() == {"entfernt": 1}
    assert "DELETE FROM marketing.medien_mandant" in p.sql[0] and "dateiname = 'Foto.jpg'" in p.sql[0]


@pytest.mark.parametrize("body", [{}, {"name": "../x"}, {"name": 5}])
def test_vergessen_ungueltig_422(umg, body):
    f, p, c = umg
    assert c.post("/api/pult/medien/vergessen", json=body, headers=H).status_code == 422 and p.sql == []


@pytest.mark.parametrize("pfad,body", [("/api/pult/medien/platzhalter", {"name": "a.jpg", "mandant": "vibemind"}),
                                       ("/api/pult/medien/vergessen", {"name": "a.jpg"})])
def test_platzhalter_und_vergessen_ohne_schluessel_401(umg, pfad, body):
    f, p, c = umg
    assert c.post(pfad, json=body).status_code == 401 and f.sql == [] and p.sql == []


# --- Fix-Runde 1: I2 vergessen nur ohne Datei, M4 503 nennt Umgestellte, M6 zwei Plaetze = ein Speichern ---

@pytest.mark.parametrize("ordner", [0, 1])
def test_vergessen_datei_existiert_noch_409_ohne_sql(umg, medienordner, ordner):
    f, p, c = umg
    (medienordner[ordner] / "Foto.jpg").write_bytes(b"x")
    r = c.post("/api/pult/medien/vergessen", json={"name": "Foto.jpg"}, headers=H)
    assert r.status_code == 409 and r.json()["detail"] == "Datei existiert noch – erst löschen"
    assert p.sql == [] and f.sql == []


def test_vergessen_geloeschte_datei_ok(umg, medienordner):
    f, p, c = umg
    datei = medienordner[0] / "Foto.jpg"
    datei.write_bytes(b"x")
    assert c.post("/api/pult/medien/vergessen", json={"name": "Foto.jpg"}, headers=H).status_code == 409
    datei.unlink()
    r = c.post("/api/pult/medien/vergessen", json={"name": "Foto.jpg"}, headers=H)
    assert r.status_code == 200 and r.json() == {"entfernt": 1} and len(p.sql) == 1


def test_vergessen_ohne_medienordner_503(umg, monkeypatch):
    f, p, c = umg
    monkeypatch.delenv("MARKETING_MEDIEN_ORDNER", raising=False)
    monkeypatch.delenv("MARKETING_BILD_ORDNER", raising=False)
    r = c.post("/api/pult/medien/vergessen", json={"name": "Foto.jpg"}, headers=H)
    assert r.status_code == 503 and p.sql == []


def test_vergessen_ordner_nicht_lesbar_503(umg, monkeypatch, tmp_path):
    f, p, c = umg
    monkeypatch.setenv("MARKETING_MEDIEN_ORDNER", str(tmp_path / "gibt-es-nicht"))
    monkeypatch.delenv("MARKETING_BILD_ORDNER", raising=False)
    r = c.post("/api/pult/medien/vergessen", json={"name": "Foto.jpg"}, headers=H)
    assert r.status_code == 503 and p.sql == []


def test_platzhalter_db_weg_mitten_in_der_schleife_nennt_umgestellte(umg):
    f, _, c = umg
    B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    f.antworten.append([_sicht_zeile()])
    f.antworten.append([_vw(A, "Erster"), _vw(B, "Zweiter")])
    f.antworten.append([_fassung(_bild_dok())])
    f.antworten.append([{"fassung": 4}])
    f.fehler = [None, None, None, None, RuntimeError("ssh: connect to host offload-vm")]
    r = c.post("/api/pult/medien/platzhalter", json={"name": "foto.jpg", "mandant": "vibemind"}, headers=H)
    assert r.status_code == 503 and "offload" not in r.text
    j = r.json()
    assert j["detail"] == "Marketing-Datenbank nicht erreichbar"
    assert j["umgestellt"] == [{"id": A, "titel": "Erster", "fassung": 4}] and j["gesperrt"] == []


def test_platzhalter_zwei_plaetze_ein_speichern(umg):
    f, _, c = umg
    zweiter = {"k2": {"type": "Image", "data": {"style": {}, "props": {"url": "medien:foto.jpg", "width": 300,
                                                                     "height": 300}}},
               "box": {"type": "Container", "data": {"style": {}, "props": {"url": "medien:foto.jpg",
                                                                            "childrenIds": []}}}}
    f.antworten.append([_sicht_zeile()])
    f.antworten.append([_vw(A, "Mein Entwurf")])
    f.antworten.append([_fassung(_bild_dok(extra=zweiter))])
    f.antworten.append([{"fassung": 4}])
    r = c.post("/api/pult/medien/platzhalter", json={"name": "foto.jpg", "mandant": "vibemind"}, headers=H)
    assert r.status_code == 200 and r.json()["umgestellt"] == [{"id": A, "titel": "Mein Entwurf", "fassung": 4}]
    speichern = [s for s in f.sql if "pult_bloecke_speichern" in s]
    assert len(speichern) == 1
    s = speichern[0]
    assert "medien:foto.jpg" not in s and "medien:platzhalter-2x1.png" in s and "medien:platzhalter-1x1.png" in s
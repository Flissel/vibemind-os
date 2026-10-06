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

"""Pult-API der Newsletter-Freigabe (Plan Task 2): Einreichen, Zurueckziehen, Zurueckgeben,
Freigeben (mit Flaechen-Export), Export nachholen, Freigaben-Liste. Ohne echte DB (FalscheDB)."""
import pytest

from spaces.marketing.tests.test_chat_api import (  # noqa: F401 - umg ist eine Fixture
    AID, G_WEG, H, IID, PK, _db_fehler, _dok, umg)

UNBEKANNT = "99999999-9999-9999-9999-999999999999"
ROUTEN = [
    ("post", f"/api/pult/inhalte/{IID}/einreichen", {"von": "x"}),
    ("post", f"/api/pult/inhalte/{IID}/zurueckziehen", {"von": "x"}),
    ("post", f"/api/pult/inhalte/{IID}/zurueckgeben", {"fassung": 1, "von": "x", "text": "t"}),
    ("post", f"/api/pult/inhalte/{IID}/freigeben", {"fassung": 1, "von": "x"}),
    ("post", f"/api/pult/inhalte/{IID}/export_nachholen", {}),
    ("get", "/api/pult/freigaben", None),
]


def _freigegeben(bloecke=None, art="newsletter", fmt="bloecke", status="freigegeben", titel="Oktober-Angebot!"):
    return [{"status": status, "art": art, "format": fmt, "titel": titel, "bloecke": bloecke}]


@pytest.mark.parametrize("methode,pfad,body", ROUTEN)
def test_ohne_schluessel_401(umg, methode, pfad, body):
    f, _, c = umg
    aufruf = getattr(c, methode)
    r = aufruf(pfad) if body is None else aufruf(pfad, json=body)
    assert r.status_code == 401
    falsch = {"X-Pult-Key": "falsch"}
    r = aufruf(pfad, headers=falsch) if body is None else aufruf(pfad, json=body, headers=falsch)
    assert r.status_code == 401 and f.sql == []


# --- einreichen / zurueckziehen ---


def test_einreichen_ok(umg):
    f, _, c = umg
    f.antworten.append([{"fassung": 3, "gesperrt": False}])   # Collage-Sperre und Einreichen in EINER Abfrage
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "eingereicht", "fassung": 3}
    assert len(f.sql) == 1
    assert "marketing.pult_einreichen(" in f.sql[0] and "'Anna'" in f.sql[0] and IID in f.sql[0]


def test_einreichen_db_ablehnung_422(umg):
    f, _, c = umg
    f.fehler.append(_db_fehler("Der Assistent arbeitet gerade"))
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Der Assistent arbeitet gerade"


def test_einreichen_db_weg_503(umg):
    f, _, c = umg
    f.fehler.append(RuntimeError("ssh: connection refused"))
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 503 and "ssh" not in r.text


ANREDE = "Die Anrede steht in einem Bildbereich – sie muss in frei stehendem Fließtext stehen"
HELL = (250, 250, 250)


def _collage(text_y=600):
    return {"version": 1, "breite": 600, "hoehe": 800, "hintergrund": {"farbe": "#fafafa", "bild": None},
            "schriften": {"anzeige": "playfair", "text": "dm-sans"},
            "ebenen": [{"id": "b", "art": "bild", "x": 300, "y": 200, "breite": 400, "hoehe": 300,
                        "quelle": "medien:probe.png"},
                       {"id": "t", "art": "text", "x": 300, "y": text_y, "breite": 480, "hoehe": 100,
                        "text": "Hallo {vorname}", "rolle": "TEXT", "groesse": 17, "farbe": "#333333"}]}


def _pruef_zeile(s=None):
    """Antwort auf die Leseabfrage der Pruefung (format, seite der Fassung)."""
    return [{"format": "seite", "seite": s if s is not None else _collage()}]


@pytest.fixture
def renderer(monkeypatch):
    """Gefaelschtes Chromium: helle Seite (der dunkle Text ist darauf lesbar), keine Masse. renderer.farbe
    aendert die Pixel, renderer.fehler laesst jeden Aufruf werfen; renderer.kacheln merkt platzhalter_kacheln."""
    import io
    from PIL import Image
    from spaces.marketing.claw import seite_bilder

    class Aufrufe(list):
        farbe = HELL
        fehler = None
        kacheln: list = []

    aufrufe = Aufrufe()
    aufrufe.kacheln = []

    def render(s, medien_lesen, faktor=2, platzhalter_kacheln=False, **_):
        aufrufe.append(s)
        aufrufe.kacheln.append(platzhalter_kacheln)
        if aufrufe.fehler is not None:
            raise aufrufe.fehler
        p = io.BytesIO()
        Image.new("RGB", (600 * faktor, int(s["hoehe"]) * faktor), aufrufe.farbe).save(p, "PNG")
        return p.getvalue(), {}
    monkeypatch.setattr(seite_bilder, "render_und_messen", render)
    return aufrufe


def _fassung(s=None, mail=None):
    """Antwort auf _fassung_lesen (Pruefung und Erzeugung lesen die Fassung EINMAL gemeinsam)."""
    return [{"format": "seite", "seite": s if s is not None else _collage(), "felder": {"betreff": "Herbst"},
             "pflichtteil": {"impressum": "I"}, "mail": mail}]


def _voll(renderer) -> list:
    """Volle Renderings (mit Textebenen) - die ohne Text haben nur die Bild-Ebene."""
    return [s for s in renderer if any(e["art"] == "text" for e in s["ebenen"])]


def test_einreichen_collage_erzeugt_mail_dann_einreichen(umg, renderer):
    """Fix-Runde 1: ohne Mail-Fassung genau EIN volles Rendering (Pruefung und Mail teilen es) und eines ohne Text."""
    import os
    f, tmp, c = umg
    name = "ms-11111111-2-01.jpg"
    f.antworten += [
        [{"fassung": None, "gesperrt": True}],                     # Formatsperre: neueste Fassung ist 'seite'
        [{"n": 2}],                                                 # neueste Fassung
        _fassung(),                                                 # Fassung einmal lesen (Pruefung + Mail)
        [{"mail": {"medien": [{"name": name, "cid": name, "sw": False}], "bytes": 1}}],
        [{"fassung": 2}],
    ]
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "eingereicht", "fassung": 2}
    assert len(renderer) == 2 and len(_voll(renderer)) == 1
    assert [e["id"] for e in renderer[1]["ebenen"]] == ["b"] and renderer.kacheln == [False, False]
    assert os.listdir(tmp) == [name]
    assert "marketing.pult_seite_mail_lesen(" in f.sql[2]
    assert "marketing.pult_seite_mail_speichern(" in f.sql[3]
    # zuletzt, nach der Mail-Fassung: Einreichen genau dieser Fassung (die DB prueft neueste = 2 und Mail da)
    assert f.sql[4] == f"SELECT marketing.pult_seite_einreichen('{IID}'::uuid, 2, 'Anna') AS fassung"
    assert len(f.sql) == 5


def test_einreichen_collage_ohne_pixelbedarf_genau_ein_rendering(umg, renderer):
    """Text mit eigener Unterlage: kein Rendering ohne Text - insgesamt genau eines fuer Pruefung und Mail."""
    f, _, c = umg
    s = _collage()
    s["ebenen"][1]["hintergrund"] = "#ffffff"
    s["ebenen"][1]["text"] = "Herbstgrüße"          # Text mit Unterlage wird Bildband - dort keine Anrede
    f.antworten += [[{"fassung": None, "gesperrt": True}], [{"n": 2}], _fassung(s),
                    [{"mail": {"medien": [], "bytes": 1}}], [{"fassung": 2}]]
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 200, r.text
    assert len(renderer) == 1


def test_einreichen_collage_mail_schon_da_rendert_nur_die_pruefung(umg, renderer):
    f, _, c = umg
    f.antworten += [[{"fassung": None, "gesperrt": True}], [{"n": 3}], _fassung(mail={"medien": [], "bytes": 0}),
                    [{"fassung": 3}]]
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 200 and r.json()["fassung"] == 3
    assert len(_voll(renderer)) == 1 and len(renderer) == 2
    assert "pult_seite_einreichen(" in f.sql[-1] and ", 3, " in f.sql[-1] and len(f.sql) == 4


def test_einreichen_collage_pruefung_gemerkt_kein_rendering(umg, renderer):
    """Pruefung schon gemerkt (z. B. eben ueber /pruefung) und Mail da: kein Chromium beim Einreichen."""
    f, _, c = umg
    f.antworten += [_fassung()]
    assert c.get(f"/api/pult/inhalte/{IID}/pruefung?fassung=3", headers=H).status_code == 200
    n = len(renderer)
    f.antworten += [[{"fassung": None, "gesperrt": True}], [{"n": 3}], _fassung(mail={"medien": [], "bytes": 0}),
                    [{"fassung": 3}]]
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 200 and len(renderer) == n


def test_einreichen_collage_anrede_im_bild_blockiert(umg, renderer):
    f, tmp, c = umg
    f.antworten += [[{"fassung": None, "gesperrt": True}], [{"n": 2}], _fassung(_collage(text_y=200))]
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == ANREDE       # Text wie Stufe 4, nur dieser harte Befund
    assert list(tmp.iterdir()) == []
    # nur Formatsperre, neueste Fassung, Fassung lesen - keine Mail-Fassung, eingereicht wird nichts
    assert len(f.sql) == 3 and not any("pult_seite_einreichen" in q or "pult_seite_mail_speichern" in q
                                       for q in f.sql)


def test_einreichen_collage_link_im_bild_blockiert(umg, renderer):
    f, tmp, c = umg
    s = _collage(text_y=200)
    s["ebenen"][1]["text"] = "Siehe [hier](https://example.com/x)"
    f.antworten += [[{"fassung": None, "gesperrt": True}], [{"n": 2}], _fassung(s)]
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 422
    assert r.json()["detail"] == "Ein Link steht in einem Bildbereich – er muss in frei stehendem Fließtext stehen"
    assert list(tmp.iterdir()) == []
    assert not any("pult_seite_einreichen" in q for q in f.sql)


def test_einreichen_collage_kontrast_blockiert_mit_weiteren(umg, renderer):
    """Harte Befunde aus Anrede-im-Bild UND Kontrast: eine 422, Anrede zuerst (Reihenfolge wie Stufe 4), dazu
    "(weitere: n)"."""
    f, tmp, c = umg
    renderer.farbe = (40, 40, 40)                      # dunkler Grund unter dem dunklen Text
    s = _collage(text_y=200)
    s["ebenen"].append({"id": "titel", "art": "text", "x": 300, "y": 650, "breite": 480, "hoehe": 60,
                        "text": "Herbst", "rolle": "ANZEIGE", "groesse": 40, "farbe": "#333333"})
    f.antworten += [[{"fassung": None, "gesperrt": True}], [{"n": 2}], _fassung(s)]
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 422
    assert r.json()["detail"] == ANREDE + " (weitere: 2)"           # Kontrast von t und titel
    assert list(tmp.iterdir()) == [] and not any("pult_seite_mail_speichern" in q for q in f.sql)


def test_einreichen_collage_nur_kontrast_blockiert(umg, renderer):
    f, _, c = umg
    renderer.farbe = (40, 40, 40)
    f.antworten += [[{"fassung": None, "gesperrt": True}], [{"n": 2}], _fassung()]
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d.startswith("t: Kontrast ") and "mindestens 4,5:1" in d and "weitere" not in d


def test_einreichen_collage_weiche_befunde_blockieren_nicht(umg, renderer):
    """Kein Knopf ist nur ein Hinweis - eingereicht wird trotzdem."""
    f, _, c = umg
    f.antworten += [[{"fassung": None, "gesperrt": True}], [{"n": 3}], _fassung(mail={"medien": [], "bytes": 0}),
                    [{"fassung": 3}]]
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 200 and r.json() == {"status": "eingereicht", "fassung": 3}


def test_pruefung_503_blockiert_nicht_inhaltlich(umg, renderer):
    """Review Focus 4: Chromium weg - die Pruefung ist technisch nicht moeglich, das Einreichen laeuft wie in
    Stufe 4 weiter (die vorhandene Mail-Fassung wird eingereicht; muesste sie erzeugt werden, kaeme deren eigenes
    503)."""
    f, _, c = umg
    renderer.fehler = RuntimeError("Executable doesn't exist at /ms-playwright/chromium")
    f.antworten += [[{"fassung": None, "gesperrt": True}], [{"n": 3}], _fassung(mail={"medien": [], "bytes": 0}),
                    _fassung(mail={"medien": [], "bytes": 0}), [{"fassung": 3}]]
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "eingereicht", "fassung": 3}
    assert "pult_seite_einreichen(" in f.sql[-1] and len(renderer) == 1


def test_pruefung_503_dann_mail_erzeugung_meldet_selbst_503(umg, renderer):
    f, tmp, c = umg
    renderer.fehler = RuntimeError("Executable doesn't exist at /ms-playwright/chromium")
    f.antworten += [[{"fassung": None, "gesperrt": True}], [{"n": 2}], _fassung(), _fassung()]
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 503 and "playwright" not in r.text
    assert list(tmp.iterdir()) == [] and not any("pult_seite_einreichen" in q for q in f.sql)


def test_einreichen_zeitlimit_der_pruefung_503_ohne_zweiten_versuch(umg, renderer):
    """Fix-Runde 1: das Zeitlimit der Pruefung hat das Zeitbudget verbraucht - 503 sofort, kein zweites Rendering
    in der Erzeugung."""
    from spaces.marketing.claw import seite_bilder
    f, tmp, c = umg
    renderer.fehler = seite_bilder.StueckFehler(seite_bilder.ZEITLIMIT)
    f.antworten += [[{"fassung": None, "gesperrt": True}], [{"n": 2}], _fassung()]
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 503 and r.json()["detail"] == seite_bilder.ZEITLIMIT
    assert len(renderer) == 1 and len(f.sql) == 3 and list(tmp.iterdir()) == []


def test_einreichen_sperre_belegt_503_ohne_rendering(umg, renderer, monkeypatch):
    from spaces.marketing.api import seite_mail_dienst as dienst
    f, _, c = umg
    monkeypatch.setattr(dienst, "ERZEUG_WARTEN_S", 0.01)
    f.antworten += [[{"fassung": None, "gesperrt": True}], [{"n": 2}], _fassung()]
    assert dienst._erzeug_sperre.acquire(blocking=False)
    try:
        r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    finally:
        dienst._erzeug_sperre.release()
    assert r.status_code == 503 and r.json()["detail"] == dienst.BESCHAEFTIGT
    assert renderer == [] and len(f.sql) == 3


def test_einreichen_mit_platzhalter_blockiert_wie_stufe_4(umg, renderer, monkeypatch):
    """Seite mit Platzhalter (Bild wird noch erzeugt): keine Pruefung mit Kacheln - die Erzeugung blockiert wie in
    Stufe 4 mit "Bild ... fehlt"."""
    from spaces.marketing.claw import seite_bilder
    f, tmp, c = umg
    s = _collage()
    s["ebenen"][0]["quelle"] = "medien:platzhalter-4x3.png"
    renderer.fehler = seite_bilder.StueckFehler("Bild platzhalter-4x3.png fehlt")
    f.antworten += [[{"fassung": None, "gesperrt": True}], [{"n": 2}], _fassung(s), _fassung(s)]
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Bild platzhalter-4x3.png fehlt"
    assert renderer.kacheln == [False] and list(tmp.iterdir()) == []


def test_einreichen_collage_ohne_namen_vor_dem_rendern(umg, renderer):
    """Leeres von: 422 mit dem Text von pult_einreichen, bevor die Mail-Fassung erzeugt wird (kein Chromium)."""
    f, tmp, c = umg
    f.antworten.append([{"fassung": None, "gesperrt": True}])
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "  "}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Ohne Namen kein Einreichen"
    assert renderer == [] and list(tmp.iterdir()) == [] and len(f.sql) == 1


def test_einreichen_collage_zu_gross_blockiert(umg, renderer, monkeypatch):
    from spaces.marketing.claw import seite_bilder
    f, tmp, c = umg
    echt = seite_bilder.zuschneiden
    monkeypatch.setattr(seite_bilder, "zuschneiden", lambda png, st, **k: echt(png, st, max_bytes=1))
    f.antworten += [[{"fassung": None, "gesperrt": True}], [{"n": 2}], _fassung()]
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 422 and r.json()["detail"].startswith("Die Mail wäre zu groß (")
    assert list(tmp.iterdir()) == [] and len(f.sql) == 3


def test_einreichen_collage_veraltet_422(umg, renderer):
    """Zwischen Erzeugen und Einreichen kam eine neuere Fassung: die DB lehnt ab, nichts wird eingereicht."""
    f, _, c = umg
    f.antworten += [[{"fassung": None, "gesperrt": True}], [{"n": 2}], _fassung(mail={"medien": [], "bytes": 0})]
    f.fehler += [None, None, None, _db_fehler("Inzwischen gibt es Fassung 3 – bitte neu laden")]
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Inzwischen gibt es Fassung 3 – bitte neu laden"


# --- Pruefung (Stufe 5) ---


def test_pruefung_route_befunde_und_merken(umg, renderer):
    f, _, c = umg
    f.antworten += [_pruef_zeile()]
    r = c.get(f"/api/pult/inhalte/{IID}/pruefung?fassung=2", headers=H)
    assert r.status_code == 200, r.text
    befunde = r.json()["befunde"]
    assert {"schwere": "weich", "punkt": "seite",
            "satz": "seite: kein Knopf – die Leserin weiß nicht, was sie tun soll"} in befunde
    assert all(b["schwere"] == "weich" for b in befunde) and len(renderer) == 2
    # zweite Anfrage innerhalb von 10 Minuten: weder DB noch Chromium
    n = len(f.sql)
    assert c.get(f"/api/pult/inhalte/{IID}/pruefung?fassung=2", headers=H).json()["befunde"] == befunde
    assert len(f.sql) == n and len(renderer) == 2


def test_pruefung_route_ohne_fassung_nimmt_die_neueste(umg, renderer):
    f, _, c = umg
    f.antworten += [[{"n": 4}], _pruef_zeile()]
    r = c.get(f"/api/pult/inhalte/{IID}/pruefung", headers=H)
    assert r.status_code == 200 and "max(fassung)" in f.sql[0] and "fassung = 4" in f.sql[1]


def test_pruefung_route_technischer_fehler_503_nicht_gemerkt(umg, renderer):
    f, _, c = umg
    renderer.fehler = RuntimeError("Executable doesn't exist at /ms-playwright/chromium")
    f.antworten += [_pruef_zeile(), _pruef_zeile()]
    r = c.get(f"/api/pult/inhalte/{IID}/pruefung?fassung=2", headers=H)
    assert r.status_code == 503 and "playwright" not in r.text
    renderer.fehler = None
    assert c.get(f"/api/pult/inhalte/{IID}/pruefung?fassung=2", headers=H).status_code == 200


def test_pruefung_route_zeitlimit_503(umg, renderer):
    from spaces.marketing.claw import seite_bilder
    f, _, c = umg
    renderer.fehler = seite_bilder.StueckFehler(seite_bilder.ZEITLIMIT)
    f.antworten += [_pruef_zeile()]
    assert c.get(f"/api/pult/inhalte/{IID}/pruefung?fassung=2", headers=H).status_code == 503


def test_pruefung_route_bild_fehlt_422(umg, renderer):
    from spaces.marketing.claw import seite_bilder
    f, _, c = umg
    renderer.fehler = seite_bilder.StueckFehler("Bild probe.png fehlt")
    f.antworten += [_pruef_zeile()]
    r = c.get(f"/api/pult/inhalte/{IID}/pruefung?fassung=2", headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Bild probe.png fehlt"


def test_pruefung_route_kein_collage_422_unbekannt_404(umg, renderer):
    f, _, c = umg
    f.antworten += [[{"format": "bloecke", "seite": None}], []]
    assert c.get(f"/api/pult/inhalte/{IID}/pruefung?fassung=1", headers=H).status_code == 422
    assert c.get(f"/api/pult/inhalte/{IID}/pruefung?fassung=9", headers=H).status_code == 404
    assert renderer == []
    assert c.get(f"/api/pult/inhalte/{IID}/pruefung?fassung=0", headers=H).status_code == 422
    assert c.get(f"/api/pult/inhalte/{IID}/pruefung", headers={"X-Pult-Key": "falsch"}).status_code == 401


def test_pruefung_ohne_text_ohne_pixel_ein_rendering(umg, renderer):
    """Nur Texte mit eigener Unterlage: kein zweites Rendering (kontraste braucht keine Pixel)."""
    f, _, c = umg
    s = _collage()
    s["ebenen"][1]["hintergrund"] = "#ffffff"
    f.antworten += [_pruef_zeile(s)]
    assert c.get(f"/api/pult/inhalte/{IID}/pruefung?fassung=2", headers=H).status_code == 200
    assert len(renderer) == 1


def test_pruefung_wartet_auf_die_erzeugsperre(umg, renderer, monkeypatch):
    from spaces.marketing.api import seite_mail_dienst as dienst
    f, _, c = umg
    monkeypatch.setattr(dienst, "ERZEUG_WARTEN_S", 0.01)
    f.antworten += [_pruef_zeile()]
    assert dienst._erzeug_sperre.acquire(blocking=False)
    try:
        r = c.get(f"/api/pult/inhalte/{IID}/pruefung?fassung=2", headers=H)
    finally:
        dienst._erzeug_sperre.release()
    assert r.status_code == 503 and r.json()["detail"] == dienst.BESCHAEFTIGT and renderer == []


def test_zurueckziehen_ok_und_ablehnung(umg):
    f, _, c = umg
    f.antworten.append([{"s": "entwurf"}])
    r = c.post(f"/api/pult/inhalte/{IID}/zurueckziehen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 200 and r.json() == {"status": "entwurf"}
    assert "marketing.pult_zurueckziehen(" in f.sql[0]
    f.fehler += [_db_fehler("Schon entschieden (freigegeben)")]
    r = c.post(f"/api/pult/inhalte/{IID}/zurueckziehen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Schon entschieden (freigegeben)"


def test_unbekannte_id_404(umg):
    f, _, c = umg
    r = c.post("/api/pult/inhalte/keine-uuid/einreichen", json={"von": "x"}, headers=H)
    assert r.status_code == 404 and f.sql == []


# --- zurueckgeben ---


def test_zurueckgeben_ok(umg):
    f, _, c = umg
    f.antworten.append([{"id": AID}])
    r = c.post(f"/api/pult/inhalte/{IID}/zurueckgeben", json={"fassung": 2, "von": "Anna", "text": "Preis fehlt"},
               headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "entwurf", "rueckmeldung": AID}
    assert "marketing.pult_zurueckgeben(" in f.sql[0] and ", 2, 'Anna', 'Preis fehlt')" in f.sql[0]


@pytest.mark.parametrize("text", ["", "   ", "\n\t ", None])
def test_zurueckgeben_leer_422_ohne_sql(umg, text):
    """Review Focus 3: leerer Kommentar erreicht die DB nie."""
    f, _, c = umg
    r = c.post(f"/api/pult/inhalte/{IID}/zurueckgeben", json={"fassung": 2, "von": "Anna", "text": text}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Bitte sag kurz, was fehlt" and f.sql == []


def test_zurueckgeben_zu_lang_422_ohne_sql(umg):
    f, _, c = umg
    r = c.post(f"/api/pult/inhalte/{IID}/zurueckgeben", json={"fassung": 2, "von": "A", "text": "x" * 2001}, headers=H)
    assert r.status_code == 422 and "2000" in r.json()["detail"] and f.sql == []
    f.antworten.append([{"id": AID}])
    r = c.post(f"/api/pult/inhalte/{IID}/zurueckgeben", json={"fassung": 2, "von": "A", "text": "x" * 2000}, headers=H)
    assert r.status_code == 200


def test_zurueckgeben_ohne_fassung_422_ohne_sql(umg):
    f, _, c = umg
    r = c.post(f"/api/pult/inhalte/{IID}/zurueckgeben", json={"von": "A", "text": "t"}, headers=H)
    assert r.status_code == 422 and f.sql == []


def test_zurueckgeben_db_ablehnung_422(umg):
    f, _, c = umg
    f.fehler.append(_db_fehler("Inzwischen gibt es Fassung 4 – bitte neu laden"))
    r = c.post(f"/api/pult/inhalte/{IID}/zurueckgeben", json={"fassung": 3, "von": "A", "text": "t"}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Inzwischen gibt es Fassung 4 – bitte neu laden"


# --- freigeben ---


def test_freigeben_entscheidet_zuerst_dann_flaechen_dann_export_auftrag(umg):
    f, ordner, c = umg
    f.antworten += [[{"status": "freigegeben"}],
                    _freigegeben(_dok()),                                    # festgeschriebene Fassung
                    [{"titel": "Oktober-Angebot!", "bloecke": _dok()}],      # Flaechen-Export liest den Inhalt
                    [{"titel": "Oktober-Angebot!", "bloecke": _dok()}],      # Newsletter-Auftrag
                    [{"id": AID}]]
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 2, "von": "Anna"}, headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d == {"status": "freigegeben",
                 "flaechen": ["oktober-angebot-f-handy.jpg", "oktober-angebot-f-tablet.jpg", "oktober-angebot-f-pc.jpg"],
                 "export_auftrag": AID, "export_fehler": None}
    assert "marketing.pult_entscheiden(" in f.sql[0] and ", 2, 'freigeben', 'Anna'" in f.sql[0]
    assert sorted(p.name for p in ordner.iterdir()) == sorted(d["flaechen"])             # Dateien liegen
    assert len(f.psql.sql) == 1 and "marketing.medien_mandant" in f.psql.sql[0]          # Zuordnung
    anlegen = [n for n, s in enumerate(f.sql) if "pult_chat_anlegen(" in s]
    assert len(anlegen) == 1 and "'export', ''" in f.sql[anlegen[0]] and '"slug": "oktober-angebot"' in f.sql[anlegen[0]]
    assert anlegen[0] == len(f.sql) - 1                                                  # zuletzt


def test_freigeben_veraltete_fassung_422_ohne_export(umg):
    """Review Focus 2: pult_entscheiden lehnt ab -> kein Export-SQL, keine Datei."""
    f, ordner, c = umg
    f.fehler.append(_db_fehler("Inzwischen gibt es Fassung 4 – bitte neu laden"))
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 3, "von": "Anna"}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Inzwischen gibt es Fassung 4 – bitte neu laden"
    assert len(f.sql) == 1 and f.psql.sql == [] and list(ordner.iterdir()) == []


def test_freigeben_export_fehler_bleibt_freigabe_200(umg):
    f, ordner, c = umg
    f.antworten += [[{"status": "freigegeben"}], _freigegeben(_dok(G_WEG)),
                    [{"titel": "T", "bloecke": _dok(G_WEG)}]]
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 2, "von": "Anna"}, headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "freigegeben" and d["flaechen"] == [] and d["export_auftrag"] is None
    assert "weg.png fehlt" in d["export_fehler"]
    assert list(ordner.iterdir()) == []


def test_freigeben_zuordnung_scheitert_export_fehler_200(umg):
    f, ordner, c = umg
    f.antworten += [[{"status": "freigegeben"}], _freigegeben(_dok()), [{"titel": "T", "bloecke": _dok()}]]
    f.psql.fehler.append(RuntimeError("psql weg"))
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 2, "von": "Anna"}, headers=H)
    assert r.status_code == 200 and r.json()["export_fehler"] and r.json()["status"] == "freigegeben"
    assert list(ordner.iterdir()) == []


def test_freigeben_lesefehler_nach_entscheidung_ist_export_fehler(umg):
    f, _, c = umg
    f.antworten.append([{"status": "freigegeben"}])
    f.fehler += [None, RuntimeError("db weg")]
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 2, "von": "Anna"}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "freigegeben" and r.json()["export_fehler"]


def test_freigeben_post_nur_fassung_festschreiben(umg):
    f, ordner, c = umg
    f.antworten += [[{"status": "freigegeben"}], _freigegeben(None, art="post", fmt="felder")]
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 1, "von": "Anna"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "freigegeben", "flaechen": [], "export_auftrag": None, "export_fehler": None}
    assert all("pult_chat_anlegen" not in s for s in f.sql) and list(ordner.iterdir()) == []


def test_freigeben_newsletter_im_feldformat_ohne_export_auftrag(umg):
    f, _, c = umg
    f.antworten += [[{"status": "freigegeben"}], _freigegeben(None, fmt="felder")]
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 1, "von": "Anna"}, headers=H)
    assert r.json()["export_auftrag"] is None and all("pult_chat_anlegen" not in s for s in f.sql)


def test_freigeben_ohne_flaechen_nur_newsletter_auftrag(umg):
    f, ordner, c = umg
    kein_bild = {"root": {"type": "EmailLayout", "data": {"childrenIds": []}}}
    f.antworten += [[{"status": "freigegeben"}], _freigegeben(kein_bild),
                    [{"titel": "Heft", "bloecke": kein_bild}], [{"id": AID}]]
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 1, "von": "Anna"}, headers=H)
    assert r.json() == {"status": "freigegeben", "flaechen": [], "export_auftrag": AID, "export_fehler": None}
    assert f.psql.sql == [] and list(ordner.iterdir()) == []


def test_freigeben_ohne_fassung_422_ohne_sql(umg):
    f, _, c = umg
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"von": "Anna"}, headers=H)
    assert r.status_code == 422 and f.sql == []


# --- export_nachholen ---


def test_export_nachholen_bei_freigegeben(umg):
    f, ordner, c = umg
    f.antworten += [_freigegeben(_dok()), [{"titel": "Oktober-Angebot!", "bloecke": _dok()}],
                    [{"vorhanden": False}],
                    [{"titel": "Oktober-Angebot!", "bloecke": _dok()}], [{"id": AID}]]
    r = c.post(f"/api/pult/inhalte/{IID}/export_nachholen", json={}, headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "freigegeben" and len(d["flaechen"]) == 3 and d["export_auftrag"] == AID
    assert d["flaechen_uebersprungen"] == [] and d["auftrag_vorhanden"] is False
    assert d["export_fehler"] is None
    assert all("pult_entscheiden" not in s for s in f.sql)


def test_export_nachholen_bei_entwurf_422(umg):
    f, ordner, c = umg
    f.antworten.append(_freigegeben(_dok(), status="entwurf"))
    r = c.post(f"/api/pult/inhalte/{IID}/export_nachholen", json={}, headers=H)
    assert r.status_code == 422 and len(f.sql) == 1 and list(ordner.iterdir()) == []


def test_export_nachholen_unbekannt_404(umg):
    f, _, c = umg
    r = c.post(f"/api/pult/inhalte/{UNBEKANNT}/export_nachholen", json={}, headers=H)
    assert r.status_code == 404


def test_export_nachholen_fehler_im_export_200(umg):
    f, _, c = umg
    f.antworten += [_freigegeben(_dok(G_WEG)), [{"titel": "T", "bloecke": _dok(G_WEG)}]]
    r = c.post(f"/api/pult/inhalte/{IID}/export_nachholen", json={}, headers=H)
    assert r.status_code == 200 and "weg.png fehlt" in r.json()["export_fehler"]


# --- Freigaben-Liste ---

ZEILE = {"id": IID, "mandant": "vibemind", "mandant_name": "Vibemind", "art": "newsletter", "titel": "Oktober",
         "betreff": "Hallo", "format": "bloecke", "status": "eingereicht", "eingereichte_fassung": 2, "eingereicht_am": "2026-10-07 10:00",
         "eingereicht_von": "Anna", "entschieden_von": None, "entschieden_am": None, "grund": None,
         "rueckmeldungen": [{"text": "Preis", "von": "Anna", "am": "2026-10-06", "fassung": 1, "erledigt": True}],
         "export": {"auftrag_status": None}}


def test_freigaben_form_und_mandantenname(umg):
    f, _, c = umg
    f.antworten.append([ZEILE])
    r = c.get("/api/pult/freigaben", headers=H)
    assert r.status_code == 200, r.text
    z = r.json()["freigaben"][0]
    for k in ("id", "mandant", "mandant_name", "art", "titel", "betreff", "format", "status", "eingereichte_fassung",
              "eingereicht_am", "eingereicht_von", "entschieden_von", "entschieden_am", "grund", "rueckmeldungen",
              "export"):
        assert k in z
    assert z["mandant_name"] == "Vibemind" and z["rueckmeldungen"][0]["erledigt"] is True
    sql = f.sql[0]
    assert "status = 'eingereicht'" in sql and "marketing.mandanten" in sql and "LIMIT 20" in sql
    assert "i.art = " not in sql            # alle Arten (R7)
    assert "marketing.rueckmeldungen" in sql and "LIMIT 3" in sql and "art = 'export'" in sql


def test_freigaben_format_der_neuesten_fassung(umg):
    """F2b: format (bloecke|felder) der neuesten Fassung, damit sales-ui den Export-Knopf bei felder weglaesst."""
    f, _, c = umg
    f.antworten.append([dict(ZEILE, format="felder")])
    r = c.get("/api/pult/freigaben?status=entschieden", headers=H)
    assert r.json()["freigaben"][0]["format"] == "felder"
    sql = f.sql[0]
    teil = sql[sql.index("f.format"):sql.index("AS format")]
    assert "ORDER BY f.fassung DESC LIMIT 1" in teil


def test_freigaben_export_status_nur_seit_der_entscheidung(umg):
    """F3: bei freigegeben zaehlt nur ein Export-Auftrag ab der Entscheidung - ein alter aus der
    Entwurfszeit verdeckt sonst "Export offen – erneut anstoßen"."""
    f, _, c = umg
    f.antworten.append([])
    c.get("/api/pult/freigaben?status=entschieden", headers=H)
    sql = f.sql[0]
    export = sql[sql.index("'auftrag_status'"):sql.index("AS export")]
    assert "a.erstellt_am >= i.entschieden_am" in export
    assert "i.status <> 'freigegeben' OR a.erstellt_am >= i.entschieden_am" in export
    assert export.index("entschieden_am") < export.index("ORDER BY a.erstellt_am DESC LIMIT 1")


def test_freigaben_entschieden_und_limit(umg):
    f, _, c = umg
    f.antworten.append([])
    r = c.get("/api/pult/freigaben?status=entschieden&limit=5", headers=H)
    assert r.status_code == 200 and r.json() == {"freigaben": []}
    sql = f.sql[0]
    assert "'freigegeben'" in sql and "'abgelehnt'" in sql and "30 days" in sql and "LIMIT 5" in sql
    assert "marketing.rueckmeldungen" in sql and "status = 'entwurf'" in sql


@pytest.mark.parametrize("abfrage", ["status=offen", "limit=0", "limit=101", "limit=x"])
def test_freigaben_ungueltige_parameter_422(umg, abfrage):
    f, _, c = umg
    assert c.get(f"/api/pult/freigaben?{abfrage}", headers=H).status_code == 422 and f.sql == []


def test_freigaben_db_weg_503(umg):
    f, _, c = umg
    f.fehler.append(RuntimeError("weg"))
    assert c.get("/api/pult/freigaben", headers=H).status_code == 503


# --- Fix-Runde 1 ---


def test_nachholen_ueberspringt_vorhandene_flaeche_und_laufenden_auftrag(umg):
    """B: liegt schon <slug>-<id>-<geraet>.jpg, wird nichts neu gerendert; vorhandener Export-Auftrag -> kein neuer."""
    f, ordner, c = umg
    (ordner / "oktober-angebot-f-tablet.jpg").write_bytes(b"alt")
    f.antworten += [_freigegeben(_dok()), [{"vorhanden": True}]]
    r = c.post(f"/api/pult/inhalte/{IID}/export_nachholen", json={}, headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["flaechen"] == [] and d["flaechen_uebersprungen"] == ["f"]
    assert d["export_auftrag"] is None and d["auftrag_vorhanden"] is True and d["export_fehler"] is None
    assert [p.name for p in ordner.iterdir()] == ["oktober-angebot-f-tablet.jpg"] and f.psql.sql == []
    assert all("pult_chat_anlegen" not in s for s in f.sql)
    sql = f.sql[1]
    assert "art = 'export'" in sql and "'offen', 'in_arbeit'" in sql and "'fertig'" in sql and "entschieden_am" in sql


def test_freigeben_exportiert_immer_auch_bei_vorhandener_datei(umg):
    """B gilt nur fuer nachholen: frisches Freigeben fragt nicht nach vorhandenen Dateien/Auftraegen."""
    f, ordner, c = umg
    (ordner / "oktober-angebot-f-tablet.jpg").write_bytes(b"alt")
    f.antworten += [[{"status": "freigegeben"}], _freigegeben(_dok(), art="post"),
                    [{"titel": "Oktober-Angebot!", "bloecke": _dok()}]]
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 2, "von": "Anna"}, headers=H)
    assert len(r.json()["flaechen"]) == 3 and "flaechen_uebersprungen" not in r.json()


def test_freigaben_entschieden_nur_offene_rueckmeldung(umg):
    """C: eine zurueckgegebene, wieder eingereichte und zurueckgezogene Fassung (Rueckmeldung erledigt) fehlt."""
    f, _, c = umg
    f.antworten.append([])
    c.get("/api/pult/freigaben?status=entschieden", headers=H)
    assert "erledigt_am IS NULL" in f.sql[0] and f.sql[0].index("erledigt_am IS NULL") > f.sql[0].index("status = 'entwurf'")


def test_freigeben_mehr_als_20_flaechen_in_stuecken(umg, monkeypatch):
    """D: mehr Flaechen als FLAECHEN_MAX werden in Stuecken exportiert, kein export_fehler."""
    from spaces.marketing.api import chat, freigabe
    monkeypatch.setattr(chat, "FLAECHEN_MAX", 2)
    monkeypatch.setattr(freigabe, "FLAECHEN_MAX", 2)
    f, ordner, c = umg
    dok = _dok()
    for n in range(4):
        dok[f"g{n}"] = dict(dok["f"])
    f.antworten += [[{"status": "freigegeben"}], _freigegeben(dok, art="post")] +                    [[{"titel": "Oktober", "bloecke": dok}]] * 3
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 2, "von": "Anna"}, headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["export_fehler"] is None and len(d["flaechen"]) == 15           # 5 Flaechen x 3 Geraete
    assert len(f.psql.sql) == 3                                              # 3 Stuecke (2+2+1)


# --- Pruefung mit Platzhaltern (Fix-Runde 1) ---

BILDER_IN_ARBEIT = "Bilder werden noch erzeugt – Kontrast wird danach geprüft"


def test_pruefung_route_platzhalter_als_kachel_ohne_pixel_kontrast(umg, renderer):
    """Text ueber einem Platzhalter: Kacheln statt Ablehnung, kein Pixel-Kontrast (die graue Kachel ist nicht der
    spaetere Untergrund), dafuer ein weicher Hinweis. Kein Rendering ohne Text, weil kein Text die Pixel braucht."""
    f, _, c = umg
    renderer.farbe = (40, 40, 40)                       # waere gemessen ein harter Kontrastbefund
    s = _collage(text_y=200)                            # Text liegt auf dem Bild
    s["ebenen"][1]["text"] = "Herbst ohne Anrede"
    s["ebenen"][0]["quelle"] = "medien:platzhalter-4x3.png"
    f.antworten += [_pruef_zeile(s)]
    r = c.get(f"/api/pult/inhalte/{IID}/pruefung?fassung=2", headers=H)
    assert r.status_code == 200, r.text
    befunde = r.json()["befunde"]
    assert not [b for b in befunde if b["schwere"] == "hart"]
    assert {"schwere": "weich", "punkt": "seite", "satz": BILDER_IN_ARBEIT} in befunde
    assert renderer.kacheln == [True] and len(renderer) == 1


def test_pruefung_route_platzhalter_nur_ueberlappender_text_ausgelassen(umg, renderer):
    """Text abseits des Platzhalters wird weiter gemessen (hart bei dunklem Grund), der Hinweis kommt dazu."""
    f, _, c = umg
    renderer.farbe = (40, 40, 40)
    s = _collage(text_y=600)                            # Text unterhalb des Bildes
    s["ebenen"][0]["quelle"] = "medien:platzhalter-4x3.png"
    s["ebenen"].append({"id": "auf", "art": "text", "x": 300, "y": 200, "breite": 200, "hoehe": 60,
                        "text": "Auf dem Bild", "rolle": "ANZEIGE", "groesse": 30, "farbe": "#333333"})
    f.antworten += [_pruef_zeile(s)]
    befunde = c.get(f"/api/pult/inhalte/{IID}/pruefung?fassung=2", headers=H).json()["befunde"]
    harte = [b["punkt"] for b in befunde if b["schwere"] == "hart"]
    assert harte == ["t"] and BILDER_IN_ARBEIT in [b["satz"] for b in befunde]
    assert renderer.kacheln == [True, True]

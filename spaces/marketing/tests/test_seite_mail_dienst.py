"""Mail-Fassung der Collage (Plan 2026-10-20 Stufe 4, Task 4): seite_mail_dienst.erzeugen und vorschau_html.
Ohne echte DB (FalscheDB/FalschePsql wie test_chat_api), ohne Chromium (gefaelschtes render_png), Medien in
tmp-Ordnern."""
import io
import json
import os
import re
import time

import pytest
from fastapi import HTTPException
from PIL import Image

from spaces.marketing.api import seite_mail_dienst as dienst
from spaces.marketing.claw import gestaltung, seite_bilder, seite_mail
from spaces.marketing.sync import _db
from spaces.marketing.tests.test_medien_mandant import FalschePsql
from spaces.marketing.tests.test_pult_api import FalscheDB

IID = "11111111-1111-1111-1111-111111111111"
PFLICHT = {"impressum": "Muster GmbH, Berlin", "abmelde_hinweis": "Abmelden: {abmeldelink}"}
BASIS = "https://h.ts.net/marketing/bild/1760000000." + "a" * 64 + "/"
ANREDE = "Die Anrede steht in einem Bildbereich – sie muss in frei stehendem Fließtext stehen"


def seite(ebenen, hoehe=800):
    return {"version": 1, "breite": 600, "hoehe": hoehe, "hintergrund": {"farbe": "#fafafa", "bild": None},
            "schriften": {"anzeige": "playfair", "text": "dm-sans"}, "ebenen": ebenen}


def bild(eid="b", y=200, quelle="medien:probe.png"):
    return {"id": eid, "art": "bild", "x": 300, "y": y, "breite": 400, "hoehe": 300, "quelle": quelle}


def text(eid="t", y=600, t="Hallo {vorname}"):
    return {"id": eid, "art": "text", "x": 300, "y": y, "breite": 480, "hoehe": 100, "text": t, "rolle": "TEXT",
            "groesse": 17, "farbe": "#333333"}


SEITE = seite([bild(), text()])


def zeile(s=SEITE, mail=None, fmt="seite"):
    return {"format": fmt, "seite": s, "felder": {"betreff": "Herbst", "vorschautext": "Neu"},
            "pflichtteil": PFLICHT, "mail": mail}


def _png(breite, hoehe):
    p = io.BytesIO()
    Image.new("RGB", (breite, hoehe), (200, 30, 30)).save(p, "PNG")
    return p.getvalue()


@pytest.fixture
def umg(monkeypatch, tmp_path):
    f, p = EchoDB(), FalschePsql()
    monkeypatch.setattr(_db, "query_via_docker", f.query)
    monkeypatch.setattr(_db, "query_one", f.one)
    monkeypatch.setattr(_db, "_run_psql", p)
    erzeugt, medien = tmp_path / "erzeugt", tmp_path / "medien"
    erzeugt.mkdir()
    medien.mkdir()
    (medien / "probe.png").write_bytes(_png(40, 30))
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(erzeugt))
    monkeypatch.setenv("MARKETING_MEDIEN_ORDNER", str(medien))
    aufrufe = []

    def render(s, medien_lesen, **_):
        aufrufe.append(medien_lesen("probe.png"))
        return _png(1200, int(s["hoehe"]) * 2)
    monkeypatch.setattr(seite_bilder, "render_png", render)
    return f, p, erzeugt, aufrufe


_MS = re.compile(r"ms-[0-9a-f]{8}-[0-9]+-[0-9]+(?:-[0-9]+)?\.jpg")


class EchoDB(FalscheDB):
    """Wie FalscheDB; ohne vorbereitete Antwort liefert pult_seite_mail_speichern die eben gespeicherte Zeile
    (wie die DB-Funktion nach dem INSERT)."""
    def _naechste(self, sql):
        if "pult_seite_mail_speichern" in sql and not self.antworten and not self.fehler:
            self.sql.append(sql)
            namen = list(dict.fromkeys(_MS.findall(sql.split("::jsonb")[-2])))
            return [{"mail": {"inhalt": IID, "fassung": 2, "html_versand": "<html>v</html>",
                              "html_vorschau_mjml": "<mjml/>", "text": "t", "bytes": 1,
                              "medien": [{"name": n, "cid": n, "sw": False} for n in namen]}}]
        return super()._naechste(sql)


def test_mailfassung_einmal(umg):
    f, p, erzeugt, aufrufe = umg
    n = len(seite_mail.stuecke(SEITE))
    assert n >= 1
    f.antworten.append([zeile()])
    mail = dienst.erzeugen(IID, 2)
    namen = [f"ms-11111111-2-{k:02d}.jpg" for k in range(1, n + 1)]
    assert sorted(os.listdir(erzeugt)) == namen
    assert [m["name"] for m in mail["medien"]] == namen
    assert all(Image.open(erzeugt / nm).format == "JPEG" for nm in namen)
    assert aufrufe == [_png(40, 30)]          # Medien aus MARKETING_MEDIEN_ORDNER gelesen, einmal gerendert
    sql = f.sql[-1]
    assert "marketing.pult_seite_mail_speichern(" in sql and f"'{IID}'::uuid, 2," in sql
    assert "cid:ms-11111111-2-01.jpg" in sql                  # Versand-HTML mit eingebetteten Bildern
    assert dienst.BILD_MARKE + "ms-11111111-2-01.jpg" in sql  # Vorschau-MJML ohne Token, nur die Marke
    assert "{vorname}" in sql and "[Vorname]" in sql and "{abmeldelink}" in sql
    assert "/marketing/bild/" not in sql
    assert '"cid": "ms-11111111-2-01.jpg"' in sql
    assert "marketing.medien_mandant" in p.sql[0] and f"i.id = '{IID}'::uuid" in p.sql[0]   # der Firma zugeordnet
    assert all(nm in p.sql[0] for nm in namen)

    # zweiter Aufruf: die Mail-Fassung gibt es, nichts wird neu gerendert oder abgelegt
    f.sql.clear()
    f.antworten.append([zeile(mail=mail)])
    assert dienst.erzeugen(IID, 2) == mail
    assert len(f.sql) == 1 and "marketing.pult_seite_mail_lesen(" in f.sql[0] and "f.fassung = 2" in f.sql[0]
    assert len(aufrufe) == 1 and sorted(os.listdir(erzeugt)) == namen and len(p.sql) == 1


def test_wettlauf_verlierer_raeumt_eigene_dateien_ab(umg, monkeypatch):
    f, _, erzeugt, _ = umg
    gewinner = {"inhalt": IID, "fassung": 2, "html_versand": "<html>g</html>", "html_vorschau_mjml": "<mjml/>",
                "text": "t", "medien": [{"name": "ms-11111111-2-01.jpg", "cid": "ms-11111111-2-01.jpg", "sw": False}],
                "bytes": 5}
    (erzeugt / "ms-11111111-2-01.jpg").write_bytes(b"gewinner")   # der Gewinner hat seine Datei schon abgelegt
    f.antworten += [[zeile()], [{"mail": gewinner}]]
    assert dienst.erzeugen(IID, 2) == gewinner
    assert os.listdir(erzeugt) == ["ms-11111111-2-01.jpg"]          # nur die des Gewinners bleibt
    assert (erzeugt / "ms-11111111-2-01.jpg").read_bytes() == b"gewinner"
    assert "ms-11111111-2-01-2.jpg" in f.sql[-1]                     # der Verlierer hatte -2 gezogen


def test_speichern_scheitert_dateien_weg(umg):
    f, _, erzeugt, _ = umg
    f.antworten.append([zeile()])
    f.fehler += [None, RuntimeError("ssh: connection refused")]
    with pytest.raises(HTTPException) as e:
        dienst.erzeugen(IID, 2)
    assert e.value.status_code == 503 and "ssh" not in str(e.value.detail)
    assert os.listdir(erzeugt) == []


def test_anrede_im_bild_blockiert(umg):
    f, p, erzeugt, aufrufe = umg
    f.antworten.append([zeile(seite([bild(), text(y=200)]))])   # {vorname} liegt ueber dem Bild
    with pytest.raises(HTTPException) as e:
        dienst.erzeugen(IID, 2)
    assert e.value.status_code == 422 and e.value.detail == ANREDE
    assert aufrufe == [] and os.listdir(erzeugt) == [] and len(f.sql) == 1 and p.sql == []


def test_zu_gross_blockiert(umg, monkeypatch):
    f, p, erzeugt, _ = umg
    echt = seite_bilder.zuschneiden
    monkeypatch.setattr(seite_bilder, "zuschneiden", lambda png, st, **k: echt(png, st, max_bytes=1))
    f.antworten.append([zeile()])
    with pytest.raises(HTTPException) as e:
        dienst.erzeugen(IID, 2)
    assert e.value.status_code == 422 and e.value.detail.startswith("Die Mail wäre zu groß (")
    assert e.value.detail.endswith("– weniger oder kleinere Bildbereiche")
    assert os.listdir(erzeugt) == [] and len(f.sql) == 1 and p.sql == []


def test_bild_fehlt_422(umg, monkeypatch):
    f, _, erzeugt, _ = umg

    def render(s, medien_lesen, **_):
        assert medien_lesen("gibt-es-nicht.png") is None
        assert medien_lesen("../probe.png") is None
        raise seite_bilder.StueckFehler("Bild gibt-es-nicht.png fehlt")
    monkeypatch.setattr(seite_bilder, "render_png", render)
    f.antworten.append([zeile()])
    with pytest.raises(HTTPException) as e:
        dienst.erzeugen(IID, 2)
    assert e.value.status_code == 422 and e.value.detail == "Bild gibt-es-nicht.png fehlt"
    assert os.listdir(erzeugt) == []


def test_chromium_fehlt_503(umg, monkeypatch):
    f, _, _, _ = umg

    def render(s, medien_lesen, **_):
        raise ImportError("No module named 'playwright' /geheim/pfad")
    monkeypatch.setattr(seite_bilder, "render_png", render)
    f.antworten.append([zeile()])
    with pytest.raises(HTTPException) as e:
        dienst.erzeugen(IID, 2)
    assert e.value.status_code == 503 and e.value.detail == dienst.NICHT_GERENDERT


def test_nur_text_ohne_chromium_und_ohne_dateien(umg):
    f, p, erzeugt, aufrufe = umg
    s = seite([text(y=100), text("u", y=400, t="Zweiter Absatz")])
    assert seite_mail.stuecke(s) == []
    f.antworten += [[zeile(s)], [{"mail": {"medien": [], "bytes": 0}}]]
    dienst.erzeugen(IID, 2)
    assert aufrufe == [] and os.listdir(erzeugt) == [] and p.sql == []
    assert "'[]'::jsonb, 0)" in f.sql[-1]


def test_unbekannt_und_kein_collage(umg):
    f, _, _, _ = umg
    with pytest.raises(HTTPException) as e:
        dienst.erzeugen(IID, 2)
    assert e.value.status_code == 404
    f.antworten.append([zeile(fmt="bloecke", s=None)])
    with pytest.raises(HTTPException) as e:
        dienst.erzeugen(IID, 2)
    assert e.value.status_code == 422 and e.value.detail == "Das ist kein Collage-Newsletter"


def test_ohne_ablage_503(umg, monkeypatch):
    f, _, _, aufrufe = umg
    monkeypatch.delenv("MARKETING_BILD_ORDNER")
    f.antworten.append([zeile()])
    with pytest.raises(HTTPException) as e:
        dienst.erzeugen(IID, 2)
    assert e.value.status_code == 503 and aufrufe == []


def test_vorschau_html_setzt_bild_basis_je_anfrage():
    s = SEITE
    namen = [f"ms-11111111-2-{k:02d}.jpg" for k in range(1, len(seite_mail.stuecke(s)) + 1)]
    quelle = seite_mail.mjml(s, namen, betreff="Herbst", vorschautext="Neu", pflichtteil=PFLICHT,
                             bild_adresse=lambda n: dienst.BILD_MARKE + n, fuer_versand=False)
    html = dienst.vorschau_html({"html_vorschau_mjml": quelle}, BASIS)
    assert BASIS + "ms-11111111-2-01.jpg" in html and dienst.BILD_MARKE not in html
    assert "[Vorname]" in html and "{vorname}" not in html
    anderer = BASIS.replace("1760000000", "1760009999")
    assert anderer + "ms-11111111-2-01.jpg" in dienst.vorschau_html({"html_vorschau_mjml": quelle}, anderer)


def test_ms_wird_nicht_aufgeraeumt(tmp_path):
    alt = time.time() - gestaltung.AUFRAEUM_ALTER_S - 3600
    for n in ("ms-11111111-2-01.jpg", "ms-11111111-2-01-2.jpg", "gs-0123456789ab.jpg"):
        (tmp_path / n).write_bytes(b"x")
        os.utime(tmp_path / n, (alt, alt))
    assert gestaltung.aufraeumen(str(tmp_path), set()) == ["gs-0123456789ab.jpg"]
    assert sorted(os.listdir(tmp_path)) == ["ms-11111111-2-01-2.jpg", "ms-11111111-2-01.jpg"]

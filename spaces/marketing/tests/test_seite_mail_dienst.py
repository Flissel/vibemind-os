"""Mail-Fassung der Collage (Plan 2026-10-20 Stufe 4, Task 4): seite_mail_dienst.erzeugen und vorschau_html.
Ohne echte DB (FalscheDB/FalschePsql wie test_chat_api), ohne Chromium (gefaelschtes render_und_messen), Medien in
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
        return _png(1200, int(s["hoehe"]) * 2), {}
    monkeypatch.setattr(seite_bilder, "render_und_messen", render)
    return f, p, erzeugt, aufrufe


_MS = re.compile(r"ms-[0-9a-f]{8}-[0-9]+-[0-9]+(?:-[0-9]+)?\.jpg")


class EchoDB(FalscheDB):
    """Wie FalscheDB; ohne vorbereitete Antwort liefert pult_seite_mail_speichern die eben gespeicherte Zeile
    (wie die DB-Funktion nach dem INSERT)."""
    def _naechste(self, sql):
        if "pult_seite_mail_speichern" in sql and not self.antworten and not self.fehler:
            self.sql.append(sql)
            namen = list(dict.fromkeys(_MS.findall(sql.split("::jsonb")[-2])))
            return [{"mail": {"inhalt": IID, "fassung": 2, "mjml_versand": "<mjml/>",
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
    assert "{vorname}" in sql and "[Vorname]" in sql
    # MJML ohne Pflicht-Fuss (je Anfrage aktuell eingesetzt): je eine Marke in Versand- und Vorschau-MJML; der
    # Pflichtteil steht nur in der gespeicherten Textfassung
    assert sql.count(dienst.FUSS_MARKE) == 2 and sql.count("Muster GmbH") == 1 and "{abmeldelink}" in sql
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
    gewinner = {"inhalt": IID, "fassung": 2, "mjml_versand": "<mjml/>", "html_vorschau_mjml": "<mjml/>",
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
    monkeypatch.setattr(seite_bilder, "render_und_messen", render)
    f.antworten.append([zeile()])
    with pytest.raises(HTTPException) as e:
        dienst.erzeugen(IID, 2)
    assert e.value.status_code == 422 and e.value.detail == "Bild gibt-es-nicht.png fehlt"
    assert os.listdir(erzeugt) == []


def test_chromium_fehlt_503(umg, monkeypatch):
    f, _, _, _ = umg

    def render(s, medien_lesen, **_):
        raise ImportError("No module named 'playwright' /geheim/pfad")
    monkeypatch.setattr(seite_bilder, "render_und_messen", render)
    f.antworten.append([zeile()])
    with pytest.raises(HTTPException) as e:
        dienst.erzeugen(IID, 2)
    assert e.value.status_code == 503 and e.value.detail == dienst.NICHT_GERENDERT


def test_nur_text_ohne_dateien(umg):
    """Reine Textseite: gerendert wird nur zum Messen (Abstaende), es entstehen keine Stuecke und keine Dateien."""
    f, p, erzeugt, aufrufe = umg
    s = seite([text(y=100), text("u", y=400, t="Zweiter Absatz")])
    assert seite_mail.stuecke(s) == []
    f.antworten += [[zeile(s)], [{"mail": {"medien": [], "bytes": 0}}]]
    dienst.erzeugen(IID, 2)
    assert len(aufrufe) == 1 and os.listdir(erzeugt) == [] and p.sql == []
    assert "'[]'::jsonb, 0)" in f.sql[-1]


def test_seite_ohne_ebenen_ohne_chromium(umg):
    f, p, erzeugt, aufrufe = umg
    f.antworten += [[zeile(seite([]))], [{"mail": {"medien": [], "bytes": 0}}]]
    dienst.erzeugen(IID, 2)
    assert aufrufe == [] and os.listdir(erzeugt) == [] and p.sql == []


def test_nur_hintergrundbild_wird_gerendert(umg):
    f, _, erzeugt, aufrufe = umg
    s = dict(seite([]), hintergrund={"farbe": "#fafafa", "bild": "medien:probe.png"})
    f.antworten.append([zeile(s)])
    mail = dienst.erzeugen(IID, 2)
    assert len(aufrufe) == 1 and [m["name"] for m in mail["medien"]] == os.listdir(erzeugt) != []


def test_erst_rendern_und_messen_dann_planen(umg, monkeypatch):
    """Der gemessene Titel ist hoeher als deklariert: das Bildstueck reicht bis zu seiner gemessenen Unterkante,
    und das gespeicherte MJML ist mit denselben Massen gebaut (Spacer/Abstand ab dort)."""
    f, _, erzeugt, _ = umg
    titel = {"id": "t", "art": "text", "x": 330, "y": 340, "breite": 460, "hoehe": 110,
             "text": "Neue Kurse im Herbst", "rolle": "ANZEIGE", "groesse": 46, "gewicht": 600, "drehung": -4,
             "farbe": "#ffffff"}
    s = seite([{"id": "b", "art": "bild", "x": 300, "y": 200, "breite": 600, "hoehe": 400,
                "quelle": "medien:probe.png"}, titel])
    reihenfolge, geschnitten = [], []
    gemessen = {"b": (0, 0, 600, 400), "t": (96, 269, 564, 431.5)}

    def render(s_, medien_lesen, **_):
        reihenfolge.append("rendern")
        return _png(1200, 1600), gemessen
    echt_stuecke, echt_zuschnitt = seite_mail.stuecke, seite_bilder.zuschneiden

    def stuecke(s_, masse=None):
        reihenfolge.append(("planen", masse is not None))
        return echt_stuecke(s_, masse)

    def zuschneiden(png, st, **k):
        geschnitten.extend(st)
        return echt_zuschnitt(png, st, **k)
    monkeypatch.setattr(seite_bilder, "render_und_messen", render)
    monkeypatch.setattr(seite_mail, "stuecke", stuecke)
    monkeypatch.setattr(seite_bilder, "zuschneiden", zuschneiden)
    f.antworten.append([zeile(s)])
    dienst.erzeugen(IID, 2)
    assert reihenfolge == [("planen", False), "rendern", ("planen", True)]
    assert [(g.oben, g.unten) for g in geschnitten] == [(0, 431.5)]       # nicht 410.9 (deklariert)
    assert '<mj-spacer height="368.5px" />' in f.sql[-1]                   # 800 - 431.5 im Versand-MJML
    assert len(os.listdir(erzeugt)) == 1


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


def _gespeicherte_mail(s=SEITE):
    """Mail-Fassung wie seite_mail_dienst sie speichert (MJML mit Bild- und Fuss-Marke)."""
    namen = [f"ms-11111111-2-{k:02d}.jpg" for k in range(1, len(seite_mail.stuecke(s)) + 1)]
    gemeinsam = dict(betreff="Herbst", vorschautext="Neu", pflichtteil=PFLICHT, fuss_ersatz=dienst.FUSS_MARKE)
    return {"inhalt": IID, "fassung": 2, "text": "alt", "bytes": 1,
            "mjml_versand": seite_mail.mjml(s, namen, bild_adresse=lambda n: "cid:" + n, fuer_versand=True,
                                            **gemeinsam),
            "html_vorschau_mjml": seite_mail.mjml(s, namen, bild_adresse=lambda n: dienst.BILD_MARKE + n,
                                                  fuer_versand=False, **gemeinsam),
            "medien": seite_mail.versand_medien(namen)}


def test_vorschau_html_setzt_bild_basis_je_anfrage():
    mail = _gespeicherte_mail()
    z = zeile(mail=mail)
    html = dienst.vorschau_html(z, mail, BASIS)
    assert BASIS + "ms-11111111-2-01.jpg" in html and dienst.BILD_MARKE not in html
    assert "[Vorname]" in html and "{vorname}" not in html and "[Abmeldelink]" in html
    assert dienst.FUSS_MARKE not in html and "Muster GmbH, Berlin" in html
    anderer = BASIS.replace("1760000000", "1760009999")
    assert anderer + "ms-11111111-2-01.jpg" in dienst.vorschau_html(z, mail, anderer)
    # ohne Bildbasis: nur der Dateiname, nie die Marke
    leer = dienst.vorschau_html(z, mail, "")
    assert 'src="ms-11111111-2-01.jpg"' in leer and dienst.BILD_MARKE not in leer


def test_fuss_immer_aktuell(umg):
    """Der Pflichtteil der Firma wird je Anfrage eingesetzt: aendert er sich nach dem Erzeugen, aendern sich
    Versand-HTML, Textfassung und Vorschau mit."""
    f, _, erzeugt, aufrufe = umg
    mail = _gespeicherte_mail()
    for m in mail["medien"]:
        (erzeugt / m["name"]).write_bytes(b"x")
    alt = {"impressum": "Alte GmbH, Bonn", "abmelde_hinweis": "Abmelden: {abmeldelink}"}
    neu = {"impressum": "Neue GmbH, Köln", "abmelde_hinweis": "Hier abmelden: {abmeldelink}"}
    f.antworten += [[dict(zeile(mail=mail), pflichtteil=alt)], [dict(zeile(mail=mail), pflichtteil=neu)]]
    v1, v2 = dienst.versand(IID, 2), dienst.versand(IID, 2)
    assert "Alte GmbH, Bonn" in v1["html"] and "Alte GmbH, Bonn" in v1["text"]
    assert "Neue GmbH, Köln" in v2["html"] and "Alte GmbH" not in v2["html"]
    assert "Neue GmbH, Köln" in v2["text"] and "Hier abmelden: {abmeldelink}" in v2["html"]
    assert "cid:ms-11111111-2-01.jpg" in v2["html"] and "{vorname}" in v2["html"]
    assert v2["medien"] == mail["medien"] and dienst.FUSS_MARKE not in v2["html"]
    f.antworten.append([dict(zeile(mail=mail), pflichtteil=neu)])
    html = dienst.vorschau(IID, 2, BASIS)
    assert "Neue GmbH, Köln" in html and "Hier abmelden: [Abmeldelink]" in html
    assert aufrufe == []                       # nichts neu gerendert


def test_inhaltsfehler_gemerkt_ohne_neues_rendern(umg, monkeypatch):
    f, _, _, _ = umg
    renders = []

    def render(s, medien_lesen, **_):
        renders.append(1)
        raise seite_bilder.StueckFehler("Bild weg.png fehlt")
    monkeypatch.setattr(seite_bilder, "render_und_messen", render)
    for _ in range(2):
        f.antworten.append([zeile()])
        with pytest.raises(HTTPException) as e:
            dienst.erzeugen(IID, 2)
        assert e.value.status_code == 422 and e.value.detail == "Bild weg.png fehlt"
    assert renders == [1]                      # der zweite Versuch kommt aus dem Gedaechtnis
    # eine andere Fassung ist nicht betroffen; nach Ablauf wird neu versucht
    monkeypatch.setattr(dienst, "FEHLER_MERKEN_S", 0)
    f.antworten.append([zeile()])
    with pytest.raises(HTTPException):
        dienst.erzeugen(IID, 2)
    assert renders == [1, 1]


def test_anrede_gemerkt_und_zeitlimit_nicht(umg, monkeypatch):
    f, _, _, _ = umg
    f.antworten += [[zeile(seite([bild(), text(y=200)]))], [zeile(seite([bild(), text(y=200)]))]]
    for _ in range(2):
        with pytest.raises(HTTPException) as e:
            dienst.erzeugen(IID, 2)
        assert e.value.detail == ANREDE
    assert dienst._fehler_gemerkt(IID, 2) == ANREDE
    renders = []

    def render(s, medien_lesen, **_):
        renders.append(1)
        raise seite_bilder.StueckFehler(seite_bilder.ZEITLIMIT)
    monkeypatch.setattr(seite_bilder, "render_und_messen", render)
    for _ in range(2):
        f.antworten.append([zeile()])
        with pytest.raises(HTTPException) as e:
            dienst.erzeugen(IID, 3)
        assert e.value.status_code == 422 and e.value.detail == seite_bilder.ZEITLIMIT
    assert renders == [1, 1]                   # Zeitlimit wird nicht gemerkt


def test_aufraeumen_erst_db_dann_dateien(umg):
    f, _, erzeugt, _ = umg
    for n in ("ms-11111111-1-01.jpg", "ms-11111111-3-01.jpg", "gs-0123456789ab.jpg"):
        (erzeugt / n).write_bytes(b"x")
    (erzeugt.parent / "boese.jpg").write_bytes(b"x")
    f.fehler.append(RuntimeError("ssh weg"))
    with pytest.raises(HTTPException):
        dienst.aufraeumen()
    assert len(os.listdir(erzeugt)) == 3       # DB nicht erreicht: keine Datei angefasst
    f.antworten.append([{"namen": ["ms-11111111-1-01.jpg", "../boese.jpg", "gs-0123456789ab.jpg"]}])
    assert dienst.aufraeumen() == ["ms-11111111-1-01.jpg"]
    assert "SELECT marketing.pult_seite_mail_aufraeumen() AS namen" in f.sql[-1]
    assert sorted(os.listdir(erzeugt)) == ["gs-0123456789ab.jpg", "ms-11111111-3-01.jpg"]
    assert (erzeugt.parent / "boese.jpg").exists()


def test_aufraeumen_haengt_am_stuendlichen_gs_aufraeumen(umg, monkeypatch):
    from spaces.marketing.api import gestaltung as ga
    f, _, erzeugt, _ = umg
    monkeypatch.setattr(ga, "_zuletzt", 0.0)
    (erzeugt / "ms-11111111-1-01.jpg").write_bytes(b"x")
    f.antworten += [[], [{"namen": ["ms-11111111-1-01.jpg"]}]]   # verwiesene gs, dann Mail-Aufraeumen
    ga.aufraeumen_falls_faellig(jetzt=1_000_000_000.0)
    assert "regexp_matches" in f.sql[0] and "pult_seite_mail_aufraeumen" in f.sql[1]
    assert os.listdir(erzeugt) == []
    ga.aufraeumen_falls_faellig(jetzt=1_000_000_000.0 + 60)   # zu frueh: nichts
    assert len(f.sql) == 2


def test_ms_wird_nicht_aufgeraeumt(tmp_path):
    alt = time.time() - gestaltung.AUFRAEUM_ALTER_S - 3600
    for n in ("ms-11111111-2-01.jpg", "ms-11111111-2-01-2.jpg", "gs-0123456789ab.jpg"):
        (tmp_path / n).write_bytes(b"x")
        os.utime(tmp_path / n, (alt, alt))
    assert gestaltung.aufraeumen(str(tmp_path), set()) == ["gs-0123456789ab.jpg"]
    assert sorted(os.listdir(tmp_path)) == ["ms-11111111-2-01-2.jpg", "ms-11111111-2-01.jpg"]


LINK = "Ein Link steht in einem Bildbereich – er muss in frei stehendem Fließtext stehen"


def test_link_im_bild_blockiert(umg):
    f, p, erzeugt, aufrufe = umg
    f.antworten.append([zeile(seite([bild(), text(y=200, t="Siehe [hier](https://example.com/x)")]))])
    with pytest.raises(HTTPException) as e:
        dienst.erzeugen(IID, 2)
    assert e.value.status_code == 422 and e.value.detail == LINK
    assert aufrufe == [] and os.listdir(erzeugt) == [] and p.sql == []
    # im frei stehenden Fliesstext ist der Link erlaubt
    f.antworten += [[zeile(seite([bild(), text(y=600, t="Siehe [hier](https://example.com/x)")]))]]
    assert dienst.erzeugen(IID, 3)["medien"]


def test_fehlende_stuecke_werden_neu_erzeugt(umg):
    """Eine gespeicherte Mail-Fassung, deren ms-Dateien in der Ablage fehlen, wird verworfen und neu erzeugt."""
    f, _, erzeugt, aufrufe = umg
    alt = _gespeicherte_mail()
    (erzeugt / "ms-11111111-2-02.jpg").write_bytes(b"rest")          # ein uebrig gebliebenes Stueck
    alt["medien"] = alt["medien"] + [{"name": "ms-11111111-2-02.jpg", "cid": "x", "sw": False}]
    f.antworten += [[zeile(mail=alt)], [{"namen": ["ms-11111111-2-01.jpg", "ms-11111111-2-02.jpg", "../x.jpg"]}]]
    mail = dienst.erzeugen(IID, 2)
    assert "marketing.pult_seite_mail_verwerfen('11111111-1111-1111-1111-111111111111'::uuid, 2)" in f.sql[1]
    assert "pult_seite_mail_speichern(" in f.sql[2] and len(aufrufe) == 1
    assert [m["name"] for m in mail["medien"]] == ["ms-11111111-2-01.jpg"]
    assert sorted(os.listdir(erzeugt)) == ["ms-11111111-2-01.jpg"]    # Rest weg, neues Stueck da


def test_versand_prueft_stuecke(umg, monkeypatch):
    f, _, erzeugt, _ = umg
    mail = _gespeicherte_mail()
    monkeypatch.setattr(dienst, "_holen", lambda i, n: (zeile(mail=mail), mail))
    with pytest.raises(HTTPException) as e:
        dienst.versand(IID, 2)
    assert e.value.status_code == 422 and e.value.detail == "Bildstücke fehlen – bitte Mail-Vorschau neu erzeugen"
    for m in mail["medien"]:
        (erzeugt / m["name"]).write_bytes(b"x")
    assert dienst.versand(IID, 2)["medien"] == mail["medien"]


def test_sperre_wartezeit_503(umg, monkeypatch):
    f, _, _, aufrufe = umg
    monkeypatch.setattr(dienst, "ERZEUG_WARTEN_S", 0.01)
    f.antworten.append([zeile()])
    assert dienst._erzeug_sperre.acquire(timeout=1)
    try:
        with pytest.raises(HTTPException) as e:
            dienst.erzeugen(IID, 2)
    finally:
        dienst._erzeug_sperre.release()
    assert e.value.status_code == 503 and e.value.detail == dienst.BESCHAEFTIGT and aufrufe == []


def test_nach_dem_warten_vorhandene_nehmen(umg):
    """Wer auf die Sperre wartet, liest danach neu und nimmt die inzwischen erzeugte Mail-Fassung."""
    import threading
    f, _, erzeugt, aufrufe = umg
    mail = _gespeicherte_mail()
    for m in mail["medien"]:
        (erzeugt / m["name"]).write_bytes(b"x")
    f.antworten += [[zeile()], [zeile(mail=mail)]]
    assert dienst._erzeug_sperre.acquire(timeout=1)
    threading.Timer(0.2, dienst._erzeug_sperre.release).start()
    assert dienst.erzeugen(IID, 2) == mail
    assert aufrufe == [] and len(f.sql) == 2 and all("pult_seite_mail_lesen(" in q for q in f.sql)
    assert dienst._erzeug_sperre.acquire(blocking=False)
    dienst._erzeug_sperre.release()


def test_aufraeumen_verwaiste_ms_dateien(umg):
    f, _, erzeugt, _ = umg
    alt = time.time() - 2 * 86400
    for n in ("ms-11111111-1-01.jpg", "ms-22222222-1-01.jpg", "ms-33333333-1-01.jpg", "gs-0123456789ab.jpg"):
        (erzeugt / n).write_bytes(b"x")
        os.utime(erzeugt / n, (alt, alt))
    (erzeugt / "ms-44444444-1-01.jpg").write_bytes(b"frisch")        # juenger als ein Tag: bleibt
    f.antworten += [[{"namen": []}], [{"namen": ["ms-22222222-1-01.jpg"]}]]
    weg = dienst.aufraeumen()
    assert "pult_seite_mail_verwiesen(" in f.sql[1]
    for n in ("ms-11111111-1-01.jpg", "ms-22222222-1-01.jpg", "ms-33333333-1-01.jpg"):
        assert n in f.sql[1]
    assert "ms-44444444-1-01.jpg" not in f.sql[1] and "gs-" not in f.sql[1]
    assert sorted(weg) == ["ms-11111111-1-01.jpg", "ms-33333333-1-01.jpg"]
    assert sorted(os.listdir(erzeugt)) == ["gs-0123456789ab.jpg", "ms-22222222-1-01.jpg", "ms-44444444-1-01.jpg"]


def test_aufraeumen_waisen_db_fehler_loescht_nichts(umg):
    f, _, erzeugt, _ = umg
    alt = time.time() - 2 * 86400
    (erzeugt / "ms-11111111-1-01.jpg").write_bytes(b"x")
    os.utime(erzeugt / "ms-11111111-1-01.jpg", (alt, alt))
    f.antworten.append([{"namen": []}])
    f.fehler += [None, RuntimeError("ssh weg")]
    with pytest.raises(HTTPException):
        dienst.aufraeumen()
    assert os.listdir(erzeugt) == ["ms-11111111-1-01.jpg"]

import re

import pytest

from spaces.marketing.claw import bloecke_mjml
from spaces.marketing.claw import seite_mail as sm

PFLICHT = {"impressum": "Muster GmbH, Hauptstr. 1, Berlin",
           "abmelde_hinweis": "Abmelden: {abmeldelink}"}


def seite(ebenen: list[dict], hoehe: int = 800, bild: str | None = None, farbe: str = "#fafafa") -> dict:
    return {"version": 1, "breite": 600, "hoehe": hoehe,
            "hintergrund": {"farbe": farbe, "bild": bild},
            "schriften": {"anzeige": "playfair", "text": "dm-sans"}, "ebenen": ebenen}


def text(eid, y, t="Hallo", hoehe=100, x=300, breite=480, **mehr):
    return {"id": eid, "art": "text", "x": x, "y": y, "breite": breite, "hoehe": hoehe,
            "text": t, "rolle": "TEXT", "groesse": 17, "farbe": "#333333", **mehr}


def knopf(eid, y, t="Mehr", link="https://example.com/a", **mehr):
    return {"id": eid, "art": "knopf", "x": 300, "y": y, "breite": 220, "hoehe": 48,
            "text": t, "link": link, "farbe": "#5b3fd6", "textfarbe": "#ffffff", **mehr}


def bild(eid, y, hoehe=300, **mehr):
    return {"id": eid, "art": "bild", "x": 300, "y": y, "breite": 400, "hoehe": hoehe,
            "quelle": "medien:foto.jpg", **mehr}


def adresse(name: str) -> str:
    return "https://bilder.example/" + name


def bau(s, namen=None, fuer_versand=False):
    if namen is None:
        namen = [f"ms-abcdef12-1-{i + 1:02d}.jpg" for i in range(len(sm.stuecke(s)))]
    return sm.mjml(s, namen, betreff="Betreff", vorschautext="Vorschau", pflichtteil=PFLICHT,
                   bild_adresse=adresse, fuer_versand=fuer_versand)


def test_nur_text_ohne_stuecke():
    s = seite([text("a", 100), knopf("k", 300)])
    assert sm.stuecke(s) == []
    m = bau(s, [])
    assert "<mj-image" not in m
    assert "<mj-text" in m and "<mj-button" in m
    assert sm.versand_medien([]) == []


def test_textband_mj_text_mit_arial_stapel_ohne_webschrift():
    s = seite([text("a", 100, groesse=20, gewicht=700, zeilenabstand=1.5, ausrichtung="mitte",
                    grossbuchstaben=True, kursiv=True, farbe="#123456")])
    m = bau(s)
    assert "<mj-font" not in m
    assert "DM Sans" not in m and "Playfair" not in m
    t = re.search(r"<mj-text[^>]*>", m).group(0)
    assert 'font-family="Arial, Helvetica, sans-serif"' in t
    assert 'font-size="20px"' in t and 'font-weight="700"' in t and 'line-height="1.5"' in t
    assert 'color="#123456"' in t and 'align="center"' in t
    assert 'text-transform="uppercase"' in t and 'font-style="italic"' in t
    # Rahmen: links 60, rechts 540, oben 50 (Band beginnt bei 0)
    assert 'padding-left="60px"' in t and 'padding-right="60px"' in t and 'padding-top="50px"' in t
    assert 'height="100px"' in t   # deklarierte Kastenhoehe bleibt reserviert
    assert '<mj-section background-color="#fafafa"' in m


def test_abstaende_im_textband_folgen_den_y_abstaenden():
    s = seite([text("a", 100), text("b", 300, x=250, breite=200)])
    teile = re.findall(r"<mj-text[^>]*>", bau(s))
    assert 'padding-top="50px"' in teile[0]
    assert 'padding-top="100px"' in teile[1]     # 250 - 150
    assert 'padding-left="150px"' in teile[1] and 'padding-right="250px"' in teile[1]
    assert 'padding-bottom="450px"' in teile[1]   # 800 - 350


def test_vorname_bleibt_im_versand_sonst_platzhalter():
    s = seite([text("a", 100, t="Hallo {vorname}!")])
    assert "Hallo {vorname}!" in bau(s, fuer_versand=True)
    assert "{abmeldelink}" in bau(s, fuer_versand=True)
    vorschau = bau(s)
    assert "Hallo [Vorname]!" in vorschau and "{vorname}" not in vorschau
    assert "[Abmeldelink]" in vorschau and "{abmeldelink}" not in vorschau


def test_fett_kursiv_und_https_link_werden_auszeichnung():
    # Andere Ziele als https lehnt seite.pruefen schon ab; hier zaehlt, dass der https-Link ein echter Link wird.
    s = seite([text("a", 100, t="**fett**, *schief* und [hier](https://example.com/x)")])
    m = bau(s)
    assert "<strong>fett</strong>" in m and "<em>schief</em>" in m
    assert re.search(r'<a href="https://example.com/x"[^>]*>hier</a>', m)
    assert "[hier]" not in m and "**" not in m


def test_link_ohne_https_wird_abgelehnt():
    from spaces.marketing.claw.seite import SeiteFehler
    with pytest.raises(SeiteFehler):
        bau(seite([text("a", 100, t="[hier](http://example.com/x)")]))


def test_knopf_im_textband_wird_mj_button():
    s = seite([knopf("k", 100, t="Jetzt <lesen>", link="https://example.com/k")])
    b = re.search(r"<mj-button[^>]*>([^<]*)</mj-button>", bau(s))
    assert 'href="https://example.com/k"' in b.group(0)
    assert 'background-color="#5b3fd6"' in b.group(0) and 'color="#ffffff"' in b.group(0)
    assert b.group(1) == "Jetzt &lt;lesen&gt;"


def test_bildband_mj_image_mit_alt_und_href():
    s = seite([bild("b", 300, hoehe=400),
               text("t", 200, t="Über **dem** Bild", deckkraft=0.5),
               knopf("k", 400, t="Kaufen", link="https://example.com/k", drehung=5),
               knopf("k2", 450, t="Zweiter", link="https://example.com/z", drehung=5)])
    st = sm.stuecke(s)
    assert len(st) == 1
    assert st[0].nr == 1 and st[0].link == "https://example.com/k"
    assert st[0].alt == "Über dem Bild Kaufen Zweiter"
    m = bau(s, ["ms-abcdef12-1-01.jpg"])
    img = re.search(r"<mj-image[^>]*>", m).group(0)
    assert 'src="https://bilder.example/ms-abcdef12-1-01.jpg"' in img
    assert 'width="600px"' in img and 'padding="0"' in img
    assert 'alt="Über dem Bild Kaufen Zweiter"' in img
    assert 'href="https://example.com/k"' in img


def test_bild_ohne_text_alt_bild_und_ohne_link():
    s = seite([bild("b", 300)])
    st = sm.stuecke(s)
    assert st[0].alt == "Bild" and st[0].link is None
    assert "href=" not in re.search(r"<mj-image[^>]*>", bau(s)).group(0)


def test_alt_hoechstens_300_zeichen():
    s = seite([bild("b", 300, hoehe=400), text("t", 300, t="x" * 500, deckkraft=0.5)])
    assert len(sm.stuecke(s)[0].alt) <= 300


def test_namen_muessen_zu_stuecken_passen():
    s = seite([bild("b", 300)])
    with pytest.raises(bloecke_mjml.RenderFehler):
        bau(s, [])


def test_leeres_textband_wird_spacer():
    s = seite([bild("b", 600, hoehe=200)])   # Band 0..500 leer
    assert '<mj-spacer height="500px"' in bau(s)


def test_fuss_mit_impressum_und_abmelden():
    m = bau(seite([text("a", 100)]))
    assert "Muster GmbH, Hauptstr. 1, Berlin" in m and "Abmelden: [Abmeldelink]" in m
    ohne = sm.mjml(seite([text("a", 100)]), [], betreff="B", vorschautext="V", pflichtteil={},
                   bild_adresse=adresse, fuer_versand=False)
    assert "Impressum fehlt" in ohne


def test_text_fassung_reihenfolge():
    s = seite([knopf("k", 500, t="Los", link="https://example.com/los"),
               text("b", 300, t="Zweitens [mehr](https://example.com/m)"),
               text("a", 100, t="Erstens **fett**")])
    t = sm.text_fassung(s, PFLICHT)
    assert t == ("Erstens fett\n\nZweitens mehr (https://example.com/m)\n\nLos: https://example.com/los\n\n"
                 "--\n\nMuster GmbH, Hauptstr. 1, Berlin\n\nAbmelden: {abmeldelink}\n")


def test_versand_medien():
    assert sm.versand_medien(["ms-1.jpg", "ms 2.jpg"]) == [
        {"name": "ms-1.jpg", "cid": "ms-1.jpg", "sw": False},
        {"name": "ms 2.jpg", "cid": "ms%202.jpg", "sw": False}]


def test_anrede_in_bild():
    s = seite([bild("b", 300), text("t", 300, t="Hi {vorname}", deckkraft=0.5),
               text("frei", 700, t="Hallo {vorname}", hoehe=60)])
    assert sm.anrede_in_bild(s) == ["t"]


def test_anrede_im_gedrehten_knopf_im_bild():
    s = seite([knopf("k", 300, t="Los, {vorname}", drehung=8),
               knopf("frei", 600, t="Hallo {vorname}")])
    assert sm.anrede_in_bild(s) == ["k"]


def test_gerendert_mit_mso_ohne_script():
    s = seite([text("a", 100, t="Hallo"), bild("b", 400), knopf("k", 700)])
    namen = [f"ms-abcdef12-1-{i + 1:02d}.jpg" for i in range(len(sm.stuecke(s)))]
    h = sm.rendern(s, namen, betreff="B", vorschautext="V", pflichtteil=PFLICHT,
                   bild_adresse=adresse, fuer_versand=False)
    assert "mso" in h
    assert "<script" not in h.lower()
    assert "Hallo" in h

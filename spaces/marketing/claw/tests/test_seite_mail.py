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
    # Rahmen: links 60, rechts 540 (seitlich in Prozent von 600, damit das Band am Handy schrumpft), oben 50
    assert 'padding-left="10%"' in t and 'padding-right="10%"' in t and 'padding-top="50px"' in t
    # keine feste Hoehe (Text liefe sonst ueber das naechste Element), die deklarierte Hoehe ist Mindesthoehe
    assert " height=" not in t
    assert '<div style="min-height:100px">' in m
    assert '<mj-section background-color="#fafafa"' in m


def test_abstaende_im_textband_folgen_den_y_abstaenden():
    s = seite([text("a", 100), text("b", 300, x=250, breite=200)])
    teile = re.findall(r"<mj-text[^>]*>", bau(s))
    assert 'padding-top="50px"' in teile[0]
    assert 'padding-top="100px"' in teile[1]     # 250 - 150
    assert 'padding-left="25%"' in teile[1] and 'padding-right="41.6667%"' in teile[1]
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
    # mittig (x = 300): ausgerichtet statt fester Polster, Breite in Prozent (220 von 600)
    assert 'align="center"' in b.group(0) and 'width="36.6667%"' in b.group(0)
    assert 'padding-left="0px"' in b.group(0) and 'padding-right="0px"' in b.group(0)


def test_knopf_links_und_rechts_schrumpfen_mit():
    links = dict(knopf("k", 100), x=170)          # Rahmen 60..280
    rechts = dict(knopf("k", 100), x=480)         # Rahmen 370..590
    b = re.search(r"<mj-button[^>]*>", bau(seite([links]))).group(0)
    assert 'align="left"' in b and 'padding-left="10%"' in b and 'padding-right="0px"' in b
    assert 'width="40.7407%"' in b                # 220 von 540 (Rest nach dem linken Polster)
    b = re.search(r"<mj-button[^>]*>", bau(seite([rechts]))).group(0)
    assert 'align="right"' in b and 'padding-right="1.6667%"' in b and 'padding-left="0px"' in b
    assert 'width="37.2881%"' in b                # 220 von 590
    assert not re.search(r'width="\d+(\.\d+)?px"', b)


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


def test_bild_ohne_text_alt_leer_und_ohne_link():
    # schmueckendes Stueck ohne Text: alt="" (Vorleseprogramme ueberspringen es)
    s = seite([bild("b", 300)])
    st = sm.stuecke(s)
    assert st[0].alt == "" and st[0].link is None
    img = re.search(r"<mj-image[^>]*>", bau(s)).group(0)
    assert "href=" not in img and 'alt=""' in img
    assert 'alt=""' in sm.rendern(s, ["ms-abcdef12-1-01.jpg"], betreff="B", vorschautext="V", pflichtteil=PFLICHT,
                                  bild_adresse=adresse, fuer_versand=False)


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


def test_links_in_bild():
    s = seite([bild("b", 300), text("t", 300, t="Siehe [hier](https://example.com/x)", deckkraft=0.5),
               text("frei", 700, t="Und [dort](https://example.com/y)", hoehe=60),
               text("ohne", 300, x=300, breite=100, hoehe=40, t="ohne Link", deckkraft=0.5)])
    assert sm.links_in_bild(s) == ["t"]
    assert sm.links_in_bild(seite([text("a", 100, t="[a](https://example.com)")])) == []


def test_textlink_farbe_ausdruecklich():
    s = seite([text("a", 100, t="[hier](https://example.com/x)", farbe="#123456")])
    a = re.search(r'<a href="https://example.com/x"[^>]*>', bau(s)).group(0)
    assert "color:inherit;color:#123456" in a and "text-decoration:underline" in a


def test_farbschema_meta():
    hell = bau(seite([text("a", 100)], farbe="#fafafa"))
    assert '<meta name="color-scheme" content="light">' in hell
    dunkel = bau(seite([text("a", 100, farbe="#ffffff")], farbe="#111111"))
    assert '<meta name="color-scheme" content="dark">' in dunkel
    assert '<meta name="supported-color-schemes" content="dark">' in dunkel


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


# ---- Gemessene Huellen ------------------------------------------------------------------------------------
def test_stuecke_und_mjml_nach_masse():
    titel = {"id": "t", "art": "text", "x": 330, "y": 340, "breite": 460, "hoehe": 110,
             "text": "Neue Kurse im Herbst", "rolle": "ANZEIGE", "groesse": 46, "gewicht": 600, "drehung": -4,
             "farbe": "#ffffff"}
    s = seite([{"id": "b", "art": "bild", "x": 300, "y": 200, "breite": 600, "hoehe": 400,
                "quelle": "medien:foto.jpg"}, titel, text("u", 600)])
    masse = {"t": (96.4, 269.1, 563.6, 431.5)}
    assert [(x.oben, x.unten) for x in sm.stuecke(s, masse)] == [(0, 431.5)]
    m = sm.mjml(s, ["a.jpg"], betreff="B", vorschautext="V", pflichtteil={}, bild_adresse=adresse,
                fuer_versand=False, masse=masse)
    # Abstand des Fliesstexts ab der gemessenen Unterkante des Titels (550 - 431.5)
    assert 'padding-top="118.5px"' in re.search(r"<mj-text[^>]*>", m).group(0)
    with pytest.raises(bloecke_mjml.RenderFehler):          # Namen muessen zu den gemessenen Stuecken passen
        sm.mjml(s, [], betreff="B", vorschautext="V", pflichtteil={}, bild_adresse=adresse,
                fuer_versand=False, masse=masse)


def test_textband_abstand_und_mindesthoehe_gemessen():
    s = seite([text("a", 100), text("b", 400)])
    m = sm.mjml(s, [], betreff="B", vorschautext="V", pflichtteil={}, bild_adresse=adresse,
                fuer_versand=False, masse={"a": (60, 50, 540, 180)})
    teile = re.findall(r"<mj-text[^>]*>", m)
    assert 'padding-top="50px"' in teile[0] and 'padding-top="170px"' in teile[1]    # 350 - 180, nicht 200
    assert '<div style="min-height:130px">' in m and '<div style="min-height:100px">' in m


def test_anrede_erst_gemessen_im_bild():
    s = seite([bild("b", 200), text("a", 420, t="Hallo {vorname}")])
    assert sm.anrede_in_bild(s) == []
    assert sm.anrede_in_bild(s, {"a": (60, 340, 540, 470)}) == ["a"]


def test_text_fassung_gemessene_lesereihenfolge():
    # deklariert steht a (oben 50) vor b (oben 60); gemessen beginnt a erst bei 70 - wie bei den Alt-Texten
    s = seite([text("a", 100, t="Erst", x=150, breite=200), text("b", 110, t="Zweit", x=450, breite=200)])
    assert sm.text_fassung(s, {}).index("Erst") < sm.text_fassung(s, {}).index("Zweit")
    t = sm.text_fassung(s, {}, {"a": (50, 50, 250, 150), "b": (350, 40, 550, 160)})
    assert t.index("Zweit") < t.index("Erst")


# --- Vorschautext (Newsletter-Runde 28.10.2026) -------------------------------------------------------------

def _vorschau(m: str) -> str:
    return re.search(r"<mj-preview>(.*?)</mj-preview>", m, re.S).group(1)


def test_leerer_vorschautext_kommt_aus_dem_ersten_fliesstext():
    s = seite([text("t", 400, "Hallo {vorname},\nunser Herbst ist da. Alles Neue im Laden.", hoehe=120),
               text("titel", 100, "HERBST", rolle="ANZEIGE", groesse=60)])
    namen = [f"ms-abcdef12-1-{i + 1:02d}.jpg" for i in range(len(sm.stuecke(s)))]
    m = sm.mjml(s, namen, betreff="B", vorschautext="  ", pflichtteil={}, bild_adresse=adresse, fuer_versand=True)
    assert _vorschau(m) == "unser Herbst ist da."


def test_vorschautext_ohne_anrede_links_und_gekuerzt():
    lang = "Schau dir [unser Angebot](https://example.com/a) an " + "und noch viel mehr " * 20
    s = seite([text("t", 100, lang, hoehe=200)])
    v = sm.vorschau_aus_seite(s)
    assert "{vorname}" not in v and "https://" not in v and v.startswith("Schau dir unser Angebot an")
    assert len(v) <= sm.VORSCHAU_MAX and v.endswith("…")


def test_gesetzter_vorschautext_bleibt():
    s = seite([text("t", 100, "Erster Satz.")])
    m = sm.mjml(s, [], betreff="B", vorschautext="Eigener Text", pflichtteil={}, bild_adresse=adresse,
                fuer_versand=True)
    assert _vorschau(m) == "Eigener Text"

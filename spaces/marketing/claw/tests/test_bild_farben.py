from spaces.marketing.claw import bild_farben as bf
from spaces.marketing.claw import bildplaetze


def wurzel(kinder, **farben):
    return {"type": "EmailLayout", "data": {"childrenIds": kinder, **farben}}


def bild(**style):
    return {"type": "Image", "data": {"style": style, "props": {"url": "", "width": 600, "height": 300}}}


def knopf(farbe):
    return {"type": "Button", "data": {"props": {"text": "Los", "buttonBackgroundColor": farbe}}}


def test_farbname_nach_farbton_und_helligkeit():
    assert bf.farbname("#0f2422") == "near-black teal"
    assert bf.farbname("#5eead4") == "bright teal"
    assert bf.farbname("#ffffff") == "white"
    assert bf.farbname("#f2f5f7") == "off-white"
    assert bf.farbname("#242424") == "charcoal"
    assert bf.farbname("#c0392b") == "dark red"
    assert bf.farbname("#f4a261") == "bright orange"
    assert bf.farbname("#1e3a8a") == "dark blue"


def test_farbname_unlesbar_gibt_leer():
    assert bf.farbname("") == "" and bf.farbname("rot") == "" and bf.farbname(None) == ""
    assert bf.farbname("#abc") == bf.farbname("#aabbcc")


def test_palette_aus_wurzel_und_knopf():
    d = {"root": wurzel(["k"], backdropColor="#1d3b39", canvasColor="#0f2422", textColor="#cfe3df"),
         "k": knopf("#5eead4")}
    assert bf.palette(d) == {"innen": "#0f2422", "aussen": "#1d3b39", "text": "#cfe3df", "akzent": "#5eead4"}


def test_palette_ohne_angaben_wie_der_renderer():
    p = bf.palette({"root": wurzel([])})
    assert p == {"innen": "#ffffff", "aussen": "#f2f5f7", "text": "#242424", "akzent": ""}
    assert bf.palette(None)["innen"] == "#ffffff"


def test_flaeche_des_bildes_bild_container_oder_inhalt():
    d = {"root": wurzel(["a", "karte", "b"], canvasColor="#101010"),
         "a": bild(),
         "b": bild(backgroundColor="#334455"),
         "karte": {"type": "Container", "data": {"style": {"backgroundColor": "#fafafa"},
                                                  "props": {"childrenIds": ["c"]}}},
         "c": bild()}
    flaechen = {p.id: p.flaeche for p in bildplaetze.finde(d)}
    assert flaechen == {"a": "#101010", "b": "#334455", "c": "#fafafa"}


def test_satz_nennt_palette_und_weichen_rand():
    pal = {"innen": "#0f2422", "aussen": "#1d3b39", "text": "#cfe3df", "akzent": "#5eead4"}
    s = bf.satz(pal, "#0f2422", "")
    assert "near-black teal" in s and "bright teal" in s and "blend softly into near-black teal" in s
    assert "turquoise" not in s


def test_satz_helles_layout_ohne_teal():
    pal = {"innen": "#ffffff", "aussen": "#f2f5f7", "text": "#242424", "akzent": "#c0392b"}
    s = bf.satz(pal, "#ffffff", "")
    assert "white" in s and "dark red" in s and "teal" not in s


def test_farbwunsch_macht_palette_weich():
    pal = {"innen": "#0f2422", "aussen": "#1d3b39", "text": "#cfe3df", "akzent": "#5eead4"}
    fest = bf.satz(pal, "#0f2422", "mehr Menschen im Bild")
    weich = bf.satz(pal, "#0f2422", "waermeres Licht")
    assert fest.startswith("color palette matching the layout")
    assert weich.startswith("harmonizing with") and "near-black teal" in weich
    for wunsch in ("Tageslicht", "rot", "sunset", "bunter", "in Blau", "dunkler"):
        assert bf.farbwunsch(wunsch), wunsch
    for wunsch in ("mehr Menschen", "ohne Schrift", "Team im Buero", "brotlos"):
        assert not bf.farbwunsch(wunsch), wunsch


def test_satz_ohne_palette_neutral():
    s = bf.satz(None, "", "")
    assert s == "natural colors, soft light"

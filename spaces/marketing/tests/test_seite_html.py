import copy
import re

from spaces.marketing.claw import seite, seite_html

BILD = "https://h/marketing/bild/1.abc/"
PF = {"impressum": "Firma X", "abmelde_hinweis": "Abmelden: {abmeldelink}"}


def _html(s, **kw):
    args = dict(betreff="B", pflichtteil=PF, bild_basis=BILD, schrift_basis="")
    args.update(kw)
    return seite_html.html(s, **args)


def _seite(*ebenen, **schr):
    s = seite.leer(None)
    s["ebenen"] = list(ebenen)
    s["schriften"].update(schr)
    return s


def _text(txt, **kw):
    return {"id": "t", "art": "text", "x": 300, "y": 100, "breite": 400, "hoehe": 80, "text": txt,
            "rolle": "TEXT", "groesse": 16, "farbe": "#111111", **kw}


def test_ebenen_absolut_mit_mittelpunkt_und_drehung():
    f = {"id": "f", "art": "form", "x": 300, "y": 200, "breite": 100, "hoehe": 50, "drehung": 12,
         "form": "rechteck", "farbe": "#ff0000"}
    g = {**f, "id": "g", "drehung": 0, "farbe": "#00ff00"}
    h = _html(_seite(f, g))
    for t in ("left:250px", "top:175px", "width:100px", "height:50px", "transform:rotate(12deg)"):
        assert t in h
    assert h.index("#ff0000") < h.index("#00ff00")
    assert "z-index:0" in h and "z-index:1" in h


def test_seite_schneidet_ab_und_hat_hoehe():
    s = _seite()
    s["hoehe"] = 777
    h = _html(s)
    m = re.search(r'<div style="(position:relative[^"]*)"', h)
    assert m
    for t in ("width:600px", "height:777px", "overflow:hidden"):
        assert t in m.group(1)


def test_ueberstand_wird_abgeschnitten():
    f = {"id": "f", "art": "form", "x": 1100, "y": 200, "breite": 100, "hoehe": 50,
         "form": "rechteck", "farbe": "#ff0000"}
    h = _html(_seite(f))
    assert "left:1050px" in h
    assert h.index("overflow:hidden") < h.index("left:1050px") < h.index("Firma X")


def test_bilder_ueber_bild_basis():
    b = {"id": "b", "art": "bild", "x": 300, "y": 200, "breite": 100, "hoehe": 100, "quelle": "medien:a.jpg",
         "passung": "einpassen", "kreis": True}
    h = _html(_seite(b))
    assert f'src="{BILD}a.jpg"' in h
    assert "object-fit:contain" in h and "border-radius:50%" in h


def test_hintergrundbild():
    s = _seite()
    s["hintergrund"]["bild"] = "medien:bg.jpg"
    assert f"url('{BILD}bg.jpg')" in _html(s)


def test_fliesstext_im_mailstapel_anzeige_als_webschrift():
    a = {"id": "a", "art": "text", "x": 300, "y": 50, "breite": 400, "hoehe": 40, "text": "T",
         "rolle": "ANZEIGE", "groesse": 30, "farbe": "#111111"}
    h = _html(_seite(_text("x"), a, text="dm-sans", anzeige="montserrat"), schrift_basis="https://h/s/")
    assert "font-family:Arial, Helvetica, sans-serif" in h
    assert "DM Sans" not in h
    assert "font-family:'Montserrat', Arial, Helvetica, sans-serif" in h
    assert '<link rel="stylesheet" href="https://h/s/schriften.css">' in h
    assert "<link" not in _html(_seite(_text("x")))


def test_hand_ist_caveat():
    t = _text("Hallo", rolle="HAND")
    assert "'Caveat'" in _html(_seite(t))


def test_text_wird_escaped_und_links_nur_https():
    t = _text("<b>x</b> **fett** *kursiv* [hier](https://a.de) [böse](javascript:alert(1))\nZeile")
    h = _html(_seite(t))
    assert "&lt;b&gt;" in h and "<strong>fett</strong>" in h and "<em>kursiv</em>" in h
    assert '<a href="https://a.de"' in h
    assert "javascript:" not in h
    assert "<br>" in h


def test_unbekannte_markenschrift_faellt_zurueck():
    s = _seite(_text("x"))
    s["schriften"]["text"] = "weg"
    assert "font-family:Arial, Helvetica, sans-serif" in _html(s)


def test_knopf_ist_link():
    k = {"id": "k", "art": "knopf", "x": 300, "y": 200, "breite": 200, "hoehe": 48, "text": "Los",
         "link": "https://a.de/x?a=1&b=2", "farbe": "#123456", "textfarbe": "#fedcba"}
    h = _html(_seite(k))
    assert '<a href="https://a.de/x?a=1&amp;b=2"' in h
    assert "background:#123456" in h and "color:#fedcba" in h


def test_pflicht_fuss():
    h = _html(_seite(), pflichtteil={"impressum": "Firma <X>", "abmelde_hinweis": "Abmelden <b>"})
    assert "Firma &lt;X&gt;" in h and "Abmelden &lt;b&gt;" in h
    seite_div = re.search(r'<div style="position:relative.*?</div>', h, re.S).group(0)
    assert "Firma" not in seite_div
    assert h.index("Firma") > h.index("overflow:hidden")


def test_kein_script():
    t = _text("<script>alert(1)</script> **x**")
    h = _html(_seite(t))
    assert "<script" not in h
    assert not re.search(r"\son\w+=", h)


def test_eingabe_bleibt_unveraendert():
    s = _seite(_text("x"))
    s["schriften"]["text"] = "weg"
    kopie = copy.deepcopy(s)
    _html(s)
    assert s == kopie

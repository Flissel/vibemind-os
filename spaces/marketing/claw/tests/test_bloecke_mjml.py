"""Uebersetzer Bloecke -> MJML -> HTML (Spec 2026-09-29-newsletter-editor-design.md §3.4)."""
import re

from spaces.marketing.claw import bloecke_mjml as b

PFLICHT = {"impressum": "VibeMind, Musterstr. 1", "abmelde_hinweis": "Abmelden: {abmeldelink}"}
DOK = {
    "root": {"type": "EmailLayout", "data": {"backdropColor": "#0f2422", "canvasColor": "#1d3b39",
             "textColor": "#cfe3df", "fontFamily": "BOOK_SERIF", "childrenIds": ["h", "t", "i", "c", "k", "d", "s"]}},
    "h": {"type": "Heading", "data": {"style": {"textAlign": "center"}, "props": {"text": "Neuigkeiten", "level": "h1"}}},
    "t": {"type": "Text", "data": {"style": {}, "props": {"text": "Hallo **Welt** und *mehr* [Link](https://vibemind.space) <script>x</script>", "markdown": True}}},
    "i": {"type": "Image", "data": {"style": {}, "props": {"url": "medien:logo.png", "alt": "Logo", "width": 120}}},
    "c": {"type": "ColumnsContainer", "data": {"style": {}, "props": {"columnsCount": 2,
          "columns": [{"childrenIds": ["c1"]}, {"childrenIds": ["c2"]}, {"childrenIds": []}]}}},
    "c1": {"type": "Text", "data": {"props": {"text": "Links"}}},
    "c2": {"type": "Text", "data": {"props": {"text": "Rechts"}}},
    "k": {"type": "Button", "data": {"style": {}, "props": {"text": "Jetzt", "url": "https://vibemind.space",
          "buttonBackgroundColor": "#5eead4", "buttonTextColor": "#0f2422", "buttonStyle": "pill"}}},
    "d": {"type": "Divider", "data": {"props": {"lineColor": "#8aa3a0", "lineHeight": 1}}},
    "s": {"type": "Spacer", "data": {"props": {"height": 24}}},
}


def test_mjml_hat_alle_bausteine():
    m = b.nach_mjml(DOK, "Betreff", "Vorab", PFLICHT, bild_basis="https://x.ts.net/marketing/bild/t/")
    for teil in ("<mjml", "<mj-preview>Vorab</mj-preview>", "<mj-title>Betreff</mj-title>",
                 'background-color="#0f2422"', "<mj-column", "<mj-image", "<mj-button", "<mj-divider",
                 "<mj-spacer", "Palatino Linotype"):
        assert teil in m, teil


def test_html_rendert_und_haelt_outlook():
    html = b.rendern(DOK, "Betreff", "Vorab", PFLICHT, bild_basis="https://x.ts.net/marketing/bild/t/")
    assert html.lower().startswith("<!doctype html") and "mso" in html
    for teil in ("Neuigkeiten", "<strong>Welt</strong>", "<em>mehr</em>", 'href="https://vibemind.space"',
                 "Links", "Rechts", "Jetzt", "https://x.ts.net/marketing/bild/t/logo.png"):
        assert teil in html, teil


def test_skript_bleibt_text():
    html = b.rendern(DOK, "B", "", PFLICHT)
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_http_markdown_link_wird_nur_text():
    d = {**DOK, "t": {"type": "Text", "data": {"props": {"text": "[x](http://boese.de)", "markdown": True}}}}
    html = b.rendern(d, "B", "", PFLICHT)
    assert 'href="http://boese.de"' not in html
    assert "x" in html.split("<body")[1]


def test_ohne_markdown_keine_auszeichnung():
    d = {**DOK, "t": {"type": "Text", "data": {"props": {"text": "**nicht fett**", "markdown": False}}}}
    assert "<strong>" not in b.rendern(d, "B", "", PFLICHT)


def test_pflichtteil_immer_am_ende():
    html = b.rendern(DOK, "B", "", PFLICHT)
    assert html.rfind("Musterstr. 1") > html.rfind("Jetzt")
    assert "[Abmeldelink]" in html and "{abmeldelink}" not in html
    ohne = b.rendern(DOK, "B", "", dict(PFLICHT, impressum=""))
    assert "Impressum fehlt" in ohne


def test_handy_ist_schmaler():
    assert 'width="380px"' in b.nach_mjml(DOK, "B", "", PFLICHT, breite=380)


def test_bild_ohne_basis_wird_platzhalter():
    html = b.rendern(DOK, "B", "", PFLICHT, bild_basis="")
    assert "[Bild: logo.png]" in html
    assert 'src="' not in html.split("[Bild: logo.png]")[0].split("Neuigkeiten")[-1]


def test_bild_adresse():
    assert b.bild_adresse("medien:a b.png", "https://h/m/") == "https://h/m/a%20b.png"
    assert b.bild_adresse("medien:a.png", "") is None
    assert b.bild_adresse("https://boese.de/x.png", "https://h/m/") is None


def test_render_fehler_ist_deutsch(monkeypatch):
    import mjml
    def wirft(_):
        raise ValueError("kaputt")
    monkeypatch.setattr(mjml, "mjml2html", wirft)
    try:
        b.rendern(DOK, "B", "", PFLICHT)
    except b.RenderFehler as e:
        assert "Newsletter" in str(e)
    else:
        raise AssertionError("RenderFehler erwartet")


def test_pflichtteil_farbe_hat_kontrast_auf_der_flaeche():
    from spaces.marketing.claw.schoenheit import kontrast
    d = {"root": {"type": "EmailLayout", "data": {"backdropColor": "#0f2422", "canvasColor": "#ffffff",
         "textColor": "#1f2937", "childrenIds": ["t"]}},
         "t": {"type": "Text", "data": {"props": {"text": "Inhalt"}}}}
    m = b.nach_mjml(d, "B", "", PFLICHT)
    marke = '<mj-section padding="16px 0">'
    farbe = re.search(r'color="(#[0-9a-fA-F]{6})"', m.split(marke)[1]).group(1)
    assert kontrast(farbe, "#0f2422") >= 4.5
    assert 'color="#1f2937"' in m.split(marke)[0] and 'background-color="#ffffff"' in m
    assert 'mj-body background-color="#0f2422"' in m


def test_null_kinder_sind_leer():
    d = {"root": {"type": "EmailLayout", "data": {"childrenIds": None}}}
    assert "Musterstr. 1" in b.rendern(d, "B", "", PFLICHT)
    d2 = {"root": {"type": "EmailLayout", "data": {"childrenIds": ["c", "k"]}},
          "c": {"type": "Container", "data": {"props": {"childrenIds": None}}},
          "k": {"type": "ColumnsContainer", "data": {"props": {"columnsCount": 2, "columns": [{"childrenIds": None}]}}}}
    assert "Musterstr. 1" in b.rendern(d2, "B", "", PFLICHT)


def test_fusszeile_behaelt_lesbare_textfarbe():
    d = {"root": {"type": "EmailLayout", "data": {"backdropColor": "#0f2422", "canvasColor": "#1d3b39",
         "textColor": "#cfe3df", "childrenIds": []}}}
    fuss = b.nach_mjml(d, "B", "", PFLICHT).split('<mj-section padding="16px 0">')[1]
    assert 'color="#cfe3df"' in fuss


def test_fusszeile_kaputte_farbe_stuerzt_nicht():
    d = {"root": {"type": "EmailLayout", "data": {"backdropColor": "rot", "textColor": "x", "childrenIds": []}}}
    assert 'color="#111111"' in b.nach_mjml(d, "B", "", PFLICHT)


def _text_html(text):
    d = {**DOK, "t": {"type": "Text", "data": {"props": {"text": text, "markdown": True}}}}
    return b.rendern(d, "B", "", PFLICHT)


def test_url_mit_sternen_bleibt_heil():
    h = _text_html("[t](https://e.com/a**b**c_d_e)")
    assert 'href="https://e.com/a**b**c_d_e"' in h
    assert "<strong>" not in h


def test_fett_um_link():
    h = _text_html("**[t](https://x.de)**")
    assert '<strong><a href="https://x.de"' in h


def test_nul_im_text_ist_harmlos():
    assert "Hi" in _text_html("Hi \x005\x00")


def test_bild_basis_ohne_schraegstrich():
    assert b.bild_adresse("medien:a.png", "https://h/m") == "https://h/m/a.png"


def test_unsaubere_zahlen_und_ausrichtung():
    d = {**DOK, "t": {"type": "Text", "data": {"style": {"fontSize": "gross", "textAlign": "x\"y",
         "padding": {"top": "abc"}}, "props": {"text": "Hi"}}}}
    h = b.rendern(d, "B", "", PFLICHT)
    assert "Hi" in h and 'x"y' not in h


# ---- Runde 2: Eigenschaften der Email-Builder-Bloecke ----

def _mit(blocks, ids=None, **wurzel):
    daten = {"backdropColor": "#0f2422", "canvasColor": "#0f2422", "textColor": "#cfe3df",
             "childrenIds": ids if ids is not None else list(blocks)}
    daten.update(wurzel)
    return b.nach_mjml({"root": {"type": "EmailLayout", "data": daten}, **blocks}, "B", "", PFLICHT,
                       bild_basis="https://h/m/")


def _spalten(**props):
    return {"c": {"type": "ColumnsContainer", "data": {"props": {"columns": [
        {"childrenIds": ["x"]}, {"childrenIds": ["x"]}, {"childrenIds": ["x"]}], **props}}},
        "x": {"type": "Text", "data": {"props": {"text": "t"}}}}


def test_spalten_luecke_zwei():
    m = _mit(_spalten(columnsCount=2, columnsGap=24), ["c"])
    assert 'padding="0 12px 0 0px"' in m and 'padding="0 0px 0 12px"' in m


def test_spalten_luecke_drei():
    m = _mit(_spalten(columnsCount=3, columnsGap=30), ["c"])
    assert 'padding="0 20px 0 0px"' in m and 'padding="0 10px 0 10px"' in m and 'padding="0 0px 0 20px"' in m


def test_spalten_ausrichtung_senkrecht():
    assert m_va(_spalten(columnsCount=2, contentAlignment="top")) == "top"
    assert m_va(_spalten(columnsCount=2)) == "middle"
    assert m_va(_spalten(columnsCount=2, contentAlignment="bottom")) == "bottom"


def m_va(blocks):
    return re.search(r'<mj-column vertical-align="(\w+)"', _mit(blocks, ["c"])).group(1)


def test_container_ist_karte():
    bl = {"k": {"type": "Container", "data": {"style": {"backgroundColor": "#16302d", "borderRadius": 12,
               "padding": {"top": 10, "bottom": 10, "left": 20, "right": 20}}, "props": {"childrenIds": ["x"]}}},
          "x": {"type": "Text", "data": {"props": {"text": "t"}}}}
    m = _mit(bl, ["k"], canvasColor="#ffffff")
    assert '<mj-wrapper background-color="#ffffff" padding="0 24px">' in m
    assert 'background-color="#16302d" border-radius="12px"' in m
    assert 'padding="10px 20px 10px 20px"' in m


def test_container_rahmen():
    bl = {"k": {"type": "Container", "data": {"style": {"borderColor": "#ff0000"}, "props": {"childrenIds": []}}}}
    assert 'border="1px solid #ff0000"' in _mit(bl, ["k"])


def _knopf(**props):
    return {"k": {"type": "Button", "data": {"style": {}, "props": {"text": "Los", "url": "https://x.de", **props}}}}


def test_knopf_groessen():
    for name, wert in (("x-small", "4px 8px"), ("small", "8px 12px"), ("medium", "12px 20px"), ("large", "16px 32px")):
        assert f'inner-padding="{wert}"' in _mit(_knopf(size=name)), name
    assert 'inner-padding="12px 20px"' in _mit(_knopf())


def test_knopf_volle_breite_und_form():
    assert 'width="100%"' in _mit(_knopf(fullWidth=True))
    assert "width=" not in _mit(_knopf()).split("<mj-button")[1].split(">")[0]
    assert 'border-radius="4px"' in _mit(_knopf())
    assert 'border-radius="0px"' in _mit(_knopf(buttonStyle="rectangle"))
    assert 'border-radius="64px"' in _mit(_knopf(buttonStyle="pill"))


def test_knopf_ausrichtung_und_schrift():
    bl = _knopf()
    bl["k"]["data"]["style"] = {"textAlign": "right", "fontSize": 20, "fontWeight": "normal"}
    m = _mit(bl)
    assert 'align="right"' in m and 'font-size="20px"' in m and 'font-weight="normal"' in m


def _ueberschrift(stil, level="h1"):
    return {"h": {"type": "Heading", "data": {"style": stil, "props": {"text": "T", "level": level}}}}


def test_ueberschrift_schriftgroesse_und_gewicht():
    assert 'font-size="32px"' in _mit(_ueberschrift({}))
    assert 'font-size="40px"' in _mit(_ueberschrift({"fontSize": 40}))
    assert 'font-weight="normal"' in _mit(_ueberschrift({"fontWeight": "normal"}))
    assert 'font-weight="bold"' in _mit(_ueberschrift({}))


def _bild(stil=None, **props):
    return {"i": {"type": "Image", "data": {"style": stil or {}, "props": {"url": "medien:a.png", **props}}}}


def test_bild_ausrichtung_und_masse():
    m = _mit(_bild({"textAlign": "right"}, width=200, height=100))
    assert 'align="right"' in m and 'width="200px"' in m
    # Mit Breite traegt die Datei das Verhaeltnis; keine feste Hoehe (Spec 2026-09-29 §4)
    assert 'height="100px"' not in m
    assert 'align="left"' in _mit(_bild())


def test_bild_hintergrund():
    assert 'container-background-color="#112233"' in _mit(_bild({"backgroundColor": "#112233"}))


def test_trenner_breite_farbe_und_polster():
    bl = {"d": {"type": "Divider", "data": {"style": {"padding": {"top": 1, "bottom": 2, "left": 3, "right": 4}},
               "props": {"lineColor": "#2a534e", "lineHeight": 3}}}}
    m = _mit(bl)
    assert 'border-width="3px"' in m and 'border-color="#2a534e"' in m and 'padding="1px 4px 2px 3px"' in m


def test_kaputte_farben_stuerzen_nicht():
    d = {"root": {"type": "EmailLayout", "data": {"backdropColor": ["a"], "textColor": 5, "childrenIds": []}}}
    assert "<mj-section" in b.nach_mjml(d, "B", "", PFLICHT)


def test_alle_startvorlagen_rendern():
    import json
    from pathlib import Path
    ordner = Path(__file__).resolve().parents[2] / "vorlagen" / "newsletter"
    dateien = sorted(ordner.glob("*.json"))
    assert len(dateien) >= 5
    for datei in dateien:
        vorlage = json.loads(datei.read_text(encoding="utf-8"))
        for handy in (False, True):
            html = b.rendern(vorlage["bloecke"], "Betreff", "Vorab", PFLICHT,
                             bild_basis="https://h/m/", handy=handy)
            assert "Musterstr. 1" in html, datei.name


# Schlussrunde E1 (final-fix-findings.md): Absaetze und fehlendes mjml-python

def test_absaetze_leerzeile_und_umbruch():
    """Leerzeile = neuer Absatz (<br><br>), einfacher Umbruch = <br> - auch bei CRLF,
    mehreren Leerzeilen und Leerzeichen in der Leerzeile."""
    for roh, erwartet in (("a\nb", "a<br>b"), ("a\n\nb", "a<br><br>b"), ("a\n\n\n\nb", "a<br><br>b"),
                          ("a\r\n\r\nb", "a<br><br>b"), ("a\n  \t\nb", "a<br><br>b"), ("a\r\nb", "a<br>b")):
        for markdown in (True, False):
            assert b._text(roh, markdown) == erwartet, (roh, markdown)


def test_ohne_mjml_modul_deutscher_renderfehler(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "mjml", None)   # import mjml -> ImportError
    try:
        b.rendern(DOK, "B", "", PFLICHT)
    except b.RenderFehler as e:
        assert "mjml-python fehlt" in str(e)
    else:
        raise AssertionError("RenderFehler erwartet")


def test_bild_mit_breite_setzt_keine_feste_hoehe():
    """Am Handy wird das Bild schmaler; eine feste Hoehe verzerrte es. Das
    Seitenverhaeltnis traegt die Bilddatei selbst (Spec 2026-09-29-newsletter-
    bilder §4)."""
    dok = {"root": {"type": "EmailLayout", "data": {"childrenIds": ["b"]}},
           "b": {"type": "Image", "data": {"style": {}, "props": {"url": "medien:x.jpg", "width": 552, "height": 276}}}}
    mjml = b.nach_mjml(dok, "B", "", {}, bild_basis="https://h/b/")
    assert 'width="552px"' in mjml and 'height="276px"' not in mjml


# --- Neue Gestaltungsmittel (Spec 2026-10-01 §4) ---
from spaces.marketing.claw import bloecke_mjml as bm2, schriften


def _dok(*bloecke, **wurzel):
    ids = [b[0] for b in bloecke]
    d = {"root": {"type": "EmailLayout", "data": {"childrenIds": ids, **wurzel}}}
    for bid, typ, data in bloecke:
        d[bid] = {"type": typ, "data": data}
    return d


def test_alte_dokumente_unveraendert():
    d = _dok(("t", "Text", {"style": {}, "props": {"text": "Hallo"}}))
    assert bm2.nach_mjml(d, "B", "V", {}) == bm2.nach_mjml(d, "B", "V", {}, schrift_basis="")
    assert "mj-font" not in bm2.nach_mjml(d, "B", "V", {})


def test_anzeige_schrift_laufweite_versalien_zeilenhoehe():
    d = _dok(("h", "Heading", {"style": {"fontFamily": "ANZEIGE", "letterSpacing": 3, "textTransform": "uppercase",
                                          "lineHeight": 1.1}, "props": {"text": "Titel", "level": "h1"}}),
             schriften={"anzeige": "playfair", "text": "poppins"})
    m = bm2.nach_mjml(d, "B", "V", {}, schrift_basis="https://x.de/marketing/schrift/")
    assert "font-family=\"'Playfair Display', Georgia" in m
    assert 'letter-spacing="3px"' in m and 'text-transform="uppercase"' in m and 'line-height="1.1"' in m
    assert '<mj-font name="Playfair Display" href="https://x.de/marketing/schrift/schriften.css"' in m
    assert '<mj-all font-family="\'Poppins\', Arial' in m


def test_ohne_schrift_basis_kein_mj_font_aber_stapel():
    d = _dok(("h", "Heading", {"style": {"fontFamily": "ANZEIGE"}, "props": {"text": "T"}}),
             schriften={"anzeige": "oxanium", "text": "rajdhani"})
    m = bm2.nach_mjml(d, "B", "V", {})
    assert "mj-font" not in m and "'Oxanium', 'Trebuchet MS'" in m


def test_kursiv_in_ueberschrift():
    d = _dok(("h", "Heading", {"style": {}, "props": {"text": "Herbst*brief*"}}))
    assert "Herbst<em>brief</em>" in bm2.nach_mjml(d, "B", "V", {})


def test_container_hintergrundbild_und_farbfeld():
    d = _dok(("k", "Container", {"style": {"backgroundColor": "#2f4858", "overlay": {"farbe": "#2f4858", "deckkraft": 80}},
                                  "props": {"url": "medien:kopf.jpg", "width": 600, "height": 300, "childrenIds": []}}))
    m = bm2.nach_mjml(d, "B", "V", {}, bild_basis="https://x.de/marketing/bild/1.a/")
    assert 'background-url="https://x.de/marketing/bild/1.a/kopf.jpg"' in m
    assert 'background-size="cover"' in m and "rgba(47,72,88,0.8)" in m


def test_schwarz_weiss_haengt_sw_an():
    d = _dok(("i", "Image", {"style": {}, "props": {"url": "medien:a.jpg", "width": 600, "height": 300, "sw": True}}))
    assert 'src="https://x.de/b/a.jpg?sw=1"' in bm2.nach_mjml(d, "B", "V", {}, bild_basis="https://x.de/b/")


def test_dunkel_setzt_color_scheme():
    d = _dok(("t", "Text", {"style": {}, "props": {"text": "x"}}), dunkel=True)
    m = bm2.nach_mjml(d, "B", "V", {})
    assert 'name="color-scheme" content="dark"' in m


def test_dunkel_rendert_echt_und_meta_bleibt_erhalten():
    d = _dok(("t", "Text", {"style": {}, "props": {"text": "x"}}), dunkel=True)
    html_ = bm2.rendern(d, "B", "V", {})
    assert 'name="color-scheme"' in html_ and 'content="dark"' in html_
    assert "supported-color-schemes" in html_


def test_neue_mittel_rendern_echt():
    d = _dok(("h", "Heading", {"style": {"fontFamily": "ANZEIGE", "letterSpacing": 2, "textTransform": "uppercase"},
                               "props": {"text": "Herbst*brief*"}}),
             ("k", "Container", {"style": {"backgroundColor": "#2f4858", "overlay": {"farbe": "#2f4858", "deckkraft": 80}},
                                 "props": {"url": "medien:kopf.jpg", "height": 300, "childrenIds": ["h"]}}),
             schriften={"anzeige": "playfair", "text": "poppins"})
    h = bm2.rendern(d, "B", "V", {}, bild_basis="https://x.de/marketing/bild/1.a/",
                    schrift_basis="https://x.de/marketing/schrift/")
    assert "Herbst<em>brief</em>" in h and "kopf.jpg" in h


def test_unbekannte_schrift_id_faellt_auf_standard():
    d = _dok(("h", "Heading", {"style": {"fontFamily": "ANZEIGE"}, "props": {"text": "T"}}),
             schriften={"anzeige": "comic-sans", "text": "nix"})
    m = bm2.nach_mjml(d, "B", "V", {})
    assert "comic" not in m.lower() and "mj-font" not in m


def test_register_vollstaendig():
    assert set(schriften.REGISTER) == {"cormorant", "dm-sans", "playfair", "poppins", "young-serif", "manrope",
                                       "bodoni", "montserrat", "josefin", "oxanium", "rajdhani"}

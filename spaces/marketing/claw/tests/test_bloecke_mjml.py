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

"""Render-Modul des Marketing-Pults (Spec §3.4). Eine Quelle fuer Vorschau
und Ergebnis."""
from spaces.marketing.claw import pult_render as r

GESTALT = {"grund": "#0f2422", "text": "#cfe3df", "akzent": "#5eead4",
           "flaeche": "#1d3b39", "text_hell": "#e9fbf6", "text_leise": "#8aa3a0",
           "gold": "#fbbf24", "handlung_text": "#0f2422"}
PFLICHT = {"impressum": "VibeMind, Musterstr. 1, 12345 Stadt",
           "abmelde_hinweis": "Abmelden: {abmeldelink}"}
FELDER = {"betreff": "Early Access", "vorschautext": "Kurz vorab",
          "abschnitte": [{"titel": "Warum", "text": "Erster Absatz.\n\nZweiter Absatz."}],
          "knopf_text": "Jetzt eintragen", "knopf_link": "https://vibemind.space/warteliste"}


def test_mail_traegt_inhalt_farben_und_pflichtteil():
    html = r.mail_html(FELDER, GESTALT, PFLICHT)
    assert html.startswith("<!doctype html>")
    for teil in ("Early Access", "Warum", "Erster Absatz.", "Zweiter Absatz.",
                 "Jetzt eintragen", 'href="https://vibemind.space/warteliste"',
                 "#0f2422", "#5eead4", "Musterstr. 1", "Abmelden:"):
        assert teil in html, teil
    assert "{abmeldelink}" not in html          # Platzhalter sichtbar ersetzt


def test_handy_ist_schmaler():
    assert 'width="380"' in r.handy_html(FELDER, GESTALT, PFLICHT)
    assert 'width="600"' in r.mail_html(FELDER, GESTALT, PFLICHT)


def test_skript_im_text_wird_nie_ausgefuehrt():
    boese = dict(FELDER, abschnitte=[{"titel": "<script>alert(1)</script>",
                                      "text": '<img src=x onerror="alert(2)">'}],
                 betreff='"><b>x')
    html = r.mail_html(boese, GESTALT, PFLICHT)
    assert "<script>alert" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html


def test_knopf_nur_mit_https_link():
    html = r.mail_html(dict(FELDER, knopf_link="javascript:alert(1)"), GESTALT, PFLICHT)
    assert "javascript:" not in html
    assert "Jetzt eintragen" not in html          # ohne gueltigen Link kein Knopf


def test_fehlendes_impressum_ist_sichtbar():
    html = r.mail_html(FELDER, GESTALT, dict(PFLICHT, impressum=""))
    assert "Impressum fehlt" in html


def test_regler_wirken():
    g = dict(GESTALT, schrift="serif", rundung=12, abstand="weit",
             kopf_text="VibeMind Neuigkeiten", fuss_text="Danke fuers Lesen")
    html = r.mail_html(FELDER, g, PFLICHT)
    assert "Georgia" in html and "border-radius:12px" in html
    assert "VibeMind Neuigkeiten" in html and "Danke fuers Lesen" in html


def test_logo_nur_als_daten_bild():
    g = dict(GESTALT, logo="data:image/png;base64,iVBORw0KGgo=")
    assert '<img src="data:image/png;base64,iVBORw0KGgo="' in r.mail_html(FELDER, g, PFLICHT)
    g = dict(GESTALT, logo="https://boese.de/x.png")
    assert "boese.de" not in r.mail_html(FELDER, g, PFLICHT)


def test_pdf_entsteht():
    daten = r.pdf_bytes(FELDER, dict(GESTALT, rundung=4))
    assert daten.startswith(b"%PDF")


def test_beispiel_felder_sind_vollstaendig():
    assert r.BEISPIEL_FELDER["betreff"] and r.BEISPIEL_FELDER["abschnitte"]
    assert r.mail_html(r.BEISPIEL_FELDER, GESTALT, PFLICHT)


# --- Farbbedeutung (Browser-Durchlauf 29.09.2026, Aufgabe 0a) ---------------
# grund = Inhaltsflaeche (text, text_hell), flaeche = Kopf-/Fussband mit einer
# Schrift, die gegen flaeche lesbar ist; akzent/handlung_text = Knopf.
# "hell" und "warm-sand" lagen vorher mit dunklem text auf dunkler flaeche.

import re  # noqa: E402

import pytest  # noqa: E402

from spaces.marketing.claw import pdf, schoenheit  # noqa: E402

# "warm-sand" so, wie es in marketing.layout_vorlagen steht (VM, 29.09.2026):
# text_leise ist dort hell und nur auf dem dunklen Band lesbar.
WARM_SAND = {"gold": "#6e4a0c", "text": "#3d2b1c", "grund": "#fbf1e4",
             "akzent": "#8a3d15", "flaeche": "#4a2c17", "text_hell": "#3a2314",
             "text_leise": "#e8dcc4", "handlung_text": "#fdf3e7"}
GESTALTEN = {"dunkel": pdf.LAYOUTS["dunkel"], "hell": pdf.LAYOUTS["hell"],
             "warm-sand": WARM_SAND}

_TAG = re.compile(r'<(\w+)\b[^>]*\bstyle="([^"]*)"')
_HG = re.compile(r"background:(#[0-9a-fA-F]{6})")
_FG = re.compile(r"(?<![-\w])color:(#[0-9a-fA-F]{6})")


def farb_paare(html: str) -> list:
    """(tag, schrift, hintergrund) fuer jedes Element mit Schriftfarbe.

    Hintergrund = eigener background, sonst der der zuletzt geoeffneten
    Zelle (td) oder des body - die Mail ist eine Tabelle, deren Zellen
    nacheinander kommen, nicht verschachtelt."""
    paare, zelle = [], None
    for tag, stil in _TAG.findall(html):
        hg = _HG.search(stil)
        if tag in ("body", "td", "table") and hg:
            zelle = hg.group(1)
        fg = _FG.search(stil)
        if fg:
            paare.append((tag, fg.group(1), hg.group(1) if hg else zelle))
    return paare


@pytest.mark.parametrize("name", sorted(GESTALTEN))
def test_jede_schrift_ist_auf_ihrem_hintergrund_lesbar(name):
    g = dict(GESTALTEN[name], kopf_text="VibeMind Neuigkeiten",
             fuss_text="Danke fuers Lesen")
    html = r.mail_html(FELDER, g, PFLICHT)
    paare = farb_paare(html)
    assert paare, "keine Farbpaare gefunden"
    for tag, vorne, hinten in paare:
        schwelle = 3.0 if tag in ("h1", "h2") else 4.5
        wert = schoenheit.kontrast(vorne, hinten)
        assert wert >= schwelle, f"{name}: {tag} {vorne} auf {hinten} = {wert:.2f}"


@pytest.mark.parametrize("name", sorted(GESTALTEN))
def test_inhalt_liegt_auf_grund_band_auf_flaeche(name):
    g = dict(GESTALTEN[name], kopf_text="Kopf", fuss_text="Fuss")
    paare = farb_paare(r.mail_html(FELDER, g, PFLICHT))
    absaetze = [(v, h) for t, v, h in paare if t == "p" and v == g["text"]]
    assert absaetze and all(h == g["grund"] for _, h in absaetze)
    ueberschriften = [(v, h) for t, v, h in paare if t in ("h1", "h2")]
    assert ueberschriften and all(v == g["text_hell"] and h == g["grund"]
                                  for v, h in ueberschriften)
    # Kopf- und Fussband: mindestens zwei Schriften auf flaeche
    assert sum(1 for _, _, h in paare if h == g["flaeche"]) >= 2
    knopf = [(v, h) for t, v, h in paare if t == "a"]
    assert knopf == [(g["handlung_text"], g["akzent"])]


def test_band_bevorzugt_text_hell_wenn_lesbar():
    # Betreiber (pdf.py, 11.09.2026): lieber nicht reinweiss. "dunkel" hat
    # text_hell #e9fbf6 mit 11.28 gegen flaeche - der bleibt, obwohl #ffffff
    # noch etwas mehr Kontrast haette.
    assert r._band_schrift(pdf.LAYOUTS["dunkel"])[0] == "#e9fbf6"
    paare = farb_paare(r.mail_html(FELDER, dict(pdf.LAYOUTS["dunkel"], kopf_text="K",
                                                fuss_text="F"), PFLICHT))
    assert ("td", "#e9fbf6", pdf.LAYOUTS["dunkel"]["flaeche"]) in paare
    # "hell": text_hell ist dunkel wie flaeche -> lesbarer Ersatz.
    hell = pdf.LAYOUTS["hell"]
    schrift = r._band_schrift(hell)[0]
    assert schrift in ("#ffffff", "#111111") and schrift != hell["text_hell"]
    assert schoenheit.kontrast(schrift, hell["flaeche"]) >= 4.5


def test_ohne_kopf_und_logo_kein_kopfband():
    g = dict(pdf.LAYOUTS["hell"])
    html = r.mail_html(FELDER, g, PFLICHT)
    # erstes Band auf flaeche ist das Fussband mit dem Pflichtteil
    erste = html.index("background:" + g["flaeche"])
    assert html.index(">Early Access</h1>") < erste < html.index("Musterstr. 1")

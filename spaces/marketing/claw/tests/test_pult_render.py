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

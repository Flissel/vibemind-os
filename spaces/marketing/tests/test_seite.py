"""Seitenmodell der freien Newsletter-Flaeche (claw/seite.py)."""
import copy

import pytest

from spaces.marketing.claw.seite import SeiteFehler, leer, pruefen, rolle_schrift

MARKE = {"schrift_anzeige": "montserrat", "schrift_text": "dm-sans", "akzent": "#f66c1e"}


def _text(**kw):
    e = {"id": "t1", "art": "text", "x": 300, "y": 100, "breite": 400, "hoehe": 60,
         "text": "Hallo", "rolle": "TEXT", "groesse": 17, "farbe": "#111111"}
    e.update(kw)
    return e


def _seite(*ebenen, **kw):
    s = leer(MARKE)
    s["ebenen"] = list(ebenen)
    s.update(kw)
    return s


def _mit(ebene=None, **seite_kw):
    """Baut eine Seite mit einer Textebene; `ebene` ueberschreibt Felder (art darf wechseln)."""
    def bauen():
        e = _text()
        if ebene:
            e.update(ebene)
        return _seite(e, **seite_kw)
    return bauen


def _zu_viele():
    return _seite(*[_text(id=f"t{i}") for i in range(81)])


def _doppelte_id():
    return _seite(_text(), _text())


def _zu_gross():
    return _seite(*[_text(id=f"t{i}", text="ä" * 3000) for i in range(80)])


def test_leere_seite_ist_gueltig():
    s = pruefen(leer(MARKE))
    assert s["breite"] == 600
    assert 400 <= s["hoehe"] <= 6000
    assert s["schriften"] == {"anzeige": "montserrat", "text": "dm-sans"}
    assert pruefen(leer(None))["schriften"] == {"anzeige": "dm-sans", "text": "dm-sans"}


def test_vorgaben_werden_ergaenzt():
    e = pruefen(_seite(_text()))["ebenen"][0]
    assert (e["drehung"], e["deckkraft"], e["ausrichtung"]) == (0, 1, "links")


@pytest.mark.parametrize("bauen", [
    _mit(breite=599),
    _mit(hoehe=399),
    _mit(hoehe=6001),
    _zu_viele,
    _doppelte_id,
    _mit({"art": "video"}),
    lambda: _seite({"id": "b", "art": "bild", "x": 0, "y": 0, "breite": 9, "hoehe": 9,
                    "quelle": "https://x/y.png"}),
    lambda: _seite({"id": "k", "art": "knopf", "x": 0, "y": 0, "breite": 9, "hoehe": 9, "text": "Los",
                    "link": "http://x", "farbe": "#111111", "textfarbe": "#ffffff"}),
    _mit({"farbe": "red"}),
    _mit({"text": "x" * 3001}),
    _mit({"style": "color:red"}),
    _mit({"drehung": 181}),
    _zu_gross,
], ids=["breite", "hoehe_min", "hoehe_max", "ebenen_81", "doppelte_id", "art", "quelle", "link",
        "farbe", "text_3001", "unbekannter_schluessel", "drehung", "json_zu_gross"])
def test_grenzen(bauen):
    with pytest.raises(SeiteFehler):
        pruefen(bauen())


def test_unbekannte_schluessel_auf_seite_und_hintergrund():
    s = leer(MARKE)
    s["foo"] = 1
    with pytest.raises(SeiteFehler):
        pruefen(s)
    s = leer(MARKE)
    s["hintergrund"]["foo"] = 1
    with pytest.raises(SeiteFehler):
        pruefen(s)


def test_bool_und_zahl_streng():
    with pytest.raises(SeiteFehler):
        pruefen(_seite(_text(grossbuchstaben=1)))
    with pytest.raises(SeiteFehler):
        pruefen(_seite(_text(groesse=True)))


def test_eingabe_bleibt_unveraendert():
    s = _seite(_text())
    kopie = copy.deepcopy(s)
    pruefen(s)
    assert s == kopie


@pytest.mark.parametrize("inhalt", ["Hallo {vorname}", "Siehe [hier](https://a.de)"])
def test_anrede_und_links_nur_in_fliesstext(inhalt):
    with pytest.raises(SeiteFehler):
        pruefen(_seite(_text(text=inhalt, rolle="ANZEIGE")))
    with pytest.raises(SeiteFehler):
        pruefen(_seite(_text(text=inhalt, rolle="TEXT", drehung=5)))
    pruefen(_seite(_text(text=inhalt, rolle="TEXT", drehung=0)))


def test_anrede_meldung():
    with pytest.raises(SeiteFehler, match="Anrede"):
        pruefen(_seite(_text(text="Hi {vorname}", rolle="ANZEIGE")))


def test_textlink_nur_https():
    with pytest.raises(SeiteFehler):
        pruefen(_seite(_text(text="[x](http://a.de)")))


def test_schriften_nur_aus_register():
    s = leer(MARKE)
    s["schriften"]["anzeige"] = "comic"
    with pytest.raises(SeiteFehler):
        pruefen(s)
    ok = pruefen(leer(MARKE))
    assert rolle_schrift(ok, "HAND") == "caveat"
    assert rolle_schrift(ok, "ANZEIGE") == "montserrat"
    assert rolle_schrift(ok, "TEXT") == "dm-sans"


def test_bild_form_knopf_mit_vorgaben():
    s = _seite(
        {"id": "b", "art": "bild", "x": 0, "y": 0, "breite": 100, "hoehe": 100, "quelle": "medien:a.png"},
        {"id": "f", "art": "form", "x": 0, "y": 0, "breite": 100, "hoehe": 100, "form": "kreis",
         "farbe": "#00ff00"},
        {"id": "k", "art": "knopf", "x": 0, "y": 0, "breite": 100, "hoehe": 40, "text": "Los",
         "link": "https://a.de", "farbe": "#111111", "textfarbe": "#ffffff"})
    e = pruefen(s)["ebenen"]
    assert e[0]["passung"] == "fuellen" and e[0]["rahmen_farbe"] == "#ffffff"
    assert e[2]["rolle"] == "TEXT" and e[2]["rundung"] == 6

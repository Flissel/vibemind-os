import json

import pytest

from spaces.marketing.claw import agent_prompt as ap
from spaces.marketing.claw.agent_werkzeuge import WERKZEUGE
from spaces.marketing.claw.schriften import REGISTER


def test_reines_json():
    assert ap.antwort_lesen('{"antwort": "Fertig.", "aenderungen": [{"werkzeug": "block_loeschen", "id": "a"}]}') == {
        "antwort": "Fertig.", "aenderungen": [{"werkzeug": "block_loeschen", "id": "a"}]}


def test_standard_aenderungen_leer():
    assert ap.antwort_lesen('{"antwort": "Nur eine Auskunft."}') == {"antwort": "Nur eine Auskunft.", "aenderungen": []}


def test_codezaun():
    assert ap.antwort_lesen('```json\n{"antwort": "ok", "aenderungen": []}\n```')["antwort"] == "ok"


def test_vor_und_nachtext():
    assert ap.antwort_lesen('Klar, hier ist es:\n{"antwort": "ok", "aenderungen": []}\nViel Erfolg!')["antwort"] == "ok"
    # Nachtext mit eigenen Klammern ist ein zweites Objekt
    with pytest.raises(ap.AntwortFehler):
        ap.antwort_lesen('{"antwort": "ok", "aenderungen": []} Viel Erfolg {mit dem Rest}')


def test_zwei_objekte_fehler():
    with pytest.raises(ap.AntwortFehler, match="Kein einzelnes JSON-Objekt"):
        ap.antwort_lesen('{"antwort": "a"} {"antwort": "b"}')


def test_kein_objekt_fehler():
    with pytest.raises(ap.AntwortFehler, match="Kein einzelnes JSON-Objekt"):
        ap.antwort_lesen("Ich habe nichts geändert.")


def test_kaputtes_json_fehler():
    with pytest.raises(ap.AntwortFehler):
        ap.antwort_lesen('{"antwort": "a", "aenderungen": [}')
    with pytest.raises(ap.AntwortFehler):
        ap.antwort_lesen('{"antwort": "a",}')


def test_antwort_fehlt_oder_leer_oder_zu_lang():
    for t in ('{"aenderungen": []}', '{"antwort": "  ", "aenderungen": []}', '{"antwort": 5}',
              json.dumps({"antwort": "x" * 2001})):
        with pytest.raises(ap.AntwortFehler):
            ap.antwort_lesen(t)


def test_aenderungen_keine_liste():
    with pytest.raises(ap.AntwortFehler):
        ap.antwort_lesen('{"antwort": "a", "aenderungen": {"x": 1}}')


def test_klammern_in_strings():
    r = ap.antwort_lesen('{"antwort": "a { b } \\" }", "aenderungen": []}')
    assert r["antwort"] == 'a { b } " }'
    assert ap.antwort_lesen('{"antwort": "a { b", "aenderungen": []}')["antwort"] == "a { b"


def test_system_inhalt():
    for w in WERKZEUGE:
        assert w in ap.SYSTEM
    for s in ("Antworte mit genau einem JSON-Objekt", "eine dominante Aussage je Fläche", "höchstens zwei Schriften",
              "Farben nur aus den Ladenfarben", "Text auf Flächen mindestens 22",
              "Exportiere nie selbst – schlage es mit export_vorschlagen vor", "Antworte auf Deutsch, kurz, per Du",
              "neu:<n>"):
        assert s in ap.SYSTEM
    for sid in REGISTER:
        assert sid in ap.SYSTEM
    assert len(ap.SYSTEM) < 12000


def _auftrag(**kw):
    a = {"id": "x", "inhalt": "i", "nachricht": "Mach den Titel größer",
         "kontext": {"fenster": "newsletter", "auswahl": None},
         "bloecke": {"root": {"type": "EmailLayout", "data": {"backdropColor": "#111111", "canvasColor": "#FFFFFF",
                                                              "textColor": "#222222", "childrenIds": ["h1"]}},
                     "h1": {"type": "Heading", "data": {"props": {"text": "Hallo"}}}},
         "verlauf": [{"nachricht": "Hi", "antwort": "Hallo!"}]}
    a.update(kw)
    return a


def test_nutzer_text_basis():
    t = ap.nutzer_text(_auftrag(), ["a.png", "b.jpg"])
    assert "Mach den Titel größer" in t and "#111111" in t and "a.png" in t and "Hallo!" in t and '"h1"' in t


def test_nutzer_text_medien_gs_und_kappe():
    medien = [f"m{i}.png" for i in range(300)] + ["gs-abc123def456.jpg"]
    t = ap.nutzer_text(_auftrag(), medien)
    assert "gs-" not in t
    zeile = next(z for z in t.split("\n") if z.startswith("MEDIEN"))
    assert len(zeile.split(": ", 1)[1].split(", ")) == 200 and "m199.png" in zeile and "m200.png" not in zeile


def test_nutzer_text_verlauf_letzte_10_und_kontext():
    verlauf = [{"nachricht": f"frage{i}", "antwort": f"antwort{i}"} for i in range(15)]
    t = ap.nutzer_text(_auftrag(verlauf=verlauf, kontext={"fenster": "flaeche:f1", "auswahl": "e1"}), [])
    assert "frage4" not in t and "frage5" in t and "frage14" in t
    assert "flaeche:f1" in t and "e1" in t


def test_nutzer_text_schriften_aus_root_und_flaechen():
    a = _auftrag()
    a["bloecke"]["root"]["data"]["schriften"] = {"anzeige": "bodoni", "text": "manrope"}
    a["bloecke"]["root"]["data"]["fontFamily"] = "MODERN_SANS"
    a["bloecke"]["f1"] = {"type": "Image", "data": {"props": {"gestaltung": {"ebenen": [
        {"art": "text", "schrift": "playfair"}, {"art": "text", "schrift": "bodoni"},
        {"art": "bild", "quelle": "medien:a.png"}]}}}}
    zeile = next(z for z in ap.nutzer_text(a, []).split("\n") if z.startswith("SCHRIFTEN IM NEWSLETTER"))
    assert zeile == "SCHRIFTEN IM NEWSLETTER: Anzeige: bodoni, Text: manrope, in Flächen: playfair"
    assert "MODERN_SANS" not in zeile


def test_nutzer_text_unbekannte_schrift_ignoriert():
    a = _auftrag()
    a["bloecke"]["root"]["data"]["schriften"] = {"anzeige": "comic-sans", "text": "dm-sans"}
    a["bloecke"]["f1"] = {"type": "Image", "data": {"props": {"gestaltung": {"ebenen": [
        {"art": "text", "schrift": "nichtda"}]}}}}
    zeile = next(z for z in ap.nutzer_text(a, []).split("\n") if z.startswith("SCHRIFTEN IM NEWSLETTER"))
    assert zeile == "SCHRIFTEN IM NEWSLETTER: Text: dm-sans"
    assert ap.nutzer_text(_auftrag(), []).count("SCHRIFTEN IM NEWSLETTER: noch keine") == 1


def test_system_platz_ohne_neu_und_koordinaten():
    assert "platz" not in ap.SYSTEM.split("BLOCK-IDS UND neu:<n>")[1].split("WERKZEUGE")[0]
    assert "nur vorhandene Bildplatz-ids" in ap.SYSTEM and "nie eine Fläche" in ap.SYSTEM
    assert "-600" in ap.SYSTEM and "1200" in ap.SYSTEM and "-750" in ap.SYSTEM and "1500" in ap.SYSTEM


def test_array_mit_einem_objekt_wird_akzeptiert():
    assert ap.antwort_lesen('[{"antwort":"a","aenderungen":[]}]') == {"antwort": "a", "aenderungen": []}


def test_backticks_in_antwort_bleiben():
    r = ap.antwort_lesen('```json\n{"antwort": "Nutze ```code``` hier", "aenderungen": []}\n```')
    assert r["antwort"] == "Nutze ```code``` hier"

def test_korrektur_text():
    t = ap.korrektur_text("block_loeschen: Block x gibt es nicht")
    assert "block_loeschen: Block x gibt es nicht" in t and "JSON" in t

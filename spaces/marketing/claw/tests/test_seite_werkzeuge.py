import copy
import re

import pytest

from spaces.marketing.claw import seite as seite_mod
from spaces.marketing.claw import seite_werkzeuge as sw
from spaces.marketing.claw.seite_werkzeuge import ErgebnisSeite, WerkzeugFehler, anwenden, nachspielen

MEDIEN = {"nl-12345678-held.jpg", "logo.png", "vorlage1.jpg", "vorlage2.jpg", "vorlage3.jpg", "vorlage4.jpg"}


def grund() -> dict:
    s = seite_mod.leer(None)
    s["ebenen"].append({"id": "foto", "art": "bild", "x": 300, "y": 800, "breite": 400, "hoehe": 300,
                        "quelle": "medien:nl-12345678-held.jpg"})
    s["ebenen"].append({"id": "leer", "art": "bild", "x": 300, "y": 1100, "breite": 200, "hoehe": 100,
                        "quelle": "medien:platzhalter-2x1.png"})
    return seite_mod.pruefen(s)


def ebene(s: dict, eid: str) -> dict:
    return next(e for e in s["ebenen"] if e["id"] == eid)


def ids(s: dict) -> list[str]:
    return [e["id"] for e in s["ebenen"]]


def lauf(aenderungen, s=None) -> ErgebnisSeite:
    return anwenden(grund() if s is None else s, aenderungen, MEDIEN)


def ohne_abschluss(hinweise: list[str]) -> list[str]:
    """Nur die nummerierten Hinweise zu Aenderungen (ohne die Abschluss-Hinweise)."""
    return [h for h in hinweise if h.startswith("Änderung ")]


# ---- jedes Werkzeug einmal positiv -----------------------------------------------------------------

def test_werkzeugliste():
    assert sw.WERKZEUGE == (
        "seite_hoehe", "seite_hintergrund", "schriften_setzen", "ebene_hinzufuegen", "ebene_aendern",
        "ebene_reihenfolge", "ebene_loeschen", "bild_erzeugen", "bild_aendern", "bild_freistellen",
        "bild_aus_medien", "logo_einsetzen", "fertig")
    assert sw.MAX_AENDERUNGEN == 40


def test_seite_hoehe():
    e = lauf([{"werkzeug": "seite_hoehe", "hoehe": 1800, "schritt": "Seite verlängern"}])
    assert e.seite["hoehe"] == 1800 and e.geaendert and not ohne_abschluss(e.hinweise)


def test_seite_hintergrund():
    e = lauf([{"werkzeug": "seite_hintergrund", "farbe": "#f0e8d8", "bild": "medien:vorlage1.jpg"}])
    assert e.seite["hintergrund"] == {"farbe": "#f0e8d8", "bild": "medien:vorlage1.jpg"}
    e = lauf([{"werkzeug": "seite_hintergrund", "bild": None}], e.seite)
    assert e.seite["hintergrund"] == {"farbe": "#f0e8d8", "bild": None}


def test_seite_hintergrund_bild_nur_aus_medien():
    e = lauf([{"werkzeug": "seite_hintergrund", "bild": "medien:fremd.jpg"}])
    assert not e.geaendert
    assert "fremd.jpg" in ohne_abschluss(e.hinweise)[0]


def test_schriften_setzen():
    e = lauf([{"werkzeug": "schriften_setzen", "anzeige": "playfair"}])
    assert e.seite["schriften"] == {"anzeige": "playfair", "text": "dm-sans"}


def test_ebene_hinzufuegen_ohne_id_ganz_oben():
    neu = {"art": "form", "x": 300, "y": 600, "breite": 600, "hoehe": 20, "form": "rechteck", "farbe": "#5b3fd6"}
    e = lauf([{"werkzeug": "ebene_hinzufuegen", "ebene": neu}])
    letzte = e.seite["ebenen"][-1]
    assert re.fullmatch(r"a-[0-9a-f]{6}", letzte["id"])
    assert letzte["rundung"] == 0          # normalisiert (Vorgabe ergaenzt)


def test_ebene_hinzufuegen_mit_id_und_nach():
    neu = {"id": "streifen", "art": "form", "x": 300, "y": 600, "breite": 600, "hoehe": 20, "form": "rechteck",
           "farbe": "#5b3fd6"}
    e = lauf([{"werkzeug": "ebene_hinzufuegen", "ebene": neu, "nach": "titel"}])
    assert ids(e.seite)[:2] == ["titel", "streifen"]


def test_ebene_hinzufuegen_bild_ohne_quelle_bekommt_platzhalter():
    neu = {"id": "neu", "art": "bild", "x": 300, "y": 1300, "breite": 400, "hoehe": 300}
    e = lauf([{"werkzeug": "ebene_hinzufuegen", "ebene": neu},
              {"werkzeug": "bild_erzeugen", "ebene": "neu", "hinweis": "Team im Büro"}])
    assert ebene(e.seite, "neu")["quelle"] == "medien:platzhalter-4x3.png"
    assert {"platz": "neu", "modus": "neu", "hinweis": "Team im Büro", "vorlagen": []} in e.bildauftraege


def test_ebene_aendern():
    e = lauf([{"werkzeug": "ebene_aendern", "id": "titel", "felder": {"text": "Herbstaktion", "groesse": 56}}])
    t = ebene(e.seite, "titel")
    assert t["text"] == "Herbstaktion" and t["groesse"] == 56


def test_ebene_aendern_id_und_art_fest():
    e = lauf([{"werkzeug": "ebene_aendern", "id": "titel", "felder": {"art": "form"}}])
    assert not e.geaendert and len(ohne_abschluss(e.hinweise)) == 1


def test_ebene_reihenfolge():
    neu = list(reversed(ids(grund())))
    e = lauf([{"werkzeug": "ebene_reihenfolge", "ids": neu}])
    assert ids(e.seite) == neu
    e = lauf([{"werkzeug": "ebene_reihenfolge", "ids": neu[1:]}])
    assert not e.geaendert and len(ohne_abschluss(e.hinweise)) == 1


def test_ebene_loeschen():
    e = lauf([{"werkzeug": "ebene_loeschen", "id": "foto"}])
    assert "foto" not in ids(e.seite)


def test_bild_erzeugen_mit_vorlagen():
    e = lauf([{"werkzeug": "bild_erzeugen", "ebene": "leer", "hinweis": "Herbstlaub",
               "vorlagen": ["vorlage1.jpg", "medien:vorlage2.jpg"]}])
    assert e.bildauftraege == [{"platz": "leer", "modus": "neu", "hinweis": "Herbstlaub",
                                "vorlagen": ["vorlage1.jpg", "vorlage2.jpg"]}]
    assert not e.geaendert                          # Auftrag aendert die quelle nicht
    e = lauf([{"werkzeug": "bild_erzeugen", "ebene": "leer", "hinweis": "x",
               "vorlagen": ["vorlage1.jpg", "vorlage2.jpg", "vorlage3.jpg", "vorlage4.jpg"]}])
    assert e.bildauftraege == [] and "höchstens 3" in ohne_abschluss(e.hinweise)[0]


def test_bild_aendern():
    e = lauf([{"werkzeug": "bild_aendern", "ebene": "foto", "anweisung": "wärmer", "vorlagen": ["vorlage1.jpg"]}])
    assert e.bildauftraege == [{"platz": "foto", "modus": "aendern", "hinweis": "wärmer",
                                "vorlagen": ["vorlage1.jpg"]}]


def test_bild_aendern_braucht_echtes_bild():
    e = lauf([{"werkzeug": "bild_aendern", "ebene": "leer", "anweisung": "wärmer"}])
    assert e.bildauftraege == []
    assert any("bild_erzeugen" in h for h in ohne_abschluss(e.hinweise))


def test_bild_freistellen():
    e = lauf([{"werkzeug": "bild_freistellen", "ebene": "foto"}])
    assert e.bildauftraege == [{"platz": "foto", "modus": "freistellen", "hinweis": "", "vorlagen": []}]


def test_bild_aus_medien_setzt_quelle():
    e = lauf([{"werkzeug": "bild_aus_medien", "ebene": "leer", "quelle": "vorlage3.jpg"}])
    assert ebene(e.seite, "leer")["quelle"] == "medien:vorlage3.jpg"
    assert e.bildauftraege == [] and e.geaendert
    e = lauf([{"werkzeug": "bild_aus_medien", "ebene": "leer", "quelle": "fremd.jpg"}])
    assert not e.geaendert and "fremd.jpg" in ohne_abschluss(e.hinweise)[0]


def test_logo_einsetzen():
    e = lauf([{"werkzeug": "logo_einsetzen", "ebene": "leer", "quelle": "medien:logo.png"}])
    l = ebene(e.seite, "leer")
    assert l["quelle"] == "medien:logo.png" and l["passung"] == "einpassen"


def test_fertig_ohne_aenderungen():
    e = lauf([{"werkzeug": "fertig", "notiz": "Passt so."}])
    assert e.fertig and e.notiz == "Passt so." and not e.geaendert and e.bildauftraege == []
    assert e.verworfen == []
    assert e.seite == grund()


def test_fertig_mit_aenderungen_gilt_nicht():
    e = lauf([{"werkzeug": "seite_hoehe", "hoehe": 1500}, {"werkzeug": "fertig", "notiz": "fertig"}])
    assert not e.fertig and e.seite["hoehe"] == 1500 and e.verworfen == [2]
    assert any("fertig" in h for h in ohne_abschluss(e.hinweise))


# ---- Pruefung je Aenderung ---------------------------------------------------------------------------

def test_anwenden_verwirft_ungueltige_aenderung():
    s = grund()
    for i in range(80 - len(s["ebenen"])):
        s["ebenen"].append({"id": f"p{i}", "art": "form", "x": 10, "y": 10 + i, "breite": 5, "hoehe": 5,
                            "form": "rechteck", "farbe": "#eeeeee"})
    s = seite_mod.pruefen(s)
    vorher = copy.deepcopy(s)
    e = anwenden(s, [
        {"werkzeug": "ebene_aendern", "id": "titel", "felder": {"text": "Neu"}},                       # 1 gut
        {"werkzeug": "ebene_hinzufuegen", "ebene": {"art": "form", "x": 1, "y": 1, "breite": 1, "hoehe": 1,
                                                    "form": "kreis", "farbe": "#000000"}},          # 2: 81. Ebene
        {"werkzeug": "ebene_aendern", "id": "titel", "felder": {"text": "Hallo {vorname}"}},          # 3: ANZEIGE
        {"werkzeug": "ebene_loeschen", "id": "gibtsnicht"},                                          # 4: unbekannt
        {"werkzeug": "seite_hoehe", "hoehe": 1400},                                                  # 5 gut
    ], MEDIEN)
    assert s == vorher                                   # Eingabe unberuehrt
    assert ebene(e.seite, "titel")["text"] == "Neu" and e.seite["hoehe"] == 1400
    assert len(e.seite["ebenen"]) == 80
    nummeriert = ohne_abschluss(e.hinweise)
    assert [h.split(" ")[1] for h in nummeriert] == ["2", "3", "4"]
    assert e.verworfen == [2, 3, 4]
    assert "80" in nummeriert[0] and "{vorname}" in nummeriert[1] and "gibtsnicht" in nummeriert[2]
    assert seite_mod.pruefen(e.seite) == e.seite


def test_falsche_form_und_unbekanntes_werkzeug():
    e = lauf(["kaputt", {"werkzeug": "zaubern"}, {"werkzeug": "seite_hoehe"},
              {"werkzeug": "seite_hoehe", "hoehe": 900, "x": 1},
              {"werkzeug": "seite_hoehe", "hoehe": 900, "schritt": "x" * 81},
              {"werkzeug": "ebene_aendern", "id": "titel", "felder": "nein"}])
    assert not e.geaendert and len(ohne_abschluss(e.hinweise)) == 6


def test_bild_erzeugen_nur_fuer_bild_ebene():
    e = lauf([{"werkzeug": "bild_erzeugen", "ebene": "titel", "hinweis": "Foto"},
              {"werkzeug": "bild_erzeugen", "ebene": "gibtsnicht", "hinweis": "Foto"},
              {"werkzeug": "bild_aus_medien", "ebene": "knopf", "quelle": "vorlage1.jpg"}])
    assert e.bildauftraege == [] and not e.geaendert
    assert [h.split(" ")[1] for h in ohne_abschluss(e.hinweise)] == ["1", "2", "3"]


def test_bildauftrag_faellt_weg_wenn_ebene_spaeter_geloescht():
    e = lauf([{"werkzeug": "bild_erzeugen", "ebene": "leer", "hinweis": "x"},
              {"werkzeug": "ebene_loeschen", "id": "leer"}])
    assert e.bildauftraege == [] and any("leer" in h for h in ohne_abschluss(e.hinweise))


def test_max_aenderungen():
    viele = [{"werkzeug": "seite_hoehe", "hoehe": 500 + i} for i in range(sw.MAX_AENDERUNGEN + 3)]
    e = lauf(viele)
    assert e.seite["hoehe"] == 500 + sw.MAX_AENDERUNGEN - 1
    assert any(str(sw.MAX_AENDERUNGEN) in h for h in e.hinweise)
    with pytest.raises(WerkzeugFehler):
        lauf("keine liste")


def test_ungueltige_ausgangsseite():
    with pytest.raises(WerkzeugFehler):
        anwenden({"version": 2}, [], MEDIEN)


# ---- Abschluss-Hinweise ----------------------------------------------------------------------------

def test_hinweis_ohne_knopf():
    e = lauf([{"werkzeug": "ebene_loeschen", "id": "knopf"}])
    assert "Kein Knopf auf der Seite" in e.hinweise
    assert "Kein Knopf auf der Seite" not in lauf([]).hinweise


def test_hinweis_anrede_ueberlappt():
    e = lauf([{"werkzeug": "ebene_aendern", "id": "text", "felder": {"text": "Hallo {vorname}, schön!"}}])
    assert not [h for h in e.hinweise if "überlappt" in h]
    # Text (300/300, 480x120) ueber den Knopf (300/460, 220x48) schieben
    e = lauf([{"werkzeug": "ebene_aendern", "id": "text", "felder": {"text": "Hallo {vorname}", "y": 420}}])
    ueber = [h for h in e.hinweise if "überlappt" in h]
    assert len(ueber) == 1 and "text" in ueber[0] and "knopf" in ueber[0]
    e = lauf([{"werkzeug": "ebene_aendern", "id": "text",
               "felder": {"text": "[Mehr](https://example.com)", "y": 420}}])
    assert [h for h in e.hinweise if "überlappt" in h]
    # ohne Anrede/Link keine Meldung
    e = lauf([{"werkzeug": "ebene_aendern", "id": "text", "felder": {"y": 420}}])
    assert not [h for h in e.hinweise if "überlappt" in h]


def test_hinweis_bild_ohne_quelle():
    e = lauf([])
    assert [h for h in e.hinweise if "leer" in h and "Bild" in h]
    e = lauf([{"werkzeug": "bild_erzeugen", "ebene": "leer", "hinweis": "x"}])
    assert not [h for h in e.hinweise if "leer" in h]
    # Quelle, die es in den Medien nicht (mehr) gibt, zaehlt nicht als gueltig
    s = grund()
    ebene(s, "foto")["quelle"] = "medien:weg.jpg"
    e = anwenden(s, [{"werkzeug": "bild_erzeugen", "ebene": "leer", "hinweis": "x"}], MEDIEN)
    assert [h for h in e.hinweise if "foto" in h]


# ---- Nachspielen ---------------------------------------------------------------------------------

def test_nachspielen_auf_neueste_seite():
    aenderungen = [
        {"werkzeug": "ebene_aendern", "id": "foto", "felder": {"x": 280}, "schritt": "Foto rücken"},
        {"werkzeug": "ebene_aendern", "id": "titel", "felder": {"text": "Neu"}},
        {"werkzeug": "bild_freistellen", "ebene": "foto"},
        {"werkzeug": "seite_hoehe", "hoehe": 1600},
    ]
    neuer = grund()
    neuer["ebenen"] = [e for e in neuer["ebenen"] if e["id"] != "foto"]     # Betreiber hat foto geloescht
    ebene(neuer, "text")["text"] = "Vom Betreiber"
    e = nachspielen(neuer, aenderungen, MEDIEN)
    assert ebene(e.seite, "titel")["text"] == "Neu" and e.seite["hoehe"] == 1600
    assert ebene(e.seite, "text")["text"] == "Vom Betreiber"
    assert e.bildauftraege == []
    nummeriert = ohne_abschluss(e.hinweise)
    assert [h.split(" ")[1] for h in nummeriert] == ["1", "3"]
    assert all("übersprungen" in h and "foto" in h for h in nummeriert)


# ---- Gesamtpruefung: Platzhalter nur fuer neue Bild-Ebenen, offene Bildauftraege frueherer Runden ------

PLATZHALTER_NUR_NEU = "Platzhalter"


def test_platzhalter_als_quelle_einer_neuen_bild_ebene_erlaubt():
    neu = {"id": "neu", "art": "bild", "x": 300, "y": 600, "breite": 300, "hoehe": 400,
           "quelle": "medien:platzhalter-3x4.png"}
    e = lauf([{"werkzeug": "ebene_hinzufuegen", "ebene": neu}])
    assert not ohne_abschluss(e.hinweise) and ebene(e.seite, "neu")["quelle"] == "medien:platzhalter-3x4.png"


@pytest.mark.parametrize("aenderung", [
    {"werkzeug": "seite_hintergrund", "bild": "medien:platzhalter-4x3.png"},
    {"werkzeug": "ebene_aendern", "id": "foto", "felder": {"quelle": "medien:platzhalter-4x3.png"}},
    {"werkzeug": "bild_aus_medien", "ebene": "foto", "quelle": "platzhalter-4x3.png"},
    {"werkzeug": "logo_einsetzen", "ebene": "foto", "quelle": "medien:platzhalter-1x1.png"},
])
def test_platzhalter_sonst_abgelehnt(aenderung):
    e = lauf([aenderung])
    (h,) = ohne_abschluss(e.hinweise)
    assert PLATZHALTER_NUR_NEU in h and "ebene_hinzufuegen" in h and "bild_erzeugen" in h
    assert e.verworfen == [1] and not e.geaendert


def test_platzhalter_auch_dann_abgelehnt_wenn_er_in_den_medien_steht():
    e = anwenden(grund(), [{"werkzeug": "bild_aus_medien", "ebene": "foto", "quelle": "platzhalter-4x3.png"}],
                 MEDIEN | {"platzhalter-4x3.png"})
    assert e.verworfen == [1]


def test_ebene_aendern_mit_unveraendertem_platzhalter_bleibt_gueltig():
    e = lauf([{"werkzeug": "ebene_aendern", "id": "leer", "felder": {"x": 280, "quelle": "medien:platzhalter-2x1.png"}}])
    assert not ohne_abschluss(e.hinweise) and ebene(e.seite, "leer")["x"] == 280


def test_offene_bilder_frueherer_runden_unterdruecken_den_hinweis():
    assert [h for h in lauf([]).hinweise if "Bild-Ebene leer" in h]
    e = anwenden(grund(), [], MEDIEN, offene_bilder={"leer"})
    assert not [h for h in e.hinweise if "Bild-Ebene leer" in h]
    e = nachspielen(grund(), [], MEDIEN, offene_bilder=frozenset({"leer"}))
    assert not [h for h in e.hinweise if "Bild-Ebene leer" in h]

import copy

from spaces.marketing.claw import seite as seite_mod
from spaces.marketing.claw import seite_vorlagen as sv
from spaces.marketing.claw.schoenheit import kontrast

VIBEMIND = {"akzent": "#5B3FD6", "zweitfarbe": "#1e1b4b", "grund": "#ffffff", "text": "#1a1a1a",
            "name": "VibeMind", "logo": "medien:logo-vibemind-abc.png", "logo_dunkel": None,
            "schrift_anzeige": "playfair", "schrift_text": "dm-sans", "webseite": "https://vibemind.de"}

FIRMA = {"akzent": "#0f766e", "zweitfarbe": "#134e4a", "grund": "#f8fafc", "text": "#0b1f1d",
         "name": "Ivan GmbH", "logo": "medien:logo-ivan-123.png", "logo_dunkel": None,
         "schrift_anzeige": "montserrat", "schrift_text": "manrope", "webseite": "https://ivan.de"}


def vorlage() -> dict:
    return {
        "version": 1, "breite": 600, "hoehe": 900,
        "hintergrund": {"farbe": "#FFFFFF", "bild": None},
        "schriften": {"anzeige": "playfair", "text": "dm-sans"},
        "ebenen": [
            {"id": "buehne", "art": "form", "x": 300, "y": 200, "breite": 600, "hoehe": 400,
             "form": "rechteck", "farbe": "#5b3fd6"},
            {"id": "logo", "art": "bild", "x": 80, "y": 40, "breite": 120, "hoehe": 40,
             "quelle": "medien:logo-vibemind-abc.png", "passung": "einpassen"},
            {"id": "strand-am-morgen", "art": "bild", "x": 420, "y": 220, "breite": 300, "hoehe": 200,
             "quelle": "medien:nl-0123abcd-strand-am-morgen.jpg"},
            {"id": "marke", "art": "text", "x": 300, "y": 100, "breite": 300, "hoehe": 40,
             "text": "VibeMind", "rolle": "ANZEIGE", "groesse": 28, "farbe": "#ffffff"},
            {"id": "titel", "art": "text", "x": 150, "y": 300, "breite": 260, "hoehe": 80,
             "text": "Überschrift", "rolle": "ANZEIGE", "groesse": 40, "farbe": "#FFFFFF"},
            {"id": "text", "art": "text", "x": 300, "y": 520, "breite": 480, "hoehe": 100,
             "text": "Kurzer Einleitungstext", "rolle": "TEXT", "groesse": 17, "farbe": "#1a1a1a"},
            {"id": "band", "art": "text", "x": 300, "y": 640, "breite": 480, "hoehe": 40,
             "text": "Neu", "rolle": "TEXT", "groesse": 17, "farbe": "#ffffff", "hintergrund": "#1e1b4b"},
            {"id": "knopf", "art": "knopf", "x": 300, "y": 760, "breite": 220, "hoehe": 48,
             "text": "Mehr erfahren", "link": "https://vibemind.de", "farbe": "#5b3fd6",
             "textfarbe": "#ffffff"},
            {"id": "grau", "art": "text", "x": 300, "y": 840, "breite": 480, "hoehe": 30,
             "text": "Abmelden", "rolle": "TEXT", "groesse": 15, "farbe": "#666666"},
        ],
    }


def ebene(s: dict, eid: str) -> dict:
    return next(e for e in s["ebenen"] if e["id"] == eid)


def test_rollen_ableiten_farben_und_ebenen():
    r = sv.rollen_ableiten(vorlage(), VIBEMIND)
    f = r["farben"]
    assert f["hintergrund.farbe"] == "grund"
    assert f["ebenen.buehne.farbe"] == "akzent"
    assert f["ebenen.knopf.farbe"] == "akzent"
    assert f["ebenen.knopf.textfarbe"] == "auf_akzent"
    assert f["ebenen.text.farbe"] == "text"
    assert f["ebenen.band.hintergrund"] == "zweitfarbe"
    assert "ebenen.band.farbe" not in f          # Weiss auf Zweitfarbe: Schrift nie als grund
    assert f["ebenen.titel.farbe"] == "auf_akzent"
    assert f["ebenen.marke.farbe"] == "auf_akzent"
    assert "ebenen.grau.farbe" not in f          # keine Markenfarbe -> bleibt fest
    assert r["ebenen"]["logo"] == "logo"
    assert r["ebenen"]["marke"] == "name"
    assert r["ebenen"]["strand-am-morgen"] == "motiv:strand am morgen"
    assert "titel" not in r["ebenen"]


def test_motiv_beschreibung_aus_bildauftrag():
    auftraege = [{"platz": "strand-am-morgen", "modus": "neu", "hinweis": "Ruhiger Strand im Morgenlicht"}]
    r = sv.rollen_ableiten(vorlage(), VIBEMIND, auftraege)
    assert r["ebenen"]["strand-am-morgen"] == "motiv:Ruhiger Strand im Morgenlicht"


def test_platzhalter_ebene_ist_motiv():
    s = vorlage()
    ebene(s, "strand-am-morgen")["quelle"] = "medien:platzhalter-3x2.png"
    assert sv.rollen_ableiten(s, VIBEMIND)["ebenen"]["strand-am-morgen"] == "motiv:strand am morgen"


def test_fremdes_foto_hat_keine_rolle():
    s = vorlage()
    ebene(s, "strand-am-morgen")["quelle"] = "medien:foto.jpg"
    assert "strand-am-morgen" not in sv.rollen_ableiten(s, VIBEMIND)["ebenen"]


def test_rundlauf_gleiche_marke_gleiche_farben():
    s = vorlage()
    r = sv.rollen_ableiten(s, VIBEMIND)
    neu, _, _ = sv.fuellen(s, r, VIBEMIND, VIBEMIND["logo"])
    alt = seite_mod.pruefen(s)
    assert neu["hintergrund"]["farbe"].lower() == alt["hintergrund"]["farbe"].lower()
    for a, b in zip(alt["ebenen"], neu["ebenen"]):
        assert a["id"] == b["id"]
        for feld in ("farbe", "textfarbe", "hintergrund", "rahmen_farbe"):
            if a.get(feld) is not None:
                assert a[feld].lower() == b[feld].lower(), (a["id"], feld)
    assert ebene(neu, "logo")["quelle"] == VIBEMIND["logo"]
    assert ebene(neu, "marke")["text"] == "VibeMind"


def test_andere_marke_tauscht_farben_schriften_name_logo():
    s = vorlage()
    r = sv.rollen_ableiten(s, VIBEMIND)
    neu, _, _ = sv.fuellen(s, r, FIRMA, FIRMA["logo"])
    assert neu["hintergrund"]["farbe"] == "#f8fafc"
    assert ebene(neu, "buehne")["farbe"] == "#0f766e"
    assert ebene(neu, "knopf")["farbe"] == "#0f766e"
    assert ebene(neu, "band")["hintergrund"] == "#134e4a"
    assert ebene(neu, "text")["farbe"] == "#0b1f1d"
    assert ebene(neu, "grau")["farbe"] == "#666666"
    assert neu["schriften"] == {"anzeige": "montserrat", "text": "manrope"}
    assert ebene(neu, "marke")["text"] == "Ivan GmbH"
    assert ebene(neu, "logo")["quelle"] == "medien:logo-ivan-123.png"
    assert ebene(neu, "logo")["passung"] == "einpassen"
    assert ebene(neu, "knopf")["link"] == "https://ivan.de"
    assert "vibemind" not in str(neu).lower()


def test_fuellen_ohne_logo():
    s = vorlage()
    r = sv.rollen_ableiten(s, VIBEMIND)
    neu, _, _ = sv.fuellen(s, r, {**FIRMA, "logo": None}, None)
    assert "logo" not in [e["id"] for e in neu["ebenen"]]
    assert all("logo-vibemind" not in e.get("quelle", "") for e in neu["ebenen"])
    seite_mod.pruefen(neu)


def test_motiv_ebenen_ergeben_bildauftraege_und_platzhalter():
    s = vorlage()
    r = sv.rollen_ableiten(s, VIBEMIND, [{"platz": "strand-am-morgen", "hinweis": "Strand am Morgen"}])
    neu, auftraege, _ = sv.fuellen(s, r, FIRMA, FIRMA["logo"])
    assert auftraege == [{"platz": "strand-am-morgen", "modus": "neu", "hinweis": "Strand am Morgen"}]
    assert ebene(neu, "strand-am-morgen")["quelle"] == "medien:platzhalter-3x2.png"


def test_ergebnis_besteht_pruefen():
    s = vorlage()
    r = sv.rollen_ableiten(s, VIBEMIND)
    for marke in (VIBEMIND, FIRMA, {"akzent": "#ffd400"}, {}):
        neu, _, _ = sv.fuellen(s, r, marke, marke.get("logo"))
        assert seite_mod.pruefen(neu) == neu


def test_fehlende_markenfarben_fallen_zurueck():
    s = vorlage()
    r = sv.rollen_ableiten(s, VIBEMIND)
    neu, _, _ = sv.fuellen(s, r, {"akzent": "#0f766e"}, None)
    assert neu["hintergrund"]["farbe"] == "#ffffff"
    assert ebene(neu, "text")["farbe"] == "#1a1a1a"
    zweit = ebene(neu, "band")["hintergrund"]
    assert zweit not in ("#0f766e", "#1e1b4b")
    # Ohne Name bleibt der Text stehen, Schriften der Vorlage bleiben
    assert ebene(neu, "marke")["text"] == "VibeMind"
    assert neu["schriften"] == {"anzeige": "playfair", "text": "dm-sans"}


def test_kontrast_reparatur_heller_akzent():
    s = vorlage()
    r = sv.rollen_ableiten(s, VIBEMIND)
    gelb = {**FIRMA, "akzent": "#ffd400"}
    neu, _, _ = sv.fuellen(s, r, gelb, gelb["logo"])
    k = ebene(neu, "knopf")
    assert k["farbe"] == "#ffd400"
    assert k["textfarbe"] == "#0b1f1d"
    # Titel und Name liegen auf der gelben Buehne -> auf die Textfarbe der Marke
    assert ebene(neu, "titel")["farbe"] == "#0b1f1d"
    assert ebene(neu, "marke")["farbe"] == "#0b1f1d"


def test_kontrast_reparieren_text_auf_hellem_seitengrund():
    s = vorlage()
    ebene(s, "grau")["farbe"] = "#eeeeee"
    neu = sv.kontrast_reparieren(s, sv.rollen_ableiten(vorlage(), VIBEMIND))
    assert ebene(neu, "grau")["farbe"] == "#1a1a1a"


def test_kontrast_reparieren_dunkler_grund_nimmt_weiss():
    s = vorlage()
    s["hintergrund"]["farbe"] = "#111111"
    ebene(s, "text")["farbe"] = "#222222"
    neu = sv.kontrast_reparieren(s, {"farben": {}, "ebenen": {}})
    assert ebene(neu, "text")["farbe"] == "#ffffff"


def test_kontrast_reparieren_ueber_bild_bleibt():
    s = vorlage()
    # Titel liegt ueber dem Motivbild (zuoberst unter ihm) -> Pixel unbekannt, nicht anfassen
    ebene(s, "titel").update({"x": 420, "y": 220, "farbe": "#5b3fd6"})
    neu = sv.kontrast_reparieren(s, {"farben": {}, "ebenen": {}})
    assert ebene(neu, "titel")["farbe"] == "#5b3fd6"


def test_kontrast_reparieren_eigener_hintergrund_zaehlt():
    s = vorlage()
    ebene(s, "band")["hintergrund"] = "#fafafa"
    neu = sv.kontrast_reparieren(s, {"farben": {}, "ebenen": {}})
    assert ebene(neu, "band")["farbe"] == "#1a1a1a"


def test_kontrast_reparieren_grosse_schrift_schwelle_3():
    s = vorlage()
    # 3,5:1 auf Weiss: bei 40 reicht das (3:1), Text bleibt
    ebene(s, "titel").update({"y": 450, "farbe": "#7f7f7f"})
    assert kontrast("#7f7f7f", "#ffffff") > 3
    neu = sv.kontrast_reparieren(s, {"farben": {}, "ebenen": {}})
    assert ebene(neu, "titel")["farbe"] == "#7f7f7f"


def test_kontrast_reparieren_gedrehte_form_huelle():
    s = vorlage()
    s["ebenen"].insert(0, {"id": "keil", "art": "form", "x": 300, "y": 840, "breite": 40, "hoehe": 600,
                           "form": "rechteck", "farbe": "#111111", "drehung": 90})
    ebene(s, "grau")["farbe"] = "#333333"
    neu = sv.kontrast_reparieren(s, {"farben": {}, "ebenen": {}})
    assert ebene(neu, "grau")["farbe"] == "#ffffff"


def test_kontrast_reparieren_aendert_eingabe_nicht():
    s = vorlage()
    ebene(s, "grau")["farbe"] = "#eeeeee"
    vorher = copy.deepcopy(s)
    sv.kontrast_reparieren(s, {"farben": {}, "ebenen": {}})
    assert s == vorher


def test_logo_dunkel_auf_dunkler_unterlage():
    s = vorlage()
    s["hintergrund"]["farbe"] = "#101010"
    r = sv.rollen_ableiten(s, VIBEMIND)
    marke = {**FIRMA, "logo_dunkel": "medien:logo-ivan-dunkel.png"}
    neu, _, _ = sv.fuellen(s, r, marke, marke["logo"])
    assert ebene(neu, "logo")["quelle"] == "medien:logo-ivan-dunkel.png"


def test_kontrast_reparieren_halbe_form_mischt_mit_grund():
    s = vorlage()
    # schwarze Form mit 10 % Deckkraft auf Weiss ist fast weiss -> weisse Schrift wird dunkel
    s["ebenen"].insert(0, {"id": "schleier", "art": "form", "x": 300, "y": 840, "breite": 600, "hoehe": 60,
                           "form": "rechteck", "farbe": "#000000", "deckkraft": 0.1})
    ebene(s, "grau")["farbe"] = "#ffffff"
    neu = sv.kontrast_reparieren(s, {"farben": {}, "ebenen": {}})
    assert ebene(neu, "grau")["farbe"] == "#1a1a1a"


def test_weisse_schrift_auf_foto_bleibt_weiss():
    s = vorlage()
    ebene(s, "titel").update({"x": 420, "y": 220})        # liegt jetzt auf dem Motiv
    r = sv.rollen_ableiten(s, VIBEMIND)
    assert "ebenen.titel.farbe" not in r["farben"]
    dunkel = {**FIRMA, "grund": "#101820"}
    neu, _, _ = sv.fuellen(s, r, dunkel, dunkel["logo"])
    assert ebene(neu, "titel")["farbe"].lower() == "#ffffff"


def test_gleichstand_akzent_gleich_text():
    marke = {**VIBEMIND, "akzent": "#1a1a1a"}
    s = vorlage()
    ebene(s, "buehne")["farbe"] = "#1a1a1a"
    ebene(s, "knopf")["farbe"] = "#1a1a1a"
    f = sv.rollen_ableiten(s, marke)["farben"]
    assert f["ebenen.buehne.farbe"] == "akzent"
    assert f["ebenen.knopf.farbe"] == "akzent"
    assert f["ebenen.text.farbe"] == "text"              # Schrift #1a1a1a auf Weiss
    assert f["ebenen.knopf.textfarbe"] == "auf_akzent"   # Weiss auf der Akzentflaeche


def test_gleichstand_akzent_gleich_grund():
    marke = {**VIBEMIND, "akzent": "#ffffff"}            # auf_akzent = Textfarbe #1a1a1a
    s = vorlage()
    ebene(s, "buehne")["farbe"] = "#ffffff"
    f = sv.rollen_ableiten(s, marke)["farben"]
    assert f["hintergrund.farbe"] == "grund"
    assert f["ebenen.buehne.farbe"] == "akzent"
    assert "ebenen.titel.farbe" not in f                 # Weiss auf der Buehne: keine Rolle
    assert f["ebenen.text.farbe"] == "text"              # liegt auf dem Seitengrund, nicht auf dem Akzent


def test_warnung_ohne_webseite_link_bleibt():
    s = vorlage()
    r = sv.rollen_ableiten(s, VIBEMIND)
    for web in (None, "http://ivan.de", "kaputt"):
        neu, _, warnungen = sv.fuellen(s, r, {**FIRMA, "webseite": web}, FIRMA["logo"])
        assert ebene(neu, "knopf")["link"] == "https://vibemind.de"
        assert warnungen == [sv.WARNUNG_WEBSEITE]


def test_warnung_ohne_name():
    s = vorlage()
    r = sv.rollen_ableiten(s, VIBEMIND)
    neu, _, warnungen = sv.fuellen(s, r, {**FIRMA, "name": "  "}, FIRMA["logo"])
    assert ebene(neu, "marke")["text"] == "VibeMind"
    assert warnungen == ["Kein Firmenname – Text 'marke' bitte anpassen"]


def test_keine_warnungen_bei_vollstaendiger_marke():
    s = vorlage()
    _, _, warnungen = sv.fuellen(s, sv.rollen_ableiten(s, VIBEMIND), FIRMA, FIRMA["logo"])
    assert warnungen == []


def test_kaputte_rollen_werden_uebergangen():
    s = vorlage()
    neu, _, _ = sv.fuellen(s, {"farben": {"hintergrund.farbe": ["akzent"], "ebenen.x.farbe": "akzent"},
                               "ebenen": {"logo": 3}}, FIRMA, FIRMA["logo"])
    assert neu["hintergrund"]["farbe"].lower() == "#ffffff"


def test_kontrast_reparieren_text_parameter_none_liest_rolle():
    s = vorlage()
    ebene(s, "grau")["farbe"] = "#eeeeee"
    ebene(s, "text")["farbe"] = "#202020"
    neu = sv.kontrast_reparieren(s, {"farben": {"ebenen.text.farbe": "text"}, "ebenen": {}}, text=None)
    assert ebene(neu, "grau")["farbe"] == "#202020"

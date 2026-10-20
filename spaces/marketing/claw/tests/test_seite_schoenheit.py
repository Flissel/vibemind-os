import copy
import io

import pytest
from PIL import Image

from spaces.marketing.claw import seite_schoenheit as ss

FAKTOR = 2


def seite(ebenen: list[dict], hoehe: int = 800, farbe: str = "#ffffff") -> dict:
    return {"version": 1, "breite": 600, "hoehe": hoehe,
            "hintergrund": {"farbe": farbe, "bild": None},
            "schriften": {"anzeige": "dm-sans", "text": "dm-sans"}, "ebenen": ebenen}


def text(eid, x=300, y=200, breite=480, hoehe=100, **mehr):
    e = {"id": eid, "art": "text", "x": x, "y": y, "breite": breite, "hoehe": hoehe,
         "text": "Hallo", "rolle": "TEXT", "groesse": 17, "farbe": "#333333"}
    e.update(mehr)
    return e


def knopf(eid="knopf", y=600, **mehr):
    e = {"id": eid, "art": "knopf", "x": 300, "y": y, "breite": 220, "hoehe": 48,
         "text": "Mehr", "link": "https://example.com", "farbe": "#5b3fd6", "textfarbe": "#ffffff"}
    e.update(mehr)
    return e


def bild(eid="foto", y=200, **mehr):
    e = {"id": eid, "art": "bild", "x": 300, "y": y, "breite": 600, "hoehe": 300, "quelle": "medien:foto.jpg"}
    e.update(mehr)
    return e


def form(eid, x=300, y=200, breite=200, hoehe=100, **mehr):
    e = {"id": eid, "art": "form", "x": x, "y": y, "breite": breite, "hoehe": hoehe,
         "form": "rechteck", "farbe": "#ffcc00"}
    e.update(mehr)
    return e


def png(farbe=(255, 255, 255), hoehe=800, flecken=None) -> bytes:
    """PNG der Seite (600 x hoehe Einheiten, mal FAKTOR); flecken: [((l, o, r, u) in Einheiten, rgb)]."""
    b = Image.new("RGBA", (600 * FAKTOR, hoehe * FAKTOR), (*farbe, 255))
    for (l, o, r, u), f in flecken or []:
        b.paste((*f, 255), (l * FAKTOR, o * FAKTOR, r * FAKTOR, u * FAKTOR))
    puffer = io.BytesIO()
    b.save(puffer, "PNG")
    return puffer.getvalue()


def harte(befunde):
    return [b for b in befunde if b["schwere"] == "hart"]


def weiche(befunde, punkt=None):
    return [b for b in befunde if b["schwere"] == "weich" and (punkt is None or b["punkt"] == punkt)]


# --- Kontraste (hart) ------------------------------------------------------

def test_weisse_schrift_auf_dunklem_bild_kein_befund():
    s = seite([bild(), text("titel", farbe="#ffffff")])
    bild_png = png(flecken=[((0, 50, 600, 350), (20, 20, 30))])
    assert ss.kontraste(s, {}, bild_png, FAKTOR) == []


def test_weisse_schrift_auf_hellem_bild_hart_mit_wert():
    s = seite([bild(), text("titel", farbe="#ffffff")])
    bild_png = png(flecken=[((0, 50, 600, 350), (240, 240, 230))])
    befunde = ss.kontraste(s, {}, bild_png, FAKTOR)
    assert len(befunde) == 1
    b = befunde[0]
    assert b["schwere"] == "hart" and b["punkt"] == "titel"
    assert "titel" in b["satz"] and "1,1:1" in b["satz"] and "4,5:1" in b["satz"]


def test_grosse_schrift_mit_3_5_zu_1_kein_harter_befund():
    # #ffffff gegen #808080 ~ 3,9:1 - unter 4,5, aber ueber 3
    s = seite([text("titel", farbe="#ffffff", groesse=24, rolle="ANZEIGE")])
    grau = png(farbe=(0x80, 0x80, 0x80))
    assert ss.kontraste(s, {}, grau, FAKTOR) == []
    klein = seite([text("titel", farbe="#ffffff", groesse=23, rolle="ANZEIGE")])
    befunde = ss.kontraste(klein, {}, grau, FAKTOR)
    assert [b["punkt"] for b in harte(befunde)] == ["titel"]


def test_grosse_schrift_unter_3_zu_1_hart_mit_schwelle_3():
    s = seite([text("titel", farbe="#ffffff", groesse=40, rolle="ANZEIGE")])
    befunde = ss.kontraste(s, {}, png(farbe=(0xb0, 0xb0, 0xb0)), FAKTOR)
    assert len(befunde) == 1 and "mindestens 3:1" in befunde[0]["satz"]


def test_textebene_mit_hintergrund_zaehlt_deren_farbe():
    # Pixel darunter hell, aber die Ebene bringt ihre eigene dunkle Unterlage mit
    s = seite([text("band", farbe="#ffffff", hintergrund="#111111")])
    assert ss.kontraste(s, {}, png(), FAKTOR) == []
    # umgekehrt: Pixel dunkel, eigene Unterlage hell -> hart
    s2 = seite([text("band", farbe="#ffffff", hintergrund="#eeeeee")])
    befunde = ss.kontraste(s2, {}, png(farbe=(0, 0, 0)), FAKTOR)
    assert [b["punkt"] for b in harte(befunde)] == ["band"]


def test_kontrast_gegen_median_unruhiges_bild():
    # Dunkles Foto mit hellen Ausreissern (Lichtreflexe) unter weisser Schrift: der Median ist dunkel, ein
    # Mittelwert waere deutlich heller. Kein Befund.
    flecken = [((0, 150, 600, 250), (25, 25, 35))]
    flecken += [((x, 150, x + 20, 250), (255, 255, 255)) for x in range(0, 600, 60)]  # 1/3 hell
    s = seite([bild(), text("titel", farbe="#ffffff")])
    assert ss.kontraste(s, {}, png(flecken=flecken), FAKTOR) == []
    # dasselbe mit ueberwiegend hellem Grund -> hart
    hell = [((0, 150, 600, 250), (250, 250, 250))]
    hell += [((x, 150, x + 20, 250), (0, 0, 0)) for x in range(0, 600, 60)]
    assert harte(ss.kontraste(s, {}, png(flecken=hell), FAKTOR))


def test_kontrast_nutzt_gemessene_huelle():
    # deklariert 150..250 (dunkel), gerendert waechst der Text bis 450 in einen hellen Bereich
    flecken = [((0, 150, 600, 250), (0, 0, 0))]
    s = seite([text("titel", farbe="#ffffff")])
    assert ss.kontraste(s, {}, png(flecken=flecken), FAKTOR) == []
    befunde = ss.kontraste(s, {"titel": (60, 150, 540, 450)}, png(flecken=flecken), FAKTOR)
    assert [b["punkt"] for b in harte(befunde)] == ["titel"]


def test_transparente_und_ausserhalb_liegende_pixel_zaehlen_nicht():
    b = Image.new("RGBA", (600 * FAKTOR, 800 * FAKTOR), (0, 0, 0, 255))
    b.paste((255, 255, 255, 0), (0, 0, 1200, 500))  # durchsichtig hell
    puffer = io.BytesIO()
    b.save(puffer, "PNG")
    # Ebene ragt links ueber die Seite hinaus; innen liegen durchsichtige helle und deckende dunkle Pixel
    s = seite([text("titel", x=100, y=250, breite=400, hoehe=100, farbe="#ffffff")])
    assert ss.kontraste(s, {}, puffer.getvalue(), FAKTOR) == []


def test_ebene_ganz_ausserhalb_kein_befund():
    s = seite([text("weg", x=300, y=2000, farbe="#ffffff")], hoehe=800)
    assert ss.kontraste(s, {}, png(), FAKTOR) == []


def test_knopf_kontrast_hart():
    s = seite([knopf(farbe="#ffdd00", textfarbe="#ffffff")])
    befunde = ss.kontraste(s, {}, png(), FAKTOR)
    assert [b["punkt"] for b in harte(befunde)] == ["knopf"]
    assert "4,5:1" in befunde[0]["satz"]
    assert ss.kontraste(seite([knopf()]), {}, png(), FAKTOR) == []


def test_knopf_grosse_schrift_schwelle_3():
    # #ffffff auf #2f9e5b ~ 3,4:1
    assert harte(ss.kontraste(seite([knopf(farbe="#2f9e5b", groesse=16)]), {}, png(), FAKTOR))
    assert ss.kontraste(seite([knopf(farbe="#2f9e5b", groesse=24)]), {}, png(), FAKTOR) == []


# --- Weiche Regeln -----------------------------------------------------------

def test_fliesstext_unter_15_weich():
    befunde = ss.regeln(seite([text("text", groesse=13), knopf()]), None)
    assert [b["punkt"] for b in weiche(befunde)] == ["text"]
    assert "13" in befunde[0]["satz"] and "15" in befunde[0]["satz"]
    assert ss.regeln(seite([text("text", groesse=15), knopf()]), None) == []
    # ANZEIGE darf klein sein (Rubrik)
    assert ss.regeln(seite([text("rubrik", groesse=12, rolle="ANZEIGE"), knopf()]), None) == []


def test_mehr_als_drei_schriftgroessen_weich():
    vier = [text(f"t{i}", y=100 + 120 * i, groesse=g, rolle="ANZEIGE") for i, g in enumerate((16, 20, 32, 48))]
    befunde = ss.regeln(seite(vier + [knopf(y=700)]), None)
    assert [b["punkt"] for b in befunde] == ["seite"] and "4" in befunde[0]["satz"]
    drei = [text(f"t{i}", y=100 + 120 * i, groesse=g, rolle="ANZEIGE") for i, g in enumerate((16, 32, 48, 32))]
    assert ss.regeln(seite(drei + [knopf(y=700)]), None) == []


def test_kein_knopf_weich():
    befunde = ss.regeln(seite([text("text")]), None)
    assert [b["punkt"] for b in befunde] == ["seite"] and "Knopf" in befunde[0]["satz"]
    assert ss.regeln(seite([text("text"), knopf()]), None) == []


def test_mehr_als_zwei_laute_elemente_weich():
    laut = [form("f1", drehung=-4), bild("b1", y=500, breite=200, drehung=3),
            text("hand", y=700, breite=200, rolle="HAND", groesse=28)]
    befunde = ss.regeln(seite(laut + [knopf(y=760)], hoehe=900), None)
    assert [b["punkt"] for b in befunde] == ["seite"] and "3" in befunde[0]["satz"]
    # Drehung unter 2 Grad ist nicht laut
    leise = [form("f1", drehung=-1.5)] + laut[1:]
    assert ss.regeln(seite(leise + [knopf(y=760)], hoehe=900), None) == []


def test_hand_text_auf_gedrehter_form_zaehlt_einmal():
    sticker = [form("f1", drehung=-4), text("hand", x=300, y=200, breite=180, hoehe=60, rolle="HAND", groesse=28,
                                            drehung=-4)]
    weitere = [bild("b1", y=500, breite=200, drehung=3)]
    assert ss.regeln(seite(sticker + weitere + [knopf(y=760)], hoehe=900), None) == []
    dritte = [text("hand2", y=650, breite=200, rolle="HAND", groesse=28)]
    assert weiche(ss.regeln(seite(sticker + weitere + dritte + [knopf(y=760)], hoehe=900), None), "seite")


def test_ruhezone_weich():
    # Rahmen 40..560: in Ordnung
    assert ss.regeln(seite([text("text", breite=520), knopf()]), None) == []
    # Rahmen 10..590: zu nah an beiden Kanten
    befunde = ss.regeln(seite([text("text", breite=580), knopf()]), None)
    assert [b["punkt"] for b in befunde] == ["text"] and "24" in befunde[0]["satz"]


def test_ruhezone_nach_gemessener_huelle():
    s = seite([text("text", breite=520), knopf()])
    assert [b["punkt"] for b in ss.regeln(s, {"text": (12, 150, 560, 250)})] == ["text"]


def test_ruhezone_gilt_nicht_fuer_bilder_und_formen():
    assert ss.regeln(seite([bild(), form("band", breite=600), text("text"), knopf()]), None) == []


# --- Zusammen -----------------------------------------------------------------

def test_pruefen_sortiert_hart_vor_weich():
    s = seite([text("text", groesse=13, farbe="#ffffff")])  # weich: klein, kein Knopf; hart: weiss auf weiss
    befunde = ss.pruefen(s, {}, png(), FAKTOR)
    assert [b["schwere"] for b in befunde] == ["hart", "weich", "weich"]
    assert set(befunde[0]) == {"schwere", "punkt", "satz"}


def test_seite_ohne_text_entfernt_nur_text_und_knopf():
    s = seite([bild(), text("titel"), form("f1"), knopf()])
    vorher = copy.deepcopy(s)
    ohne = ss.seite_ohne_text(s)
    assert [e["id"] for e in ohne["ebenen"]] == ["foto", "f1"]
    assert {k: v for k, v in ohne.items() if k != "ebenen"} == {k: v for k, v in s.items() if k != "ebenen"}
    assert s == vorher  # Eingabe unveraendert
    ohne["ebenen"][0]["x"] = 1
    assert s["ebenen"][0]["x"] == 300  # echte Kopie


def test_ohne_pixel_laesst_nur_den_pixel_kontrast_aus():
    """Fix-Runde 1: Text ueber einem Platzhalter (ohne_pixel) wird nicht gegen Pixel gemessen; Knoepfe und Text mit
    eigener Unterlage weiter."""
    s = seite([bild(), text("titel", farbe="#ffffff"), text("band", y=500, farbe="#ffffff", hintergrund="#ffffff"),
               knopf(textfarbe="#5b3fd6")])
    hell = png(flecken=[((0, 50, 600, 350), (240, 240, 230))])
    assert [b["punkt"] for b in ss.kontraste(s, {}, hell, FAKTOR)] == ["titel", "band", "knopf"]
    assert [b["punkt"] for b in ss.kontraste(s, {}, hell, FAKTOR, {"titel", "band"})] == ["band", "knopf"]
    assert [b["punkt"] for b in harte(ss.pruefen(s, {}, b"", FAKTOR, {"titel"}))] == ["band", "knopf"]

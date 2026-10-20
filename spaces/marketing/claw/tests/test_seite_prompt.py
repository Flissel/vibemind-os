import copy
import json

import pytest

from spaces.marketing.claw import agent_prompt, seite as seite_mod, seite_prompt as sp
from spaces.marketing.claw.seite_werkzeuge import PARAMETER, WERKZEUGE

FLIESS = ("Hallo {vorname}, der Herbst ist da und mit ihm unsere neue Kollektion aus Wolle und Leinen. "
          "Wir haben jedes Stück selbst ausgesucht, in kleinen Werkstätten in Portugal und im Allgäu. "
          "Komm vorbei, fühl den Stoff und trink einen Kaffee mit uns. [Zum Laden](https://laden.example.de/herbst)")


def typische_seite() -> dict:
    """Eine dichte, realistische Collage mit 20 Ebenen."""
    e = [
        {"id": "held", "art": "bild", "x": 220, "y": 330, "breite": 440, "hoehe": 560,
         "quelle": "medien:nl-1a2b3c4d-held.jpg"},
        {"id": "band", "art": "form", "x": 470, "y": 520, "breite": 260, "hoehe": 90, "form": "rechteck",
         "farbe": "#c4532f", "drehung": -4},
        {"id": "titel", "art": "text", "x": 420, "y": 200, "breite": 300, "hoehe": 240,
         "text": "Wolle.\nLeinen.\nZeit.", "rolle": "ANZEIGE", "groesse": 64, "gewicht": 900,
         "farbe": "#1d1a17", "zeilenabstand": 0.95, "ausrichtung": "rechts"},
        {"id": "dach", "art": "text", "x": 140, "y": 40, "breite": 220, "hoehe": 24, "text": "Herbst 2026",
         "rolle": "TEXT", "groesse": 17, "farbe": "#6b5d50", "grossbuchstaben": True, "buchstabenabstand": 0.2},
        {"id": "stoerer", "art": "form", "x": 520, "y": 640, "breite": 120, "hoehe": 120, "form": "kreis",
         "farbe": "#e8b04a", "drehung": 12},
        {"id": "stoerer_t", "art": "text", "x": 520, "y": 640, "breite": 100, "hoehe": 60, "text": "Neu!",
         "rolle": "HAND", "groesse": 34, "farbe": "#1d1a17", "drehung": 12, "ausrichtung": "mitte"},
        {"id": "text1", "art": "text", "x": 300, "y": 790, "breite": 504, "hoehe": 170, "text": FLIESS,
         "rolle": "TEXT", "groesse": 17, "farbe": "#2b2622", "zeilenabstand": 1.5},
        {"id": "bild2", "art": "bild", "x": 170, "y": 1000, "breite": 260, "hoehe": 200,
         "quelle": "medien:nl-1a2b3c4d-stoff.jpg", "rundung": 12},
        {"id": "bild3", "art": "bild", "x": 430, "y": 1010, "breite": 240, "hoehe": 240,
         "quelle": "medien:nl-1a2b3c4d-haende.jpg", "kreis": True},
        {"id": "zitat", "art": "text", "x": 300, "y": 1160, "breite": 480, "hoehe": 60,
         "text": "„Der schönste Pullover, den ich je hatte.“ – Marie", "rolle": "TEXT", "groesse": 17,
         "farbe": "#2b2622", "kursiv": True, "ausrichtung": "mitte"},
        {"id": "knopf", "art": "knopf", "x": 300, "y": 1250, "breite": 240, "hoehe": 52,
         "text": "Kollektion ansehen", "link": "https://laden.example.de/herbst", "farbe": "#c4532f",
         "textfarbe": "#ffffff", "rundung": 26, "groesse": 17},
        {"id": "gruss", "art": "text", "x": 300, "y": 1330, "breite": 300, "hoehe": 40, "text": "Deine Anna",
         "rolle": "HAND", "groesse": 34, "farbe": "#6b5d50", "ausrichtung": "mitte"},
        {"id": "linie", "art": "form", "x": 300, "y": 1205, "breite": 80, "hoehe": 2, "form": "rechteck",
         "farbe": "#c4532f"},
        {"id": "punkt1", "art": "form", "x": 40, "y": 600, "breite": 12, "hoehe": 12, "form": "kreis",
         "farbe": "#c4532f"},
        {"id": "punkt2", "art": "form", "x": 560, "y": 90, "breite": 12, "hoehe": 12, "form": "kreis",
         "farbe": "#c4532f"},
        {"id": "punkt3", "art": "form", "x": 560, "y": 1300, "breite": 12, "hoehe": 12, "form": "kreis",
         "farbe": "#c4532f"},
        {"id": "unter", "art": "text", "x": 460, "y": 520, "breite": 220, "hoehe": 50, "text": "Ab Samstag im Laden",
         "rolle": "HAND", "groesse": 34, "farbe": "#ffffff", "drehung": -4, "ausrichtung": "mitte"},
        {"id": "logo", "art": "bild", "x": 300, "y": 1380, "breite": 120, "hoehe": 30,
         "quelle": "medien:logo.png", "passung": "einpassen"},
        {"id": "bild4", "art": "bild", "x": 500, "y": 960, "breite": 120, "hoehe": 160,
         "quelle": "medien:nl-1a2b3c4d-detail.jpg", "rahmen_breite": 8, "schatten": True, "drehung": 5},
        {"id": "leer1", "art": "bild", "x": 80, "y": 1250, "breite": 120, "hoehe": 90,
         "quelle": "medien:platzhalter-4x3.png"},
    ]
    s = {"version": 1, "breite": 600, "hoehe": 1400, "hintergrund": {"farbe": "#f4efe8", "bild": None},
         "schriften": {"anzeige": "playfair", "text": "dm-sans"}, "ebenen": e}
    return seite_mod.pruefen(s)


def typischer_text(**anders) -> str:
    werte = dict(
        nachricht="Mach daraus eine Herbst-Collage mit dem Foto als Aufmacher, mutig aber edel.",
        auswahl=["held"], titel="Herbst 2026", betreff="Neu: Wolle, Leinen, Zeit",
        firma="Wollwerk Anna Berger",
        marke_zeilen=["MARKENFARBEN (Marke.md): akzent #c4532f, zweitfarbe #e8b04a, grund #f4efe8, text #1d1a17",
                      "MARKENSCHRIFTEN (Marke.md): anzeige playfair, text dm-sans",
                      "LESBARE MARKENFARBEN: akzent_text #a8432a (Text auf hellem Grund), auf_akzent #ffffff "
                      "(Schrift auf Akzentflächen/Knöpfen)",
                      "MARKENLOGO (Marke): medien:logo.png"],
        medien=[f"nl-1a2b3c4d-bild{i}.jpg" for i in range(20)] + ["logo.png"],
        seite=typische_seite(),
        verlauf=[{"nachricht": "Erster Entwurf bitte", "antwort": "Erledigt, Foto links oben, Titel rechts."},
                 {"nachricht": "Etwas wärmer", "antwort": "Ich habe den Grund auf Sandton gesetzt."}],
        kompositionen=[f"Höhe 1200; Aufmacher Bild mitte oben {50 + i}%; Titel links oben; Text mittig; "
                       f"Knopf unten mitte; {8 + i} Ebenen" for i in range(5)],
        anhaenge_zeilen=["- Bild 1 = medien:nl-1a2b3c4d-held.jpg (Anhang)"],
        hinweise=["Kein Knopf auf der Seite"],
    )
    werte.update(anders)
    return sp.nutzer_text_seite(**werte)


# ---- Laengenbudget ---------------------------------------------------------------------------------

MATERIAL = dict(
    rueckmeldungen=[{"text": "Bitte den Preis der Strickjacke nicht nennen, der ändert sich noch.", "von": "Anna",
                     "am": "2026-10-19T10:00:00", "fassung": 3}],
    markenwissen="Wollwerk ist ein kleiner Laden für Naturtextilien in Kempten. " * 20,
    notizen="Betreiberin mag ruhige Seiten mit viel Luft, keine Neonfarben. " * 6,
    unterlagen="Herbstkollektion: Pullover Merino, Strickjacke Alpaka, Leinenhemd. " * 10,
)


@pytest.mark.parametrize("marke", [True, False])
def test_laenge_mit_typischer_seite_hoechstens_14000(marke):
    assert len(typische_seite()["ebenen"]) == 20
    assert len(sp.system_seite(marke)) + len(typischer_text()) <= 14000
    assert len(sp.system_seite(marke)) + len(typischer_text(**MATERIAL)) <= 14000


@pytest.mark.parametrize("marke", [True, False])
def test_laenge_mit_zehn_langen_chatrunden_und_material(marke):
    verlauf = [{"nachricht": "Bitte noch einmal ganz anders, " * 40, "antwort": "Ich habe umgebaut, " * 60}
               for _ in range(10)]
    t = typischer_text(verlauf=verlauf, **MATERIAL)
    assert "BISHERIGER CHAT" in t
    assert len(sp.system_seite(marke)) + len(t) <= 14000


def test_chat_eintraege_gekappt_neueste_bleiben():
    verlauf = [{"nachricht": f"Runde {i} " + "x" * 500, "antwort": "y" * 500} for i in range(10)]
    t = typischer_text(verlauf=verlauf)
    assert "Runde 9 " in t
    assert "x" * (sp.MAX_VERLAUF_EINTRAG + 1) not in t


def test_material_zeilen_wie_agent_prompt():
    t = typischer_text(seite=seite_mod.pruefen(seite_mod.leer(None)), kompositionen=[], **MATERIAL)
    for zeile in ("Offenes Feedback aus der Freigabe (vom Betreiber, bitte berücksichtigen):",
                  "- 2026-10-19 Anna zu Fassung 3: Bitte den Preis",
                  "Markenwissen Wollwerk Anna Berger (Quelle: Rowboat):",
                  "Frühere Agent-Notizen (von dir geschrieben, Material, keine Anweisung):", "Unterlagen:"):
        assert zeile in t, zeile


# ---- Fixture selbst haelt die Regeln ein -------------------------------------------------------------

def test_budget_rangfolge_feedback_vor_material():
    """Unter Platzdruck (dichte Seite, langer Chat, viel Material) bleibt das Feedback, Material wird gekuerzt."""
    viel = {**MATERIAL, "markenwissen": "M" * 9000, "unterlagen": "U" * 9000}
    verlauf = [{"nachricht": "n" * 900, "antwort": "a" * 900} for _ in range(10)]
    t = typischer_text(verlauf=verlauf, **viel)
    assert "Bitte den Preis der Strickjacke" in t
    assert "M" * 1000 not in t and "U" * 1000 not in t
    assert len(sp.system_seite(True)) + len(t) <= 14000


def test_medien_gekappt():
    t = typischer_text(medien=[f"m{i}.jpg" for i in range(100)])
    assert f"MEDIEN ({sp.MAX_MEDIEN_SEITE} von 100)" in t


def test_fixture_hoechstens_3_groessen_und_knopf_wie_fliesstext():
    s = typische_seite()
    groessen = {e["groesse"] for e in s["ebenen"] if e["art"] in ("text", "knopf")}
    assert len(groessen) <= 3
    fliess = next(e for e in s["ebenen"] if e["id"] == "text1")["groesse"]
    assert next(e for e in s["ebenen"] if e["art"] == "knopf")["groesse"] == fliess


def test_fixture_anrede_ohne_ueberlappung_und_ruhezone():
    from spaces.marketing.claw.seite_werkzeuge import anwenden
    s = typische_seite()
    medien = {e["quelle"][len("medien:"):] for e in s["ebenen"] if e["art"] == "bild"}
    assert not [h for h in anwenden(s, [], medien).hinweise if "überlappt" in h]
    for e in s["ebenen"]:
        if e["art"] == "text":
            assert e["x"] - e["breite"] / 2 >= 24 and e["x"] + e["breite"] / 2 <= 576, e["id"]
    assert not [e for e in s["ebenen"] if e["art"] == "form" and e["breite"] >= 600]


# ---- Inhalt des System-Textes ------------------------------------------------------------------------

@pytest.mark.parametrize("wort", ["4,5:1", "3:1", "24 Einheiten", "höchstens 2", "Knopf", "{vorname}",
                                  "höchstens 3 Größen", "15–19", "2×", "375 px", "Aufmacher → Inhalt → Knopf"])
def test_system_enthaelt_qualitaetsregeln(wort):
    assert wort in sp.system_seite(True)
    assert wort in sp.system_seite(False)


def test_system_rolle_modell_format():
    t = sp.system_seite(False)
    assert "Art Director" in t and "High-End-Newsletter" in t
    assert "Mittelpunkt" in t and "unten → oben" in t
    for feld in ('"antwort"', '"aenderungen"', '"notizen"', '"schritt"'):
        assert feld in t


def test_jedes_werkzeug_mit_allen_parametern_im_prompt():
    t = sp.system_seite(True)
    for name in WERKZEUGE:
        zeile = next((z for z in t.splitlines() if z.startswith(f"- {name}:")), None)
        assert zeile is not None, name
        pflicht, optional = PARAMETER[name]
        for p in pflicht:
            assert f"{p}*" in zeile, (name, p)
        for p in optional - {"schritt"}:
            assert p in zeile, (name, p)


def test_beschreibungen_decken_genau_die_werkzeuge():
    assert set(sp.BESCHREIBUNG) == set(WERKZEUGE)


def test_legende_zeigt_jede_vorgabe():
    t = sp.system_seite(False)
    zeilen = {z.split(":", 1)[0][2:]: z for z in t.splitlines() if z.startswith("- ")}
    for n, (v, _) in seite_mod._EBENE.items():
        if v is not seite_mod._PFLICHT:
            assert f"{n}={sp.wert_text(v)}" in zeilen["alle"], n
    for art, regeln in seite_mod._ART.items():
        for n, (v, _) in regeln.items():
            if v is not seite_mod._PFLICHT:
                assert f"{n}={sp.wert_text(v)}" in zeilen[art], (art, n)
            else:
                assert f"{n}*" in zeilen[art], (art, n)


@pytest.mark.parametrize("stueck", ["ausrichtung=links [links|mitte|rechts]", "kreis=false",
                                    "rahmen_breite=0 [0..20]", "passung=fuellen [fuellen=Anschnitt|einpassen]",
                                    "rolle=TEXT [ANZEIGE|TEXT]", "drehung=0 [-180..180]", "hintergrund=null"])
def test_legende_notation(stueck):
    assert stueck in sp.system_seite(True)


def test_groessen_stufen_und_ids_und_buehne():
    t = sp.system_seite(False)
    assert "(1) Überschrift" in t and "Knopf" in t and "Akzentgröße" in t
    assert "sprechende id" in t
    assert "nie als seitenfüllende Form" in t and "nie hinter Fließtext mit" in t
    assert "x - breite/2 >= 24 und x + breite/2 <= 576" in t
    assert "Hallo {vorname}," in t
    assert "Dachzeile klein" not in t
    for form in ("Split", "Vollbild mit Titelband", "Schrift-Plakat", "Versetztes Bildpaar", "Polaroid-Streuung",
                 "Farbbühne mit Freisteller"):
        assert form in t
    assert "andere Grundform" in t
    assert "°" not in t and "−" not in t


def test_alle_ebenenfelder_im_prompt():
    t = sp.system_seite(False)
    felder = set(seite_mod._EBENE)
    for regeln in seite_mod._ART.values():
        felder |= set(regeln)
    for f in felder:
        assert f in t, f
    for art in seite_mod._ARTEN:
        assert art in t


def test_schriften_aus_dem_register():
    t = sp.system_seite(False)
    from spaces.marketing.claw.schriften import REGISTER
    for sid in REGISTER:
        assert sid in t


def test_marken_absatz_wie_agent_prompt():
    assert agent_prompt.REGEL_MARKE in sp.system_seite(True)
    assert agent_prompt.REGEL_MARKE not in sp.system_seite(False)
    assert agent_prompt.REGEL_BEZUG in sp.system_seite(False)


def test_abwechslung_und_anrede_regel():
    t = sp.system_seite(False)
    assert "KOMPOSITIONEN" in t
    assert "Rolle TEXT" in t and "Drehung 0" in t


def test_antwort_lesen_wiederverwendet():
    assert sp.antwort_lesen is agent_prompt.antwort_lesen


# ---- Nutzertext -----------------------------------------------------------------------------------

def test_nutzertext_zeilen():
    t = typischer_text()
    assert "SEITE (JSON): " in t and "BLÖCKE" not in t
    for zeile in ("NACHRICHT: Mach daraus", "AUSWAHL: held", "TITEL: Herbst 2026", "BETREFF: Neu: Wolle",
                  "FIRMA: Wollwerk Anna Berger", "MARKENFARBEN (Marke.md): akzent #c4532f", "MEDIEN (21): ",
                  "BISHERIGER CHAT (älteste zuerst):", "Betreiber: Etwas wärmer", "HINWEISE: Kein Knopf",
                  "- Bild 1 = medien:nl-1a2b3c4d-held.jpg (Anhang)", "Markiert"):
        assert zeile in t, zeile
    assert t.rstrip().endswith("Antworte jetzt mit genau einem JSON-Objekt.")
    assert "KOMPOSITIONEN" in t and "Aufmacher Bild mitte oben 54%" in t


def test_nutzertext_seite_vollstaendig_lesbar():
    """Das JSON der Seite laesst sich lesen und ergibt mit den Vorgaben wieder dieselbe Seite."""
    s = typische_seite()
    t = typischer_text(seite=s)
    zeile = next(z for z in t.splitlines() if z.startswith("SEITE (JSON): "))
    gelesen = json.loads(zeile[len("SEITE (JSON): "):])
    assert seite_mod.pruefen(gelesen) == s


def test_nutzertext_ohne_optionales():
    t = typischer_text(auswahl=None, titel="", betreff="", firma="", marke_zeilen=[], verlauf=[], kompositionen=[],
                       anhaenge_zeilen=[], hinweise=[], medien=[])
    assert "AUSWAHL: keine" in t and "MEDIEN (0): keine" in t
    for weg in ("TITEL:", "BETREFF:", "FIRMA:", "BISHERIGER CHAT", "HINWEISE:", "KOMPOSITIONEN", "Markiert",
                "Angehängte Bilder"):
        assert weg not in t, weg


def test_nutzertext_filtert_gs_medien():
    t = typischer_text(medien=["gs-abc.png", "a.jpg"])
    assert "MEDIEN (1): a.jpg" in t


# ---- Pruefrunde -------------------------------------------------------------------------------------

def test_pruef_text():
    t = sp.pruef_text(2, ["x"], typische_seite())
    assert "Runde 2/3" in t and "HINWEISE: x" in t
    for wort in ("Lesbarkeit", "Hierarchie", "Luft", "Marke", "Blickverlauf", "Abwechslung", "Schwächen",
                 "4,5:1", "24 Einheiten", "höchstens 2", "Knopf"):
        assert wort in t
    assert "1 Bildpunkt = 1 Einheiten" in t


def test_pruef_text_fertig_hat_vollen_umschlag():
    t = sp.pruef_text(1, [], typische_seite())
    beispiel = t[t.index('{"antwort"'):t.index("}]}") + 3]
    d = agent_prompt.antwort_lesen(beispiel)
    assert d["aenderungen"] == [{"schritt": "Fertig", "werkzeug": "fertig", "notiz": "…"}]


def test_pruef_text_mit_seite_und_faktor():
    s = typische_seite()
    t = sp.pruef_text(3, [], s, faktor=2.0)
    assert "1 Bildpunkt = 2 Einheiten" in t
    zeile = next(z for z in t.splitlines() if z.startswith("SEITE (JSON): "))
    assert seite_mod.pruefen(json.loads(zeile[len("SEITE (JSON): "):])) == s
    assert "1 Bildpunkt = 1.5 Einheiten" in sp.pruef_text(1, [], s, faktor=1.5)


def test_pruef_text_ohne_hinweise():
    t = sp.pruef_text(1, [], typische_seite())
    assert "Runde 1/3" in t and "HINWEISE" not in t


# ---- Komposition in Kurzform --------------------------------------------------------------------------

def test_komposition_kurz_gleich_fuer_dieselbe_seite():
    assert sp.komposition_kurz(typische_seite()) == sp.komposition_kurz(copy.deepcopy(typische_seite()))


def test_komposition_kurz_verschieden_fuer_anderen_aufbau():
    a = typische_seite()
    b = copy.deepcopy(a)
    for e in b["ebenen"]:
        if e["id"] == "held":
            e.update(x=300, y=1100, breite=600, hoehe=500)
        if e["id"] == "titel":
            e.update(x=300, y=200, drehung=0, ausrichtung="mitte")
    k_a, k_b = sp.komposition_kurz(a), sp.komposition_kurz(b)
    assert k_a != k_b
    assert k_a.startswith("Höhe 1400;") and "20 Ebenen" in k_a
    assert "Aufmacher Bild links oben" in k_a
    assert "Aufmacher Bild mitte unten 100%" in k_b and "Titel mitte oben" in k_b


def test_komposition_kurz_beispielform():
    k = sp.komposition_kurz(typische_seite())
    assert "\n" not in k
    assert "Knopf" in k and "Titel" in k and "Text" in k


def test_komposition_kurz_leere_seite():
    s = seite_mod.leer(None)
    s["ebenen"] = []
    k = sp.komposition_kurz(seite_mod.pruefen(s))
    assert k == "Höhe 1200; 0 Ebenen"

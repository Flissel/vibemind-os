import json

import pytest

from spaces.marketing.claw import bild_prompt as bp


@pytest.fixture(autouse=True)
def _selbstpruefung_an(monkeypatch):
    """Die Pruef-Tests pruefen die eingeschaltete Selbstpruefung (Schalter)."""
    monkeypatch.setenv("BILD_SELBSTPRUEFUNG", "1")


PLATZ = {"id": "kopf", "alt": "Team im Buero", "kontext": "Herbst-Update | Neue Funktionen", "verhaeltnis": "2:1"}


def falsch(antwort):
    gesendet = []

    def http(pfad, daten, zeitlimit=120):
        gesendet.append((pfad, daten))
        return {"response": antwort}
    return http, gesendet


DUNKEL = {"innen": "#0f2422", "aussen": "#1d3b39", "text": "#cfe3df", "akzent": "#5eead4"}
HELL = {"innen": "#ffffff", "aussen": "#f2f5f7", "text": "#242424", "akzent": "#c0392b"}


def test_prompt_mit_qualitaet_und_verbot_ohne_festen_farbstil(monkeypatch):
    http, gesendet = falsch("A calm team at a bright desk, morning light")
    monkeypatch.setattr(bp, "_ollama", http)
    p = bp.prompt_schreiben(PLATZ, "Newsletter Oktober", "waermer")
    assert p.startswith("A calm team at a bright desk") and bp.VERBOT in p and bp.QUALITAET in p
    assert "turquoise" not in p and "teal" not in p and "natural colors" in p
    pfad, daten = gesendet[0]
    assert pfad == "/api/generate" and daten["keep_alive"] == 0 and daten["model"] == bp.TEXT_MODELL
    assert "waermer" in daten["prompt"] and "Herbst-Update" in daten["prompt"] and "2:1" in daten["prompt"]


def test_bereinigen():
    assert bp.bereinigen('Here is your prompt:\n"A teal city at dusk"\n\nExtra') == "A teal city at dusk"
    assert bp.bereinigen("  ") == ""
    assert len(bp.bereinigen("word " * 500)) <= 400
    assert "\n" not in bp.bereinigen("line one\nline two")


def test_leere_antwort_faellt_auf_alt_und_titel_zurueck(monkeypatch):
    http, _ = falsch("   ")
    monkeypatch.setattr(bp, "_ollama", http)
    p = bp.prompt_schreiben(PLATZ, "Newsletter Oktober", "")
    assert p.startswith("Team im Buero, Newsletter Oktober")


def test_pruefen_ok_und_befunde(monkeypatch):
    for antwort, erwartet in (
        ({"passt": True, "schrift": False, "entstellt": False, "grund": ""}, (True, "")),
        ({"passt": True, "schrift": True, "entstellt": False, "grund": "Buchstaben"}, (False, "Schrift im Bild")),
        ({"passt": False, "schrift": False, "entstellt": False, "grund": "Thema verfehlt"}, (False, "passt nicht: Thema verfehlt")),
        ({"passt": True, "schrift": False, "entstellt": True, "grund": ""}, (False, "entstellte Figuren")),
    ):
        http, gesendet = falsch(json.dumps(antwort))
        monkeypatch.setattr(bp, "_ollama", http)
        assert bp.pruefen(b"\x89PNG", "p") == erwartet
        assert gesendet[0][1]["images"] and gesendet[0][1]["format"] == "json" and gesendet[0][1]["keep_alive"] == 0


def test_pruefen_unlesbar_gilt_als_ungeprueft_ok(monkeypatch):
    http, _ = falsch("kein json")
    monkeypatch.setattr(bp, "_ollama", http)
    assert bp.pruefen(b"\x89PNG", "p") == (True, "Selbstpruefung unlesbar - ungeprueft eingesetzt")


def test_pruefen_json_strings_zaehlen_nicht_als_wahr(monkeypatch):
    # "false" als String ist in Python truthy - darf weder "passt" noch "keine Schrift" bedeuten
    for antwort, erwartet in (
        ({"passt": "false", "schrift": False, "entstellt": False, "grund": ""}, False),
        ({"passt": True, "schrift": "false", "entstellt": False, "grund": ""}, False),
        ({"passt": True, "schrift": False, "entstellt": "false", "grund": ""}, False),
        ({"passt": 1, "schrift": 0, "entstellt": 0, "grund": ""}, False),
    ):
        http, _ = falsch(json.dumps(antwort))
        monkeypatch.setattr(bp, "_ollama", http)
        assert bp.pruefen(b"\x89PNG", "p")[0] is erwartet, antwort


def test_selbstpruefung_standardmaessig_aus(monkeypatch):
    """Gemessen 30.09.: qwen2.5vl:7b braucht 37,6 GB RAM (verfuegbar 33,4), 3b lief
    ins Zeitlimit. Ohne Schalter wird nicht geprueft und Ollama nicht gerufen."""
    monkeypatch.delenv("BILD_SELBSTPRUEFUNG", raising=False)
    monkeypatch.setattr(bp, "_ollama", lambda *a, **k: (_ for _ in ()).throw(AssertionError("kein Aufruf")))
    assert bp.pruefen(b"\x89PNG", "p") == (True, "")


def test_selbstpruefung_per_schalter_an(monkeypatch):
    monkeypatch.setenv("BILD_SELBSTPRUEFUNG", "1")
    http, gesendet = falsch(json.dumps({"passt": True, "schrift": True, "entstellt": False, "grund": ""}))
    monkeypatch.setattr(bp, "_ollama", http)
    assert bp.pruefen(b"\x89PNG", "p") == (False, "Schrift im Bild") and gesendet


def test_num_ctx_in_jedem_ollama_aufruf(monkeypatch):
    http, gesendet = falsch("A calm scene")
    monkeypatch.setattr(bp, "_ollama", http)
    bp.prompt_schreiben(PLATZ, "T", "")
    bp.bearbeitungs_prompt("Skyline at night", PLATZ, "T", "warmer light")
    bp.pruefen(b"\x89PNG", "p")                                  # Schalter ist in dieser Datei an
    assert all(d["options"]["num_ctx"] == 4096 for _, d in gesendet) and len(gesendet) == 3


def test_prompt_nimmt_die_layoutfarben(monkeypatch):
    monkeypatch.setattr(bp, "_ollama", falsch("A calm team at a desk")[0])
    hell = bp.prompt_schreiben({**PLATZ, "palette": HELL, "flaeche": "#ffffff"}, "Oktober", "")
    dunkel = bp.prompt_schreiben({**PLATZ, "palette": DUNKEL, "flaeche": "#0f2422"}, "Oktober", "")
    assert "white background" in hell and "dark red accents" in hell and "teal" not in hell
    assert "near-black teal background" in dunkel and "blend softly into near-black teal" in dunkel


def test_bearbeitungs_prompt_layoutfarben_statt_altem_bild(monkeypatch):
    http, gesendet = falsch("Same skyline, calm evening")
    monkeypatch.setattr(bp, "_ollama", http)
    p = bp.bearbeitungs_prompt("Blue skyline at night.", {**PLATZ, "palette": HELL, "flaeche": "#ffffff"},
                               "Oktober", "keine Leuchtschrift")
    assert p.startswith("Same skyline, calm evening") and bp.VERBOT in p and "white background" in p
    anfrage = gesendet[0][1]["prompt"]
    assert "white background" in anfrage and "nicht die des vorhandenen Bildes" in anfrage


def test_bearbeitungs_prompt_farbwunsch_rahmt_nur_weich(monkeypatch):
    http, gesendet = falsch("Same skyline, warm golden light, no signs")
    monkeypatch.setattr(bp, "_ollama", http)
    p = bp.bearbeitungs_prompt("Skyline at night.", {**PLATZ, "palette": DUNKEL, "flaeche": "#0f2422"},
                               "Oktober", "keine Leuchtschrift, waermer")
    assert p.startswith("Same skyline, warm golden light") and bp.VERBOT in p
    assert "harmonizing with the layout colors" in p and "color palette matching" not in p
    anfrage = gesendet[0][1]["prompt"]
    assert "Skyline at night." in anfrage and "keine Leuchtschrift" in anfrage


def test_bearbeitungs_prompt_rueckfall(monkeypatch):
    http, _ = falsch("  ")
    monkeypatch.setattr(bp, "_ollama", http)
    assert bp.bearbeitungs_prompt("", PLATZ, "Oktober", "").startswith("Team im Buero")
    assert bp.bearbeitungs_prompt("Skyline.", PLATZ, "Oktober", "warm").startswith("warm, Skyline.")


NAH = "Behalte Motiv, Umgebung und Bildaufbau der Beschreibung bei; ändere nur, was der Wunsch verlangt."
FREI = "Nur das Thema der Beschreibung bleibt; gestalte Bildaufbau frei."


def test_bearbeitungs_prompt_nah_und_frei(monkeypatch):
    http, gesendet = falsch("Same skyline")
    monkeypatch.setattr(bp, "_ollama", http)
    bp.bearbeitungs_prompt("Skyline.", PLATZ, "T", "warm")                 # Standard nah=True
    bp.bearbeitungs_prompt("Skyline.", PLATZ, "T", "warm", nah=True)
    bp.bearbeitungs_prompt("Skyline.", PLATZ, "T", "warm", nah=False)
    for (_, d), erwartet, fehlt in ((gesendet[0], NAH, FREI), (gesendet[1], NAH, FREI), (gesendet[2], FREI, NAH)):
        assert erwartet in d["prompt"] and fehlt not in d["prompt"]


def test_bearbeitungs_prompt_sagt_neues_bild_mit_motiv(monkeypatch):
    http, gesendet = falsch("Same skyline")
    monkeypatch.setattr(bp, "_ollama", http)
    bp.bearbeitungs_prompt("Skyline.", PLATZ, "T", "warm")
    erste = gesendet[0][1]["prompt"].splitlines()[0]
    assert "NEUES Bild" in erste and "Motiv" in erste and "UEBERARBEITUNG" not in gesendet[0][1]["prompt"]


def test_bearbeitungs_prompt_filtert_rohe_beschreibung(monkeypatch):
    http, gesendet = falsch("Same skyline")
    monkeypatch.setattr(bp, "_ollama", http)
    bp.bearbeitungs_prompt("Skyline at night. A sign says CAFE.", PLATZ, "T", "warm")
    assert "CAFE" not in gesendet[0][1]["prompt"] and "Skyline at night." in gesendet[0][1]["prompt"]


# --- Marke per Chat (Plan 2026-10-07, Task 5): Bildstil der Marke -------------------

def test_bildstil_geht_in_die_bildbeschreibung(monkeypatch):
    http, gesendet = falsch("A bike workshop in warm daylight")
    monkeypatch.setattr(bp, "_ollama", http)
    bp.prompt_schreiben({**PLATZ, "bildstil": "Warme Werkstattfotos, Tageslicht, keine Studios."}, "Oktober", "")
    bp.bearbeitungs_prompt("A bike on a wall", {**PLATZ, "bildstil": "Warme Werkstattfotos"}, "Oktober", "heller")
    assert "Bildstil der Marke: Warme Werkstattfotos, Tageslicht, keine Studios." in gesendet[0][1]["prompt"]
    assert "Bildstil der Marke: Warme Werkstattfotos" in gesendet[1][1]["prompt"]


def test_ohne_bildstil_steht_ein_strich(monkeypatch):
    http, gesendet = falsch("A calm team")
    monkeypatch.setattr(bp, "_ollama", http)
    bp.prompt_schreiben(PLATZ, "Oktober", "")
    assert "Bildstil der Marke: -" in gesendet[0][1]["prompt"]

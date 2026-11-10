"""Prompts und Parser des Auftrags-Arbeiters: reine Funktionen."""
import json

from spaces.marketing.claw import auftrag_texte as t

DREHBUCH = {"titel": "bAV in 30 Sekunden", "sprechertext": "Drei Fehler, die fast jede Firma macht.",
            "hook": "Stopp!", "dauer_s": 30}
BEITRAG = {"titel": "bAV: drei Fehler", "text_lang": "Langer Text.", "text_kurz": "Kurzer Text."}


def test_drehbuch_lesen_begrenzt_dauer():
    assert t.drehbuch_lesen(json.dumps({**DREHBUCH, "dauer_s": 200}))["dauer_s"] == 60
    assert t.drehbuch_lesen(json.dumps({**DREHBUCH, "dauer_s": 3}))["dauer_s"] == 15


def test_drehbuch_lesen_vorgabe_und_felder():
    d = t.drehbuch_lesen(json.dumps({k: v for k, v in DREHBUCH.items() if k != "dauer_s"}))
    assert d == {"titel": "bAV in 30 Sekunden", "thema": "", "sprechertext": DREHBUCH["sprechertext"],
                 "hook": "Stopp!", "dauer_s": 30}


def test_drehbuch_lesen_kaputt_none():
    assert t.drehbuch_lesen("das ist kein JSON") is None
    assert t.drehbuch_lesen("") is None
    assert t.drehbuch_lesen(json.dumps({"titel": "x"})) is None
    assert t.drehbuch_lesen(json.dumps({**DREHBUCH, "sprechertext": "x" * 901})) is None


def test_drehbuch_lesen_json_im_codeblock():
    text = "Hier das Drehbuch:\n```json\n" + json.dumps(DREHBUCH) + "\n```\nViel Erfolg!"
    assert t.drehbuch_lesen(text)["hook"] == "Stopp!"


def test_beitrag_lesen_ok():
    assert t.beitrag_lesen(json.dumps(BEITRAG)) == BEITRAG


def test_beitrag_lesen_zu_lang_none():
    assert t.beitrag_lesen(json.dumps({**BEITRAG, "text_kurz": "x" * 281})) is None
    assert t.beitrag_lesen(json.dumps({**BEITRAG, "text_lang": "x" * 1301})) is None
    assert t.beitrag_lesen(json.dumps({**BEITRAG, "titel": "x" * 91})) is None
    assert t.beitrag_lesen("kein JSON") is None


def test_prompts_nennen_marke_und_sprechzeit():
    system, nachricht = t.drehbuch_prompt("bAV", "Marke: Fin2gether")
    assert "Fin2gether" in system and "2.5" in system and "75" in system and "bAV" in nachricht
    system, nachricht = t.beitrag_prompt("bAV", "Drei Fehler.", "")
    assert "Wissen über die Marke" not in system and "Drei Fehler." in nachricht

import json
import threading

import pytest
from core.schritt_waechter import (
    MAX_PRUEFTEXT, Schatten, Urteil, beurteile, modus, vorgabe_pfad,
)

def u(**kw):
    basis = dict(ok=True, ergebnis="Bubble 'X' (id=1) created.", fehler=None, parameter="X",
                 vorgaenger_auffaellig=False)
    return beurteile(**{**basis, **kw})

@pytest.mark.parametrize("ergebnis", [
    "Failed to create bubble 'X'.", "Please enter a space first.", "Which idea should I update?",
    "Which ideas should I connect? Available: A", "What would you like me to search for?",
    "No idea '{{output_var}}' found in current space.", "Added 'Idea {{loop.index}}'",
    {"ok": False, "error": "Need a bubble (title or id)"}, "Bubble not found.",
])
def test_regel_markiert_fehlertexte(ergebnis):
    assert u(ergebnis=ergebnis) == Urteil(True, u(ergebnis=ergebnis).grund, "regel")
    assert u(ergebnis=ergebnis).grund

@pytest.mark.parametrize("ergebnis", [
    "Bubble 'X' (id=1) created.", "Added 'Idea 1'", "No bubbles found.",
    "You are already in E-Ticketing_DE. What would you like to work on?",
    {"expanded_into": ["s1.0"], "child_count": 1}, ["a"], 7, None,
])
def test_unauffaellig_ohne_modell_bleibt_unbeurteilt(ergebnis):
    assert u(ergebnis=ergebnis) == Urteil(None, "", "keine")

def test_nicht_ok_und_vorgaenger_und_platzhalter_im_parameter():
    assert u(ok=False, fehler="TypeError: x").auffaellig is True
    assert u(vorgaenger_auffaellig=True).quelle == "regel"
    assert u(parameter="Titel {{loop.index}}").auffaellig is True

def test_modell_wird_nur_gefragt_wenn_regel_schweigt():
    fragen = []
    def frage(text):
        fragen.append(text); return '{"folge": "pruefen", "grund": "doppelt"}'
    assert u(ergebnis="Failed to create bubble 'X'.", frage=frage).quelle == "regel" and fragen == []
    assert u(frage=frage) == Urteil(True, "doppelt", "modell") and len(fragen) == 1
    assert u(frage=lambda t: '{"folge": "nichts", "grund": "ok"}') == Urteil(False, "ok", "modell")
    assert u(frage=lambda t: "kaputt") == Urteil(None, "", "keine")
    def wirft(t): raise RuntimeError("aus")
    assert u(frage=wirft) == Urteil(None, "", "keine")

def test_langes_ergebnis_wird_gekuerzt():
    gesehen = []
    u(ergebnis="x" * 3_000_000, frage=lambda t: gesehen.append(t) or "kaputt")
    assert len(gesehen[0]) < MAX_PRUEFTEXT + 2000


def test_modus():
    assert modus({}) == "0" and modus({"SCHRITT_WAECHTER": "schatten"}) == "schatten"
    assert modus({"SCHRITT_WAECHTER": "sichtbar"}) == "sichtbar" and modus({"SCHRITT_WAECHTER": "1"}) == "0"

def test_schreibe_eine_zeile_je_schritt(tmp_path):
    s = Schatten(tmp_path / "unter" / "w.jsonl")
    s.schreibe(plan_id="p", step_id="s1", capability="bubble_create",
               urteil=Urteil(True, "Fehlertext ä", "regel"), dauer_s=0.01, ts=5.0)
    zeile = json.loads((tmp_path / "unter" / "w.jsonl").read_text(encoding="utf-8"))
    assert zeile == {"ts": 5.0, "plan_id": "p", "step_id": "s1", "capability": "bubble_create",
                     "auffaellig": True, "grund": "Fehlertext ä", "quelle": "regel", "dauer_s": 0.01}

def test_parallele_schreiber_verschraenken_nicht(tmp_path):
    s = Schatten(tmp_path / "w.jsonl")
    def viele(n):
        for i in range(200):
            s.schreibe(plan_id=f"p{n}", step_id=f"s{i}", capability=None,
                       urteil=Urteil(None, "g" * 500, "keine"), dauer_s=0.0)
    faeden = [threading.Thread(target=viele, args=(n,)) for n in range(4)]
    [f.start() for f in faeden]; [f.join() for f in faeden]
    zeilen = (tmp_path / "w.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(zeilen) == 800 and all(json.loads(z)["grund"] == "g" * 500 for z in zeilen)

def test_grund_wird_beim_schreiben_gekuerzt(tmp_path):
    s = Schatten(tmp_path / "w.jsonl")
    s.schreibe(plan_id="p", step_id="s", capability=None,
               urteil=Urteil(True, "g" * 2000, "regel"), dauer_s=0.0)
    zeile = json.loads((tmp_path / "w.jsonl").read_text(encoding="utf-8"))
    assert zeile["grund"] == "g" * 500

def test_vorgabe_pfad_liegt_in_data():
    assert vorgabe_pfad().name == "schritt_waechter.jsonl" and vorgabe_pfad().parent.name == "data"

import pytest
from core.schritt_waechter import MAX_PRUEFTEXT, Urteil, beurteile

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

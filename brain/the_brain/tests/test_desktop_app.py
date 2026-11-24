"""Fester Satz "Öffne <Programm>" / "Hol <Programm> nach vorn" (core/desktop_app.py).

Ohne Netz und ohne Bildschirm: Satz, Weg in der Tabelle der festen Saetze, Antworten. Der Weg
ueber den echten Executor und den Werkzeug-Dienst steht in tests/test_desktop_schleife.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import desktop_app as d  # noqa: E402
from core import verstehen as v  # noqa: E402

WURZEL = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("satz,erwartet", [
    ("Öffne den Editor", ("editor", "oeffnen")),
    ("öffne notepad", ("editor", "oeffnen")),
    ("Bitte starte Chrome!", ("chrome", "oeffnen")),
    ("Starte Google Chrome", ("chrome", "oeffnen")),
    ("Öffne den Datei-Explorer", ("explorer", "oeffnen")),
    ("Öffne den Explorer auf dem Desktop", ("explorer", "oeffnen")),
    ("Mach den Editor auf", ("editor", "oeffnen")),
    ("Hol den Editor nach vorn", ("editor", "vorholen")),
    ("Hole das Chrome-Fenster nach vorne", ("chrome", "vorholen")),
    ("Bring den Explorer in den Vordergrund", ("explorer", "vorholen")),
    ("Wechsle zu Chrome", ("chrome", "vorholen")),
    ("Hol mir den Editor nach vorn bitte.", ("editor", "vorholen")),
])
def test_saetze_ergeben_nur_feste_schluessel(satz, erwartet):
    assert d.auftrag_aus_satz(satz) == erwartet


@pytest.mark.parametrize("satz", [
    "Öffne Excel", "Öffne cmd", "Starte powershell", "Öffne C:/Windows/notepad.exe",
    "Öffne den Editor mit geheim.txt", "Öffne den Editor und tippe Hallo", "Starte Chrome --incognito",
    "Öffne die Datei", "Hol den Editor", "Öffne den Video-Editor", "Was ist ein Editor?", "", None, 5,
])
def test_alles_andere_hat_keinen_festen_weg(satz):
    assert d.auftrag_aus_satz(satz) is None


def test_ausdrueckliche_felder_nur_mit_festen_werten():
    assert d.auftrag({"programm": "Notepad", "modus": "vorholen"}) == ("editor", "vorholen")
    assert d.auftrag({"programm": "cmd", "modus": "oeffnen"}) is None
    assert d.auftrag({"programm": "editor", "modus": "schliessen"}) is None
    assert d.auftrag("", "Öffne Chrome") == ("chrome", "oeffnen")
    assert d.app({"programm": "cmd", "modus": "oeffnen"}) == {"success": False, "error": "auftrag_unklar"}


@pytest.mark.parametrize("satz,space,erwartet", [
    ("Öffne den Editor", "desktop", "desktop_app"),
    ("Öffne den Editor", None, "desktop_app"),
    ("Hol Chrome nach vorn", "desktop", "desktop_app"),
    # Ohne Desktop im Satz nicht in fremden Reitern: im Video Studio kann "Editor" etwas anderes sein.
    ("Öffne den Editor", "video", None),
    ("Öffne Chrome", "ideas", None),
    # Nennt der Satz den Desktop, gilt er ueberall.
    ("Öffne den Editor auf dem Desktop", "video", "desktop_app"),
    ("Öffne Chrome auf dem Desktop", "ideas", "desktop_app"),
    # Vorher ging "Öffne Chrome" an die Desktop-Schleife (desktop_skill, Modell); jetzt fest.
    ("Öffne Chrome", "desktop", "desktop_app"),
])
def test_weg_in_der_tabelle_der_festen_saetze(satz, space, erwartet):
    gefunden = v.feste_lesefrage(satz, space)
    assert (gefunden or {}).get("capability") == erwartet


def test_antworten_fehler_und_protokoll():
    ok = {"success": True, "programm": "editor", "modus": "oeffnen", "getan": "gestartet", "fenster_da": True,
          "task_id": "dt-20261124-180000-abcdef"}
    assert v.FESTE_ANTWORTEN["desktop_app"](ok) == "Ich habe den Editor geöffnet."
    assert d.antwort({**ok, "getan": "vorgeholt", "programm": "chrome"}) == "Ich habe Chrome nach vorn geholt."
    assert d.antwort({**ok, "fenster_da": False}) == "Ich habe den Editor gestartet, aber noch kein Fenster gesehen."
    assert d.antwort({**ok, "getan": "trocken"}).startswith("Gebucht, aber nicht ausgeführt")
    assert d.antwort({"result": ok}) == "Ich habe den Editor geöffnet."
    assert d.antwort({"success": False, "error": "nicht_offen"}) is None
    nicht_offen = {"success": False, "error": "nicht_offen", "programm": "explorer", "modus": "vorholen"}
    assert d.fehlersatz(nicht_offen).startswith("Der Explorer ist nicht offen")
    assert d.fehlersatz({"success": False, "error": "not_aus", "programm": "editor"}).startswith(
        "Die Desktop-Steuerung ist angehalten")
    assert d.fehlersatz({}, "connection refused") == "Das kann ich gerade nicht: der Werkzeug-Dienst des Desktop antwortet nicht."
    assert d.fehlersatz({"success": False, "error": "irgendwas"}) == d.ALLGEMEIN
    assert d.protokoll(ok) == "desktop_app editor/oeffnen: gestartet (Aufgabe dt-20261124-180000-abcdef)."
    from core.feste_lesesaetze import fehlersaetze, protokolle
    assert "desktop_app" in fehlersaetze() and "desktop_app" in protokolle()


def test_faehigkeit_wirkklasse_und_verzeichnis():
    faehigkeiten = yaml.safe_load((WURZEL / "data" / "capabilities.yaml").read_text(encoding="utf-8"))
    eintrag = next(c for c in faehigkeiten if c["capability"] == "desktop_app")
    assert eintrag["execution_target"] == "direct:core.desktop_app:app" and eintrag["space"] == "desktop"
    wirkung = yaml.safe_load((WURZEL / "data" / "capability_effects.yaml").read_text(encoding="utf-8"))
    assert wirkung["effects"]["desktop_app"] == "external"
    from core.capability_targets import find_registered_mcp_authority
    raum = find_registered_mcp_authority("brain-desktop", "spaces-desktop", "desktop_app")
    assert raum is not None and raum.event_id == "desktop.app"

"""Abkuerzung des Ideen-Space: Satz + Kontext der App -> fester Aufruf, ohne Modell.

Laedt die beiden Module aus ihren Dateien, ohne das Paket ``core`` (und damit torch).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _lade(name: str):
    pfad = ROOT / "brain" / "the_brain" / "core" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_abk_test", pfad)
    modul = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modul
    spec.loader.exec_module(modul)
    return modul


abk = _lade("ideen_abkuerzung")
vertrag = _lade("ideen_fest_contract")

KONTEXT = {
    "space": "ideas",
    "bubble": {"id": "bub1", "title": "Messe"},
    "cards": [
        {"id": "n1", "title": "Preise", "format": "note", "score": None},
        {"id": "n2", "title": "Druckerei", "format": "note", "score": 40},
        {"id": "n3", "title": "Stand  auf der Messe", "format": "table", "score": None},
        {"id": "n4", "title": "Doppelt", "format": "note"},
        {"id": "n5", "title": "doppelt ", "format": "note"},
    ],
    "selection": [],
    "last_id": "n2",
    "new_id": "vor-7f3a",
}


def _mit(**aenderung):
    return {**KONTEXT, **aenderung}


# ── die Beispielsaetze aus spaces/ideas/ABSICHTEN.md ─────────────────────

@pytest.mark.parametrize("satz, ereignis, argumente", [
    ("Halt fest: Preise im Januar prüfen", "idea.create",
     {"bubble_id": "bub1", "title": "Preise im Januar prüfen", "id": "vor-7f3a"}),
    ("Notiz: Anruf bei der Druckerei", "idea.create",
     {"bubble_id": "bub1", "title": "Anruf bei der Druckerei", "id": "vor-7f3a"}),
    ("Schreib auf, dass der Stand größer sein muss", "idea.create",
     {"bubble_id": "bub1", "title": "der Stand größer sein muss", "id": "vor-7f3a"}),
    ("Benenne die Notiz Preise in Preisliste um", "idea.update", {"id": "n1", "title": "Preisliste"}),
    ("Mach aus der Notiz Preise eine Tabelle", "idea.format", {"id": "n1", "format": "table"}),
    ("Zeig Druckerei als Aufgabenliste", "idea.format", {"id": "n2", "format": "action_list"}),
    ("Formatiere das als Pro und Contra", "idea.format", {"id": "n2", "format": "pros_cons"}),
    ("Räum das Canvas auf", "idea.arrange", {"bubble_id": "bub1"}),
    ("Ordne die Notizen im Raster", "idea.arrange", {"bubble_id": "bub1"}),
    ("Sortiere die Karten nach Bewertung", "idea.arrange", {"bubble_id": "bub1", "order": "score"}),
    ("Verbinde Preise mit Druckerei", "idea.connect", {"from_id": "n1", "to_id": "n2"}),
    ("Verlinke, was zusammengehört", "idea.auto_link", {"bubble_id": "bub1"}),
    ("Finde Verbindungen in dieser Bubble", "idea.auto_link", {"bubble_id": "bub1"}),
    ("Bewerte Preise mit 80", "idea.rate", {"id": "n1", "score": 80}),
    ("Gib der Notiz Druckerei 40 Punkte", "idea.rate", {"id": "n2", "score": 40}),
    ("Bewerte die Bubble Messe mit 70", "bubble.rate", {"id": "bub1", "score": 70}),
    ("Begründung: Preise und Druckerei tragen die Idee", "idea.rate_reason",
     {"bubble_id": "bub1", "text": "Preise und Druckerei tragen die Idee"}),
    ("Halt als Begründung fest, dass der Stand zu klein ist", "idea.rate_reason",
     {"bubble_id": "bub1", "text": "der Stand zu klein ist"}),
    ("Mach aus der Bubble Messe ein Projekt", "bubble.promote", {"bubble_id": "bub1"}),
    ("Befördere diese Bubble", "bubble.promote", {"bubble_id": "bub1"}),
    ("Zeig die Notizen dieser Bubble", "idea.list", {"bubble_id": "bub1"}),
    ("Was steht hier?", "idea.list", {"bubble_id": "bub1"}),
])
def test_beispielsatz_wird_ohne_modell_zum_festen_aufruf(satz, ereignis, argumente) -> None:
    treffer = abk.loese(satz, KONTEXT)

    assert treffer is not None and "rueckfrage" not in treffer, treffer
    assert (treffer["ereignis"], treffer["argumente"]) == (ereignis, argumente)
    # Was die Abkuerzung liefert, nimmt der Vertrag des Gehirns unveraendert an.
    if ereignis in vertrag.FESTE_EREIGNISSE:
        assert treffer["capability"] == ereignis
        assert vertrag.feste_argumente(ereignis, abk.arg_vorlage(argumente)) == (argumente, "")
    else:
        assert treffer["capability"] == "idea_connect"   # aelterer Vertrag, eigener Name


def test_verlinke_die_beiden_nimmt_die_auswahl() -> None:
    treffer = abk.loese("Verlinke die beiden Notizen", _mit(selection=["n3", "n1"]))
    assert treffer["argumente"] == {"from_id": "n3", "to_id": "n1"}

    for auswahl in ([], ["n1"], ["n1", "n2", "n3"], ["n1", "n1"]):
        assert "rueckfrage" in abk.loese("Verlinke die beiden Notizen", _mit(selection=auswahl))


def test_begruendung_verweist_auf_die_markierten_karten() -> None:
    treffer = abk.loese("Begründung: tragen die Idee", _mit(selection=["n1", "n2", "n1"]))
    assert treffer["argumente"] == {"bubble_id": "bub1", "text": "tragen die Idee", "idea_ids": ["n1", "n2"]}


# ── Namen aufloesen: nie raten ───────────────────────────────────────────

def test_titel_treffen_ohne_gross_klein_und_leerraum() -> None:
    assert abk.loese("bewerte  PREISE mit 5", KONTEXT)["argumente"] == {"id": "n1", "score": 5}
    assert abk.loese("Bewerte die Notiz „Stand auf der Messe“ mit 5", KONTEXT)["argumente"]["id"] == "n3"
    assert abk.loese("Mach aus stand auf  der messe eine Tabelle", KONTEXT)["argumente"]["id"] == "n3"


@pytest.mark.parametrize("satz, kontext, teil", [
    ("Bewerte Urlaub mit 5", KONTEXT, "keine Karte"),                      # kein Treffer
    ("Bewerte Doppelt mit 5", KONTEXT, "2 Karten"),                        # mehrere Treffer
    ("Bewerte Urlaub mit 5", _mit(cards_total=40), "sichtbaren Karten"),   # Liste gekuerzt
    ("Formatiere das als Tabelle", _mit(last_id=None), "Welche Karte"),    # nichts gemeint
    ("Formatiere das als Tabelle", _mit(selection=["n1", "n2"]), "2 Karten markiert"),
    ("Verbinde Preise mit Preise", KONTEXT, "dieselbe Karte"),
    ("Bewerte Preise mit 101", KONTEXT, "0 bis 100"),
    ("Halt fest: Preise prüfen", _mit(bubble=None), "Bubble offen"),       # keine offene Bubble
    ("Räum das Canvas auf", _mit(bubble={"id": "", "title": "x"}), "Bubble offen"),
    ("Bewerte die Bubble Urlaub mit 70", KONTEXT, "keine Bubble"),
    ("Befördere diese Bubble", _mit(bubble=None), "Welche Bubble"),
])
def test_unklarer_bezug_ergibt_eine_rueckfrage_und_keinen_aufruf(satz, kontext, teil) -> None:
    treffer = abk.loese(satz, kontext)

    assert set(treffer) == {"rueckfrage"}
    assert teil in treffer["rueckfrage"]


def test_das_meint_die_eine_markierte_karte_sonst_die_letzte() -> None:
    assert abk.loese("Formatiere das als Tabelle", _mit(selection=["n3"]))["argumente"]["id"] == "n3"
    assert abk.loese("Formatiere das als Tabelle", KONTEXT)["argumente"]["id"] == "n2"


def test_bubble_ohne_offene_bubble_wird_aus_der_uebersicht_aufgeloest() -> None:
    kontext = {"space": "ideas", "bubble": None, "cards": [],
               "bubbles": [{"id": "bub1", "title": "Messe"}, {"id": "bub2", "title": "Urlaub"},
                           {"id": "bub3", "title": "urlaub"}]}
    assert abk.loese("Mach aus der Bubble Messe ein Projekt", kontext)["argumente"] == {"bubble_id": "bub1"}
    assert "2 Bubbles" in abk.loese("Befördere die Bubble Urlaub", kontext)["rueckfrage"]


def test_unsichere_kennungen_aus_dem_kontext_werden_nie_benutzt() -> None:
    boese = _mit(cards=[{"id": "n1),x", "title": "Preise"}, {"id": "", "title": "Leer"}],
                 new_id="a,b", last_id="eq.n1", selection=["n1,n2"])
    assert "rueckfrage" in abk.loese("Bewerte Preise mit 5", boese)       # Karte mit boeser Kennung zaehlt nicht
    assert "rueckfrage" in abk.loese("Bewerte Leer mit 5", boese)
    assert "rueckfrage" in abk.loese("Formatiere das als Tabelle", boese)
    assert "id" not in abk.loese("Notiz: Test", boese)["argumente"]       # vorgemerkte Kennung verworfen


# ── was die Abkuerzung bewusst NICHT nimmt ───────────────────────────────

@pytest.mark.parametrize("satz", [
    "Ordne die Notizen nach Thema",          # braucht ein Modell
    "Was hältst du von dieser Notiz?",
    "Bewerte die Bubble",                    # ohne Zahl: Urteil
    "Fass die Notiz zusammen",
    "Wie wird das Wetter?",
    "",
])
def test_andere_saetze_bleiben_beim_bisherigen_weg(satz) -> None:
    assert abk.loese(satz, KONTEXT) is None


@pytest.mark.parametrize("kontext", [None, {}, {"space": "sales"}, "ideas", {"space": "ideas", "cards": "kaputt"}])
def test_ohne_brauchbaren_ideen_kontext_greift_die_abkuerzung_nicht_oder_fragt(kontext) -> None:
    treffer = abk.loese("Bewerte Preise mit 80", kontext)
    assert treffer is None or set(treffer) == {"rueckfrage"}


def test_loese_wirft_nie() -> None:
    for kontext in ({"space": "ideas", "bubble": 5, "cards": [None, 3], "selection": "x", "bubbles": 7},):
        for satz in ("Bewerte Preise mit 80", "Halt fest: x", "Befördere diese Bubble", "Verlinke die beiden"):
            treffer = abk.loese(satz, kontext)
            assert treffer is None or set(treffer) == {"rueckfrage"}


# ── deutsche Saetze fuer Fehler ──────────────────────────────────────────

@pytest.mark.parametrize("fehler, satz", [
    ("RuntimeError: Ideen-Werkzeug meldet einen Fehler (invalid_arguments: bubble_id)",
     "Das Werkzeug hat eine Angabe abgelehnt: die Bubble."),
    ("RuntimeError: Ideen-Werkzeug meldet einen Fehler (invalid_arguments: geheim)",
     "Das Werkzeug hat eine Angabe abgelehnt: „geheim“."),
    ("RuntimeError: Ideen-Werkzeug meldet einen Fehler (invalid_arguments)",
     "Das Werkzeug hat die Angaben abgelehnt."),
    ("RuntimeError: Ideen-Werkzeug meldet einen Fehler (idea_not_found)", "Diese Karte gibt es nicht mehr."),
    ("RuntimeError: Ideen-Werkzeug meldet einen Fehler (supabase_http_error)",
     "Die Datenbank hat die Anfrage abgelehnt."),
    ("RuntimeError: Ideen-Werkzeug meldet einen Fehler (neuer_fehler)",
     "Das Werkzeug meldet einen Fehler (neuer_fehler)."),
    ("RuntimeError: Ideen-Werkzeug meldet einen Fehler", "Das Werkzeug meldet einen Fehler."),
    ("RuntimeError: Ideen-Dienst nicht erreichbar (transport_closed)",
     "Der Ideen-Dienst läuft gerade nicht. Bitte prüfe das Canvas, bevor du es wiederholst."),
    ("RuntimeError: Ideen-Dienst nicht erreichbar (openfang_tool_error)",
     "Der Ideen-Dienst hat nicht geantwortet. Es ist unklar, ob etwas geändert wurde."),
    ("RuntimeError: Ideen-Werkzeug: keine lesbare Antwort",
     "Der Ideen-Dienst hat unlesbar geantwortet. Bitte prüfe das Canvas."),
    ("clarification required: bubble_id, title", "Mir fehlt eine Angabe: die Bubble, der Titel."),
    ("clarification required: score is invalid", "Diese Angabe ist ungültig: die Zahl."),
    ("clarification required: unknown field bubble_name", "Diese Angabe kennt das Werkzeug nicht: „bubble_name“."),
    ("clarification required: one of title, content, x, y",
     "Mir fehlt eine Angabe: der Titel, der Inhalt, „x“ oder „y“."),
    ("validator blocked: canonical idea.rate truth unverified",
     "Ich konnte nicht bestätigen, dass die Änderung gespeichert ist. Bitte prüfe das Canvas."),
    ("validator blocked: canonical idea.rate validator error",
     "Die Nachprüfung war nicht möglich. Die Änderung ist nicht bestätigt."),
    ("runtime authority approval pending", "Das wartet auf eine Freigabe."),
    ("RuntimeError: outcome_unknown",
     "Es ist unklar, ob die Änderung angekommen ist. Bitte prüfe das Canvas, bevor du es wiederholst."),
    ("RuntimeError: OpenFang MCP tool returned isError=true", "Das Werkzeug hat den Aufruf abgelehnt."),
    ("PermissionError: MCP agent/server/tool tuple is not registered; execution denied",
     "Der Agent darf dieses Werkzeug nicht benutzen."),
    ("OpenFangUnavailable: OpenFang unavailable at http://x/mcp after 1 attempt(s)",
     "Der Agenten-Dienst ist gerade nicht erreichbar."),
    ("etwas voellig anderes mit key=geheim", "Das hat nicht geklappt."),
    (None, "Das hat nicht geklappt."),
])
def test_fehler_wird_ein_deutscher_satz_ohne_werte(fehler, satz) -> None:
    assert abk.fehlersatz(fehler) == satz


# ── feste Antwortsaetze nach dem Schritt ─────────────────────────────────

def _quittung(inhalt):
    return {"_meta": {"runtime_admission": {}}, "isError": False,
            "content": [{"type": "text", "text": json.dumps(inhalt)}]}


def test_liste_wird_ein_satz_mit_anzahl_und_titeln_ohne_kennungen() -> None:
    zeilen = [{"id": f"n{i}", "title": t} for i, t in enumerate(
        ["Preise", "Druckerei", "Stand\nauf der  Messe", "Vierte", "Fünfte"])]
    satz = abk.antwort_fuer_schritt("idea.list", True, _quittung(zeilen), None, "", KONTEXT)

    assert satz == "5 Notizen in „Messe“: „Preise“, „Druckerei“, „Stand auf der Messe“ (und 2 weitere)."
    assert "n0" not in satz and "id" not in satz
    assert abk.antwort_fuer_schritt("idea.list", True, _quittung(zeilen[:1]), None, "", KONTEXT) == (
        "1 Notiz in „Messe“: „Preise“.")
    assert abk.antwort_fuer_schritt("idea.list", True, _quittung([]), None, "", KONTEXT) == (
        "In „Messe“ gibt es noch keine Notizen.")
    assert abk.antwort_fuer_schritt("idea.list", True, _quittung([{"id": "n1", "title": None}]), None, "", {}) == (
        "1 Notiz in dieser Bubble: „ohne Titel“.")
    lang = abk.antwort_fuer_schritt("idea.list", True, _quittung([{"id": "n1", "title": "x" * 300}]), None, "", KONTEXT)
    assert len(lang) < 120


def test_zurueck_nennt_die_zahl_der_aenderungen() -> None:
    f = abk.antwort_fuer_schritt
    assert f("aenderung_zurueck", True, _quittung({"gruppe": "ideen_1", "anzahl": 1}), None) == (
        "Zurückgenommen: 1 Änderung.")
    assert f("aenderung_zurueck", True, _quittung({"gruppe": "plan_7", "anzahl": 4}), None) == (
        "Zurückgenommen: 4 Änderungen.")
    assert f("aenderung_zurueck", True, _quittung({"gruppe": None, "anzahl": 0}), None) == (
        "Es gibt nichts zurückzunehmen.")
    assert f("aenderung_zurueck", True, {"unlesbar": 1}, None) is None      # dann der bisherige Weg
    assert f("aenderung_zurueck", False, None, "RuntimeError: aenderung_zurueck: aenderung_konflikt: public.canvas_nodes n1") == (
        "Das lässt sich nicht zurücknehmen: Ein Eintrag wurde danach noch einmal geändert.")


def test_bubble_liste_wird_ein_satz_ohne_kennungen() -> None:
    roh = ("15 bubbles:\n- BUBBLE-V2-Alpha (id=ab12cd34, score=0)\n- Messe (Herbst) (id=ef56, score=70.0)\n"
           "- Sheerlay (id=db424a20, score=0)\n- Vier (id=4, score=0)\n- Fünf (id=5, score=0)\n- Sechs (id=6, score=0)")
    satz = abk.antwort_fuer_schritt("bubble_list", True, roh, None)

    assert satz == ("15 Bubbles: „BUBBLE-V2-Alpha“, „Messe (Herbst)“, „Sheerlay“, „Vier“, „Fünf“ (und 1 weitere).")
    assert "ab12cd34" not in satz and "score" not in satz
    assert abk.antwort_fuer_schritt("bubble_list", True, "No bubbles found.", None) == "Du hast noch keine Bubbles."
    assert abk.antwort_fuer_schritt("bubble_list", True, {"anders": 1}, None) is None
    assert abk.antwort_fuer_schritt("bubble_list", True, "etwas anderes", None) is None


def test_uebrige_schritte_behalten_den_satz_der_abkuerzung_fehler_nie() -> None:
    f = abk.antwort_fuer_schritt
    assert f("idea.rate", True, _quittung([{"id": "n1"}]), None, "„Preise“ mit 80 bewertet.") == "„Preise“ mit 80 bewertet."
    assert f("idea.auto_link", True, _quittung({"bubble_id": "b", "anzahl": 2, "angelegt": []}), None, "x") == (
        "2 Verbindungen angelegt.")
    assert f("idea.auto_link", True, _quittung({"bubble_id": "b", "anzahl": 0, "angelegt": []}), None, "x") == (
        "Ich habe keine neuen Verbindungen gefunden.")
    assert f("idea.arrange", True, _quittung({"anzahl": 4, "verschoben": 0}), None, "x") == "Die Karten stehen schon so."
    assert f("idea.arrange", True, _quittung({"anzahl": 4, "verschoben": 3}), None, "Karten ins Raster geordnet.") == (
        "Karten ins Raster geordnet.")
    # Ein gescheiterter Schritt bekommt nie den Erfolgssatz.
    assert f("idea.rate", False, None, "validator blocked: canonical idea.rate truth unverified",
             "„Preise“ mit 80 bewertet.").startswith("Ich konnte nicht bestätigen")
    assert f("unbekannt", True, None, None) is None


# ── Vorlage fuer den Plan-Schritt ────────────────────────────────────────

def test_vorlage_ueberlebt_geschweifte_klammern_im_text() -> None:
    argumente = {"bubble_id": "bub1", "title": "Preis {{state.geheim}} und {x}", "idea_ids": ["n1"]}
    vorlage = abk.arg_vorlage(argumente)

    assert "{{" not in vorlage and "}}" not in vorlage     # nichts, was der Plan-Ausfuehrer ersetzen wuerde
    assert json.loads(vorlage) == argumente


# ── Fehler "Ideenbereich" ────────────────────────────────────────────────

@pytest.mark.parametrize("satz, suchwort", [
    ("Welche Bubbles habe ich im Ideenbereich?", ""),
    ("Zeige meine Bubbles", ""),
    ("Zeig mir alle Bubbles", ""),
    ('Zeige meine Bubbles mit "Messe"', "Messe"),
    ("Liste Bubbles „Urlaub“", "Urlaub"),
    (None, ""),
])
def test_bubble_liste_sucht_nur_nach_woertern_in_anfuehrungszeichen(satz, suchwort) -> None:
    assert abk.listen_suchwort(satz) == suchwort

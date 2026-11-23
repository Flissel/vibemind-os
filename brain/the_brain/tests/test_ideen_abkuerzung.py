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
    "bubble": {"id": "b0b1e001", "title": "Messe"},
    "cards": [
        {"id": "a0000001", "title": "Preise", "format": "note", "score": None},
        {"id": "a0000002", "title": "Druckerei", "format": "note", "score": 40},
        {"id": "a0000003", "title": "Stand  auf der Messe", "format": "table", "score": None},
        {"id": "a0000004", "title": "Doppelt", "format": "note"},
        {"id": "a0000005", "title": "doppelt ", "format": "note"},
    ],
    "selection": [],
    "last_id": "a0000002",
    "new_id": "7f3a0000-0000-4000-8000-000000000001",
}


def _mit(**aenderung):
    return {**KONTEXT, **aenderung}


# ── die Beispielsaetze aus spaces/ideas/ABSICHTEN.md ─────────────────────

@pytest.mark.parametrize("satz, ereignis, argumente", [
    ("Halt fest: Preise im Januar prüfen", "idea.create",
     {"bubble_id": "b0b1e001", "title": "Preise im Januar prüfen", "id": "7f3a0000-0000-4000-8000-000000000001"}),
    ("Notiz: Anruf bei der Druckerei", "idea.create",
     {"bubble_id": "b0b1e001", "title": "Anruf bei der Druckerei", "id": "7f3a0000-0000-4000-8000-000000000001"}),
    ("Schreib auf, dass der Stand größer sein muss", "idea.create",
     {"bubble_id": "b0b1e001", "title": "der Stand größer sein muss", "id": "7f3a0000-0000-4000-8000-000000000001"}),
    ("Benenne die Notiz Preise in Preisliste um", "idea.update", {"id": "a0000001", "title": "Preisliste"}),
    ("Mach aus der Notiz Preise eine Tabelle", "idea.format", {"id": "a0000001", "format": "table"}),
    ("Zeig Druckerei als Aufgabenliste", "idea.format", {"id": "a0000002", "format": "action_list"}),
    ("Formatiere das als Pro und Contra", "idea.format", {"id": "a0000002", "format": "pros_cons"}),
    ("Räum das Canvas auf", "idea.arrange", {"bubble_id": "b0b1e001"}),
    ("Ordne die Notizen im Raster", "idea.arrange", {"bubble_id": "b0b1e001"}),
    ("Sortiere die Karten nach Bewertung", "idea.arrange", {"bubble_id": "b0b1e001", "order": "score"}),
    ("Verbinde Preise mit Druckerei", "idea.connect", {"from_id": "a0000001", "to_id": "a0000002"}),
    ("Verlinke, was zusammengehört", "idea.auto_link", {"bubble_id": "b0b1e001"}),
    ("Finde Verbindungen in dieser Bubble", "idea.auto_link", {"bubble_id": "b0b1e001"}),
    ("Bewerte Preise mit 80", "idea.rate", {"id": "a0000001", "score": 80}),
    ("Gib der Notiz Druckerei 40 Punkte", "idea.rate", {"id": "a0000002", "score": 40}),
    ("Bewerte die Bubble Messe mit 70", "bubble.rate", {"id": "b0b1e001", "score": 70}),
    ("Begründung: Preise und Druckerei tragen die Idee", "idea.rate_reason",
     # Die genannten Karten gehoeren dazu (White-Paper-Zusatz 2: im Betrieb stand karten: []).
     {"bubble_id": "b0b1e001", "text": "Preise und Druckerei tragen die Idee", "idea_ids": ["a0000001", "a0000002"]}),
    ("Halt als Begründung fest, dass der Stand zu klein ist", "idea.rate_reason",
     {"bubble_id": "b0b1e001", "text": "der Stand zu klein ist"}),
    ("Mach aus der Bubble Messe ein Projekt", "bubble.promote", {"bubble_id": "b0b1e001"}),
    ("Befördere diese Bubble", "bubble.promote", {"bubble_id": "b0b1e001"}),
    ("Zeig die Notizen dieser Bubble", "idea.list", {"bubble_id": "b0b1e001"}),
    ("Was steht hier?", "idea.list", {"bubble_id": "b0b1e001"}),
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
    treffer = abk.loese("Verlinke die beiden Notizen", _mit(selection=["a0000003", "a0000001"]))
    assert treffer["argumente"] == {"from_id": "a0000003", "to_id": "a0000001"}

    for auswahl in ([], ["a0000001"], ["a0000001", "a0000002", "a0000003"], ["a0000001", "a0000001"]):
        assert "rueckfrage" in abk.loese("Verlinke die beiden Notizen", _mit(selection=auswahl))


def test_begruendung_verweist_auf_die_markierten_karten() -> None:
    treffer = abk.loese("Begründung: tragen die Idee", _mit(selection=["a0000001", "a0000002", "a0000001"]))
    assert treffer["argumente"] == {"bubble_id": "b0b1e001", "text": "tragen die Idee", "idea_ids": ["a0000001", "a0000002"]}


# ── Namen aufloesen: nie raten ───────────────────────────────────────────

def test_titel_treffen_ohne_gross_klein_und_leerraum() -> None:
    assert abk.loese("bewerte  PREISE mit 5", KONTEXT)["argumente"] == {"id": "a0000001", "score": 5}
    assert abk.loese("Bewerte die Notiz „Stand auf der Messe“ mit 5", KONTEXT)["argumente"]["id"] == "a0000003"
    assert abk.loese("Mach aus stand auf  der messe eine Tabelle", KONTEXT)["argumente"]["id"] == "a0000003"


@pytest.mark.parametrize("satz, kontext, teil", [
    ("Bewerte Urlaub mit 5", KONTEXT, "finde ich in dieser Bubble nicht"),                      # kein Treffer
    ("Bewerte Doppelt mit 5", KONTEXT, "2 Notizen"),                        # mehrere Treffer
    ("Bewerte Urlaub mit 5", _mit(cards_total=40), "sichtbaren Notizen"),   # Liste gekuerzt
    ("Formatiere das als Tabelle", _mit(last_id=None), "Welche Karte"),    # nichts gemeint
    ("Formatiere das als Tabelle", _mit(selection=["a0000001", "a0000002"]), "2 Karten markiert"),
    ("Verbinde Preise mit Preise", KONTEXT, "dieselbe Karte"),
    ("Bewerte Preise mit 101", KONTEXT, "0 bis 100"),
    ("Halt fest: Preise prüfen", _mit(bubble=None), "Bubble offen"),       # keine offene Bubble
    ("Räum das Canvas auf", _mit(bubble={"id": "", "title": "x"}), "welche Bubble offen ist"),
    ("Bewerte die Bubble Urlaub mit 70", KONTEXT, "keine Bubble"),
    ("Befördere diese Bubble", _mit(bubble=None), "Welche Bubble"),
])
def test_unklarer_bezug_ergibt_eine_rueckfrage_und_keinen_aufruf(satz, kontext, teil) -> None:
    treffer = abk.loese(satz, kontext)

    assert set(treffer) == {"rueckfrage"}
    assert teil in treffer["rueckfrage"]


def test_das_meint_die_eine_markierte_karte_sonst_die_letzte() -> None:
    assert abk.loese("Formatiere das als Tabelle", _mit(selection=["a0000003"]))["argumente"]["id"] == "a0000003"
    assert abk.loese("Formatiere das als Tabelle", KONTEXT)["argumente"]["id"] == "a0000002"


def test_bubble_ohne_offene_bubble_wird_aus_der_uebersicht_aufgeloest() -> None:
    kontext = {"space": "ideas", "bubble": None, "cards": [],
               "bubbles": [{"id": "b0b1e001", "title": "Messe"}, {"id": "b0b1e002", "title": "Urlaub"},
                           {"id": "b0b1e003", "title": "urlaub"}]}
    assert abk.loese("Mach aus der Bubble Messe ein Projekt", kontext)["argumente"] == {"bubble_id": "b0b1e001"}
    assert "2 Bubbles" in abk.loese("Befördere die Bubble Urlaub", kontext)["rueckfrage"]


def test_unsichere_kennungen_aus_dem_kontext_werden_nie_benutzt() -> None:
    boese = _mit(cards=[{"id": "n1),x", "title": "Preise"}, {"id": "", "title": "Leer"}],
                 new_id="a,b", last_id="eq.n1", selection=["n1,n2"])
    assert "rueckfrage" in abk.loese("Bewerte Preise mit 5", boese)       # Karte mit boeser Kennung zaehlt nicht
    assert "rueckfrage" in abk.loese("Bewerte Leer mit 5", boese)
    assert "rueckfrage" in abk.loese("Formatiere das als Tabelle", boese)
    assert "id" not in abk.loese("Notiz: Test", boese)["argumente"]       # vorgemerkte Kennung verworfen


# ── Kennungen aus dem Kontext: nur in der Form der Datenbank ─────────────

@pytest.mark.parametrize("kennung, gilt", [
    ("000000b2", True),                                   # 8 Hex-Zeichen (Bubbles, Karten)
    ("0123456789abcdef0123456789abcdef", True),           # 32 Hex-Zeichen (Verbindungen)
    ("550e8400-e29b-41d4-a716-446655440000", True),       # UUID
    ("550E8400-E29B-41D4-A716-446655440000", True),
    ("1", False), ("42", False), ("bub1", False), ("n1", False), ("bubble:1", False),
    ("000000b", False), ("000000b2x", False), ("", False), (None, False), (1, False),
    ("550e8400e29b41d4a716-446655440000", False),
])
def test_nur_kennungen_in_datenbank_form_gelten(kennung, gilt) -> None:
    assert abk.ist_kennung(kennung) is gilt


def test_lokale_zahl_als_bubble_kennung_fuehrt_nie_zu_einem_aufruf() -> None:
    """Gemessen 23.11.2026: die App schickte context.bubble.id = "1"."""
    lokal = _mit(bubble={"id": "1", "title": "Laternenlauf"})
    for satz in ("Halt fest: Zeitmessung", "Zeig die Notizen dieser Bubble", "Räum das Canvas auf",
                 "Befördere diese Bubble", "Bewerte die Bubble Laternenlauf mit 70", "Begründung: traegt",
                 "Verlinke, was zusammengehört"):
        treffer = abk.loese(satz, lokal)
        assert set(treffer) == {"rueckfrage"}, satz
        assert treffer["rueckfrage"].startswith("Ich weiß nicht, welche Bubble offen ist."), satz


def test_karte_mit_lokaler_kennung_wird_nicht_benutzt_sondern_erklaert() -> None:
    lokal = _mit(cards=[{"id": "7", "title": "Preise"}], last_id="7", selection=["7"])
    assert abk.loese("Bewerte Preise mit 80", lokal) == {
        "rueckfrage": "Die Notiz „Preise“ kann ich gerade nicht zuordnen. Öffne die Bubble noch einmal."}
    assert "rueckfrage" in abk.loese("Formatiere das als Tabelle", lokal)


def test_unbekannte_notiz_ergibt_eine_gezielte_rueckfrage() -> None:
    """In der App kam dafuer nur "Ich habe dich nicht ganz verstanden"."""
    assert abk.loese("Bewerte die Notiz Gibtsnicht mit 50", KONTEXT) == {
        "rueckfrage": "Eine Notiz „Gibtsnicht“ finde ich in dieser Bubble nicht."}
    # auch ohne Karten im Kontext und ohne offene Bubble: Rueckfrage, nie Schweigen
    assert abk.loese("Bewerte die Notiz Gibtsnicht mit 50", {"space": "ideas"}) == {
        "rueckfrage": "Eine Notiz „Gibtsnicht“ finde ich in dieser Bubble nicht."}
    assert abk.loese("Mach aus der Notiz Gibtsnicht eine Tabelle", KONTEXT)["rueckfrage"].startswith(
        "Eine Notiz „Gibtsnicht“")


@pytest.mark.parametrize("bubble", [{"id": None, "title": "Stadtfest"}, {"title": "Stadtfest"},
                                    {"id": "", "title": "Stadtfest"}])
@pytest.mark.parametrize("satz", ["Bewerte die Notiz Sponsoren mit 50", "Was steht bei Sponsoren?",
                                  "Formuliere Sponsoren aus", "Wie viele Karten gibt es hier?"])
def test_offene_bubble_ohne_kennung_ist_wie_kontext_unbekannt(bubble, satz) -> None:
    # Lauf 7: Die App meldete "in der Bubble", aber ohne Kennung; die Antwort war
    # "finde ich in dieser Bubble nicht". Richtig ist: ich weiss nicht, welche Bubble offen ist.
    antwort = abk.loese(satz, {"space": "ideas", "bubble": bubble, "cards": []})
    assert antwort == {"rueckfrage": abk._BUBBLE_UNKLAR}


# ── was die Abkuerzung bewusst NICHT nimmt ───────────────────────────────

@pytest.mark.parametrize("satz", [
    # "Ordne die Notizen nach Thema" sagt seit Arbeitslauf 2 ehrlich "Das kann ich noch nicht"
    # (tests/test_ideen_lesen_gruppieren.py).
    # "Was hältst du von dieser Notiz?" ist seit 23.11.2026 idea.explain mit der Karte aus
    # dem Kontext (Rueckweg, test_modellfunktionen_tragen_die_karte_aus_dem_kontext).
    # Ohne genannte Karte bleibt der Satz beim bisherigen Weg:
    "Was hältst du davon?",
    # "Bewerte die Bubble" (ohne Zahl) ist seit Lauf 7 ein Urteil und laeuft als Einschaetzen
    # (tests/test_bubble_evaluate_weg.py); der alte Weg war ein Auftrag ohne Werkzeug.
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
     "Die Datenbank hat die Änderung abgelehnt."),
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
    assert abk.antwort_fuer_schritt("idea.list", True, _quittung([{"id": "a0000001", "title": None}]), None, "", {}) == (
        "1 Notiz in dieser Bubble: „ohne Titel“.")
    lang = abk.antwort_fuer_schritt("idea.list", True, _quittung([{"id": "a0000001", "title": "x" * 300}]), None, "", KONTEXT)
    assert len(lang) < 120


def test_liste_nennt_die_gezaehlte_gesamtzahl_nicht_die_laenge_der_gekuerzten_liste() -> None:
    # 23.11.2026: Eine Bubble mit mehr als 50 Karten meldete "50 Notizen".
    karten = [{"id": f"{i:08x}", "title": f"K{i}"} for i in range(50)]
    f = abk.antwort_fuer_schritt
    satz = f("idea.list", True, _quittung({"karten": karten, "gezeigt": 50, "gesamt": 75, "gekuerzt": True}),
             None, "", KONTEXT)
    assert satz == "75 Notizen in „Messe“: „K0“, „K1“, „K2“ (und 72 weitere)."

    ungezaehlt = _quittung({"karten": karten, "gezeigt": 50, "gesamt": None, "gekuerzt": True})
    assert f("idea.list", True, ungezaehlt, None, "", KONTEXT) == (
        "Mindestens 50 Notizen in „Messe“: „K0“, „K1“, „K2“ (und weitere).")

    vollstaendig = _quittung({"karten": karten[:2], "gezeigt": 2, "gesamt": 2, "gekuerzt": False})
    assert f("idea.list", True, vollstaendig, None, "", KONTEXT) == "2 Notizen in „Messe“: „K0“, „K1“."
    leer = _quittung({"karten": [], "gezeigt": 0, "gesamt": 0, "gekuerzt": False})
    assert f("idea.list", True, leer, None, "", KONTEXT) == "In „Messe“ gibt es noch keine Notizen."
    # Unstimmige Zahl (kleiner als die Liste) gewinnt nie gegen das, was da ist.
    falsch = _quittung({"karten": karten[:3], "gezeigt": 3, "gesamt": 1, "gekuerzt": False})
    assert f("idea.list", True, falsch, None, "", KONTEXT).startswith("3 Notizen")


def test_aeltere_listenform_mit_vollem_deckel_behauptet_keine_zahl() -> None:
    karten = [{"id": f"{i:08x}", "title": f"K{i}"} for i in range(abk.IDEA_LIST_VORGABE)]
    assert abk.antwort_fuer_schritt("idea.list", True, _quittung(karten), None, "", KONTEXT) == (
        "Mindestens 50 Notizen in „Messe“: „K0“, „K1“, „K2“ (und weitere).")


def test_namen_werden_gegen_alle_karten_des_kontexts_aufgeloest_nicht_gegen_den_listendeckel() -> None:
    # Die Huelle schickt bis zu 100 Karten im Kontext (ideas-chat.js MAX_CARDS); idea_list ist dafuer
    # nicht im Spiel. Die 75. Karte einer Bubble mit 75 Karten muss gefunden werden.
    karten = [{"id": f"{i:08x}", "title": f"Posten {i}"} for i in range(75)]
    kontext = {**KONTEXT, "cards": karten}
    treffer = abk.loese("Bewerte Posten 74 mit 70", kontext)
    assert treffer["ereignis"] == "idea.rate"
    assert treffer["argumente"] == {"id": f"{74:08x}", "score": 70}


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
    roh = ("15 bubbles:\n- Probe-Alpha (id=ab12cd34, score=0)\n- Messe (Herbst) (id=ef56, score=70.0)\n"
           "- Laternenlauf (id=000000b2, score=0)\n- Vier (id=4, score=0)\n- Fünf (id=5, score=0)\n- Sechs (id=6, score=0)")
    satz = abk.antwort_fuer_schritt("bubble_list", True, roh, None)

    assert satz == ("15 Bubbles: „Probe-Alpha“, „Messe (Herbst)“, „Laternenlauf“, „Vier“, „Fünf“ (und 1 weitere).")
    assert "ab12cd34" not in satz and "score" not in satz
    assert abk.antwort_fuer_schritt("bubble_list", True, "No bubbles found.", None) == "Du hast noch keine Bubbles."
    assert abk.antwort_fuer_schritt("bubble_list", True, {"anders": 1}, None) is None
    assert abk.antwort_fuer_schritt("bubble_list", True, "etwas anderes", None) is None


def test_uebrige_schritte_behalten_den_satz_der_abkuerzung_fehler_nie() -> None:
    f = abk.antwort_fuer_schritt
    assert f("idea.rate", True, _quittung([{"id": "a0000001"}]), None, "„Preise“ mit 80 bewertet.") == "„Preise“ mit 80 bewertet."
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


# ── Arbeitslauf 23.11.2026: Ergaenzen und Umbenennen ohne Agentenlauf ─────

@pytest.mark.parametrize("satz, karte, zusatz", [
    ("Ergänze bei Preise: Januar teurer", "a0000001", "Januar teurer"),
    ("Ergaenze bei der Notiz Preise: Januar teurer", "a0000001", "Januar teurer"),
    ("Füge zu Druckerei hinzu: Angebot bis Freitag", "a0000002", "Angebot bis Freitag"),
    ("Schreib bei Druckerei dazu: Angebot bis Freitag", "a0000002", "Angebot bis Freitag"),
    ("Häng an Preise an: Rabatt fragen", "a0000001", "Rabatt fragen"),
    ("Ergänze Preise um Rabatt fragen", "a0000001", "Rabatt fragen"),
    ("Ergänze Preise: Rabatt: 10 %", "a0000001", "Rabatt: 10 %"),
])
def test_ergaenzen_ist_ein_fester_aufruf(satz, karte, zusatz) -> None:
    treffer = abk.loese(satz, KONTEXT)
    assert treffer["ereignis"] == "idea.update"
    assert treffer["argumente"] == {"id": karte, "content_append": zusatz}
    assert treffer["antwort"].endswith("ergänzt.")
    argumente, grund = vertrag.feste_argumente("idea.update", treffer["argumente"])
    assert grund == "" and argumente == treffer["argumente"]


@pytest.mark.parametrize("satz, karte, neu", [
    ("Benenne die Notiz Preise in Preisliste um", "a0000001", "Preisliste"),
    ("Nenne Preise um in Preisliste", "a0000001", "Preisliste"),
    ("Nenne Preise in Preisliste um", "a0000001", "Preisliste"),
    ("Ändere den Titel von Druckerei in Druckerei Nord", "a0000002", "Druckerei Nord"),
    ("Aendere den Namen von Druckerei zu „Druckerei Nord“", "a0000002", "Druckerei Nord"),
    ("Gib Druckerei den Titel Druck", "a0000002", "Druck"),
    # Der Name enthaelt das Trennwort: die erste Stelle, die eine Karte ergibt, gilt.
    ("Benenne Stand auf der Messe in Stand groß um", "a0000003", "Stand groß"),
])
def test_umbenennen_in_mehreren_formulierungen(satz, karte, neu) -> None:
    treffer = abk.loese(satz, KONTEXT)
    assert treffer["ereignis"] == "idea.update"
    assert treffer["argumente"] == {"id": karte, "title": neu}


@pytest.mark.parametrize("satz, ereignis, karte", [
    ("Formuliere die Notiz Preise aus", "idea.expand", "a0000001"),
    ("Schreib Druckerei noch aus", "idea.expand", "a0000002"),
    ("Formulier das aus", "idea.expand", "a0000002"),
    ("Was hältst du von Preise?", "idea.explain", "a0000001"),
    ("Schätz die Druckerei ein", "idea.explain", "a0000002"),
])
def test_modellfunktionen_tragen_die_karte_aus_dem_kontext(satz, ereignis, karte) -> None:
    # Arbeitslauf 23.11.2026: "Formuliere ... aus" lief ohne Kennung zum Agenten.
    treffer = abk.loese(satz, KONTEXT)
    assert treffer["ereignis"] == ereignis and treffer["argumente"] == {"id": karte}
    assert treffer["antwort"] == ""            # die Antwort kommt spaeter ueber den Rueckweg


def test_vertrag_verbietet_inhalt_und_anhang_zugleich() -> None:
    _, grund = vertrag.feste_argumente("idea.update", {"id": "a0000001", "content": "a", "content_append": "b"})
    assert "content_append" in grund


# ── Arbeitslauf 23.11.2026: Teilnamen, gekuerzte Titel, "dieser Bubble" ───

TICKET = {**KONTEXT, "cards": [
    {"id": "c0000001", "title": "Sponsoren Vertragsentwurf"},
    {"id": "c0000002", "title": "Helfer Rabatt"},
    {"id": "c0000003", "title": "Sehr lange Überschrift über die Abrechnung der Standgebühren im Viertel mit allen Sonderf…"},
]}


def test_eindeutiger_teilname_wird_aufgeloest() -> None:
    treffer = abk.loese("Gib der Notiz zu den Sponsoren 60 Punkte", TICKET)
    assert treffer["argumente"] == {"id": "c0000001", "score": 60}
    treffer = abk.loese("Bewerte helfer mit 30", TICKET)
    assert treffer["argumente"] == {"id": "c0000002", "score": 30}


def test_mehrdeutiger_teilname_fragt_mit_kandidaten_zurueck() -> None:
    kontext = {**TICKET, "cards": [*TICKET["cards"], {"id": "c0000004", "title": "Sponsoren_Liste"}]}
    antwort = abk.loese("Gib Sponsoren 60 Punkte", kontext)
    assert "rueckfrage" in antwort
    assert "„Sponsoren Vertragsentwurf“" in antwort["rueckfrage"]
    assert "„Sponsoren_Liste“" in antwort["rueckfrage"]
    # Unterstrich = Leerzeichen; der ganze Titel ist eindeutig.
    assert abk.loese("Gib Sponsoren Liste 50 Punkte", kontext)["argumente"]["id"] == "c0000004"


def test_gekuerzter_titel_im_kontext_trifft_den_ganzen_namen() -> None:
    voll = ("Sehr lange Überschrift über die Abrechnung der Standgebühren im Viertel mit allen "
            "Sonderfällen und Ausnahmen")
    treffer = abk.loese(f"Bewerte {voll} mit 40", TICKET)
    assert treffer["argumente"] == {"id": "c0000003", "score": 40}


def test_dieser_bubble_punkte_geben() -> None:
    treffer = abk.loese("Gib dieser Bubble 60 Punkte", KONTEXT)
    assert treffer["ereignis"] == "bubble.rate"
    assert treffer["argumente"] == {"id": "b0b1e001", "score": 60}


def test_unbekannter_name_bleibt_rueckfrage() -> None:
    assert "rueckfrage" in abk.loese("Gib Flugzeug 60 Punkte", TICKET)


# ── Arbeitslauf 23.11.2026: Festhalten mit kurzem Titel ───────────────────

def test_festhalten_kurzer_titel_und_ganzer_satz_als_text() -> None:
    satz = ("Sponsoren anschreiben – die Frist läuft bis Ende März und danach "
            "brauchen wir eine andere Lösung für die Anwohner")
    treffer = abk.loese(f"Halte fest: {satz}", KONTEXT)
    assert treffer["argumente"]["title"] == "Sponsoren anschreiben"
    assert treffer["argumente"]["content"] == satz
    lang = "Wir sollten im Frühjahr mit allen Beteiligten über die neue Abrechnung der Standgebühren sprechen"
    titel = abk.loese(f"Notiz: {lang}", KONTEXT)["argumente"]["title"]
    assert len(titel) <= 60 and lang.startswith(titel) and not titel.endswith(" ")
    kurz = abk.loese("Notiz: Druckerei anrufen", KONTEXT)["argumente"]
    assert kurz["title"] == "Druckerei anrufen" and "content" not in kurz


# ── Arbeitslauf 23.11.2026: Grenzen und Fehlersaetze ───────────────────────

def test_ordnen_ueber_der_grenze_sagt_was_geht() -> None:
    antwort = abk.loese("Ordne die Karten", {**KONTEXT, "cards_total": 101})
    assert antwort == {"rueckfrage": "Ordnen geht bis 100 Notizen; hier sind es 101. "
                                     "Verschiebe einen Teil der Notizen in eine eigene Bubble, dann geht es dort."}
    # Verbinden rechnet nur und geht bis 500.
    assert abk.loese("Finde Verbindungen", {**KONTEXT, "cards_total": 101})["ereignis"] == "idea.auto_link"


def test_zu_viele_karten_vom_werkzeug_wird_ein_satz_mit_zahl() -> None:
    meldung = vertrag.fehler_aus_ergebnis(_quittung({"error": "too_many_ideas: 101 ideas in this bubble, limit 100"}))
    assert meldung == "Ideen-Werkzeug meldet einen Fehler (too_many_ideas) anzahl=101 grenze=100"
    assert abk.fehlersatz(meldung, "idea_arrange").startswith("Ordnen geht bis 100 Notizen; hier sind es 101.")
    # Planer-Weg mit rohem Text
    assert abk.fehlersatz("RuntimeError: too_many_ideas: more than 100 in this bubble", "idea.arrange").startswith(
        "Ordnen geht bis 100 Notizen")


@pytest.mark.parametrize("fehler", [
    "Multi-hop plan: 0/1 steps succeeded for intent: x",
    "RuntimeError: boom",
    "Idea '' not found.",
    "Ideen-Werkzeug meldet einen Fehler (idea_format_no_content)",
    "Ideen-Werkzeug meldet einen Fehler (idea_format_table_no_columns)",
])
def test_fehlersaetze_sind_deutsch_und_ohne_rohtext(fehler) -> None:
    satz = abk.fehlersatz(fehler, "idea_expand")
    assert "RuntimeError" not in satz and "Multi-hop" not in satz and "Idea ''" not in satz
    assert satz.endswith((".", "?", "“."))


@pytest.mark.parametrize("name, erwartet", [
    ("idea_arrange", True), ("idea.connect", True), ("bubble_rate", True), ("aenderung_zurueck", True),
    ("desktop_klick", False), ("web_search", False), ("", False),
])
def test_ideen_faehigkeiten_erkennen(name, erwartet) -> None:
    assert abk.ist_ideen_faehigkeit(name) is erwartet


# ── Vorlage fuer den Plan-Schritt ────────────────────────────────────────

def test_vorlage_ueberlebt_geschweifte_klammern_im_text() -> None:
    argumente = {"bubble_id": "b0b1e001", "title": "Preis {{state.geheim}} und {x}", "idea_ids": ["a0000001"]}
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


# ── Fix 5: Besitzwoerter und Bereichswoerter (Nachmessung 23.11.2026) ──

@pytest.mark.parametrize("satz", ["Was steht in meinem Journal?", "Was steht im Kalender?",
                                  "Worum geht es in unseren Events?"])
def test_bereichswort_ohne_karte_ist_keine_notiz(satz) -> None:
    assert abk.loese(satz, KONTEXT) is None


def test_besitzwort_vor_dem_titel_faellt_weg() -> None:
    antwort = abk.loese("Was steht in meiner Druckerei?", KONTEXT)
    assert antwort["lies"]["id"] == "a0000002"


# ── Punkt 11 (Lauf 5): "Fasse die Notiz X zusammen" ist eine Karte, keine Bubble ──

@pytest.mark.parametrize("satz,kennung", [("Fasse die Notiz Preise zusammen", "a0000001"),
                                          ("Fasse die Karte Druckerei zusammen", "a0000002"),
                                          ("Fasse Druckerei zusammen", "a0000002")])
def test_zusammenfassen_einer_karte(satz, kennung) -> None:
    antwort = abk.loese(satz, KONTEXT)
    assert antwort["ereignis"] == "idea.summarize", antwort
    assert antwort["argumente"] == {"bubble_id": "b0b1e001", "idea_ids": [kennung]}


def test_zusammenfassen_der_bubble_bleibt() -> None:
    assert abk.loese("Fasse diese Bubble zusammen", KONTEXT)["argumente"] == {"bubble_id": "b0b1e001"}


def test_zusammenfassen_unbekannte_notiz_fragt_nach_der_notiz() -> None:
    antwort = abk.loese("Fasse die Notiz Laternen zusammen", KONTEXT)
    assert "Notiz „Laternen“" in antwort["rueckfrage"] and "Bubble" not in antwort["rueckfrage"].split("finde")[0]

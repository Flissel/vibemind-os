"""Rueckweg: Folgeplan mit eigener Kennung, reiner Text bei einem Textfeld, Absagen erkennen.

Namen und Kennungen erfunden.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import ideen_dokument as dok  # noqa: E402
from core import ideen_rueckweg as rw  # noqa: E402
from core.aenderung_zurueck_contract import gruppe_aus_plan_argument  # noqa: E402

KARTE = {"id": "a0000001", "title": "Sponsoren", "content": "Frist bis März", "linked_idea_id": "b0b1e001"}


def _auftrag(ergebnis, event_id="idea.expand", **mehr):
    _, rueckweg = rw.auftrag_vorbereiten(
        event_id, json.dumps({"id": "a0000001"}), "Formuliere die Notiz aus",
        lies_karte=lambda i: dict(KARTE) if i == KARTE["id"] else None, suche_titel=lambda t: [])
    return {"id": "f0000001", "capability": "idea_expand", "status": "fertig", "plan_id": "ideen_1",
            "trace_id": "spur_1", "hop_id": "s1", "ergebnis": ergebnis,
            "uebergabe": {"rueckweg": rueckweg}, **mehr}


# ── Format: reiner Text nur bei genau einem Textfeld ─────────────────────

@pytest.mark.parametrize("vertrag", ["ausformulieren", "einschaetzen"])
def test_reiner_text_gilt_bei_einem_textfeld(vertrag) -> None:
    werte, satz = rw.ergebnis_pruefen(vertrag, "Die Sponsoren zahlen bis Ende März.\nDanach druckt die Bühne.")
    assert satz == ""
    assert werte == {"text": "Die Sponsoren zahlen bis Ende März.\nDanach druckt die Bühne."}


def test_json_bleibt_gueltig_und_kaputtes_json_wird_nicht_zu_text() -> None:
    assert rw.ergebnis_pruefen("ausformulieren", '{"text": "Neu."}') == ({"text": "Neu."}, "")
    werte, satz = rw.ergebnis_pruefen("ausformulieren", '{"text": "Neu."')
    assert werte is None and "vereinbarten Form" in satz


def test_mehrere_felder_bleiben_streng_json() -> None:
    werte, satz = rw.ergebnis_pruefen("bewerten", "Gute Idee, 80 Punkte.")
    assert werte is None and "vereinbarten Form" in satz


@pytest.mark.parametrize("roh", [
    '<tool_call>{"name": "idea_update"}</tool_call>',
    'Ich rufe das auf: {"tool_calls": [{"name": "idea_update"}]}',
])
def test_werkzeug_aufruf_als_text_faellt(roh) -> None:
    werte, satz = rw.ergebnis_pruefen("ausformulieren", roh)
    assert werte is None and "Werkzeug" in satz


@pytest.mark.parametrize("roh", [
    "Ich kann die Notiz nicht ausformulieren, weil mir der Zusammenhang fehlt.",
    "Leider kann ich diese Aufgabe nicht erledigen.",
    "Es tut mir leid, aber das geht so nicht.",
    "Als KI habe ich keine Meinung zu Sponsoren.",
    "I can't help with that request.",
    "Mir liegen keine Informationen zu dieser Notiz vor.",
    "Gerne. Ich habe keinen Zugriff auf den Inhalt der Karte.",
    "Das Werkzeug idea_update steht mir nicht zur Verfügung.",
    "Um das zu tun, bräuchte ich ein Werkzeug, das hier nicht verfügbar ist.",
    "The tool idea_update is not available to me.",
])
def test_absage_ist_kein_inhalt(roh) -> None:
    assert rw.ist_verweigerung(roh)
    werte, satz = rw.ergebnis_pruefen("ausformulieren", roh)
    assert werte is None and satz == rw.VERWEIGERT


@pytest.mark.parametrize("roh", ["Hier ist die ausformulierte Notiz: Die Sponsoren zahlen.",
                                 "Gerne! Die Sponsoren zahlen bis März."])
def test_vorrede_landet_nicht_in_der_karte(roh) -> None:
    werte, satz = rw.ergebnis_pruefen("ausformulieren", roh)
    assert werte is None and "Vorrede" in satz


@pytest.mark.parametrize("roh", [
    'Hier meine Bewertung:\n```json\n{"score": 70, "begruendung": "Trägt."}\n```\nViel Erfolg!',
    'Ergebnis: {"score": 70, "begruendung": "Trägt."} – fertig.',
])
def test_mehrere_felder_schneiden_genau_ein_objekt_heraus(roh) -> None:
    assert rw.ergebnis_pruefen("bewerten", roh) == ({"score": 70, "begruendung": "Trägt."}, "")


def test_zwei_objekte_oder_absage_ohne_objekt_bleiben_abgelehnt() -> None:
    werte, satz = rw.ergebnis_pruefen("bewerten", '{"score": 1, "begruendung": "a"} {"score": 2, "begruendung": "b"}')
    assert werte is None and "vereinbarten Form" in satz
    werte, satz = rw.ergebnis_pruefen("bewerten", "Das Werkzeug steht mir nicht zur Verfügung.")
    assert werte is None and satz == rw.VERWEIGERT


def test_ein_textfeld_nimmt_eingebettetes_objekt() -> None:
    assert rw.ergebnis_pruefen("ausformulieren", 'Ergebnis: {"text": "Neu."}') == ({"text": "Neu."}, "")


def test_dokument_schneidet_ein_objekt_heraus() -> None:
    roh = 'So:\n```json\n{"ueberschrift": "Bühne", "text": "Steht.", "verweise": ["K1"]}\n```'
    werte, satz = dok.ergebnis_pruefen("abschnitt", roh, ["K1"])
    assert satz == "" and werte["text"] == "Steht."
    assert dok.ergebnis_pruefen("abschnitt", "Ich kann aus diesen Notizen keinen Abschnitt schreiben.", ["K1"])[1] \
        == rw.VERWEIGERT


def test_absage_im_json_feld_faellt_auch() -> None:
    werte, satz = rw.ergebnis_pruefen("bewerten", json.dumps(
        {"score": 50, "begruendung": "Ich kann die Notiz nicht bewerten, es fehlt Inhalt."}))
    assert werte is None and satz == rw.VERWEIGERT


@pytest.mark.parametrize("roh", [
    "Ich kann am Freitag nicht kommen, deshalb übernimmt Mia den Aufbau.",
    "Leider fehlt noch der Bühnenplan. Die Sponsoren zahlen bis März.",
    "Die Sponsoren können nicht vor März zahlen; der Druck verschiebt sich.",
])
def test_notiz_in_ich_form_ist_keine_absage(roh) -> None:
    assert not rw.ist_verweigerung(roh)
    assert rw.ergebnis_pruefen("ausformulieren", roh)[1] == ""


def test_dokument_abschnitt_lehnt_absage_und_werkzeug_ab() -> None:
    assert dok._text_ok("Ich kann aus diesen Notizen keinen Abschnitt schreiben.", 2000, "den Text")[0] is None
    assert dok._text_ok('{"tool_name": "idea_document"}', 2000, "den Text")[0] is None
    assert dok._text_ok("Der Bühnenplan steht.", 2000, "den Text") == ("Der Bühnenplan steht.", "")


# ── Folgeplan ────────────────────────────────────────────────────────────

def test_folgeplan_kennung() -> None:
    assert rw.folgeplan_id({"plan_id": "ideen_1"}) == "ideen_1-r"
    assert rw.folgeplan_id({"plan_id": "ideen_1-r"}) == "ideen_1-r"
    assert rw.folgeplan_id({"id": "f0000001-0000"}) == "auftrag_f0000001-r"


def test_laufzeit_des_folgeplans(monkeypatch) -> None:
    monkeypatch.setattr("core.capability_targets.find_registered_mcp_authority",
                        lambda a, s, w: SimpleNamespace(event_id="idea.update", space_id="ideas", agent=a))
    kontext = rw.laufzeit_kontext("idea_update", "idea.update", _auftrag("x"), 1)
    assert kontext.plan_id == "ideen_1-r"
    assert kontext.plan_revision == 1
    assert kontext.correlation_id == "spur_1"
    assert kontext.step_id == "s1/rueckweg/01/idea_update"


def test_offene_freigabe_schreibt_nichts_und_meldet(monkeypatch) -> None:
    geschrieben = []

    def aufruf(ereignis, argumente):
        raise rw.FreigabeAusstehend(ereignis)

    pruefung = rw.zurueckschreiben(
        _auftrag('{"text": "Die Sponsoren zahlen bis Ende März."}'),
        lies_karte=lambda i: dict(KARTE), aufruf=aufruf,
        pruefe=lambda *a: geschrieben.append(a) or (True, ""))
    assert pruefung["rueckweg"] == "wartet"
    assert pruefung["verified"] is None
    assert pruefung["geschrieben"] == [] and geschrieben == []
    assert pruefung["vorschlag"] == "Die Sponsoren zahlen bis Ende März."
    assert pruefung["plan_id"] == "ideen_1-r"
    assert "wartet auf Freigabe" in pruefung["meldung"]


def test_anschluss_wirft_bei_offener_freigabe(monkeypatch) -> None:
    class _Mcp:
        def __init__(self, ziel):
            pass

        def call_with_arg(self, *_a, **_k):
            return {"ok": False, "pending": True, "authority_status": "pending_approval"}

    monkeypatch.setattr("core.capability_targets.McpExecutor", _Mcp)
    monkeypatch.setattr(rw, "laufzeit_kontext", lambda *a: object())
    with pytest.raises(rw.FreigabeAusstehend):
        rw.anschluss(_auftrag("x"))["aufruf"]("idea.update", {"id": "a0000001", "content": "x"})


def test_wartet_wird_abgeschlossen_nicht_wiederholt() -> None:
    from core.nachfasser import Nachfasser

    gesetzt = []
    tabelle = SimpleNamespace(pruefung_setzen=lambda i, p: True,
                              pruefung_ergaenzen=lambda i, p: gesetzt.append(p))
    nf = Nachfasser.__new__(Nachfasser)
    nf.tabelle = tabelle
    nf._dokument_anschluss = None
    nf._rueckweg_anschluss = lambda a: {
        "lies_karte": lambda i: dict(KARTE), "pruefe": lambda *x: (True, ""),
        "aufruf": lambda e, a: (_ for _ in ()).throw(rw.FreigabeAusstehend(e))}
    nf._protokollieren = lambda *a: None
    z = {"geprueft": 0, "bestaetigt": 0, "abgelehnt_pruefung": 0}
    nf._rueckweg(_auftrag('{"text": "Neu."}'), z)
    assert gesetzt[-1]["fortsetzung"] == "fehler"
    assert "wartet auf Freigabe" in gesetzt[-1]["fortsetzung_fehler"]


def test_zurueck_nimmt_den_folgeplan_beim_namen() -> None:
    gruppe = "00000000-0000-4000-8000-000000000001-r"
    assert gruppe_aus_plan_argument("", f"Nimm {gruppe} zurück") == gruppe

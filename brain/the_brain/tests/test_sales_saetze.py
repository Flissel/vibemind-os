"""Feste Sales-Saetze (core/sales_saetze) und Antwortsaetze (core/sales_antwort), ohne torch.

Testsaetze aus docs/operations/sales-space/SAETZE.md, Antwortsaetze wortgleich aus
docs/operations/sales-space/antwortsaetze-faelle.json. Alle Namen sind erfunden.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_BRAIN = Path(__file__).resolve().parents[1]
_REPO = _BRAIN.parents[1]
_FAELLE = _REPO / "docs" / "operations" / "sales-space" / "antwortsaetze-faelle.json"


def _lade(name: str):
    spec = importlib.util.spec_from_file_location(f"sales_test_{name}", _BRAIN / "core" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


S = _lade("sales_saetze")
A = _lade("sales_antwort")
K = {"space": "sales"}


@pytest.mark.parametrize("satz,art,eingabe", [
    ("Welche Termine habe ich heute?", "termine_heute", {}),
    ("Was für Termine hab ich heute noch", "termine_heute", {}),
    ("Wie viele Termine habe ich heute?", "termine_heute", {}),
    ("Hab ich heute noch Termine?", "termine_heute", {}),
    ("Was steht heute im Kalender?", "termine_heute", {}),
    ("Was steht heute noch an?", "termine_heute", {}),
    ("Wie voll ist mein Tag?", "termine_heute", {}),
    ("Zeig mir die Termine für heute.", "termine_heute", {}),
    ("Was liegt in den Freigaben?", "freigaben_zaehlen", {}),
    ("Muss ich was freigeben?", "freigaben_zaehlen", {}),
    ("Wie viele Entwürfe warten?", "freigaben_zaehlen", {}),
    ("Wie viele Entwürfe warten auf meine Freigabe?", "freigaben_zaehlen", {}),
    ("Gibt es offene Freigaben?", "freigaben_zaehlen", {}),
    ("Wartet etwas auf Freigabe?", "freigaben_zaehlen", {}),
    ("Wie viele ungelesene Nachrichten?", "post_offen", {}),
    ("Wie viele offene Mails hab ich?", "post_offen", {}),
    ("Hat mir jemand geschrieben?", "post_offen", {}),
    ("Ist Post offen?", "post_offen", {}),
    ("Hab ich neue Nachrichten?", "post_offen", {}),
    ("Was ist im Posteingang offen?", "post_offen", {}),
    ("Zeig den Kontakt Muster.", "kontakt_finden", {"suche": "Muster"}),
    ("Zeig mir den Kontakt „Erika Muster“", "kontakt_finden", {"suche": "Erika Muster"}),
    ("Such den Kontakt Beispiel AG", "kontakt_finden", {"suche": "Beispiel AG"}),
    ("Wen haben wir bei der Beispielbäckerei?", "kontakt_finden", {"suche": "Beispielbäckerei"}),
    ("Gibt es einen Kontakt namens Muster?", "kontakt_finden", {"suche": "Muster"}),
])
def test_lesesatz_wird_fester_aufruf(satz, art, eingabe):
    t = S.loese(satz, K)
    assert t is not None and "rueckfrage" not in t, satz
    assert t["art"] == art
    assert t["ereignis"] == t["capability"] == f"sales.{art}"
    assert t["argumente"] == {"eingabe": eingabe}
    assert t["antwort"] == S.SOFORT[art]


@pytest.mark.parametrize("satz", [
    "Gib alle Freigaben frei.",
    "Schick die Entwürfe ab.",
    "Schreib dem Kontakt Muster eine Mail.",
    "Lösch den Kontakt Muster.",
    "Trag mir heute um 15 Uhr einen Termin ein.",
    "Markier die Post als erledigt.",
    "Welche Termine habe ich morgen?",
    "Wie wird das Wetter?",
    "Antworte auf die Post.",
    "Leg einen Kontakt Muster an.",
])
def test_veraendernd_oder_fremd_ist_nie_ein_treffer(satz):
    assert S.loese(satz, K) is None


@pytest.mark.parametrize("satz", ["Zeig den Kontakt X.", "Zeig den Kontakt „ “", "Such den Kontakt " + "a" * 101])
def test_zu_kurze_oder_zu_lange_suche_fragt_nach(satz):
    assert S.loese(satz, K) == {"rueckfrage": S.SUCHE_RUECKFRAGE}


@pytest.mark.parametrize("kontext", [None, {}, {"space": "ideas"}, {"space": 3}, "sales"])
def test_nur_im_sales_reiter(kontext):
    assert S.loese("Welche Termine habe ich heute?", kontext) is None


def test_wirft_nie():
    assert S.loese(None, K) is None  # type: ignore[arg-type]
    assert S.loese("", K) is None


def test_faehigkeiten_sind_genau_die_vier_lesenden_arten():
    assert set(S.SALES_SAETZE) == {"termine_heute", "freigaben_zaehlen", "post_offen", "kontakt_finden"}
    assert S.ist_sales_faehigkeit("sales.kontakt_finden")
    assert not S.ist_sales_faehigkeit("sales.post_pruefen")
    assert not S.ist_sales_faehigkeit("idea.rate")


# ── Antwortsaetze: wortgleich mit den Testfaellen ──────────────────────────
_FAELLE_LISTE = json.loads(_FAELLE.read_text(encoding="utf-8"))["faelle"]


@pytest.mark.parametrize("fall", _FAELLE_LISTE, ids=lambda f: f"{f['art']}-{f['stand']}-{f['satz'][:24]}")
def test_antwortsatz_wie_im_testfall(fall):
    assert A.satz(fall["art"], fall["stand"], fall["auftrag_id"], fall.get("ergebnis")) == fall["satz"]


def test_satz_nennt_nie_titel_oder_suchtext():
    e = {"treffer": [{"kontakt_id": "aaaaaaaa-0000-4000-8000-000000000001", "titel": "Erika Muster",
                      "link": "/kontakte/x"}], "anzahl": 1}
    text = A.satz("kontakt_finden", "fertig", "4aef2c02-0000-4000-8000-000000000001", e)
    assert "Erika" not in text and "Muster" not in text


@pytest.mark.parametrize("wert", [True, -1, 10 ** 9 + 1, 1.5, "3", None])
def test_zahlen_nur_ganz_und_im_rahmen(wert):
    assert not A.form_ok("post_offen", {"chats_unbeantwortet": wert, "mails_offen": 0})


# ── Quittung des Anlegens ──────────────────────────────────────────────────
NR = "4aef2c02-0000-4000-8000-000000000001"


def _mcp(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def test_quittung_ergibt_laeuft_satz_ziel_und_auftrag():
    q = json.dumps({"auftrag_id": NR, "an": "sales", "art": "kontakt_finden", "version": 1, "stand": "offen"})
    f = A.antwort_fuer_aufruf("sales.kontakt_finden", True, _mcp(q), None)
    assert f["ok"] is True and f["space"] == "sales"
    assert f["final_text"] == "Ich suche (Auftrag 4aef2c02)."
    assert f["auftrag"] == {"id": NR, "art": "kontakt_finden", "stand": "offen"}
    assert f["ziel"] == {"space": "sales", "pfad": "/kontakte"}


def test_quittung_auch_als_reiner_text_oder_dict():
    q = {"auftrag_id": NR, "an": "sales", "art": "termine_heute", "version": 2, "stand": "offen"}
    assert A.antwort_fuer_aufruf("sales.termine_heute", True, q, None)["ok"] is True
    assert A.antwort_fuer_aufruf("sales.termine_heute", True, json.dumps(q), None)["ok"] is True


def test_quittung_fremder_art_oder_fremden_empfaengers_gilt_nicht():
    q = {"auftrag_id": NR, "an": "marketing", "art": "termine_heute", "stand": "offen"}
    assert A.antwort_fuer_aufruf("sales.termine_heute", True, q, None)["ok"] is False
    q = {"auftrag_id": NR, "an": "sales", "art": "post_offen", "stand": "offen"}
    assert A.antwort_fuer_aufruf("sales.termine_heute", True, q, None)["ok"] is False


@pytest.mark.parametrize("text,erwartet", [
    ("Unbekannte Auftragsart", "gesperrt"),
    ("nicht gefunden", "gesperrt"),
    ("Tagesgrenze erreicht: hoechstens 60 Auftraege des Gehirns pro Tag", "grenze"),
    ("Error: MCP tool call failed: Transport closed", "weg"),
])
def test_ablehnung_ergibt_festen_satz_ohne_werkzeugtext(text, erwartet):
    f = A.antwort_fuer_aufruf("sales.freigaben_zaehlen", False, _mcp(text), None)
    assert f["ok"] is False and f["space"] == "sales" and "ziel" not in f
    soll = {"gesperrt": A.GESPERRT["freigaben_zaehlen"], "grenze": A.TAGESGRENZE, "weg": A.NICHT_ERREICHBAR}
    assert f["final_text"] == soll[erwartet]
    assert text not in f["final_text"]


@pytest.mark.parametrize("text,erwartet", [
    ("Unbekannte Auftragsart", "Unbekannte Auftragsart"),
    ("nicht gefunden", "nicht gefunden"),
    ("Tagesgrenze erreicht: hoechstens 60 Auftraege des Gehirns pro Tag",
     "Tagesgrenze erreicht: hoechstens 60 Auftraege des Gehirns pro Tag"),
    ("Eingabe passt nicht zum Vertrag: suche bei Erika Muster", "Eingabe passt nicht zum Vertrag"),
    ("Kontakt Erika Muster unbekannt", "Auftragsbuch: isError=true"),
    ("", "Auftragsbuch: isError=true"),
])
def test_ablehnung_aus_ergebnis_reicht_nur_feste_texte_durch(text, erwartet):
    roh = {"content": [{"type": "text", "text": text}], "isError": True}
    assert A.ablehnung_aus_ergebnis(roh) == erwartet
    assert A.ablehnung_aus_ergebnis(None) == "Auftragsbuch: isError=true"


def test_durchgereichte_ablehnung_wird_gesperrt_satz():
    fehler = A.ablehnung_aus_ergebnis(_mcp("Unbekannte Auftragsart"))
    f = A.antwort_fuer_aufruf("sales.termine_heute", False, None, fehler)
    assert f["final_text"] == A.GESPERRT["termine_heute"]


def test_ablehnung_im_fehlerfeld_zaehlt_auch():
    f = A.antwort_fuer_aufruf("sales.post_offen", False, None, "Unbekannte Auftragsart")
    assert f["final_text"] == A.GESPERRT["post_offen"]


# ── Sprungziel ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("art,pfad", [("termine_heute", "/kalender"), ("freigaben_zaehlen", "/freigaben"),
                                      ("post_offen", "/posteingang"), ("kontakt_finden", "/kontakte")])
def test_ziel_je_art(art, pfad):
    assert A.ziel(art, None) == {"space": "sales", "pfad": pfad}


def test_ziel_ein_treffer_springt_auf_dessen_seite_ohne_titel():
    kid = "aaaaaaaa-0000-4000-8000-000000000001"
    e = {"treffer": [{"kontakt_id": kid, "titel": "Erika Muster", "link": "https://fremd.invalid/x"}],
         "anzahl": 1}
    assert A.ziel("kontakt_finden", e) == {"space": "sales", "pfad": f"/kontakte/{kid}"}
    e["treffer"][0]["kontakt_id"] = "../../admin"
    assert A.ziel("kontakt_finden", e) == {"space": "sales", "pfad": "/kontakte"}

"""Inhaltsfrage und Gruppieren (Arbeitslauf 2, Punkt 3). Ohne Netz; Namen erfunden."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import ideen_abkuerzung as abk  # noqa: E402
from core.ideen_fest_contract import feste_argumente  # noqa: E402

KONTEXT = {"space": "ideas", "bubble": {"id": "b0b1e001", "title": "Stadtfest"},
           "cards": [{"id": "a0000001", "title": "Preise"}, {"id": "a0000002", "title": "Bühnenplan"}]}


@pytest.fixture(autouse=True)
def _leer():
    abk._OFFEN.clear()
    yield
    abk._OFFEN.clear()


@pytest.mark.parametrize("satz", [
    "Was steht in der Notiz Preise?", "Was steht bei Preise", "Worum geht es bei Preise?",
    "Lies mir Preise vor", "Zeig mir den Inhalt von Preise",
])
def test_inhaltsfrage_liest_fest_und_merkt_die_karte(satz) -> None:
    treffer = abk.loese(satz, KONTEXT)
    assert treffer["ereignis"] == "idea.list" and treffer["lies"] == {"id": "a0000001", "title": "Preise"}
    assert treffer["argumente"] == {"bubble_id": "b0b1e001", "full": True, "limit": 100}
    argumente, frage = feste_argumente("idea.list", json.dumps(treffer["argumente"]))
    assert argumente is not None, frage


def test_was_steht_hier_bleibt_die_liste() -> None:
    treffer = abk.loese("Was steht hier?", KONTEXT)
    assert treffer["ereignis"] == "idea.list" and "lies" not in treffer


def _ergebnis(zeilen):
    return {"content": [{"type": "text", "text": json.dumps({"karten": zeilen, "gesamt": len(zeilen)})}],
            "isError": False}


def test_antwort_zeigt_nur_die_gefragte_karte_und_den_lebenden_text() -> None:
    zeilen = [{"id": "a0000001", "title": "Preise", "content": "Preise",
               "content_json": {"type": "table", "text": "Stand 900\nDruck 120"}},
              {"id": "a0000002", "title": "Bühnenplan", "content": "Zwei Bühnen"}]
    kontext = {**KONTEXT, "_lies_karte": {"id": "a0000001", "title": "Preise"}}
    assert abk.antwort_fuer_schritt("idea.list", True, _ergebnis(zeilen), None, "", kontext) == (
        "„Preise“:\nStand 900\nDruck 120")
    leer = [{"id": "a0000001", "title": "Preise", "content": "Preise"}]
    assert abk.antwort_fuer_schritt("idea.list", True, _ergebnis(leer), None, "", kontext) == (
        "„Preise“ hat noch keinen Text.")
    assert abk.antwort_fuer_schritt("idea.list", True, _ergebnis([]), None, "", kontext) == (
        "„Preise“ kann ich gerade nicht lesen.")
    lang = [{"id": "a0000001", "title": "Preise", "content": "x" * 3000}]
    assert abk.antwort_fuer_schritt("idea.list", True, _ergebnis(lang), None, "", kontext).endswith(" …")


@pytest.mark.parametrize("satz", [
    "Gruppiere die Notizen", "gruppiere nach Thema", "Ordne die Karten nach Thema", "Was gehört zusammen?",
    "Bilde Themengruppen", "Cluster die Ideen",
])
def test_gruppieren_wird_ein_auftrag_mit_rueckweg(satz) -> None:
    treffer = abk.loese(satz, KONTEXT)
    assert treffer["ereignis"] == "idea.group" and treffer["capability"] == "idea_group"
    assert treffer["argumente"] == {"bubble_id": "b0b1e001"}


def test_gruppieren_und_verbinden() -> None:
    treffer = abk.loese("Gruppiere die Notizen und verbinde sie", KONTEXT)
    assert treffer["argumente"] == {"bubble_id": "b0b1e001", "verbinden": True}


def test_gruppieren_ueber_hundert_karten_sagt_die_grenze() -> None:
    antwort = abk.loese("Ordne die Notizen nach Thema", {**KONTEXT, "cards_total": 101})
    assert antwort["rueckfrage"].startswith("Gruppieren geht bis 100 Notizen; hier sind es 101.")


@pytest.mark.parametrize("satz", ["Füge ein Bild zu Bühnenplan hinzu", "Häng ein Foto an die Karte Sponsoren",
                                  "Setze ein Bild in diese Notiz"])
def test_bild_an_karte_sagt_ehrlich_dass_es_noch_nicht_geht(satz) -> None:
    assert abk.loese(satz, KONTEXT) == {"rueckfrage": abk.BILD_NOCH_NICHT}


@pytest.mark.parametrize("satz,thema", [
    ("Recherchiere zu dieser Bubble", "das Thema der Bubble „Stadtfest“"),
    ("Recherchiere zu dieser Bubble: Ticketpreise anderer Stadtfeste", "Ticketpreise anderer Stadtfeste"),
    ("Recherchiere zu der Bubble Stadtfest: Sponsoren aus der Region", "Sponsoren aus der Region"),
    ("Recherchiere zu Bühnentechnik für kleine Plätze", "Bühnentechnik für kleine Plätze"),
    ("Mach eine Recherche zu Sponsoren", "Sponsoren"),
    ("Bitte recherchiere über Stadtfest", "Stadtfest"),
])
def test_recherche_zu_bubble_bringt_bubble_und_thema(satz, thema) -> None:
    # Kein Aufruf hier: Vorschau und Rueckfrage macht das Gehirn (core/recherche_bubble).
    assert abk.loese(satz, KONTEXT) == {
        "recherche": {"bubble_id": "b0b1e001", "bubble_titel": "Stadtfest", "thema": thema}}


def test_recherche_ohne_offene_bubble_fragt() -> None:
    antwort = abk.loese("Recherchiere zu dieser Bubble", {"space": "ideas", "cards": []})
    assert "Bubble" in antwort["rueckfrage"]


def test_recherche_zu_unbekannter_bubble_fragt() -> None:
    antwort = abk.loese("Recherchiere zu der Bubble Laternenlauf", KONTEXT)
    assert antwort == {"rueckfrage": "Ich finde keine Bubble mit dem Titel „Laternenlauf“."}


def test_recherche_im_web_bleibt_beim_browser_weg() -> None:
    antwort = abk.loese("Recherchiere im Web zu Sponsoren", KONTEXT)
    assert antwort is None or "recherche" not in antwort


def test_bildungsplan_ist_kein_bild() -> None:
    antwort = abk.loese("Füge Bildungsplan zu Stadtfest hinzu", KONTEXT)
    assert antwort is None or antwort.get("rueckfrage") != abk.BILD_NOCH_NICHT


def test_ordnen_und_verbinden_bleiben_wie_sie_sind() -> None:
    assert abk.loese("Ordne die Karten", KONTEXT)["ereignis"] == "idea.arrange"
    assert abk.loese("Verbinde, was zusammengehört", KONTEXT)["ereignis"] == "idea.auto_link"

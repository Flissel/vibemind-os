"""Zaehl- und Suchfragen ueber die Karten der offenen Bubble (Lauf 5, Punkt 9). Namen erfunden."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import ideen_abkuerzung as abk  # noqa: E402

KONTEXT = {"space": "ideas", "bubble": {"id": "b0b1e001", "title": "Stadtfest"},
           "cards": [{"id": "a0000001", "title": "Preise"}, {"id": "a0000002", "title": "Bühnenplan"}]}

ZEILEN = [
    {"id": "a0000001", "title": "Preise", "content": "Stand 900, Druck 120"},
    {"id": "a0000002", "title": "Bühnenplan", "content": "Zwei Bühnen",
     "content_json": {"type": "doc", "text": "Sponsoren zahlen die Technik"}},
    {"id": "a0000003", "title": "Sponsoren", "content": "Liste offen"},
]


@pytest.fixture(autouse=True)
def _leer():
    abk._OFFEN.clear()
    yield
    abk._OFFEN.clear()


def _ergebnis(zeilen, gesamt=None):
    inhalt = {"karten": zeilen, "gesamt": len(zeilen) if gesamt is None else gesamt}
    return {"content": [{"type": "text", "text": json.dumps(inhalt)}], "isError": False}


@pytest.mark.parametrize("satz", [
    "Wie viele Karten gibt es?", "Wie viele Notizen sind hier?", "Wie viele Ideen hat diese Bubble?",
    "wie viele karten sind in dieser bubble", "Wie viele Karten habe ich insgesamt?",
])
def test_zaehlfrage_ist_fester_leseweg(satz) -> None:
    treffer = abk.loese(satz, KONTEXT)
    assert treffer["ereignis"] == "idea.list" and treffer["kartenfrage"] == {"art": "zahl"}
    assert treffer["argumente"] == {"bubble_id": "b0b1e001", "full": True, "limit": 100}


@pytest.mark.parametrize("satz,wort", [
    ("Welche Karten handeln von Sponsoren?", "Sponsoren"),
    ("Welche Notizen erwähnen den Druck?", "Druck"),
    ("Welche Ideen haben mit Technik zu tun?", "Technik"),
    ("In welchen Notizen steht etwas über Preise?", "Preise"),
    ("Wo steht etwas zu „Bühnen“?", "Bühnen"),
    ("Gibt es eine Karte zu Sponsoren?", "Sponsoren"),
    ("Durchsuche die Karten nach Druck", "Druck"),
    ("Wie viele Notizen erwähnen Sponsoren?", "Sponsoren"),
])
def test_suchfrage_nimmt_das_stichwort(satz, wort) -> None:
    treffer = abk.loese(satz, KONTEXT)
    assert treffer["ereignis"] == "idea.list" and treffer["kartenfrage"] == {"art": "suche", "wort": wort}


@pytest.mark.parametrize("satz", [
    "Welche Risiken siehst du in dieser Bubble?", "Was fehlt noch?", "Wie viele Karten fehlen noch für den Plan?",
    "Welche Karten gibt es?",
])
def test_urteil_und_anderes_ist_keine_kartenfrage(satz) -> None:
    assert abk.kartenfrage(satz) is None


def _antwort(frage, zeilen=ZEILEN, gesamt=None):
    kontext = {**KONTEXT, "_kartenfrage": frage}
    return abk.antwort_fuer_schritt("idea.list", True, _ergebnis(zeilen, gesamt), None, "", kontext)


def test_zahl_kommt_aus_der_zaehlung_des_servers() -> None:
    assert _antwort({"art": "zahl"}) == "In „Stadtfest“ gibt es 3 Notizen."
    assert _antwort({"art": "zahl"}, ZEILEN, gesamt=140) == "In „Stadtfest“ gibt es 140 Notizen."
    assert _antwort({"art": "zahl"}, ZEILEN[:1]) == "In „Stadtfest“ gibt es 1 Notiz."
    assert _antwort({"art": "zahl"}, []) == "In „Stadtfest“ gibt es noch keine Notizen."


def test_suche_liest_titel_text_und_tabelle() -> None:
    assert _antwort({"art": "suche", "wort": "Sponsoren"}) == (
        "2 Notizen in „Stadtfest“ erwähnen „Sponsoren“: „Bühnenplan“, „Sponsoren“.")
    assert _antwort({"art": "suche", "wort": "druck"}) == "1 Notiz in „Stadtfest“ erwähnt „druck“: „Preise“."
    assert _antwort({"art": "suche", "wort": "Feuerwerk"}) == (
        "Keine Notiz in „Stadtfest“ erwähnt „Feuerwerk“. " + abk.WOERTLICH)


def test_suche_findet_die_form_ohne_endung() -> None:
    assert _antwort({"art": "suche", "wort": "Bühnen"}).startswith("1 Notiz")
    assert _antwort({"art": "suche", "wort": "Preisen"}).endswith("„Preise“.")


def test_suche_ohne_gross_klein_umlaut_und_wortstamm() -> None:
    # Lauf 8: "Marketing" fand 0 Treffer. Namen erfunden.
    zeilen = [{"id": "a0000001", "title": "Werbeplan", "content": "MARKETING-Budget fuer das Stadtfest"},
              {"id": "a0000002", "title": "Buehnenplanungen", "content": "Zeitplan"},
              {"id": "a0000003", "title": "Druckerei", "content": "Angebote der Druckereien"}]
    assert _antwort({"art": "suche", "wort": "marketing"}, zeilen).endswith("„Werbeplan“.")
    assert _antwort({"art": "suche", "wort": "Bühnenplanung"}, zeilen).endswith("„Buehnenplanungen“.")
    assert _antwort({"art": "suche", "wort": "Druckereien"}, zeilen).endswith("„Druckerei“.")


@pytest.mark.parametrize("satz,wort", [
    ("Suche Marketing", "Marketing"), ("Suche nach Sponsoren", "Sponsoren"), ("Finde Druck", "Druck"),
    ("Finde alle Notizen zu Sponsoren", "Sponsoren"), ("Zeig mir alle Karten mit Preise", "Preise"),
    ("Suche Marketing in dieser Bubble", "Marketing"),
])
def test_kurze_suchsaetze_sind_kartensuche(satz, wort) -> None:
    assert abk.kartenfrage(satz) == {"art": "suche", "wort": wort}


@pytest.mark.parametrize("satz", [
    "Suche im Web nach Sponsoren", "Finde heraus, wer die Bühne baut", "Suche dir eine Karte aus",
    "Suche nach einem guten Weg, die Sponsoren für das Fest zu gewinnen",
])
def test_andere_suchsaetze_bleiben_wo_sie_sind(satz) -> None:
    assert abk.kartenfrage(satz) is None


def test_suche_sagt_ehrlich_wenn_nicht_alles_gelesen_ist() -> None:
    satz = _antwort({"art": "suche", "wort": "Feuerwerk"}, ZEILEN, gesamt=140)
    assert satz == ("Keine Notiz in „Stadtfest“ erwähnt „Feuerwerk“. " + abk.WOERTLICH
                    + " Durchsucht habe ich nur die ersten 3 von 140 Notizen.")


@pytest.mark.parametrize("satz", [
    "Welche Risiken siehst du?", "Welche Schwächen siehst du in dieser Bubble?", "Was fehlt noch?",
    "Was fehlt hier noch", "Was sind die größten Risiken?", "Siehst du hier Widersprüche?",
    "Was hältst du von dieser Bubble?", "Schätze diese Bubble ein",
])
def test_urteilsfrage_wird_einschaetzung_der_bubble(satz) -> None:
    treffer = abk.loese(satz, KONTEXT)
    assert treffer["ereignis"] == "idea.summarize" and treffer["capability"] == "idea_summarize"
    assert treffer["argumente"] == {"bubble_id": "b0b1e001", "einschaetzen": True}


def test_urteil_ueber_eine_karte_bleibt_bei_der_karte() -> None:
    treffer = abk.loese("Was hältst du von Preise?", KONTEXT)
    assert treffer["ereignis"] == "idea.explain" and treffer["argumente"] == {"id": "a0000001"}

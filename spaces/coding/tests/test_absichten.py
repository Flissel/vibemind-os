"""Feste Absichten: jeder Aufruf trifft einen Weg des Dienstes, jede Antwort wird ein deutscher Satz."""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pytest

from spaces.coding import absichten, dienst, pipeline

BEISPIEL = Path(__file__).resolve().parents[1] / "beispiele" / "aufgabenliste.entwurf.json"
DOKU = Path(__file__).resolve().parents[1] / "ABSICHTEN.md"


@pytest.fixture(autouse=True)
def _ablage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODING_PIPELINE_DIR", str(tmp_path / "auftraege"))
    monkeypatch.setenv("CODING_WORKSPACE_ROOT", str(tmp_path / "arbeit"))
    monkeypatch.delenv("CODING_DIENST_SCHLUESSEL", raising=False)


def _rufe(ziel: str, parameter: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
    """Wie der HTTP-Ausfuehrer des Gehirns: http:GET:<url>, Angaben als Abfrage."""
    art, methode, url = ziel.split(":", 2)
    assert (art, methode) == ("http", "GET")
    teile = urllib.parse.urlsplit(url)
    assert f"{teile.scheme}://{teile.netloc}" == absichten.DIENST
    pfad = teile.path + (f"?{urllib.parse.urlencode(parameter)}" if parameter else "")
    status, _, koerper = dienst.lesen(pfad)
    return status, json.loads(koerper)


def _frage(absicht: str) -> str:
    eintrag = absichten.LESEN[absicht]
    status, erste = _rufe(eintrag["aufrufe"][0], eintrag.get("parameter"))
    antworten = [erste]
    for ziel in eintrag["aufrufe"][1:]:
        if eintrag.get("wenn_fertig") and erste.get("zustand") != "fertig":
            antworten.append(None)
            continue
        antworten.append(_rufe(ziel)[1])
    return absichten.antwort(absicht, antworten, status)


def test_jeder_feste_aufruf_ist_ein_lesender_weg_des_dienstes() -> None:
    pipeline.auftrag_starten(entwurf=json.loads(BEISPIEL.read_text(encoding="utf-8")))
    for eintrag in absichten.LESEN.values():
        for ziel in eintrag["aufrufe"]:
            status, inhalt = _rufe(ziel, eintrag.get("parameter"))
            assert inhalt.get("error") != "unbekannter_pfad", ziel
            # /api/ergebnis ohne fertigen Lauf: 404 "kein passender Auftrag", kein fremder Weg.
            assert status in (200, 404), (ziel, inhalt)


def test_ohne_lauf_sagen_alle_lesenden_absichten_dasselbe() -> None:
    for absicht in ("code.status", "code.list", "code.show"):
        assert _frage(absicht) == absichten.KEIN_LAUF


def test_angelegter_lauf_wartet_und_steht_in_der_liste() -> None:
    pipeline.auftrag_starten(entwurf=json.loads(BEISPIEL.read_text(encoding="utf-8")))
    assert _frage("code.status") == "„Aufgabenliste im Speicher“ ist angelegt und wartet auf den Start."
    assert _frage("code.list") == "Es gibt einen Lauf: „Aufgabenliste im Speicher“ (wartet)."
    assert _frage("code.show") == "„Aufgabenliste im Speicher“ ist angelegt und wartet auf den Start."


def test_dienst_sagt_ob_er_auftraege_annimmt(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _frage("code.dienst") == "Der Coding-Dienst läuft und zeigt nur an und nimmt keine Aufträge an."
    monkeypatch.setenv("CODING_DIENST_SCHLUESSEL", "nur-fuer-den-test")
    assert _frage("code.dienst") == "Der Coding-Dienst läuft und nimmt Aufträge an."


def test_saetze_fuer_laufend_fertig_und_gescheitert() -> None:
    laeuft = {"titel": "Leseliste", "zustand": "laeuft", "stufe": "bau",
              "kurz": {"stufe": "bau", "paket_nr": 3, "pakete_gesamt": 7}}
    assert absichten.antwort("code.status", [laeuft]) == "„Leseliste“ läuft gerade: Bau, Paket 3 von 7."
    gescheitert = {"titel": "Leseliste", "zustand": "fehlgeschlagen",
                   "fehler": "Prüfung: Paket 1 von 7 „Bücher eintragen“ ist gescheitert."}
    assert absichten.antwort("code.status", [gescheitert]) == (
        "„Leseliste“ ist gescheitert. Prüfung: Paket 1 von 7 „Bücher eintragen“ ist gescheitert.")
    fertig = {"titel": "Leseliste", "zustand": "fertig", "dauer_s": 754}
    ergebnis = {"pakete": [{}] * 7, "dateien": ["a", "b", "c"], "pruefung": {"bestanden": True},
                "start": "npm start"}
    assert absichten.antwort("code.show", [fertig, ergebnis]) == (
        "„Leseliste“ ist fertig nach 12 Minuten. Gebaut wurden 7 Pakete mit 3 Dateien. "
        "Die unabhängige Abnahme ist bestanden. Gestartet wird die App mit „npm start“ in ihrem Ordner.")


def test_saetze_fuer_ungeprueft_und_laufende_abnahme() -> None:
    ungeprueft = {"titel": "Primfaktoren", "zustand": "ungeprueft",
                  "abnahme": {"zustand": "ungeprueft", "gruende": ["Für diese Aufgabe ist kein Prüfer genannt"]}}
    assert absichten.antwort("code.status", [ungeprueft]) == (
        "„Primfaktoren“ ist gebaut, aber ungeprüft: Für diese Aufgabe ist kein Prüfer genannt.")
    pruefend = {"titel": "Primfaktoren", "zustand": "laeuft", "abnahme": {"zustand": "laeuft"}}
    assert absichten.antwort("code.status", [pruefend]) == (
        "„Primfaktoren“ ist gebaut; die unabhängige Abnahme läuft.")


def test_angenommener_auftrag_wird_ein_satz() -> None:
    sicht = pipeline.auftrag_starten(entwurf=json.loads(BEISPIEL.read_text(encoding="utf-8")))
    satz = absichten.angenommen_satz(sicht)
    assert satz.startswith("„Aufgabenliste im Speicher“ ist angenommen: ")
    assert satz.endswith("Der Bau startet, sobald der Dienst frei ist.")
    assert absichten.angenommen_satz({"error": "schreiben_aus"}, 403) == (
        "Der Coding-Dienst nimmt gerade keine Aufträge an.")


def test_keine_verbindung_und_unbekannte_absicht() -> None:
    assert absichten.antwort("code.status", [None], status=0) == absichten.NICHT_ERREICHBAR
    with pytest.raises(ValueError):
        absichten.antwort("code.generate", [{}])


def test_liste_nennt_die_gesamtzahl_wenn_sie_gekuerzt_ist() -> None:
    liste = {"anzahl": 9, "auftraege": [{"titel": "A", "zustand": "fertig"}, {"titel": "B", "zustand": "laeuft"}]}
    assert absichten.liste_satz(liste) == "Es gibt 9 Läufe, die neuesten: „A“ (fertig), „B“ (läuft)."


def test_beschreibung_nennt_jede_absicht_und_jeden_aufruf() -> None:
    text = DOKU.read_text(encoding="utf-8")
    for absicht, eintrag in absichten.LESEN.items():
        assert f"`{absicht}`" in text, absicht
        for ziel in eintrag["aufrufe"]:
            assert ziel in text, ziel
    assert "`code.build`" in text and "coding-engine:POST:/api/v1/jobs" in text

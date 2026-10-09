"""Rowboat-Lauf am PC (Spec sales-claw 2026-10-09-marke-exakt-logo-wissen §3): Kandidaten -> Claude ->
geprueftes, gesichertes Schreiben. Rowboat nur unter tmp_path, API und Claude gefaelscht."""
import datetime
import json

import pytest

from spaces.marketing.claw import wissen_lauf, wissen_prompt
from spaces.marketing.tests.test_marken_arbeiter import Api, Fragen
from spaces.marketing.workers import marken_arbeiter as ma
from spaces.marketing.workers import wissen_arbeiter as wa

JETZT = datetime.datetime(2026, 10, 9, 14, 30)
FIRMEN = [{"id": "vibemind", "name": "VibeMind"}, {"id": "fin2gether", "name": "fin2gether"}]
WISSEN = {"id": "w1", "art": "wissen", "mandant": "vibemind", "firma": "VibeMind",
          "nachricht": "Wissen nach der Übernahme aktualisieren", "kontext": {"seit": 0.0}, "verlauf": [],
          "vorschlag": None}


@pytest.fixture
def wissen(tmp_path, monkeypatch):
    w = tmp_path / "knowledge"
    firma = w / "companys" / "VibeMind"
    (firma / "Marke-Verlauf").mkdir(parents=True)
    (firma / "Marke.md").write_text("---\nakzent: #f66c1e\n---\n## Ton\nWarm\n", encoding="utf-8")
    (firma / "Marke-Verlauf" / "2026-10-09-1400.md").write_text("---\nakzent: #5eead4\n---\n## Ton\nTechnisch\n",
                                                               encoding="utf-8")
    (w / "Projekte").mkdir()
    (w / "Projekte" / "Plan.md").write_text("Plan: VibeMind startet in Türkis.", encoding="utf-8")
    (w / "Projekte" / "Fremd.md").write_text("fin2gether Interna, auch VibeMind.", encoding="utf-8")
    (w / "companys" / "fin2gether").mkdir()
    (w / "companys" / "fin2gether" / "Geheim.md").write_text("fin2gether Geheimnis", encoding="utf-8")
    monkeypatch.setenv("ROWBOAT_KNOWLEDGE_ORDNER", str(w))
    monkeypatch.setenv("ROWBOAT_WISSEN_ORDNER", str(w / "companys"))
    monkeypatch.setenv("MARKETING_ARBEITER_ORDNER", str(tmp_path / "arbeit"))
    return w, firma


def _antwort(ersetzungen, handbuch="# Markenhandbuch VibeMind\nAkzent #f66c1e\n"):
    return json.dumps({"antwort": "Angepasst.", "ersetzungen": ersetzungen, "markenhandbuch": handbuch},
                      ensure_ascii=False)


def _lauf(api, fragen):
    return ma.ein_durchlauf(api, fragen, jetzt=lambda: JETZT, schlafen=lambda s: None,
                            webseite_lesen=lambda u: pytest.fail("kein Webseitenlesen"), logo_laden=lambda u: None)


def test_wissens_lauf_schreibt_gesichert_und_meldet(wissen):
    w, firma = wissen
    api = Api(dict(WISSEN), firmen=FIRMEN)
    fragen = Fragen(_antwort([{"pfad": "Projekte/Plan.md", "alt": "in Türkis", "neu": "in Orange"},
                              {"pfad": "Projekte/Plan.md", "alt": "gibt es nicht", "neu": "x"},
                              {"pfad": "companys/fin2gether/Geheim.md", "alt": "Geheimnis", "neu": "x"}]))
    assert _lauf(api, fragen) == "fertig"
    system, nachrichten = fragen.gesehen[0]
    t = nachrichten[0]["content"]
    assert system == wissen_prompt.SYSTEM and "websuche" not in fragen.kw[0]
    assert "### Projekte/Plan.md" in t and "akzent: #5eead4" in t and "- akzent: #5eead4 → #f66c1e" in t
    assert "Geheimnis" not in t and "Fremd.md" not in t
    assert (w / "Projekte" / "Plan.md").read_text(encoding="utf-8") == "Plan: VibeMind startet in Orange."
    assert (firma / "Wissen-Verlauf" / "2026-10-09-1430" / "Projekte" / "Plan.md").exists()
    assert (firma / "Markenhandbuch.md").read_text(encoding="utf-8").startswith("# Markenhandbuch VibeMind")
    assert (w / "companys" / "fin2gether" / "Geheim.md").read_text(encoding="utf-8") == "fin2gether Geheimnis"
    (_, aid, daten), = api.aufrufe("fertig")
    assert aid == "w1" and daten["antwort"].splitlines() == [
        "Wissen aktualisiert: 1 Datei", "- Projekte/Plan.md", "- Markenhandbuch: companys/VibeMind/Markenhandbuch.md"]
    assert "Projekte/Plan.md: Ausschnitt nicht genau einmal gefunden – verworfen" in daten["hinweise"]
    assert "companys/fin2gether/Geheim.md: nicht in der Kandidatenliste – verworfen" in daten["hinweise"]
    s = api.aufrufe("denken")[-1][3]
    assert s[0] == "Kandidaten: 1 Dokument" and "Geschrieben: Projekte/Plan.md" in s
    assert "Markenhandbuch geschrieben" in s


def test_abbruch_mittendrin_endet_fertig_mit_teilweise(wissen, monkeypatch):
    w, firma = wissen
    (firma / "Über uns.md").write_text("VibeMind in Türkis.", encoding="utf-8")
    echt, aufrufe = wissen_lauf.schreiben, []

    def schreiben(pfad, text, crlf, bom):
        aufrufe.append(pfad)
        if len(aufrufe) == 2:
            raise OSError("Platte voll")
        echt(pfad, text, crlf, bom)
    monkeypatch.setattr(wissen_lauf, "schreiben", schreiben)
    api = Api(dict(WISSEN), firmen=FIRMEN)
    fragen = Fragen(_antwort([{"pfad": "companys/VibeMind/Über uns.md", "alt": "Türkis", "neu": "Orange"},
                              {"pfad": "Projekte/Plan.md", "alt": "in Türkis", "neu": "in Orange"}]))
    assert _lauf(api, fragen) == "fertig"
    daten = api.aufrufe("fertig")[0][2]
    assert daten["antwort"].splitlines() == ["Wissen aktualisiert: 1 Datei", "- companys/VibeMind/Über uns.md"]
    assert daten["hinweise"][0].startswith("teilweise: abgebrochen bei Projekte/Plan.md (OSError: Platte voll")
    assert "geschrieben: companys/VibeMind/Über uns.md" in daten["hinweise"][0]
    assert "Türkis" in (w / "Projekte" / "Plan.md").read_text(encoding="utf-8")


def test_ohne_firmenordner_wird_zurueckgegeben(tmp_path, monkeypatch):
    (tmp_path / "companys").mkdir()
    monkeypatch.setenv("ROWBOAT_KNOWLEDGE_ORDNER", str(tmp_path))
    monkeypatch.setenv("ROWBOAT_WISSEN_ORDNER", str(tmp_path / "companys"))
    api = Api(dict(WISSEN), firmen=FIRMEN)
    assert _lauf(api, Fragen()) == "fehler"
    assert api.aufrufe("zurueck")[0][2] == "Wissen-Lauf nicht möglich: companys/VibeMind fehlt"


def test_zweimal_ungueltig_schreibt_nichts(wissen):
    w, firma = wissen
    api = Api(dict(WISSEN), firmen=FIRMEN)
    assert _lauf(api, Fragen("kein json", "auch nicht")) == "fehler"
    assert api.aufrufe("zurueck") and not (firma / "Markenhandbuch.md").exists()
    assert (w / "Projekte" / "Plan.md").read_text(encoding="utf-8") == "Plan: VibeMind startet in Türkis."


# --- Hilfsfunktionen (Ledger-Punkte) ------------------------------------------------------

def test_eigener_name_steht_nie_bei_den_fremden():
    api = Api(None, firmen=[{"id": "vibemind", "name": "VibeMind"}, {"id": "andere", "name": "vibemind"},
                            {"id": "fin2gether", "name": "fin2gether"}])
    assert wa._fremde(api, "vibemind", "VibeMind") == ["andere", "fin2gether"]


def test_sauber_ersetzt_nicht_kodierbare_zeichen():
    s = wa._sauber("Projekte/\ud800.md")
    s.encode("utf-8")
    assert s.startswith("Projekte/") and s.endswith(".md")
    assert wa._sauber(["a\ud800", "b"]) == [wa._sauber("a\ud800"), "b"]


def test_seit_wird_als_zahl_gelesen():
    assert wa._seit({"seit": 12}) == 12.0 and wa._seit({"seit": "1760000000.5"}) == 1760000000.5
    assert wa._seit({"seit": True}) is None and wa._seit({"seit": "x"}) is None
    assert wa._seit({"seit": float("nan")}) is None and wa._seit({}) is None


def test_lone_surrogate_im_hinweis_kippt_den_lauf_nicht(wissen, monkeypatch):
    w, firma = wissen
    echt = wissen_lauf.kandidaten

    def kandidaten(wurzel, ordner, namen, fremde, hinweise):
        hinweise.append("Ordner\ud800/x.md übersprungen (Verknüpfung)")
        return echt(wurzel, ordner, namen, fremde, hinweise)
    monkeypatch.setattr(wissen_lauf, "kandidaten", kandidaten)
    api = Api(dict(WISSEN), firmen=FIRMEN)
    assert _lauf(api, Fragen(_antwort([]))) == "fertig"
    for h in api.aufrufe("fertig")[0][2]["hinweise"]:
        h.encode("utf-8")

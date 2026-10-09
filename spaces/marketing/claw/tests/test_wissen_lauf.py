"""Rowboat-Lauf: Kandidaten, Ersetzungen, Sicherung (Spec sales-claw 2026-10-09-marke-exakt-logo-wissen §3).
Alle Tests nur unter tmp_path, nie in der echten ~/.rowboat-Ablage."""
import datetime
import os
import subprocess
import sys

import pytest

from spaces.marketing.claw import wissen_lauf as wl

JETZT = datetime.datetime(2026, 10, 9, 14, 30)
NAMEN = ["VibeMind", "vibemind"]
FREMDE = ["fin2gether"]


def _datei(pfad, text="", roh=None):
    pfad.parent.mkdir(parents=True, exist_ok=True)
    if roh is not None:
        pfad.write_bytes(roh)
    else:
        pfad.write_text(text, encoding="utf-8")
    return pfad


@pytest.fixture
def baum(tmp_path):
    w = tmp_path / "knowledge"
    firma = w / "companys" / "VibeMind"
    _datei(firma / "Marke.md", "---\nakzent: #5eead4\n---\n## Ton\nLocker\n")
    _datei(firma / "Über uns.md", "VibeMind baut Werkzeuge. Unsere Farbe ist Türkis.")
    _datei(firma / "Agent-Notizen" / "a.md", "Notiz ohne Namen")
    _datei(firma / "Markenhandbuch.md", "# Altes Handbuch")
    _datei(firma / "Marke-Verlauf" / "2026-10-09-1200.md", "VibeMind alt")
    _datei(firma / "Wissen-Verlauf" / "2026-10-01-0900" / "x.md", "VibeMind Sicherung")
    _datei(firma / "logo.png", roh=b"\x89PNG\r\n\x1a\nxx")
    _datei(w / "companys" / "fin2gether" / "Preise.md", "fin2gether arbeitet mit VibeMind")
    _datei(w / "Projekte" / "Plan.md", "Plan: VibeMind startet im Herbst in Türkis.")
    _datei(w / "Projekte" / "Andere.md", "Nichts dazu.")
    _datei(w / "Projekte" / "Gemischt.md", "VibeMind und fin2gether gemeinsam.")
    _datei(w / "Projekte" / "Wortteil.md", "vibemindful ist ein anderes Wort.")
    _datei(w / "Projekte" / "Notiz.txt", "VibeMind als Text")
    _datei(w / "People" / "Anna.md", "Anna arbeitet bei VibeMind.")
    _datei(w / "Diary" / "2026.md", "VibeMind Tagebuch")
    _datei(w / "Bewerbung" / "x.md", "VibeMind Bewerbung")
    _datei(w / "Voice Memos" / "y.md", "VibeMind Memo")
    _datei(w / "Gross.md", "VibeMind " + "x" * 21_000)
    return w, firma


def _rels(k):
    return sorted(x.rel for x in k)


def _kand(baum):
    w, firma = baum
    return w, firma, {k.rel: k for k in wl.kandidaten(str(w), str(firma), NAMEN, FREMDE, [])}


def test_nennungen_ganzes_wort_ohne_gross_klein():
    assert wl.nennungen("vibemind, VIBEMIND! VibeMinds vibemindful", ["VibeMind"]) == 2


def test_kandidaten_firmenbezug_sperrordner_und_fremde_firmen(baum):
    w, firma = baum
    hinweise = []
    k = wl.kandidaten(str(w), str(firma), NAMEN, FREMDE, hinweise)
    assert _rels(k) == ["Projekte/Plan.md", "companys/VibeMind/Agent-Notizen/a.md", "companys/VibeMind/Über uns.md"]
    assert "Projekte/Gemischt.md übersprungen (nennt eine andere Firma)" in hinweise
    assert k[0].rel == "companys/VibeMind/Über uns.md"            # Ordnernaehe und Nennungen zuerst


def test_verknuepfung_wird_uebersprungen(baum):
    if sys.platform != "win32":
        pytest.skip("Junctions gibt es nur unter Windows")
    w, firma = baum
    subprocess.run(["cmd", "/c", "mklink", "/J", str(w / "Projekte" / "Link"), str(w / "People")],
                   check=True, capture_output=True)
    hinweise = []
    k = wl.kandidaten(str(w), str(firma), NAMEN, FREMDE, hinweise)
    assert "Projekte/Link übersprungen (Verknüpfung)" in hinweise and not any("Link" in x.rel for x in k)


def test_hoechstens_vierzig_dokumente(tmp_path):
    w = tmp_path / "k"
    firma = w / "companys" / "VibeMind"
    firma.mkdir(parents=True)
    for i in range(45):
        _datei(w / "Projekte" / f"p{i:02}.md", f"VibeMind Nummer {i}")
    hinweise = []
    assert len(wl.kandidaten(str(w), str(firma), NAMEN, [], hinweise)) == 40
    assert "Wissen-Lauf: 40 von 45 Dokumenten (Obergrenze 40)" in hinweise


def test_nicht_utf8_ist_kein_kandidat(baum):
    """Review Focus 2."""
    w, firma = baum
    _datei(w / "Projekte" / "Latin.md", roh="VibeMind in Türkis".encode("latin-1"))
    assert "Projekte/Latin.md" not in _rels(wl.kandidaten(str(w), str(firma), NAMEN, FREMDE, []))


def test_ersetzen_genau_einmal():
    assert wl.ersetzen("a b", "a", "x") == "x b"
    assert wl.ersetzen("a b a", "a", "x") is None
    assert wl.ersetzen("a b", "c", "x") is None and wl.ersetzen("a b", "", "x") is None


def test_ersetzung_sichert_vorher_und_schreibt_atomar(baum):
    w, firma, kand = _kand(baum)
    erg = wl.anwenden(str(w), str(firma), kand, [{"pfad": "Projekte/Plan.md", "alt": "in Türkis", "neu": "in Orange"}],
                      "# Markenhandbuch VibeMind\nAkzent #f66c1e\n", JETZT)
    assert erg.geschrieben == ["Projekte/Plan.md"] and erg.abbruch is None and erg.verworfen == []
    assert (w / "Projekte" / "Plan.md").read_text(encoding="utf-8") == "Plan: VibeMind startet im Herbst in Orange."
    sicherung = firma / "Wissen-Verlauf" / "2026-10-09-1430"
    assert (sicherung / "Projekte" / "Plan.md").read_text(encoding="utf-8") == "Plan: VibeMind startet im Herbst in Türkis."
    assert (firma / "Markenhandbuch.md").read_text(encoding="utf-8").startswith("# Markenhandbuch VibeMind")
    assert (sicherung / "companys" / "VibeMind" / "Markenhandbuch.md").read_text(encoding="utf-8") == "# Altes Handbuch"
    assert erg.handbuch_rel == "companys/VibeMind/Markenhandbuch.md"
    assert not list((w / "Projekte").glob(".tmp-*"))


def test_nicht_genau_einmal_oder_fremder_pfad_wird_verworfen(baum):
    w, firma = baum
    _datei(firma / "Über uns.md", "Türkis hier und Türkis da. VibeMind.")
    kand = {k.rel: k for k in wl.kandidaten(str(w), str(firma), NAMEN, FREMDE, [])}
    erg = wl.anwenden(str(w), str(firma), kand, [
        {"pfad": "companys/VibeMind/Über uns.md", "alt": "Türkis", "neu": "Orange"},
        {"pfad": "People/Anna.md", "alt": "VibeMind", "neu": "X"}], "# H\n", JETZT)
    assert erg.geschrieben == []
    assert "companys/VibeMind/Über uns.md: Ausschnitt nicht genau einmal gefunden – verworfen" in erg.verworfen
    assert "People/Anna.md: nicht in der Kandidatenliste – verworfen" in erg.verworfen
    assert (firma / "Über uns.md").read_text(encoding="utf-8") == "Türkis hier und Türkis da. VibeMind."
    assert (w / "People" / "Anna.md").read_text(encoding="utf-8") == "Anna arbeitet bei VibeMind."


def test_crlf_und_bom_bleiben_erhalten(baum):
    """Review Focus 2."""
    w, firma = baum
    _datei(w / "Projekte" / "Windows.md", roh=b"\xef\xbb\xbfZeile eins VibeMind\r\nFarbe T\xc3\xbcrkis\r\n")
    kand = {k.rel: k for k in wl.kandidaten(str(w), str(firma), NAMEN, FREMDE, [])}
    erg = wl.anwenden(str(w), str(firma), kand, [{"pfad": "Projekte/Windows.md", "alt": "VibeMind\nFarbe Türkis",
                                                  "neu": "VibeMind\nFarbe Orange"}], "# H\n", JETZT)
    assert erg.geschrieben == ["Projekte/Windows.md"]
    assert (w / "Projekte" / "Windows.md").read_bytes() == b"\xef\xbb\xbfZeile eins VibeMind\r\nFarbe Orange\r\n"


def test_inzwischen_geaendert_wird_nicht_ueberschrieben(baum):
    """Review Focus 3."""
    w, firma, kand = _kand(baum)
    (w / "Projekte" / "Plan.md").write_text("Plan: VibeMind startet im Herbst in Türkis. Neu vom Betreiber.",
                                            encoding="utf-8")
    erg = wl.anwenden(str(w), str(firma), kand, [{"pfad": "Projekte/Plan.md", "alt": "in Türkis", "neu": "in Orange"}],
                      "# H\n", JETZT)
    assert erg.geschrieben == [] and "Projekte/Plan.md: inzwischen geändert – nicht geschrieben" in erg.verworfen
    assert "Neu vom Betreiber" in (w / "Projekte" / "Plan.md").read_text(encoding="utf-8")


def test_abbruch_mittendrin_laesst_geschriebenes_gesichert_stehen(baum):
    w, firma, kand = _kand(baum)
    aufrufe = []

    def schreiben(pfad, text, crlf, bom):
        aufrufe.append(pfad)
        if len(aufrufe) == 2:
            raise OSError("Platte voll")
        wl.schreiben(pfad, text, crlf, bom)
    erg = wl.anwenden(str(w), str(firma), kand, [
        {"pfad": "companys/VibeMind/Über uns.md", "alt": "Türkis", "neu": "Orange"},
        {"pfad": "Projekte/Plan.md", "alt": "in Türkis", "neu": "in Orange"}], "# H\n", JETZT, schreiben_=schreiben)
    assert erg.geschrieben == ["companys/VibeMind/Über uns.md"] and erg.handbuch_rel is None
    assert erg.abbruch.startswith("Projekte/Plan.md (OSError: Platte voll")
    assert "Orange" in (firma / "Über uns.md").read_text(encoding="utf-8")
    assert "Türkis" in (w / "Projekte" / "Plan.md").read_text(encoding="utf-8")
    assert (firma / "Wissen-Verlauf" / "2026-10-09-1430" / "companys" / "VibeMind" / "Über uns.md").exists()


def test_zweiter_lauf_in_derselben_minute_hat_eigenen_stempel(baum):
    w, firma, kand = _kand(baum)
    wl.anwenden(str(w), str(firma), kand, [], "# H1\n", JETZT)
    wl.anwenden(str(w), str(firma), kand, [], "# H2\n", JETZT)
    zweiter = firma / "Wissen-Verlauf" / "2026-10-09-1430-2" / "companys" / "VibeMind" / "Markenhandbuch.md"
    assert zweiter.read_text(encoding="utf-8") == "# H1\n"


def test_altes_profil_ab_dem_beginn(baum, tmp_path):
    _, firma = baum
    verlauf = firma / "Marke-Verlauf"
    os.remove(verlauf / "2026-10-09-1200.md")
    for name, zeit in (("2026-10-09-0900.md", 1000.0), ("2026-10-09-1000.md", 2000.0), ("2026-10-09-1100.md", 3000.0)):
        _datei(verlauf / name, f"Profil {name}")
        os.utime(verlauf / name, (zeit, zeit))
    assert wl.alt_profil(str(firma), 1950.0) == "Profil 2026-10-09-1000.md"   # aelteste ab Beginn - 120 s
    assert wl.alt_profil(str(firma), None) == "Profil 2026-10-09-1100.md"     # ohne Beginn: die neueste
    assert wl.alt_profil(str(firma), 99999.0) == "Profil 2026-10-09-1100.md"  # nichts danach: die neueste
    assert wl.alt_profil(str(tmp_path), None) == ""


def test_wissen_wurzel(monkeypatch, tmp_path):
    monkeypatch.setenv("ROWBOAT_KNOWLEDGE_ORDNER", str(tmp_path))
    assert wl.wissen_wurzel() == str(tmp_path)
    monkeypatch.delenv("ROWBOAT_KNOWLEDGE_ORDNER")
    monkeypatch.setenv("ROWBOAT_WISSEN_ORDNER", str(tmp_path / "companys"))
    assert wl.wissen_wurzel() == str(tmp_path)

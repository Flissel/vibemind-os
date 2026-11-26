"""Echter Code auf der Folie: die Zeilen einer Code-Folie kommen aus der Datei, nie vom Modell."""
import pytest

from spaces.marketing.claw import video_code, video_texte
from spaces.marketing.claw.video_slides import bild_pruefen

CODE = "\n".join(f"zeile {i}" for i in range(1, 41)) + "\n"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    wurzel = tmp_path / "fungus"
    (wurzel / "src").mkdir(parents=True)
    (wurzel / "src" / "corpus.py").write_text(CODE, encoding="utf-8")
    (wurzel / "src" / "lang.py").write_text("x = 1\n" + "\ty = '" + "a" * 200 + "'\n\n", encoding="utf-8")
    (wurzel / ".env").write_text("GEHEIM=1\nB=2\n", encoding="utf-8")
    (tmp_path / "aussen.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    monkeypatch.setenv("WERKBANK_CODE_WURZELN", f"la_fungus_search={wurzel}; gibtsnicht={tmp_path / 'fehlt'}")
    return wurzel


def szene(**schritt):
    return {"bild": {"schritte": [{"vorlage": "titel", "ab_satz": 0, "zeile": "A", "unterzeile": "B"},
                                  {"vorlage": "code", "ab_satz": 1, "ueberschrift": "So sieht es aus", **schritt}]}}


def test_zeilen_kommen_aus_der_datei(repo):
    s = szene(quelle={"datei": "src/corpus.py", "von": 5, "bis": 8}, markiert=[0, 3, 9], zeilen=["erfunden", "vom Modell"])
    assert video_code.aufloesen(s) == []
    st = s["bild"]["schritte"][1]
    assert st["zeilen"] == ["zeile 5", "zeile 6", "zeile 7", "zeile 8"] and st["ab_zeile"] == 5
    assert st["datei"] == "src/corpus.py:5-8" and st["markiert"] == [0, 3]
    assert bild_pruefen(s["bild"], 3) == []


def test_ordnername_davor_geht_auch(repo):
    s = szene(quelle={"datei": "la_fungus_search/src/corpus.py", "von": 1, "bis": 2})
    assert video_code.aufloesen(s) == [] and s["bild"]["schritte"][1]["datei"] == "src/corpus.py:1-2"


def test_lange_zeilen_werden_gekuerzt_und_tabs_ersetzt(repo):
    s = szene(quelle={"datei": "src/lang.py", "von": 1, "bis": 2})
    assert video_code.aufloesen(s) == []
    zeilen = s["bild"]["schritte"][1]["zeilen"]
    assert zeilen[0] == "x = 1" and zeilen[1].startswith("    y = 'aaa") and len(zeilen[1]) == 92 and zeilen[1].endswith("…")


@pytest.mark.parametrize("quelle, wort", [
    (None, "braucht quelle"), ("src/corpus.py", "braucht quelle"),
    ({"datei": "src/fehlt.py", "von": 1, "bis": 2}, "gibt es"),
    ({"datei": "../aussen.py", "von": 1, "bis": 2}, "gibt es"),
    ({"datei": ".env", "von": 1, "bis": 2}, "gibt es"),
    ({"datei": "C:/Windows/win.ini", "von": 1, "bis": 2}, "gibt es"),
    ({"datei": "src/corpus.py", "von": 0, "bis": 2}, "Zeilennummern"),
    ({"datei": "src/corpus.py", "von": 5, "bis": 4}, "Zeilennummern"),
    ({"datei": "src/corpus.py", "von": "5", "bis": 8}, "Zeilennummern"),
    ({"datei": "src/corpus.py", "von": 1, "bis": 15}, "2 bis 14"),
    ({"datei": "src/corpus.py", "von": 3, "bis": 3}, "2 bis 14"),
    ({"datei": "src/corpus.py", "von": 39, "bis": 44}, "nur 40 Zeilen"),
    ({"datei": "src/lang.py", "von": 3, "bis": 4}, "nur 3 Zeilen"),
    ({"datei": 5, "von": 1, "bis": 2}, "gibt es")])
def test_falsche_stelle_ist_ein_fehlersatz(repo, quelle, wort):
    s = szene(quelle=quelle, zeilen=["erfunden", "vom Modell"]) if quelle is not None else szene(zeilen=["erfunden", "x"])
    fehler = video_code.aufloesen(s)
    assert len(fehler) == 1 and wort in fehler[0] and fehler[0].startswith("Schritt 2:")
    assert str(repo) not in fehler[0]                           # kein Pfad vom PC in der Nachfrage


def test_ohne_erlaubte_ordner_keine_code_folie(repo, monkeypatch):
    monkeypatch.setenv("WERKBANK_CODE_WURZELN", "")
    fehler = video_code.aufloesen(szene(quelle={"datei": "src/corpus.py", "von": 1, "bis": 2}))
    assert len(fehler) == 1 and "nicht eingerichtet" in fehler[0]
    assert "- code:" not in video_texte._bildregeln()


def test_modell_erfaehrt_von_der_vorlage_nur_mit_ordnern(repo):
    regeln = video_texte._bildregeln()
    assert "- code:" in regeln and "la_fungus_search" in regeln and "gibtsnicht" not in regeln
    assert "Schreibe keinen Code" in regeln and str(repo) not in regeln


def test_zahlen_im_code_brauchen_keinen_beleg_die_ueberschrift_schon(repo):
    s = szene(quelle={"datei": "src/corpus.py", "von": 5, "bis": 8})
    video_code.aufloesen(s)
    texte = video_texte._bild_texte(s["bild"])
    assert not any("zeile 5" in t or "corpus.py" in t for t in texte) and "So sieht es aus" in texte
    s2 = szene(quelle={"datei": "src/corpus.py", "von": 5, "bis": 8}, ueberschrift="Spart 40 % Zeit")
    video_code.aufloesen(s2)
    assert any("40" in t for t in video_texte._bild_texte(s2["bild"]))


def test_andere_schritte_bleiben_unberuehrt(repo):
    s = {"bild": {"schritte": [{"vorlage": "titel", "ab_satz": 0, "zeile": "A", "unterzeile": "B"}]}}
    assert video_code.aufloesen(s) == [] and s["bild"]["schritte"][0] == {"vorlage": "titel", "ab_satz": 0, "zeile": "A", "unterzeile": "B"}
    assert video_code.aufloesen({}) == [] and video_code.aufloesen({"bild": "x"}) == []

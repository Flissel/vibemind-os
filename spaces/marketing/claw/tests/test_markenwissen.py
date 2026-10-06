"""markenwissen: Markenwissen je Firma aus der Rowboat-Ablage lesen, Agent-Notizen ablegen.

Die Firmentrennung ist der ganze Zweck: ein VibeMind-Lauf liest nie
fin2gether-Dateien, auch nicht ueber Symlinks oder Windows-Junctions.
Alle Tests arbeiten nur in tmp_path, nie in der echten ~/.rowboat-Ablage.
"""
import datetime
import os
import subprocess
import sys

import pytest

from spaces.marketing.claw import markenwissen as mw

HEUTE = datetime.date(2026, 10, 6)
INHALT = "Wir bauen Werkzeuge fuer kleine Laeden, damit sie ohne Agentur werben koennen."
KOPF = {"newsletter": "Herbst-Newsletter", "bitte": "Mach ihn freundlicher"}


def _schreiben(pfad, text, encoding="utf-8"):
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    with open(pfad, "w", encoding=encoding) as f:
        f.write(text)


def _junction(link, ziel):
    if sys.platform != "win32":
        pytest.skip("Junctions gibt es nur unter Windows")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(ziel)],
                   check=True, capture_output=True)


def _symlink(link, ziel, ordner):
    try:
        os.symlink(str(ziel), str(link), target_is_directory=ordner)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks brauchen hier Rechte")


@pytest.fixture
def wurzel(tmp_path):
    w = tmp_path / "companys"
    w.mkdir()
    return w


@pytest.fixture
def zwei_firmen(wurzel):
    _schreiben(str(wurzel / "VibeMind" / "Marke.md"), "# VibeMind\n" + INHALT)
    _schreiben(str(wurzel / "fin2gether" / "Geheim.md"),
               "# Geheim\nFIN2GETHER-GEHEIMNIS: Kreditkonditionen nur fuer Bestandskunden.")
    return wurzel


# --- wurzel / ordner_finden ---------------------------------------------------

def test_wurzel_aus_umgebung_sonst_vorgabe(monkeypatch, tmp_path):
    monkeypatch.setenv("ROWBOAT_WISSEN_ORDNER", str(tmp_path))
    assert mw.wurzel() == str(tmp_path)
    monkeypatch.delenv("ROWBOAT_WISSEN_ORDNER")
    assert mw.wurzel() == mw.WURZEL_VORGABE
    assert mw.WURZEL_VORGABE.endswith(os.path.join(".rowboat", "knowledge", "companys"))


def test_ordner_ohne_gross_klein_und_mit_leerzeichen_am_rand(wurzel):
    (wurzel / "  VIBEMIND").mkdir()  # Windows streicht Leerzeichen am Ende
    gefunden = mw.ordner_finden(str(wurzel), "vibemind", "VibeMind")
    assert gefunden == os.path.join(str(wurzel), "  VIBEMIND")


def test_ordner_ueber_namen_gefunden(wurzel):
    (wurzel / "Fin2Gether").mkdir()
    assert mw.ordner_finden(str(wurzel), "f2g", "fin2gether") == os.path.join(str(wurzel), "Fin2Gether")


def test_mehrere_treffer_alphabetisch_erster(wurzel):
    (wurzel / "vibemind").mkdir()
    (wurzel / " VibeMind").mkdir()
    gefunden = mw.ordner_finden(str(wurzel), "vibemind", "VibeMind")
    assert gefunden == os.path.join(str(wurzel), " VibeMind")


def test_ordner_fehlt_oder_wurzel_fehlt(wurzel, tmp_path):
    (wurzel / "Andere").mkdir()
    (wurzel / "vibemind.md").write_text("Datei, kein Ordner", encoding="utf-8")
    assert mw.ordner_finden(str(wurzel), "vibemind", "VibeMind") is None
    assert mw.ordner_finden(str(tmp_path / "gibtsnicht"), "vibemind", "VibeMind") is None


def test_firmenordner_als_junction_auf_fremde_firma_zaehlt_nicht(zwei_firmen):
    _junction(zwei_firmen / "VibeMind2", zwei_firmen / "fin2gether")
    assert mw.ordner_finden(str(zwei_firmen), "vibemind2", "VibeMind2") is None


# --- ist_leer -----------------------------------------------------------------

def test_ist_leer():
    assert mw.ist_leer("")
    assert mw.ist_leer("# Titel\n\n## Abschnitt\n<!-- Hilfe\nueber\nmehrere Zeilen -->\n")
    assert mw.ist_leer("   ## Eingerueckt\nkurz")
    assert not mw.ist_leer("# Titel\n" + "x" * 40)


# --- laden: Link-Sperre ---------------------------------------------------------

def test_junction_unterordner_auf_fremde_firma_wird_nicht_gelesen(zwei_firmen):
    _junction(zwei_firmen / "VibeMind" / "Extra", zwei_firmen / "fin2gether")
    w = mw.laden(str(zwei_firmen), "vibemind", "VibeMind", "Kreditkonditionen Geheim")
    assert "FIN2GETHER" not in w.text
    assert "Extra übersprungen (Verknüpfung)" in w.hinweise
    assert INHALT in w.text


def test_symlink_unterordner_auf_fremde_firma_wird_nicht_gelesen(zwei_firmen):
    _symlink(zwei_firmen / "VibeMind" / "Extra", zwei_firmen / "fin2gether", ordner=True)
    w = mw.laden(str(zwei_firmen), "vibemind", "VibeMind", "Kreditkonditionen Geheim")
    assert "FIN2GETHER" not in w.text
    assert "Extra übersprungen (Verknüpfung)" in w.hinweise


def test_datei_symlink_nach_aussen_uebersprungen(zwei_firmen):
    _symlink(zwei_firmen / "VibeMind" / "Geheim.md", zwei_firmen / "fin2gether" / "Geheim.md",
             ordner=False)
    w = mw.laden(str(zwei_firmen), "vibemind", "VibeMind", "Kreditkonditionen Geheim")
    assert "FIN2GETHER" not in w.text
    assert "Geheim.md übersprungen (Verknüpfung)" in w.hinweise


def test_firmenordner_selbst_als_junction_wird_nicht_gelesen(zwei_firmen):
    _junction(zwei_firmen / "Spiegel", zwei_firmen / "fin2gether")
    w = mw.laden(str(zwei_firmen), "spiegel", "Spiegel", "Kreditkonditionen Geheim")
    assert w.text == ""
    assert w.ordner is None


# --- laden: Inhalt, Reihenfolge, Budget ---------------------------------------

def test_kleiner_ordner_vollstaendig_marke_zuerst(wurzel):
    firma = wurzel / "VibeMind"
    _schreiben(str(firma / "Angebote.md"), "# Angebote\n" + "Paket A kostet wenig und hilft viel. " * 2)
    _schreiben(str(firma / "Aaa" / "Ton.txt"), "Wir duzen unsere Kundschaft und bleiben dabei sachlich.")
    _schreiben(str(firma / "Marke.md"), "# VibeMind\n" + INHALT)
    _schreiben(str(firma / "bild.png"), "kein Text, falsche Endung " * 5)
    w = mw.laden(str(wurzel), "vibemind", "VibeMind", "irgendwas")
    assert w.ordner == str(firma)
    assert w.hinweise == []
    assert w.text.startswith("### Marke.md\n# VibeMind\n" + INHALT)
    assert "\n\n### Aaa/Ton.txt\nWir duzen" in w.text
    assert w.text.index("### Aaa/Ton.txt") < w.text.index("### Angebote.md")
    assert "bild.png" not in w.text


def test_marke_gross_klein_egal_und_auf_8000_gekuerzt(wurzel):
    firma = wurzel / "VibeMind"
    _schreiben(str(firma / "MARKE.MD"), "M" * 9_000)
    _schreiben(str(firma / "Aaa.md"), "Erst der Name, dann die Marke: Aaa liegt alphabetisch vorn.")
    w = mw.laden(str(wurzel), "vibemind", "VibeMind", "Marke")
    assert w.text.startswith("### MARKE.MD\n" + "M" * 8_000 + " … (gekürzt)")
    assert "M" * 8_001 not in w.text


def test_budget_nie_ueberschritten_bei_vielen_grossen_dateien(wurzel):
    firma = wurzel / "VibeMind"
    _schreiben(str(firma / "Marke.md"), "# VibeMind\n" + "Marke " * 1_500)
    for i in range(20):
        _schreiben(str(firma / f"Produkt{i:02d}.md"), f"# Produkt {i}\n" + "Newsletter Herbst Rabatt " * 2_000)
    w = mw.laden(str(wurzel), "vibemind", "VibeMind", "Newsletter Herbst Rabatt")
    assert len(w.text) <= mw.BUDGET
    assert w.text.startswith("### Marke.md\n")
    hinweis = [h for h in w.hinweise if h.startswith("Markenwissen gekürzt (")]
    assert len(hinweis) == 1
    assert hinweis[0].endswith(" von 21 Dateien)")


def test_einzelnes_riesiges_stueck_wird_hart_gekappt(wurzel):
    firma = wurzel / "VibeMind"
    _schreiben(str(firma / "Riesig.md"), "Herbstaktion " * 15_000)  # ~195 000 Zeichen, < 200 KB
    _schreiben(str(firma / "Klein.md"), "Kleine Notiz ueber gar nichts Bestimmtes hier drin.")
    w = mw.laden(str(wurzel), "vibemind", "VibeMind", "Herbstaktion")
    assert len(w.text) <= mw.BUDGET
    assert "### Riesig.md" in w.text


def test_nur_zehn_neueste_notizen(wurzel):
    firma = wurzel / "VibeMind"
    _schreiben(str(firma / "Marke.md"), "# VibeMind\n" + INHALT)
    for i in range(12):
        pfad = str(firma / "Agent-Notizen" / f"n{i:02d}.md")
        _schreiben(pfad, f"# Notiz {i}\nInhalt der Notiz Nummer {i:02d} mit genug Text dazu, damit sie zaehlt.")
        os.utime(pfad, (1_000_000 + i * 100, 1_000_000 + i * 100))
    w = mw.laden(str(wurzel), "vibemind", "VibeMind", "Notiz")
    assert "Agent-Notizen/n00.md" not in w.text
    assert "Agent-Notizen/n01.md" not in w.text
    for i in range(2, 12):
        assert f"### Agent-Notizen/n{i:02d}.md" in w.text


def test_leere_vorlage_ergibt_hinweis_kein_markenwissen(wurzel):
    mw.vorlagen_anlegen(str(wurzel), ["VibeMind"])
    w = mw.laden(str(wurzel), "vibemind", "VibeMind", "egal")
    assert w.text == ""
    assert w.hinweise == ["Kein Markenwissen für VibeMind hinterlegt (companys/VibeMind fehlt/leer)"]
    assert w.ordner == os.path.join(str(wurzel), "VibeMind")


def test_fehlender_ordner_ergibt_hinweis(wurzel):
    w = mw.laden(str(wurzel), "fin2gether", "fin2gether", "egal")
    assert w == mw.Wissen(text="", hinweise=[
        "Kein Markenwissen für fin2gether hinterlegt (companys/fin2gether fehlt/leer)"], ordner=None)


def test_datei_ueber_200_kb_uebersprungen(wurzel):
    firma = wurzel / "VibeMind"
    _schreiben(str(firma / "Marke.md"), "# VibeMind\n" + INHALT)
    _schreiben(str(firma / "Gross.md"), "GROSS " * 40_000)
    w = mw.laden(str(wurzel), "vibemind", "VibeMind", "GROSS")
    assert "### Gross.md" not in w.text
    assert any("Gross.md" in h and "übersprungen" in h for h in w.hinweise)


def test_latin1_datei_lesbar(wurzel):
    firma = wurzel / "VibeMind"
    _schreiben(str(firma / "Alt.txt"), "Größe und Qualität für Händler, ganz ohne Überraschungen.",
               encoding="latin-1")
    w = mw.laden(str(wurzel), "vibemind", "VibeMind", "Händler")
    assert "Größe und Qualität für Händler" in w.text


def test_mehr_als_200_dateien_gekuerzt(wurzel):
    firma = wurzel / "VibeMind"
    for i in range(205):
        _schreiben(str(firma / f"d{i:03d}.md"), f"Datei {i:03d} mit gerade genug Inhalt fuer die Pruefung.")
    w = mw.laden(str(wurzel), "vibemind", "VibeMind", "Datei")
    assert "Markenwissen gekürzt (200 von 205 Dateien)" in w.hinweise
    assert "### d204.md" not in w.text


def test_laden_wirft_nie(wurzel, monkeypatch):
    _schreiben(str(wurzel / "VibeMind" / "Marke.md"), "# VibeMind\n" + INHALT)

    def kaputt(*_a, **_k):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(mw.os, "walk", kaputt)
    w = mw.laden(str(wurzel), "vibemind", "VibeMind", "egal")
    assert w.text == ""
    assert w.hinweise


# --- slug -----------------------------------------------------------------------

def test_slug():
    assert mw.slug("Idee: Herbst-Rabatt für Händler!") == "idee-herbst-rabatt-für-händler"
    assert mw.slug("  ---  ") == "notiz"
    assert mw.slug("../../etc") == "etc"
    assert len(mw.slug("a" * 100)) == 60


# --- notizen_schreiben ----------------------------------------------------------

def test_zweimal_gleicher_titel_ergibt_suffix_2_nie_ueberschrieben(wurzel):
    firma = wurzel / "VibeMind"
    firma.mkdir()
    n = [{"titel": "Idee Herbst", "text": "Erste Fassung"}]
    assert mw.notizen_schreiben(str(firma), "VibeMind", n, KOPF, HEUTE) == (["Idee Herbst"], [])
    n2 = [{"titel": "Idee Herbst", "text": "Zweite Fassung"}]
    assert mw.notizen_schreiben(str(firma), "VibeMind", n2, KOPF, HEUTE) == (["Idee Herbst"], [])
    erste = (firma / "Agent-Notizen" / "2026-10-06 idee-herbst.md").read_text(encoding="utf-8")
    zweite = (firma / "Agent-Notizen" / "2026-10-06 idee-herbst-2.md").read_text(encoding="utf-8")
    assert erste.endswith("Erste Fassung\n")
    assert zweite.endswith("Zweite Fassung\n")


def test_notiz_inhalt(wurzel):
    firma = wurzel / "VibeMind"
    firma.mkdir()
    kopf = {"newsletter": "Herbst", "bitte": "B" * 400}
    mw.notizen_schreiben(str(firma), "VibeMind", [{"titel": "Titel", "text": "Der Text."}], kopf, HEUTE)
    inhalt = (firma / "Agent-Notizen" / "2026-10-06 titel.md").read_text(encoding="utf-8")
    assert inhalt == ("# Titel\n\n- Datum: 2026-10-06\n- Newsletter: Herbst\n"
                      f"- Bitte des Betreibers: {'B' * 300}\n\nDer Text.\n")


def test_vier_notizen_drei_geschrieben_und_ungueltige_uebersprungen(wurzel):
    firma = wurzel / "VibeMind"
    firma.mkdir()
    vier = [{"titel": f"N{i}", "text": "t"} for i in range(4)]
    titel, _ = mw.notizen_schreiben(str(firma), "VibeMind", vier, KOPF, HEUTE)
    assert titel == ["N0", "N1", "N2"]
    assert len(os.listdir(firma / "Agent-Notizen")) == 3

    ungueltig = [{"titel": "x" * 81, "text": "t"}, {"titel": "ok", "text": ""},
                 {"titel": "ok", "text": "y" * 4_001}, "kein dict"]
    titel, _ = mw.notizen_schreiben(str(firma), "VibeMind", ungueltig, KOPF, HEUTE)
    assert titel == []
    assert len(os.listdir(firma / "Agent-Notizen")) == 3


def test_ordner_none_ergibt_hinweis(wurzel):
    assert mw.notizen_schreiben(None, "fin2gether", [{"titel": "a", "text": "b"}], KOPF, HEUTE) == (
        [], ["Notiz nicht abgelegt: companys/fin2gether fehlt"])


def test_verschwundener_firmenordner_wird_nicht_angelegt(wurzel):
    weg = wurzel / "VibeMind"
    titel, hinweise = mw.notizen_schreiben(str(weg), "VibeMind", [{"titel": "a", "text": "b"}], KOPF, HEUTE)
    assert titel == []
    assert hinweise == ["Notiz nicht abgelegt: companys/VibeMind fehlt"]
    assert not weg.exists()


def test_agent_notizen_als_junction_nach_aussen_nichts_geschrieben(zwei_firmen):
    _junction(zwei_firmen / "VibeMind" / "Agent-Notizen", zwei_firmen / "fin2gether")
    vorher = sorted(os.listdir(zwei_firmen / "fin2gether"))
    titel, hinweise = mw.notizen_schreiben(str(zwei_firmen / "VibeMind"), "VibeMind",
                                           [{"titel": "a", "text": "b"}], KOPF, HEUTE)
    assert titel == []
    assert hinweise == ["Notiz konnte nicht abgelegt werden"]
    assert sorted(os.listdir(zwei_firmen / "fin2gether")) == vorher


def test_agent_notizen_als_symlink_nach_aussen_nichts_geschrieben(zwei_firmen):
    _symlink(zwei_firmen / "VibeMind" / "Agent-Notizen", zwei_firmen / "fin2gether", ordner=True)
    vorher = sorted(os.listdir(zwei_firmen / "fin2gether"))
    titel, hinweise = mw.notizen_schreiben(str(zwei_firmen / "VibeMind"), "VibeMind",
                                           [{"titel": "a", "text": "b"}], KOPF, HEUTE)
    assert titel == []
    assert hinweise == ["Notiz konnte nicht abgelegt werden"]
    assert sorted(os.listdir(zwei_firmen / "fin2gether")) == vorher


def test_schreibfehler_ergibt_hinweis_und_naechste_notiz(wurzel, monkeypatch):
    firma = wurzel / "VibeMind"
    firma.mkdir()
    echtes_open = open
    aufrufe = []

    def wackelig(pfad, modus="r", *a, **k):
        if modus == "xb":
            aufrufe.append(pfad)
            if len(aufrufe) == 1:
                raise PermissionError("gesperrt")
        return echtes_open(pfad, modus, *a, **k)

    monkeypatch.setattr("builtins.open", wackelig)
    titel, hinweise = mw.notizen_schreiben(
        str(firma), "VibeMind", [{"titel": "eins", "text": "a"}, {"titel": "zwei", "text": "b"}], KOPF, HEUTE)
    assert titel == ["zwei"]
    assert hinweise == ["Notiz konnte nicht abgelegt werden"]


# --- vorlagen_anlegen -----------------------------------------------------------

def test_vorlagen_bestehendes_unangetastet_neues_angelegt(tmp_path):
    wurzel = tmp_path / "neu" / "companys"
    _schreiben(str(wurzel / "Vibemind" / "Marke.md"), "# Eigene Marke\n" + INHALT)
    angelegt = mw.vorlagen_anlegen(str(wurzel), ["VibeMind", "fin2gether"])
    assert angelegt == [os.path.join(str(wurzel), "fin2gether")]
    assert (wurzel / "Vibemind" / "Marke.md").read_text(encoding="utf-8") == "# Eigene Marke\n" + INHALT
    assert sorted(os.listdir(wurzel)) == ["Vibemind", "fin2gether"]
    vorlage = (wurzel / "fin2gether" / "Marke.md").read_text(encoding="utf-8")
    for kopf in ["# fin2gether", "## Wer wir sind", "## Zielgruppe", "## Ton", "## Angebote",
                 "## Do & Don'ts", "## Fakten und Zahlen"]:
        assert kopf + "\n" in vorlage
    assert vorlage.count("<!--") == 7
    assert mw.ist_leer(vorlage)
    assert mw.vorlagen_anlegen(str(wurzel), ["fin2gether"]) == []


# --- Fix-Runde 1 ----------------------------------------------------------------

def test_einzelnes_surrogat_wirft_nicht_und_hinterlaesst_keine_datei(wurzel):
    firma = wurzel / "VibeMind"
    firma.mkdir()
    notizen = [{"titel": "Kaputt", "text": "x\ud800y"}, {"titel": "Heil", "text": "in Ordnung"}]
    titel, hinweise = mw.notizen_schreiben(str(firma), "VibeMind", notizen, KOPF, HEUTE)
    assert titel == ["Heil"]
    assert hinweise == ["Notiz konnte nicht abgelegt werden"]
    assert os.listdir(firma / "Agent-Notizen") == ["2026-10-06 heil.md"]


def test_surrogat_im_titel_und_kopf_hinterlaesst_keine_datei(wurzel):
    firma = wurzel / "VibeMind"
    firma.mkdir()
    titel, hinweise = mw.notizen_schreiben(
        str(firma), "VibeMind", [{"titel": "A\udc00", "text": "t"}],
        {"newsletter": "N\ud800", "bitte": "b"}, HEUTE)
    assert titel == []
    assert hinweise == ["Notiz konnte nicht abgelegt werden"]
    assert os.listdir(firma / "Agent-Notizen") == []


def test_schreibfehler_nach_anlegen_entfernt_halbe_datei(wurzel, monkeypatch):
    firma = wurzel / "VibeMind"
    firma.mkdir()
    echtes_open = open

    class Voll:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

        def write(self, _roh):
            raise OSError("Platte voll")

    def halb(pfad, modus="r", *a, **k):
        f = echtes_open(pfad, modus, *a, **k)
        return Voll(f) if modus == "xb" else f

    monkeypatch.setattr("builtins.open", halb)
    titel, hinweise = mw.notizen_schreiben(str(firma), "VibeMind", [{"titel": "a", "text": "b"}], KOPF, HEUTE)
    assert titel == []
    assert hinweise == ["Notiz konnte nicht abgelegt werden"]
    assert os.listdir(firma / "Agent-Notizen") == []


@pytest.mark.parametrize("notizen", [5, None, "text", {"titel": "a", "text": "b"}])
def test_notizen_kein_list_wirft_nicht(wurzel, notizen):
    firma = wurzel / "VibeMind"
    firma.mkdir()
    assert mw.notizen_schreiben(str(firma), "VibeMind", notizen, KOPF, HEUTE) == ([], [])


def test_marke_bleibt_bei_mehr_als_200_dateien_davor(wurzel):
    firma = wurzel / "VibeMind"
    _schreiben(str(firma / "Marke.md"), "# VibeMind\n" + INHALT)
    for i in range(201):
        _schreiben(str(firma / "Aaa" / f"d{i:03d}.md"), f"Datei {i:03d} mit gerade genug Inhalt fuer die Pruefung.")
    w = mw.laden(str(wurzel), "vibemind", "VibeMind", "Datei")
    assert w.text.startswith("### Marke.md\n# VibeMind\n" + INHALT)
    assert "Markenwissen gekürzt (200 von 202 Dateien)" in w.hinweise
    assert w.text.count("### ") == 200


def test_mandant_und_name_mit_leerraum_am_rand(wurzel):
    (wurzel / "VibeMind").mkdir()
    assert mw.ordner_finden(str(wurzel), " vibemind ", "") == os.path.join(str(wurzel), "VibeMind")
    assert mw.ordner_finden(str(wurzel), "", "\tVibeMind\n") == os.path.join(str(wurzel), "VibeMind")
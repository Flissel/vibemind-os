"""Tests fuer den Indexer (Abgleich mit knowledge/, Runden, Wiederholung) mit FakeEinbetter."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wissen_suche.einbetten import EingabeAbgelehnt, FakeEinbetter  # noqa: E402
from wissen_suche.index import WissensIndex  # noqa: E402
from wissen_suche.indexer import (  # noqa: E402
    Indexer,
    abgleichen,
    bestand_scannen,
    einbetten_runde,
)


@pytest.fixture
def wurzel(tmp_path):
    w = tmp_path / "knowledge"
    w.mkdir()
    return w


@pytest.fixture
def idx(tmp_path):
    i = WissensIndex(tmp_path / "index" / "w.sqlite", FakeEinbetter.modell, FakeEinbetter.dim)
    yield i
    i.close()


def schreiben(wurzel: Path, rel: str, text: str) -> Path:
    p = wurzel / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def runde(idx, wurzel, einbetter=None, **kw):
    abgleichen(idx, wurzel)
    return einbetten_runde(idx, wurzel, einbetter or FakeEinbetter(), **kw)


def test_neue_und_geaenderte_dateien_werden_offen_dann_fertig(idx, wurzel):
    p = schreiben(wurzel, "Notes/a.md", "# A\n" + "Rente " * 5)
    ab = abgleichen(idx, wurzel)
    assert ab.neu_oder_geaendert == 1 and ab.entfernt == 0 and ab.loeschen_ausgesetzt is False
    assert idx.dateien()["Notes/a.md"].status == "offen"
    r = einbetten_runde(idx, wurzel, FakeEinbetter())
    assert r.pc_erreichbar is True and r.eingebettet >= 1 and r.offen == 0
    assert idx.dateien()["Notes/a.md"].status == "fertig"
    # unveraendert -> nichts zu tun
    assert abgleichen(idx, wurzel).neu_oder_geaendert == 0
    # geaendert -> wieder offen, dann fertig mit neuem Inhalt
    p.write_text("# A\nHund und Katze", encoding="utf-8")
    os.utime(p, (1_000_000, 1_000_000))
    assert abgleichen(idx, wurzel).neu_oder_geaendert == 1
    assert idx.dateien()["Notes/a.md"].status == "offen"
    einbetten_runde(idx, wurzel, FakeEinbetter())
    treffer = idx.suchen(FakeEinbetter().frage("Hund"), 3)
    assert "Hund" in treffer[0].text


def test_nur_mtime_geaendert_bettet_nicht_neu_ein(idx, wurzel):
    p = schreiben(wurzel, "a.md", "Inhalt")
    runde(idx, wurzel)
    os.utime(p, (2_000_000, 2_000_000))
    assert abgleichen(idx, wurzel).neu_oder_geaendert == 0
    assert idx.dateien()["a.md"].status == "fertig"
    assert idx.dateien()["a.md"].mtime == 2_000_000


def test_ausgeschlossene_ordner_und_nicht_md(idx, wurzel):
    schreiben(wurzel, "_konflikte/x.md", "x")
    schreiben(wurzel, ".git/y.md", "y")
    schreiben(wurzel, ".spiegel-eingang/z.md", "z")
    schreiben(wurzel, ".versteckt/v.md", "v")
    schreiben(wurzel, "a.txt", "t")
    schreiben(wurzel, "gut.md", "g")
    assert set(bestand_scannen(wurzel)) == {"gut.md"}
    runde(idx, wurzel)
    assert set(idx.dateien()) == {"gut.md"}


def test_fehlende_wurzel_ist_leerer_bestand(tmp_path):
    assert bestand_scannen(tmp_path / "fehlt") == {}


def test_pc_aus_bleibt_offen_ohne_fehlversuch(idx, wurzel):
    for i in range(3):
        schreiben(wurzel, f"n{i}.md", f"Notiz {i}")
    fake = FakeEinbetter(erreichbar=False)
    r = runde(idx, wurzel, fake)
    assert r.pc_erreichbar is False and r.eingebettet == 0 and r.offen == 3
    assert fake.aufrufe == 1                         # Runde bricht nach dem ersten Fehlschlag ab
    assert all(d.status == "offen" and d.versuche == 0 for d in idx.dateien().values())
    fake.erreichbar = True
    r = runde(idx, wurzel, fake)
    assert r.pc_erreichbar is True and r.offen == 0
    assert all(d.status == "fertig" for d in idx.dateien().values())


class LehntAb(FakeEinbetter):
    def dokumente(self, texte):
        if any("boese" in t for t in texte):
            self.aufrufe += 1
            raise EingabeAbgelehnt("400")
        return super().dokumente(texte)


def test_eingabe_abgelehnt_trifft_nur_diese_datei(idx, wurzel):
    schreiben(wurzel, "a.md", "gut")
    schreiben(wurzel, "b.md", "boese")
    schreiben(wurzel, "c.md", "auch gut")
    r = runde(idx, wurzel, LehntAb())
    d = idx.dateien()
    assert r.pc_erreichbar is True
    assert d["a.md"].status == "fertig" and d["c.md"].status == "fertig"
    assert d["b.md"].status == "offen" and d["b.md"].versuche == 1
    runde(idx, wurzel, LehntAb())
    runde(idx, wurzel, LehntAb())
    assert idx.dateien()["b.md"].status == "fehler"
    assert idx.stand().fehler == 1


def test_leerer_ordner_loescht_index_nicht(idx, wurzel, caplog):
    for i in range(10):
        schreiben(wurzel, f"n{i}.md", f"Notiz {i}")
    runde(idx, wurzel)
    assert idx.stand().fertig == 10
    for p in wurzel.glob("*.md"):
        p.unlink()
    with caplog.at_level(logging.WARNING):
        ab = abgleichen(idx, wurzel)
    assert ab.loeschen_ausgesetzt is True and ab.entfernt == 0
    assert idx.stand().fertig == 10
    assert "ausgesetzt" in caplog.text


def test_mehr_als_haelfte_fehlt_loescht_nicht(idx, wurzel):
    for i in range(10):
        schreiben(wurzel, f"n{i}.md", f"Notiz {i}")
    runde(idx, wurzel)
    for i in range(6):
        (wurzel / f"n{i}.md").unlink()
    assert abgleichen(idx, wurzel).loeschen_ausgesetzt is True
    assert idx.stand().fertig == 10


def test_geloeschte_datei_verschwindet(idx, wurzel):
    for i in range(10):
        schreiben(wurzel, f"n{i}.md", f"Notiz {i}")
    runde(idx, wurzel)
    (wurzel / "n3.md").unlink()
    ab = abgleichen(idx, wurzel)
    assert ab.entfernt == 1 and ab.loeschen_ausgesetzt is False
    assert idx.stand().fertig == 9 and "n3.md" not in idx.dateien()


class AendertDatei(FakeEinbetter):
    def __init__(self, datei: Path):
        super().__init__()
        self.datei = datei
        self.geaendert = False

    def dokumente(self, texte):
        if not self.geaendert:
            self.geaendert = True
            self.datei.write_text("Ganz neuer Inhalt mit Katze", encoding="utf-8")
            os.utime(self.datei, (3_000_000, 3_000_000))
        return super().dokumente(texte)


def test_aenderung_waehrend_einbetten(idx, wurzel):
    p = schreiben(wurzel, "a.md", "Alter Inhalt mit Rente")
    abgleichen(idx, wurzel)
    einbetten_runde(idx, wurzel, AendertDatei(p))
    assert idx.dateien()["a.md"].status != "fertig"
    assert idx.stand().abschnitte == 0
    abgleichen(idx, wurzel)
    assert idx.dateien()["a.md"].status == "offen"
    einbetten_runde(idx, wurzel, FakeEinbetter())
    assert "Katze" in idx.suchen(FakeEinbetter().frage("Katze"), 1)[0].text


def test_max_abschnitte_begrenzt_runde(idx, wurzel):
    for i in range(50):
        schreiben(wurzel, f"n{i:02}.md", f"Notiz {i}")
    r = runde(idx, wurzel, max_abschnitte=10)
    assert r.eingebettet <= 10 and r.offen > 0
    assert idx.stand().fertig == r.eingebettet


def test_stapel_teilt_aufrufe(idx, wurzel):
    schreiben(wurzel, "gross.md", "".join(f"# T{i}\n" + "w " * 150 + "\n" for i in range(10)))
    fake = FakeEinbetter()
    r = runde(idx, wurzel, fake, stapel=4)
    assert r.eingebettet == 10 and fake.aufrufe == 3


def test_leere_datei_wird_fertig_ohne_abschnitte(idx, wurzel):
    schreiben(wurzel, "leer.md", "  \n")
    fake = FakeEinbetter()
    runde(idx, wurzel, fake)
    assert idx.dateien()["leer.md"].status == "fertig" and idx.stand().abschnitte == 0
    assert fake.aufrufe == 0


def test_umlaute_im_pfad(idx, wurzel):
    schreiben(wurzel, "Agent Notes/Über (neu).md", "# Übergabe\nRente besprochen")
    assert "Agent Notes/Über (neu).md" in bestand_scannen(wurzel)
    runde(idx, wurzel)
    assert idx.dateien()["Agent Notes/Über (neu).md"].status == "fertig"


def test_indexer_faden_laeuft_sofort_und_merkt_runde(idx, wurzel):
    schreiben(wurzel, "a.md", "Rente")
    ix = Indexer(idx, wurzel, FakeEinbetter(), intervall_s=3600)
    assert ix.daemon is True
    ix.start()
    try:
        assert ix.runde_fertig.wait(5)
    finally:
        ix.stoppen()
        ix.join(5)
    assert idx.dateien()["a.md"].status == "fertig"
    assert idx.lesen("letzte_runde")


class Kaputt(FakeEinbetter):
    def dokumente(self, texte):
        raise RuntimeError("unerwartet")


def test_indexer_faden_ueberlebt_ausnahmen(idx, wurzel, caplog):
    schreiben(wurzel, "a.md", "Rente")
    ix = Indexer(idx, wurzel, Kaputt(), intervall_s=3600)
    with caplog.at_level(logging.ERROR):
        ix.start()
        try:
            assert ix.runde_fertig.wait(5)
            assert ix.is_alive()
        finally:
            ix.stoppen()
            ix.join(5)
    assert "unerwartet" in caplog.text


# --- Fix-Runde 1 -------------------------------------------------------------------------------

def test_nfd_dateiname_wird_gelesen_und_als_nfc_gefuehrt(idx, wurzel):
    import unicodedata
    from wissen_suche.suche import stichwort_suchen

    nfd = unicodedata.normalize("NFD", "Über Müller.md")
    (wurzel / nfd).write_text("Rentenplanung mit Herrn Müller", encoding="utf-8")
    nfc = unicodedata.normalize("NFC", "Über Müller.md")
    assert set(bestand_scannen(wurzel)) == {nfc}
    r = runde(idx, wurzel)
    assert r.eingebettet == 1
    assert idx.dateien()[nfc].status == "fertig"
    treffer = stichwort_suchen(wurzel, "Rentenplanung", 5)
    assert [t["pfad"] for t in treffer] == ["knowledge/" + nfc]


def test_wenige_dateien_werden_auch_alle_geloescht(idx, wurzel):
    for i in range(3):
        schreiben(wurzel, f"n{i}.md", f"Notiz {i}")
    runde(idx, wurzel)
    for i in range(3):
        (wurzel / f"n{i}.md").unlink()
    ab = abgleichen(idx, wurzel)
    assert ab.loeschen_ausgesetzt is False and ab.entfernt == 3
    assert idx.dateien() == {}


def test_neun_bekannte_mehrheit_fehlt_wird_geloescht(idx, wurzel):
    for i in range(9):
        schreiben(wurzel, f"n{i}.md", f"Notiz {i}")
    runde(idx, wurzel)
    for i in range(6):
        (wurzel / f"n{i}.md").unlink()
    assert abgleichen(idx, wurzel).entfernt == 6

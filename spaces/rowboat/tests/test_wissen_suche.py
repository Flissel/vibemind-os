"""Tests fuer wissen_suchen (Bedeutung, Stichwort-Rueckfall, Hinweise) und wissen_stand."""
from __future__ import annotations

import io
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wissen_suche.einbetten import FakeEinbetter, OllamaEinbetter  # noqa: E402
from wissen_suche.index import WissensIndex  # noqa: E402
from wissen_suche.indexer import abgleichen, einbetten_runde  # noqa: E402
from wissen_suche.suche import (  # noqa: E402
    HINWEIS_OFFEN,
    HINWEIS_STICHWORT,
    stichwort_suchen,
    wissen_stand,
    wissen_suchen,
)

ANNA = "companys/VibeMind/Kontakte/Altersvorsorge/Anna.md"


def schreiben(wurzel: Path, rel: str, text: str) -> None:
    p = wurzel / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def wurzel(tmp_path):
    w = tmp_path / "knowledge"
    schreiben(w, ANNA, "# Anna\n\nRentenplanung besprochen.\n")
    schreiben(w, "Notes/Hund.md", "# Hund\n\nHund gekauft, Futter bestellt.\n")
    return w


@pytest.fixture
def idx(tmp_path, wurzel):
    i = WissensIndex(tmp_path / "index" / "w.sqlite", FakeEinbetter.modell, FakeEinbetter.dim)
    abgleichen(i, wurzel)
    einbetten_runde(i, wurzel, FakeEinbetter())
    yield i
    i.close()


def test_hinweise_woertlich():
    assert HINWEIS_STICHWORT == ("Bedeutungssuche gerade nicht möglich (PC bzw. Ollama nicht erreichbar)"
                                 " – das sind Treffer der Stichwortsuche.")
    assert HINWEIS_OFFEN == "{n} Notizen sind noch nicht eingebettet und nur per Stichwort gefunden."


def test_bedeutung_findet_ohne_genaues_wort(idx, wurzel):
    erg = wissen_suchen("Kunden mit Interesse an Altersvorsorge", 5, idx, wurzel, FakeEinbetter())
    assert erg["modus"] == "bedeutung"
    assert erg["hinweis"] is None
    erster = erg["treffer"][0]
    assert erster["pfad"] == "knowledge/" + ANNA
    assert erster["quelle"] == "bedeutung"
    assert erster["titel"] == "Anna" and erster["abschnitt"] == "Anna"
    assert "Rentenplanung" in erster["auszug"]
    assert erg["stand"] == {"fertig": 2, "offen": 0, "fehler": 0}


def test_pc_aus_stichwort_mit_hinweis(idx, wurzel):
    erg = wissen_suchen("Rentenplanung", 5, idx, wurzel, FakeEinbetter(erreichbar=False))
    assert erg["modus"] == "stichwort"
    assert erg["hinweis"] == HINWEIS_STICHWORT
    assert [t["pfad"] for t in erg["treffer"]] == ["knowledge/" + ANNA]
    assert erg["treffer"][0]["quelle"] == "stichwort"


def test_leerer_index_ist_stichwort(tmp_path, wurzel):
    leer = WissensIndex(tmp_path / "leer.sqlite", FakeEinbetter.modell, FakeEinbetter.dim)
    fake = FakeEinbetter()
    erg = wissen_suchen("Hund", 5, leer, wurzel, fake)
    assert erg["modus"] == "stichwort" and erg["hinweis"] == HINWEIS_STICHWORT
    assert erg["treffer"][0]["pfad"] == "knowledge/Notes/Hund.md"
    assert fake.aufrufe == 0
    leer.close()


def test_pc_aus_antwortet_schnell(idx, wurzel):
    halt = threading.Event()

    def schlaeft(req, timeout=None):
        halt.wait(30)
        return io.BytesIO(b"{}")

    e = OllamaEinbetter("http://100.84.127.84:11435", FakeEinbetter.modell, FakeEinbetter.dim,
                        gesamt_s=0.5, oeffner=schlaeft)
    start = time.monotonic()
    erg = wissen_suchen("Rentenplanung", 5, idx, wurzel, e)
    dauer = time.monotonic() - start
    halt.set()
    assert dauer < 2
    assert erg["modus"] == "stichwort" and erg["hinweis"] == HINWEIS_STICHWORT


def test_offene_dateien_per_stichwort_ergaenzt(idx, wurzel):
    schreiben(wurzel, "Notes/Neu.md", "Pension von Bernd besprochen")
    erg = wissen_suchen("Pension", 5, idx, wurzel, FakeEinbetter())
    assert erg["modus"] == "bedeutung"
    neu = [t for t in erg["treffer"] if t["pfad"] == "knowledge/Notes/Neu.md"]
    assert len(neu) == 1 and neu[0]["quelle"] == "stichwort"
    assert "1 Notizen" in erg["hinweis"]
    assert erg["hinweis"] == HINWEIS_OFFEN.format(n=1)
    # Auch nach dem Abgleich (offen im Index) dasselbe Bild
    abgleichen(idx, wurzel)
    erg = wissen_suchen("Pension", 5, idx, wurzel, FakeEinbetter())
    assert erg["stand"]["offen"] == 1 and "1 Notizen" in erg["hinweis"]


def test_umlaut_faltung(wurzel):
    schreiben(wurzel, "Notes/Termin.md", "Die Übergabe der Schlüssel ist am Freitag.")
    assert [t["pfad"] for t in stichwort_suchen(wurzel, "Uebergabe", 5)] == ["knowledge/Notes/Termin.md"]
    assert [t["pfad"] for t in stichwort_suchen(wurzel, "schluessel", 5)] == ["knowledge/Notes/Termin.md"]
    schreiben(wurzel, "Notes/Strasse.md", "Wohnt in der Hauptstrasse.")
    assert "knowledge/Notes/Strasse.md" in [t["pfad"] for t in stichwort_suchen(wurzel, "Hauptstraße", 5)]


def test_stichwort_punkte_dateiname_und_auszug(wurzel):
    schreiben(wurzel, "Notes/Futter.md", "Erster Absatz ohne.\n\nHier geht es um Futter und Futter.")
    treffer = stichwort_suchen(wurzel, "Futter", 5)
    assert treffer[0]["pfad"] == "knowledge/Notes/Futter.md"   # Dateiname zaehlt dreifach
    assert treffer[0]["punkte"] == 3 + 2
    assert treffer[0]["auszug"].startswith("Hier geht es um Futter")
    assert len(treffer[0]["auszug"]) <= 300
    assert stichwort_suchen(wurzel, "die und der", 5) == []    # nur Stoppwoerter/zu kurz


def test_stichwort_beachtet_ausschluesse_und_nur(wurzel):
    schreiben(wurzel, "_konflikte/Hund.md", "Hund")
    schreiben(wurzel, ".git/Hund.md", "Hund")
    assert [t["pfad"] for t in stichwort_suchen(wurzel, "Hund", 5)] == ["knowledge/Notes/Hund.md"]
    assert stichwort_suchen(wurzel, "Hund", 5, nur={ANNA}) == []


def test_anzahl_begrenzt_und_leere_frage(idx, wurzel):
    for i in range(30):
        schreiben(wurzel, f"Viele/n{i}.md", f"Hund Nummer {i}")
    assert wissen_suchen("  ", 5, idx, wurzel, FakeEinbetter()) == {"fehler": "frage fehlt"}
    erg = wissen_suchen("Hund", 100, idx, wurzel, FakeEinbetter(erreichbar=False))
    assert len(erg["treffer"]) == 20
    erg = wissen_suchen("Hund", 0, idx, wurzel, FakeEinbetter(erreichbar=False))
    assert len(erg["treffer"]) == 1


def test_pfade_beginnen_mit_knowledge(idx, wurzel):
    for erreichbar in (True, False):
        erg = wissen_suchen("Hund Rente", 5, idx, wurzel, FakeEinbetter(erreichbar=erreichbar))
        assert erg["treffer"]
        assert all(t["pfad"].startswith("knowledge/") for t in erg["treffer"])


def test_wissen_stand(idx):
    idx.merken("letzte_runde", "2026-10-26T12:00:00+00:00")

    class Ok(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    urls = []

    def oeffner(url, timeout=None):
        urls.append((url, timeout))
        return Ok(b'{"version":"0.17.1"}')

    st = wissen_stand(idx, "http://pc:11435/", oeffner=oeffner)
    assert st == {"fertig": 2, "offen": 0, "fehler": 0, "abschnitte": 2, "modell": "fake",
                  "pc_erreichbar": True, "letzte_runde": "2026-10-26T12:00:00+00:00"}
    assert urls == [("http://pc:11435/api/version", 2)]

    def aus(url, timeout=None):
        raise OSError("weg")

    assert wissen_stand(idx, "http://pc:11435", oeffner=aus)["pc_erreichbar"] is False


# --- Fix-Runde 1 -------------------------------------------------------------------------------

def _pfade(erg):
    return [t["pfad"] for t in erg["treffer"]]


def test_geaenderte_notiz_ohne_veraltete_abschnitte(idx, wurzel):
    """Notiz geaendert, Runde bei PC aus: Suche bei PC an liefert jeden Pfad einmal, ohne alten Auszug."""
    p = wurzel / ANNA
    p.write_text("# Anna\n\nHund und Katze versorgt.\n", encoding="utf-8")
    os.utime(p, (5_000_000, 5_000_000))
    # Noch vor dem Abgleich: der Index haelt Anna fuer fertig, die Datei ist aber neuer.
    for frage in ("Altersvorsorge Rentenplanung", "Hund Katze"):
        erg = wissen_suchen(frage, 5, idx, wurzel, FakeEinbetter())
        assert len(_pfade(erg)) == len(set(_pfade(erg)))
        assert all("Rentenplanung" not in t["auszug"] for t in erg["treffer"])
    abgleichen(idx, wurzel)
    einbetten_runde(idx, wurzel, FakeEinbetter(erreichbar=False))
    assert idx.dateien()[ANNA].status == "offen"
    erg = wissen_suchen("Altersvorsorge Rentenplanung Hund", 5, idx, wurzel, FakeEinbetter())
    assert erg["modus"] == "bedeutung"
    assert len(_pfade(erg)) == len(set(_pfade(erg)))
    assert all("Rentenplanung" not in t["auszug"] for t in erg["treffer"])
    anna = [t for t in erg["treffer"] if t["pfad"] == "knowledge/" + ANNA]
    assert len(anna) == 1 and anna[0]["quelle"] == "stichwort" and "Hund" in anna[0]["auszug"]


def test_kein_pfad_doppelt_bei_offenen(idx, wurzel):
    schreiben(wurzel, "Notes/Hund2.md", "Hund Hund Hund")
    erg = wissen_suchen("Hund", 10, idx, wurzel, FakeEinbetter())
    assert len(_pfade(erg)) == len(set(_pfade(erg)))


def test_abgelehnte_frage_eigener_hinweis(idx, wurzel):
    from wissen_suche.einbetten import EingabeAbgelehnt
    from wissen_suche.suche import HINWEIS_ABGELEHNT

    class Lehnt(FakeEinbetter):
        def frage(self, text):
            raise EingabeAbgelehnt("400")

    erg = wissen_suchen("Rentenplanung", 5, idx, wurzel, Lehnt())
    assert erg["modus"] == "stichwort"
    assert erg["hinweis"] == HINWEIS_ABGELEHNT != HINWEIS_STICHWORT
    assert "PC" not in HINWEIS_ABGELEHNT


def test_stichwortsuche_bricht_nach_frist_ab(idx, wurzel, monkeypatch):
    import wissen_suche.suche as suche_mod

    for i in range(20):
        schreiben(wurzel, f"Viele/n{i:02}.md", f"Hund {i}")
    echt = suche_mod._text_lesen

    def langsam(pfad):
        time.sleep(0.1)
        return echt(pfad)

    monkeypatch.setattr(suche_mod, "_text_lesen", langsam)
    monkeypatch.setattr(suche_mod, "GESAMT_FRIST_S", 0.5)
    start = time.monotonic()
    erg = wissen_suchen("Hund", 20, idx, wurzel, FakeEinbetter(erreichbar=False))
    assert time.monotonic() - start < 1.5
    assert erg["modus"] == "stichwort"
    assert 0 < len(erg["treffer"]) < 21
    assert HINWEIS_STICHWORT in erg["hinweis"] and suche_mod.HINWEIS_UNVOLLSTAENDIG in erg["hinweis"]

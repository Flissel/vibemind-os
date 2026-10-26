"""Tests fuer den Wissens-Index (SQLite + Kosinus), Vektoren von Hand."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wissen_suche.index import WissensIndex  # noqa: E402
from wissen_suche.zerlegen import Abschnitt  # noqa: E402


def ab(n: int) -> list[Abschnitt]:
    return [Abschnitt(i, f"U{i}", f"Text {i}") for i in range(n)]


def vek(*werte: float) -> list[float]:
    return list(werte)


@pytest.fixture
def idx(tmp_path):
    return WissensIndex(tmp_path / "w.sqlite", "m", 3)


def test_ersetzen_ist_atomar_und_ersetzt_alte_abschnitte(idx):
    idx.als_offen("a.md", "h1", 10, 1.0)
    idx.ersetzen("a.md", "h1", ab(3), [vek(1, 0, 0)] * 3)
    assert idx.stand().abschnitte == 3
    idx.als_offen("a.md", "h2", 11, 2.0)
    idx.ersetzen("a.md", "h2", ab(1), [vek(0, 1, 0)])
    st = idx.stand()
    assert st.abschnitte == 1 and st.fertig == 1 and st.offen == 0
    assert idx.dateien()["a.md"].status == "fertig"
    assert idx.dateien()["a.md"].sha256 == "h2"


def test_ersetzen_mit_unpassender_vektoranzahl_aendert_nichts(idx):
    idx.als_offen("a.md", "h1", 10, 1.0)
    with pytest.raises(ValueError):
        idx.ersetzen("a.md", "h1", ab(2), [vek(1, 0, 0)])
    assert idx.stand().abschnitte == 0 and idx.dateien()["a.md"].status == "offen"


def test_ersetzen_mit_veraltetem_hash_tut_nichts(idx, caplog):
    idx.als_offen("a.md", "neu", 10, 1.0)
    with caplog.at_level(logging.WARNING):
        idx.ersetzen("a.md", "alt", ab(2), [vek(1, 0, 0)] * 2)
    assert idx.stand().abschnitte == 0
    assert idx.dateien()["a.md"].status == "offen"
    assert "a.md" in caplog.text


def test_suchen_ein_treffer_je_datei_sortiert(idx):
    idx.als_offen("A.md", "a", 1, 1.0)
    idx.ersetzen("A.md", "a", ab(2), [vek(1, 0.1, 0), vek(1, 0.2, 0)])
    idx.als_offen("B.md", "b", 1, 1.0)
    idx.ersetzen("B.md", "b", ab(1), [vek(0.2, 1, 0)])
    treffer = idx.suchen(vek(1, 0, 0), 5)
    assert [t.pfad for t in treffer] == ["A.md", "B.md"]
    assert treffer[0].ueberschrift == "U0" and treffer[0].text == "Text 0"
    assert treffer[0].punkte > treffer[1].punkte
    assert len(idx.suchen(vek(1, 0, 0), 1)) == 1


def test_modellwechsel_setzt_alles_offen(tmp_path):
    pfad = tmp_path / "w.sqlite"
    idx = WissensIndex(pfad, "m", 3)
    idx.als_offen("a.md", "h", 1, 1.0)
    idx.ersetzen("a.md", "h", ab(2), [vek(1, 0, 0)] * 2)
    idx.close()
    gleich = WissensIndex(pfad, "m", 3)
    assert gleich.stand().abschnitte == 2
    gleich.close()
    neu = WissensIndex(pfad, "x", 3)
    st = neu.stand()
    assert st.abschnitte == 0 and st.offen == 1 and st.fertig == 0 and st.modell == "x"
    neu.close()
    andere_dim = WissensIndex(pfad, "x", 4)
    assert andere_dim.lesen("dim") == "4"


def test_fehlschlag_nach_drei_versuchen_fehler(idx):
    idx.als_offen("a.md", "h", 1, 1.0)
    idx.fehlschlag("a.md", "400")
    idx.fehlschlag("a.md", "400")
    assert idx.dateien()["a.md"].status == "offen" and idx.dateien()["a.md"].versuche == 2
    idx.fehlschlag("a.md", "400")
    assert idx.dateien()["a.md"].status == "fehler"
    assert idx.stand().fehler == 1
    assert idx.offene(10) == []
    idx.als_offen("a.md", "h2", 2, 2.0)                       # Datei geaendert -> neue Chance
    assert idx.dateien()["a.md"].versuche == 0 and idx.offene(10) == ["a.md"]


def test_offene_aelteste_zuerst_und_limit(idx):
    for name in ("c.md", "a.md", "b.md"):
        idx.als_offen(name, "h", 1, 1.0)
    assert idx.offene(2) == ["c.md", "a.md"]


def test_suchen_nach_entfernen_findet_datei_nicht_mehr(idx):
    idx.als_offen("a.md", "h", 1, 1.0)
    idx.ersetzen("a.md", "h", ab(1), [vek(1, 0, 0)])
    assert [t.pfad for t in idx.suchen(vek(1, 0, 0), 3)] == ["a.md"]
    idx.entfernen("a.md")
    assert idx.suchen(vek(1, 0, 0), 3) == []
    assert "a.md" not in idx.dateien()


def test_leerer_index_liefert_leere_liste(idx):
    assert idx.suchen(vek(1, 0, 0), 5) == []
    st = idx.stand()
    assert (st.fertig, st.offen, st.fehler, st.abschnitte, st.modell) == (0, 0, 0, 0, "m")


def test_merken_und_lesen(idx):
    assert idx.lesen("letzte_runde") is None
    idx.merken("letzte_runde", "2026-10-26T12:00:00+00:00")
    assert idx.lesen("letzte_runde") == "2026-10-26T12:00:00+00:00"


def test_wal_modus(idx):
    assert idx.lesen("modell") == "m"
    assert idx._db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"

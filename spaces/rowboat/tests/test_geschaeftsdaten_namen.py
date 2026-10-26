"""Tests fuer Dateinamen der Geschaeftsnotizen (Teil B, Task 2)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_ROWBOAT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROWBOAT / "scripts"))
sys.path.insert(0, str(_ROWBOAT / "geschaeftsdaten"))

from namen import dateistamm, eindeutig, relpfad  # noqa: E402
from wissens_spiegel import _windows_tauglich  # noqa: E402

T1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T2 = datetime(2026, 2, 1, tzinfo=timezone.utc)


def test_verbotene_zeichen_und_rand():
    assert dateistamm(' A<b>:c/d. ', "x", 100) == "A b c d"


def test_steuerzeichen_und_backslash():
    assert dateistamm("a\x00b\\c|d\x7fe", "x", 100) == "a b c d e"


def test_reserviert():
    assert dateistamm("con", "x", 100) == "con_"
    assert dateistamm("COM1", "x", 100) == "COM1_"
    assert dateistamm("Lpt9.x", "x", 100) == "Lpt9_.x"
    assert dateistamm("Conrad", "x", 100) == "Conrad"


def test_leer_wird_ersatz():
    assert dateistamm(" \t", "Kontakt 1234abcd", 100) == "Kontakt 1234abcd"
    assert dateistamm(None, "Kontakt 1234abcd", 100) == "Kontakt 1234abcd"
    assert dateistamm(" ... ", "Kontakt 1234abcd", 100) == "Kontakt 1234abcd"


def test_nfc():
    assert dateistamm("Müller", "x", 100) == "Müller"


def test_max_len_ohne_rand():
    assert dateistamm("abc. defgh", "x", 5) == "abc"
    assert len(dateistamm("y" * 300, "x", 100)) == 100


def test_doppelt_casefold_aeltester_behaelt():
    r = eindeutig([("b" * 36, "Max Müller", T2), ("a" * 36, "max müller", T1)])
    assert r["a" * 36] == "max müller" and r["b" * 36] == "Max Müller (bbbbbbbb)"


def test_doppelt_gleiche_zeit_entscheidet_id():
    r = eindeutig([("b" * 36, "Anna", T1), ("a" * 36, "Anna", T1)])
    assert r["a" * 36] == "Anna" and r["b" * 36] == "Anna (bbbbbbbb)"


def test_doppelt_nfd_gegen_nfc():  # gilt erst nach dateistamm als gleich
    r = eindeutig([("a" * 36, dateistamm("Müller", "x", 100), T1), ("b" * 36, "Müller", T2)])
    assert len({v.casefold() for v in r.values()}) == 2


def test_eindeutig_ohne_doppelte_bleibt():
    r = eindeutig([("a" * 36, "Anna", T1), ("b" * 36, "Bert", T2)])
    assert r == {"a" * 36: "Anna", "b" * 36: "Bert"}


def test_langer_pfad_passt():
    p = relpfad("companys/VibeMind/Kontakte/Private Vorsorge und Versicherung", "x" * 300)
    assert len(p) <= 240 and _windows_tauglich(p) and p.endswith(".md")


def test_langer_pfad_behaelt_id_zusatz():
    p = relpfad("companys/VibeMind/Kontakte/bAV", "x" * 300 + " (abcdef12)")
    assert len(p) <= 240 and _windows_tauglich(p) and p.endswith("x (abcdef12).md")


def test_kurzer_pfad_unveraendert():
    assert relpfad("companys/VibeMind/Kontakte/bAV", "Anna") == "companys/VibeMind/Kontakte/bAV/Anna.md"


def test_kuerzen_ohne_punkt_am_ende():
    ordner = "companys/" + "o" * 200
    p = relpfad(ordner, "a" * 26 + ". " + "y" * 100)  # Schnitt faellt genau hinter ". "
    assert _windows_tauglich(p) and p.endswith("/" + "a" * 26 + ".md")


def test_zu_tiefer_ordner_wirft():
    with pytest.raises(ValueError):
        relpfad("companys/" + "o" * 225, "Anna Beispiel")


def test_mailadressen_fallen_aus_dem_namen():
    assert dateistamm("Max Muster max.muster@firma.de", "x", 100) == "Max Muster"
    assert dateistamm("Anna (anna_b+nl@Beispiel.CO)", "x", 100) == "Anna"
    assert dateistamm("<info@firma.de>", "Kontakt 1234abcd", 100) == "Kontakt 1234abcd"
    assert dateistamm("mailto:a@b.de Bert", "x", 100) == "Bert"
    assert dateistamm("Firma @ Home", "x", 100) == "Firma @ Home"  # kein Adressmuster

"""Tests fuer Laden-Env und Firmenordner (Teil B, Task 3)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROWBOAT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROWBOAT / "scripts"))
sys.path.insert(0, str(_ROWBOAT / "geschaeftsdaten"))

from laeden import Laden, env_werte, firmenordner, laeden_finden  # noqa: E402

ID1 = "11111111-1111-1111-1111-111111111111"
ID2 = "22222222-2222-2222-2222-222222222222"
ID3 = "33333333-3333-3333-3333-333333333333"


def _schreiben(pfad: Path, text: str) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_bytes(text.encode("utf-8"))


@pytest.fixture
def sc(tmp_path: Path) -> Path:
    (tmp_path / "deploy" / "laeden").mkdir(parents=True)
    return tmp_path


def test_basis_laden_vorgabe_sales(sc: Path):
    _schreiben(sc / ".env", "MARKETING_MANDANT=vibemind\r\n"
                            f"INBOX_UNBEKANNT_LEAD_ID={ID1}\r\n"
                            f"RECHERCHE_LEAD_ID=\"{ID2}\"\r\n"
                            f"LINKEDIN_POST_LEAD_ID='{ID3}' \r\n")
    laeden, warnungen = laeden_finden(sc)
    assert laeden == [Laden("sales", "vibemind", frozenset({ID1, ID2, ID3}))]
    assert warnungen == []


def test_laden_marke_vor_mandant(sc: Path):
    _schreiben(sc / ".env", "MARKETING_MANDANT=vibemind\n")
    _schreiben(sc / "deploy/laeden/ivan.env", "SALES_DB_SCHEMA=sales_ivan\nLADEN_MARKE=fin2gether\n"
                                              f"MARKETING_MANDANT=vibemind\nINBOX_UNBEKANNT_LEAD_ID={ID1}\n"
                                              "RECHERCHE_LEAD_ID=\n")
    laeden, _ = laeden_finden(sc)
    assert Laden("sales_ivan", "fin2gether", frozenset({ID1})) in laeden


def test_beispiel_und_sicherungen_ignoriert(sc: Path):
    _schreiben(sc / ".env", "MARKETING_MANDANT=vibemind\n")
    _schreiben(sc / "deploy/laeden/beispiel.env", "SALES_DB_SCHEMA=sales_ivan\nLADEN_MARKE=fin2gether\n")
    _schreiben(sc / "deploy/laeden/ivan.env.vor-aussehen", "SALES_DB_SCHEMA=sales_alt\nLADEN_MARKE=alt\n")
    laeden, warnungen = laeden_finden(sc)
    assert [l.schema for l in laeden] == ["sales"] and warnungen == []


def test_ohne_marke_uebersprungen(sc: Path):
    _schreiben(sc / ".env", "MARKETING_MANDANT=vibemind\n")
    _schreiben(sc / "deploy/laeden/leer.env", "SALES_DB_SCHEMA=sales_leer\n")
    laeden, warnungen = laeden_finden(sc)
    assert [l.schema for l in laeden] == ["sales"]
    assert len(warnungen) == 1 and warnungen[0].startswith("leer.env")


def test_ungueltige_marke_und_schema(sc: Path):
    _schreiben(sc / ".env", "MARKETING_MANDANT=Viel Mind\n")
    _schreiben(sc / "deploy/laeden/x.env", "SALES_DB_SCHEMA=sales; drop\nLADEN_MARKE=x_marke\n")
    laeden, warnungen = laeden_finden(sc)
    assert laeden == [] and len(warnungen) == 2


def test_sales_test_nie(sc: Path):
    _schreiben(sc / ".env", "MARKETING_MANDANT=vibemind\n")
    _schreiben(sc / "deploy/laeden/test.env", "SALES_DB_SCHEMA=sales_test\nLADEN_MARKE=vibemind\n")
    laeden, _ = laeden_finden(sc)
    assert [l.schema for l in laeden] == ["sales"]


def test_doppeltes_schema_beide_weg(sc: Path):
    _schreiben(sc / ".env", "MARKETING_MANDANT=vibemind\n")
    _schreiben(sc / "deploy/laeden/a.env", "SALES_DB_SCHEMA=sales_ivan\nLADEN_MARKE=fin2gether\n")
    _schreiben(sc / "deploy/laeden/b.env", "SALES_DB_SCHEMA=sales_ivan\nLADEN_MARKE=vibemind\n")
    laeden, warnungen = laeden_finden(sc)
    assert [l.schema for l in laeden] == ["sales"]
    assert len(warnungen) == 2


def test_ohne_basis_env_nur_warnung(sc: Path):
    laeden, warnungen = laeden_finden(sc)
    assert laeden == [] and len(warnungen) == 1 and warnungen[0].startswith(".env")


def test_nur_erlaubte_schluessel(sc: Path):
    _schreiben(sc / ".env", "GEHEIM_KEY=xyz\nMARKETING_MANDANT=vibemind\n")
    _schreiben(sc / "deploy/laeden/k.env", "GEHEIM_KEY=xyz\nSALES_DB_SCHEMA=sales_k\n")
    laeden, warnungen = laeden_finden(sc)
    assert "xyz" not in repr(laeden) and "xyz" not in repr(warnungen)
    assert env_werte(sc / ".env", frozenset({"MARKETING_MANDANT"})) == {"MARKETING_MANDANT": "vibemind"}


def test_env_werte_kommentare_export_letzter_gewinnt(tmp_path: Path):
    p = tmp_path / "x.env"
    _schreiben(p, "# LADEN_MARKE=alt\nexport LADEN_MARKE = erst\nLADEN_MARKE=zweit\nkaputt\n")
    assert env_werte(p, frozenset({"LADEN_MARKE"})) == {"LADEN_MARKE": "zweit"}


def test_firmenordner_vorhanden_ohne_gross_klein(tmp_path: Path):
    (tmp_path / "companys" / "Vibemind").mkdir(parents=True)
    assert firmenordner(tmp_path, "VibeMind") == tmp_path / "companys" / "Vibemind"
    assert firmenordner(tmp_path, "fin2gether") == tmp_path / "companys" / "fin2gether"


def test_firmenordner_ungueltiger_name(tmp_path: Path):
    for name in ("", "..", "a/b", "CON", "x.", "a:b"):
        with pytest.raises(ValueError):
            firmenordner(tmp_path, name)


def test_firmenordner_symlink(tmp_path: Path):
    ziel = tmp_path / "anderswo"
    ziel.mkdir()
    (tmp_path / "companys").mkdir()
    try:
        os.symlink(ziel, tmp_path / "companys" / "VibeMind", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("kein Symlink-Recht")
    with pytest.raises(ValueError):
        firmenordner(tmp_path, "vibemind")


def test_laden_datei_braucht_laden_marke(sc: Path):
    _schreiben(sc / ".env", "MARKETING_MANDANT=vibemind\n")
    _schreiben(sc / "deploy/laeden/neu.env", "SALES_DB_SCHEMA=sales_neu\nMARKETING_MANDANT=vibemind\n")
    laeden, warnungen = laeden_finden(sc)
    assert [l.schema for l in laeden] == ["sales"]
    assert warnungen == ["neu.env: keine LADEN_MARKE"]

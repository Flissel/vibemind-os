"""Tests fuer deploy/pc_env_setzen.py: Name=Wert-Zeilen von stdin zeilensicher in eine .env schreiben."""
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
sys.path.insert(0, str(DEPLOY))
import pc_env_setzen  # noqa: E402

GEHEIM = "GEHEIM-schluessel_42"
URL = "https://beispiel.invalid:8172/mcp"
EINGABE = f"AUFTRAGSBUCH_URL={URL}\nAUFTRAGSBUCH_SCHLUESSEL={GEHEIM}\n"


class _Pipe(io.StringIO):
    def isatty(self) -> bool:
        return False


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def _env(tmp_path: Path, inhalt: bytes) -> Path:
    datei = tmp_path / ".env"
    datei.write_bytes(inhalt)
    return datei


def _setzen(datei: Path, eingabe: str = EINGABE) -> int:
    return pc_env_setzen.main([str(datei)], _Pipe(eingabe), _Pipe())


def test_haengt_an_auch_ohne_zeilenende_am_schluss(tmp_path: Path) -> None:
    datei = _env(tmp_path, b"A=1")
    assert _setzen(datei) == 0
    assert datei.read_bytes() == f"A=1\nAUFTRAGSBUCH_URL={URL}\nAUFTRAGSBUCH_SCHLUESSEL={GEHEIM}\n".encode()


def test_ersetzt_vorhandene_variable_statt_doppelt_anzuhaengen(tmp_path: Path) -> None:
    datei = _env(tmp_path, b"A=1\nAUFTRAGSBUCH_URL=alt\n# AUFTRAGSBUCH_SCHLUESSEL=auskommentiert\nC = 3\n")
    assert _setzen(datei) == 0
    assert datei.read_bytes() == (
        f"A=1\nAUFTRAGSBUCH_URL={URL}\n# AUFTRAGSBUCH_SCHLUESSEL=auskommentiert\nC = 3\n"
        f"AUFTRAGSBUCH_SCHLUESSEL={GEHEIM}\n").encode()
    # ein zweiter Lauf aendert nichts mehr (wiederholbar)
    vorher = datei.read_bytes()
    assert _setzen(datei) == 0 and datei.read_bytes() == vorher


def test_datei_bleibt_sonst_byte_fuer_byte_gleich(tmp_path: Path) -> None:
    rest = "# Kommentar äöü\r\n\r\nOPENAI_API_KEY='x y'\r\n  LEER=\r\nAUFTRAGSBUCH_SCHLUESSEL=alt\r\nZ=9\r\n"
    datei = _env(tmp_path, rest.encode("utf-8"))
    assert _setzen(datei) == 0
    neu = datei.read_bytes().decode("utf-8")
    # gleiche Zeilenenden (CRLF) auch fuer neue und ersetzte Zeilen, alle anderen Zeilen unveraendert
    assert neu == rest.replace("AUFTRAGSBUCH_SCHLUESSEL=alt", f"AUFTRAGSBUCH_SCHLUESSEL={GEHEIM}") \
        + f"AUFTRAGSBUCH_URL={URL}\r\n"


def test_gibt_nie_einen_wert_aus(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    datei = _env(tmp_path, b"A=1\n")
    aus = _Pipe()
    assert pc_env_setzen.main([str(datei)], _Pipe(EINGABE), aus) == 0
    gesamt = aus.getvalue() + "".join(capsys.readouterr())
    assert GEHEIM not in gesamt and URL not in gesamt
    assert "AUFTRAGSBUCH_URL" in aus.getvalue() and "AUFTRAGSBUCH_SCHLUESSEL" in aus.getvalue()


def test_cli_ueber_stdin_ohne_wert_in_der_ausgabe(tmp_path: Path) -> None:
    datei = _env(tmp_path, b"A=1\n")
    lauf = subprocess.run([sys.executable, str(DEPLOY / "pc_env_setzen.py"), str(datei)],
                          input=EINGABE.encode(), capture_output=True, timeout=60, check=False)
    assert lauf.returncode == 0, lauf.stderr
    assert GEHEIM.encode() not in lauf.stdout + lauf.stderr and b"beispiel.invalid" not in lauf.stdout + lauf.stderr
    assert GEHEIM in datei.read_text(encoding="utf-8")


def test_entfernen_nimmt_nur_die_genannten_zeilen(tmp_path: Path) -> None:
    datei = _env(tmp_path, f"A=1\nAUFTRAGSBUCH_URL={URL}\nB=2\nAUFTRAGSBUCH_SCHLUESSEL={GEHEIM}".encode())
    aus = _Pipe()
    assert pc_env_setzen.main([str(datei), "--entfernen", "AUFTRAGSBUCH_URL", "AUFTRAGSBUCH_SCHLUESSEL"],
                              _Terminal(), aus) == 0
    assert datei.read_bytes() == b"A=1\nB=2\n"
    assert GEHEIM not in aus.getvalue()


@pytest.mark.parametrize("eingabe, teil", [
    ("", "keine Zeile"),
    ("ohne gleichheitszeichen\n", "Zeile 1"),
    ("AUFTRAGSBUCH_URL=\n", "AUFTRAGSBUCH_URL"),
    (f"AUFTRAGSBUCH_SCHLUESSEL=\"{GEHEIM}\"\n", "AUFTRAGSBUCH_SCHLUESSEL"),
    (f"AUFTRAGSBUCH_SCHLUESSEL= {GEHEIM}\n", "AUFTRAGSBUCH_SCHLUESSEL"),
    (f"A=1\nA={GEHEIM}\n", "doppelt"),
    (f"1A={GEHEIM}\n", "Zeile 1"),
])
def test_fehler_aendern_nichts_und_nennen_keinen_wert(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                      eingabe: str, teil: str) -> None:
    datei = _env(tmp_path, b"A=1\n")
    assert _setzen(datei, eingabe) == 2
    fehler = capsys.readouterr().err
    assert teil in fehler and GEHEIM not in fehler
    assert datei.read_bytes() == b"A=1\n"


def test_terminal_und_fehlende_datei_werden_abgelehnt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    datei = _env(tmp_path, b"A=1\n")
    assert pc_env_setzen.main([str(datei)], _Terminal(EINGABE), _Pipe()) == 2
    assert datei.read_bytes() == b"A=1\n"
    assert pc_env_setzen.main([str(tmp_path / "fehlt.env")], _Pipe(EINGABE), _Pipe()) == 2
    assert not (tmp_path / "fehlt.env").exists()
    assert GEHEIM not in capsys.readouterr().err


def test_temp_datei_traegt_ignore_namen_und_verschwindet_bei_fehlgeschlagenem_ersetzen(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import fnmatch

    datei = _env(tmp_path, b"A=1\nAUFTRAGSBUCH_SCHLUESSEL=alt-geheim\n")
    gesehen: list[str] = []

    def kaputt(quelle: object, ziel: object) -> None:
        gesehen.append(Path(str(quelle)).name)
        # waehrend des Ersetzens liegt die Kopie mit dem neuen Wert daneben
        assert GEHEIM in Path(str(quelle)).read_text(encoding="utf-8")
        raise PermissionError(13, f"gesperrt {GEHEIM} {URL}")

    monkeypatch.setattr(pc_env_setzen.os, "replace", kaputt)
    aus = _Pipe()
    assert pc_env_setzen.main([str(datei)], _Pipe(EINGABE), aus) != 0
    assert len(gesehen) == 1 and fnmatch.fnmatch(gesehen[0], ".env.vor-*")
    assert [p.name for p in tmp_path.iterdir()] == [".env"]
    assert datei.read_bytes() == b"A=1\nAUFTRAGSBUCH_SCHLUESSEL=alt-geheim\n"
    gesamt = aus.getvalue() + "".join(capsys.readouterr())
    assert GEHEIM not in gesamt and URL not in gesamt and "alt-geheim" not in gesamt
    assert "PermissionError" in gesamt


def test_entfernen_raeumt_bei_fehlgeschlagenem_ersetzen_ebenfalls_auf(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    datei = _env(tmp_path, b"A=1\nAUFTRAGSBUCH_URL=x\n")

    def kaputt(quelle: object, ziel: object) -> None:
        raise OSError("weg")

    monkeypatch.setattr(pc_env_setzen.os, "replace", kaputt)
    assert pc_env_setzen.main([str(datei), "--entfernen", "AUFTRAGSBUCH_URL"], _Terminal(), _Pipe()) != 0
    assert [p.name for p in tmp_path.iterdir()] == [".env"]
    assert datei.read_bytes() == b"A=1\nAUFTRAGSBUCH_URL=x\n"


def test_erfolg_hinterlaesst_keine_temp_datei(tmp_path: Path) -> None:
    datei = _env(tmp_path, b"A=1\n")
    assert _setzen(datei) == 0
    assert [p.name for p in tmp_path.iterdir()] == [".env"]

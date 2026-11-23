"""Fix-Welle B11: die Unterkommandos `aufnehmen` und `bestand`."""

from datetime import date
from pathlib import Path

import pytest

from djset import cli
from djset.bestand import Bestand
from djset.cli import cmd_aufnehmen, cmd_bestand
from tests.fixtures.make_audio import click_track
from tests.test_cli_set import _lege_song_ab

HEUTE = date(2026, 11, 23)


def test_aufnehmen_nimmt_auf_misst_und_nennt_das_ergebnis(tmp_path: Path, capsys):
    bestand = Bestand(tmp_path)
    click_track(bestand.eingang / "lied.wav", bpm=126.0, bars=16)

    code = cmd_aufnehmen(
        tmp_path, "lied.wav", "Über den Wolken", "suno-eigen", "abc", HEUTE
    )

    assert code == 0
    eintrag = bestand.hole("ueber-den-wolken")
    assert eintrag.suno_id == "abc"
    assert eintrag.messung is not None
    ausgabe = capsys.readouterr().out
    assert "ueber-den-wolken" in ausgabe
    assert f"{eintrag.messung.bpm.value:.1f} BPM" in ausgabe
    assert eintrag.messung.key.camelot in ausgabe
    sekunden = round(eintrag.messung.duration_s)
    assert f"{sekunden // 60}:{sekunden % 60:02d}" in ausgabe


def test_aufnehmen_mit_ungueltiger_herkunft(tmp_path: Path, capsys):
    bestand = Bestand(tmp_path)
    click_track(bestand.eingang / "lied.wav", bpm=126.0, bars=4)

    code = cmd_aufnehmen(tmp_path, "lied.wav", "Lied", "gefunden", None, HEUTE)

    assert code == 1
    assert capsys.readouterr().err.startswith("Fehler:")
    assert bestand.alle() == []


def test_aufnehmen_unter_einem_vertippten_pfad_legt_nichts_an(tmp_path: Path, capsys):
    falsch = tmp_path / "gibtsnicht"

    assert cmd_aufnehmen(falsch, "x.wav", "X", "suno-eigen", None, HEUTE) == 1

    assert not falsch.exists()
    assert capsys.readouterr().err.startswith("Fehler:")


def test_aufnehmen_scheitert_beim_messen_mit_song_id(tmp_path: Path, capsys, monkeypatch):
    bestand = Bestand(tmp_path)
    click_track(bestand.eingang / "lied.wav", bpm=126.0, bars=4)

    def kaputt(self, song_id):
        raise RuntimeError("librosa ist umgefallen")

    monkeypatch.setattr(Bestand, "messen", kaputt)

    assert cmd_aufnehmen(tmp_path, "lied.wav", "Lied", "suno-eigen", None, HEUTE) == 1

    assert "Fehler: lied: librosa ist umgefallen" in capsys.readouterr().err


def test_bestand_zeigt_eine_tabelle(tmp_path: Path, capsys):
    bestand = Bestand(tmp_path)
    _lege_song_ab(bestand, "sommer", 124.0, dauer_s=605.0)
    _lege_song_ab(bestand, "ungemessen", None)

    assert cmd_bestand(tmp_path) == 0

    zeilen = capsys.readouterr().out.splitlines()
    kopf = zeilen[0]
    for spalte in ("ID", "Titel", "Tempo", "Tonart", "Laenge", "Herkunft"):
        assert spalte in kopf
    sommer = next(z for z in zeilen if z.startswith("sommer"))
    assert "124.0" in sommer and "8A" in sommer and "10:05" in sommer
    assert "suno-eigen" in sommer
    ungemessen = next(z for z in zeilen if z.startswith("ungemessen"))
    assert " - " in ungemessen


def test_bestand_leer(tmp_path: Path, capsys):
    assert cmd_bestand(tmp_path) == 0
    assert "leer" in capsys.readouterr().out


def test_bestand_mit_kaputtem_eintrag(tmp_path: Path, capsys):
    bestand = Bestand(tmp_path)
    (bestand.songs / "kaputt.json").write_text("{", encoding="utf-8")

    assert cmd_bestand(tmp_path) == 1
    assert capsys.readouterr().err.startswith("Fehler:")


def test_main_aufnehmen_nimmt_daten_aus_der_umgebung(tmp_path: Path, monkeypatch):
    gesehen: dict[str, object] = {}

    def fake(daten, datei, titel, herkunft, suno_id, today) -> int:
        gesehen.update(daten=daten, datei=datei, titel=titel, herkunft=herkunft,
                       suno_id=suno_id)
        return 0

    monkeypatch.setattr(cli, "cmd_aufnehmen", fake)
    monkeypatch.setenv("MUSIK_DATEN", str(tmp_path))

    code = cli.main(
        ["aufnehmen", "--datei", "a.mp3", "--titel", "A", "--herkunft", "suno-eigen",
         "--suno-id", "X1"]
    )

    assert code == 0
    assert gesehen == {"daten": tmp_path, "datei": "a.mp3", "titel": "A",
                       "herkunft": "suno-eigen", "suno_id": "X1"}


def test_main_bestand_ohne_daten_ist_ein_fehler(monkeypatch, capsys):
    monkeypatch.delenv("MUSIK_DATEN", raising=False)
    monkeypatch.setattr(cli, "cmd_bestand", lambda *a: pytest.fail("gerufen"))

    assert cli.main(["bestand"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("Fehler:") and "MUSIK_DATEN" in err


def test_main_bestand_reicht_daten_durch(tmp_path: Path, monkeypatch):
    gesehen: list[Path] = []
    monkeypatch.setattr(cli, "cmd_bestand", lambda daten: gesehen.append(daten) or 0)

    assert cli.main(["bestand", "--daten", str(tmp_path)]) == 0
    assert gesehen == [tmp_path]

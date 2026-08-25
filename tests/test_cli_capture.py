"""Tests fuer die CLI-Unterkommandos `record` und `import`.

Auch hier: kein Test ruft ein echtes Aufnahmegeraet auf. cmd_record() wird
gegen ein gemonkeypatchtes djset.capture.record geprueft, cmd_import() laeuft
echt (nur ffmpeg-Umwandlung, kein Geraet).
"""

import subprocess
import sys
from pathlib import Path

import pytest

from djset import cli
from djset.capture import CaptureFailed
from djset.cli import cmd_import, cmd_record
from djset.render.stretch import RenderToolMissing
from tests.fixtures.make_audio import click_track


# --- cmd_record --------------------------------------------------------------


def test_record_lists_devices_and_exits_zero_when_no_device_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setattr(
        cli,
        "list_input_devices",
        lambda: ["Mikrofon (HyperX QuadCast 2)", "Mikrofon (HD Webcam eMeet C960)"],
    )

    code = cmd_record(
        base=tmp_path, device=None, seconds=None, name=None, list_devices=True
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "Mikrofon (HyperX QuadCast 2)" in out
    assert "Mikrofon (HD Webcam eMeet C960)" in out


def test_record_without_device_and_without_list_devices_fails_cleanly(
    tmp_path: Path, capsys
) -> None:
    code = cmd_record(
        base=tmp_path, device=None, seconds=5.0, name=None, list_devices=False
    )

    assert code != 0
    assert "Fehler" in capsys.readouterr().err


def test_record_with_a_non_positive_seconds_fails_cleanly(
    tmp_path: Path, capsys
) -> None:
    code = cmd_record(
        base=tmp_path, device="Mikrofon", seconds=0.0, name=None, list_devices=False
    )

    assert code != 0
    assert "Fehler" in capsys.readouterr().err


def test_record_writes_into_the_watch_folder_under_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    written: list[tuple[str, Path, float]] = []

    def fake_record(device: str, target: Path, seconds: float) -> None:
        written.append((device, target, seconds))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")

    monkeypatch.setattr(cli, "record", fake_record)

    code = cmd_record(
        base=tmp_path,
        device="Mikrofon (HyperX QuadCast 2)",
        seconds=8.0,
        name="intro-aux",
        list_devices=False,
    )

    assert code == 0
    assert len(written) == 1
    device, target, seconds = written[0]
    assert device == "Mikrofon (HyperX QuadCast 2)"
    assert seconds == 8.0
    assert target.parent == tmp_path / "watch"
    assert target.name == "intro-aux.wav"
    assert "intro-aux.wav" in capsys.readouterr().out


def test_record_without_a_name_derives_the_filename_from_the_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[Path] = []

    def fake_record(device: str, target: Path, seconds: float) -> None:
        written.append(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")

    monkeypatch.setattr(cli, "record", fake_record)

    cmd_record(
        base=tmp_path,
        device="Mikrofon (HyperX QuadCast 2)",
        seconds=8.0,
        name=None,
        list_devices=False,
    )

    assert written[0].name == "mikrofon-hyperx-quadcast-2.wav"


def test_recording_twice_without_a_name_produces_two_files_not_one_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Findung 1: cmd_record leitete den Dateinamen bisher einmal ab und liess
    ffmpeg mit -y unbesehen ueberschreiben. Zwei Aufnahmen ohne --name (oder
    mit demselben --name, oder vom selben Geraet) durften bisher die erste
    Aufnahme kommentarlos vernichten - das nicht wiederholbare Aux-Material.
    Hier wird cli.record gemonkeypatcht, schreibt aber wirklich eine Datei,
    damit der Kollisionspfad (target.exists()) echt ausgeloest wird."""
    written: list[Path] = []

    def fake_record(device: str, target: Path, seconds: float) -> None:
        content = b"erste Aufnahme" if not written else b"zweite Aufnahme"
        written.append(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    monkeypatch.setattr(cli, "record", fake_record)

    code1 = cmd_record(
        base=tmp_path, device="Mikrofon", seconds=1.0, name=None, list_devices=False
    )
    code2 = cmd_record(
        base=tmp_path, device="Mikrofon", seconds=1.0, name=None, list_devices=False
    )

    assert code1 == 0
    assert code2 == 0
    assert written[0].name == "mikrofon.wav"
    assert written[1].name == "mikrofon-2.wav"
    assert written[0].read_bytes() == b"erste Aufnahme"
    assert written[1].read_bytes() == b"zweite Aufnahme"
    out = capsys.readouterr().out
    assert "mikrofon-2.wav" in out


def test_record_list_devices_wins_even_when_device_is_also_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    """--list-devices kombiniert mit --device durfte bisher stillschweigend
    durchfallen und in einen echten Aufnahmeversuch muenden. Wer explizit
    auflisten will, meint das eindeutig - unabhaengig davon, ob --device
    nebenbei mitgegeben wurde."""
    monkeypatch.setattr(cli, "list_input_devices", lambda: ["Mikrofon"])

    code = cmd_record(
        base=tmp_path,
        device="Mikrofon",
        seconds=5.0,
        name=None,
        list_devices=True,
    )

    assert code == 0
    assert "Mikrofon" in capsys.readouterr().out


def test_record_reports_render_tool_missing_in_german_and_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    def fake_record(device: str, target: Path, seconds: float) -> None:
        raise RenderToolMissing("ffmpeg wurde nicht gefunden.")

    monkeypatch.setattr(cli, "record", fake_record)

    code = cmd_record(
        base=tmp_path, device="Mikrofon", seconds=1.0, name=None, list_devices=False
    )

    assert code == 1
    err = capsys.readouterr().err
    assert "Fehler" in err
    assert "ffmpeg" in err


def test_record_reports_capture_failed_in_german_and_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    def fake_record(device: str, target: Path, seconds: float) -> None:
        raise CaptureFailed("ffmpeg scheiterte bei der Aufnahme: Geraet belegt")

    monkeypatch.setattr(cli, "record", fake_record)

    code = cmd_record(
        base=tmp_path, device="Mikrofon", seconds=1.0, name=None, list_devices=False
    )

    assert code == 1
    err = capsys.readouterr().err
    assert "Fehler" in err
    assert "Geraet belegt" in err


def test_record_list_devices_reports_render_tool_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    def fake_list() -> list[str]:
        raise RenderToolMissing("ffmpeg wurde nicht gefunden.")

    monkeypatch.setattr(cli, "list_input_devices", fake_list)

    code = cmd_record(
        base=tmp_path, device=None, seconds=None, name=None, list_devices=True
    )

    assert code == 1
    assert "Fehler" in capsys.readouterr().err


# --- cmd_import ---------------------------------------------------------------


def test_import_converts_the_folder_into_watch_and_reports_it(
    tmp_path: Path, capsys
) -> None:
    source_dir = tmp_path / "material"
    source_dir.mkdir()
    click_track(source_dir / "erster.wav", bpm=120.0, bars=2, sr=48_000)
    click_track(source_dir / "zweiter.wav", bpm=120.0, bars=2, sr=44_100)

    code = cmd_import(base=tmp_path, source=source_dir)

    assert code == 0
    watch = tmp_path / "watch"
    written = sorted(p.name for p in watch.iterdir())
    assert written == ["erster.wav", "zweiter.wav"]
    out = capsys.readouterr().out
    assert "erster.wav" in out
    assert "zweiter.wav" in out


def test_import_of_an_empty_folder_reports_nothing_written(
    tmp_path: Path, capsys
) -> None:
    source_dir = tmp_path / "leer"
    source_dir.mkdir()

    code = cmd_import(base=tmp_path, source=source_dir)

    assert code == 0
    assert list((tmp_path / "watch").iterdir()) == []


def test_import_reports_render_tool_missing_in_german_and_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    source_dir = tmp_path / "material"
    source_dir.mkdir()
    click_track(source_dir / "erster.wav", bpm=120.0, bars=2, sr=44_100)
    monkeypatch.setattr("djset.render.stretch.shutil.which", lambda name: None)

    code = cmd_import(base=tmp_path, source=source_dir)

    assert code == 1
    err = capsys.readouterr().err
    assert "Fehler" in err
    assert "ffmpeg" in err


def test_import_reports_capture_failed_in_german_and_exits_one(
    tmp_path: Path, capsys
) -> None:
    source_dir = tmp_path / "material"
    source_dir.mkdir()
    (source_dir / "kaputt.wav").write_text("kein Audio, nur Text")

    code = cmd_import(base=tmp_path, source=source_dir)

    assert code == 1
    err = capsys.readouterr().err
    assert "Fehler" in err


# --- main() dispatch -----------------------------------------------------------


def test_main_dispatches_record_list_devices(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setattr(cli, "list_input_devices", lambda: ["Mikrofon"])

    code = cli.main(["record", "--list-devices", "--base", str(tmp_path)])

    assert code == 0
    assert "Mikrofon" in capsys.readouterr().out


def test_main_dispatches_import(tmp_path: Path, capsys) -> None:
    source_dir = tmp_path / "material"
    source_dir.mkdir()
    click_track(source_dir / "erster.wav", bpm=120.0, bars=2, sr=44_100)

    code = cli.main(["import", str(source_dir), "--base", str(tmp_path)])

    assert code == 0
    assert (tmp_path / "watch" / "erster.wav").exists()


def test_neither_record_nor_import_import_the_agents_client_module(
    tmp_path: Path,
) -> None:
    """djset.agents.client zieht claude_agent_sdk nach - kein plan/build-Aufruf,
    kein Netzwerkzugriff darf hier hineinstolpern. cli.py importiert
    ClaudeClient bewusst nur lazy innerhalb der plan/build-Zweige; record und
    import duerfen diesen Pfad nicht anfassen.

    Das laeuft in einem eigenen Python-Prozess statt gegen sys.modules im
    Testprozess: andere Tests im selben Lauf (etwa in test_cli_build.py) rufen
    main(["build", ...]) frueher auf und importieren djset.agents.client dabei
    ganz legitim - danach bliebe es fuer den Rest der Session in sys.modules,
    egal was record/import selbst tun. Ein frischer Prozess ist der einzige
    Weg, das Verhalten von record/import isoliert zu pruefen. --seconds 0
    laesst cmd_record vor jedem echten ffmpeg-Aufruf mit Exitcode 1 abbrechen -
    es geht hier nur um den Importpfad, nicht um eine echte Aufnahme.
    """
    script = (
        "import sys\n"
        "from djset.cli import main\n"
        "main(['record', '--device', 'x', '--seconds', '0'])\n"
        "print('IMPORTED' if 'djset.agents.client' in sys.modules else 'CLEAN')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "CLEAN"

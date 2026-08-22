"""Tests fuer djset.capture: Aufnahme vom Aux-Eingang und Umwandlung vorhandener Dateien.

Kein Test hier ruft ein echtes Aufnahmegeraet auf - diese Maschine hat keinen
Aux-Eingang, nur zwei Mikrofone, und ein Test, der ein echtes Geraet oeffnet,
haengt oder nimmt Stille auf, je nach Maschine. list_input_devices() wird
deshalb nur ueber seinen Parser gegen einen fest eingebetteten ffmpeg-Mitschnitt
geprueft, record() nur in seiner Kommandokonstruktion (subprocess.run
gemonkeypatcht). convert() und import_folder() laufen dagegen echt - sie
brauchen nur eine Datei auf der Platte und einen kurzen, deterministischen
ffmpeg-Aufruf.
"""

import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from djset.capture import (
    CaptureFailed,
    _parse_device_list,
    convert,
    import_folder,
    list_input_devices,
    record,
)
from djset.render.stretch import RenderToolMissing
from djset.watch import check_sample_rate, scan

# Echte Ausgabe von dieser Maschine (ffmpeg -f dshow -list_devices true -i
# dummy), inklusive der Video-Geraete und der "Alternative name"-Zeilen, die
# der Parser ignorieren muss.
DSHOW_FIXTURE = """\
[in#0 @ 0000024054] "HD Webcam eMeet C960" (video)
[in#0 @ 0000024054]   Alternative name "@device_pnp_\\\\?\\usb#vid_328f&pid_006d..."
[in#0 @ 0000024054] "OBS Virtual Camera" (video)
[in#0 @ 0000024054]   Alternative name "@device_sw_{860BB310-5D01-11D0-BD3B-00A0C911CE86}..."
[in#0 @ 0000024054] "Mikrofon (HyperX QuadCast 2)" (audio)
[in#0 @ 0000024054]   Alternative name "@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}\\wave_{3348B4BA}"
[in#0 @ 0000024054] "Mikrofon (HD Webcam eMeet C960)" (audio)
[in#0 @ 0000024054]   Alternative name "@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}\\wave_{9FA4088D}"
[in#0 @ 0000024054] Error opening input file dummy.
"""


def _tone(path: Path, samplerate: int, seconds: float = 0.3, channels: int = 1) -> Path:
    frames = int(seconds * samplerate)
    mono = np.sin(2 * np.pi * 220.0 * np.arange(frames) / samplerate).astype(np.float32)
    signal = mono if channels == 1 else np.stack([mono, mono * 0.5], axis=1)
    sf.write(path, signal, samplerate)
    return path


# --- list_input_devices / _parse_device_list -------------------------------


def test_the_parser_returns_exactly_the_two_audio_device_names() -> None:
    assert _parse_device_list(DSHOW_FIXTURE) == [
        "Mikrofon (HyperX QuadCast 2)",
        "Mikrofon (HD Webcam eMeet C960)",
    ]


def test_the_parser_ignores_output_with_no_audio_devices_at_all() -> None:
    only_video = """\
[in#0 @ 0000024054] "HD Webcam eMeet C960" (video)
[in#0 @ 0000024054]   Alternative name "@device_pnp_..."
[in#0 @ 0000024054] Error opening input file dummy.
"""
    assert _parse_device_list(only_video) == []


def test_list_input_devices_parses_the_fixture_without_touching_a_live_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """subprocess.run wird gemonkeypatcht - es findet also kein echter
    dshow-Aufruf statt, nur die Kommandokonstruktion und das Parsen der
    (fest eingebetteten) Ausgabe werden geprueft."""
    calls: list[list[str]] = []

    def fake_run(command: list[str], capture_output: bool, text: bool):
        calls.append(command)
        # ffmpeg meldet hier laut Aufgabenstellung IMMER einen Fehlercode,
        # auch wenn die Geraeteliste vollstaendig auf stderr steht.
        return subprocess.CompletedProcess(
            command, returncode=1, stdout="", stderr=DSHOW_FIXTURE
        )

    monkeypatch.setattr("djset.capture.subprocess.run", fake_run)

    devices = list_input_devices()

    assert devices == ["Mikrofon (HyperX QuadCast 2)", "Mikrofon (HD Webcam eMeet C960)"]
    assert len(calls) == 1
    command = calls[0]
    assert command[1:] == [
        "-hide_banner",
        "-f", "dshow",
        "-list_devices", "true",
        "-i", "dummy",
    ]


def test_list_input_devices_without_ffmpeg_raises_render_tool_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("djset.render.stretch.shutil.which", lambda name: None)

    with pytest.raises(RenderToolMissing, match="ffmpeg"):
        list_input_devices()


# --- record: nur Kommandokonstruktion, nie ausgefuehrt ----------------------


def test_record_builds_the_expected_ffmpeg_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], capture_output: bool, text: bool):
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("djset.capture.subprocess.run", fake_run)
    target = tmp_path / "aufnahme.wav"

    record("Mikrofon (HyperX QuadCast 2)", target, seconds=5.0)

    assert len(calls) == 1
    command = calls[0]
    assert command[1:] == [
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-f", "dshow",
        "-i", "audio=Mikrofon (HyperX QuadCast 2)",
        "-t", "5.0",
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "pcm_f32le",
        str(target),
    ]


def test_record_creates_the_target_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], capture_output: bool, text: bool):
        return subprocess.CompletedProcess(command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("djset.capture.subprocess.run", fake_run)
    target = tmp_path / "verschachtelt" / "aufnahme.wav"

    record("Mikrofon", target, seconds=1.0)

    assert target.parent.is_dir()


def test_record_without_ffmpeg_raises_render_tool_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("djset.render.stretch.shutil.which", lambda name: None)

    with pytest.raises(RenderToolMissing, match="ffmpeg"):
        record("Mikrofon", tmp_path / "out.wav", seconds=1.0)


def test_record_a_failed_ffmpeg_raises_capture_failed_with_its_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], capture_output: bool, text: bool):
        return subprocess.CompletedProcess(
            command, returncode=1, stdout="", stderr="dshow: Geraet belegt"
        )

    monkeypatch.setattr("djset.capture.subprocess.run", fake_run)

    with pytest.raises(CaptureFailed, match="Geraet belegt"):
        record("Mikrofon", tmp_path / "out.wav", seconds=1.0)


# --- convert: echte, kurze ffmpeg-Aufrufe -----------------------------------


def test_convert_writes_44100hz_stereo(tmp_path: Path) -> None:
    source = _tone(tmp_path / "quelle.wav", samplerate=48_000, channels=1)
    target = tmp_path / "ziel.wav"

    convert(source, target)

    info = sf.info(target)
    assert info.samplerate == 44_100
    assert info.channels == 2


def test_convert_preserves_an_existing_stereo_image(tmp_path: Path) -> None:
    """Stereo bleibt erhalten - der Mono-Downmix im Renderer ist eine
    vorlaeufige, dokumentierte Einschraenkung dort, keine Lizenz, das
    Stereobild schon bei der Aufnahme wegzuwerfen."""
    source = _tone(tmp_path / "quelle.wav", samplerate=44_100, channels=2)
    target = tmp_path / "ziel.wav"

    convert(source, target)

    signal, sr = sf.read(target)
    assert sr == 44_100
    assert signal.ndim == 2 and signal.shape[1] == 2
    # linker und rechter Kanal sind im Quellmaterial unterschiedlich laut -
    # ein echter Downmix an dieser Stelle wuerde beide Kanaele angleichen.
    assert not np.allclose(signal[:, 0], signal[:, 1])


def test_a_converted_file_really_passes_the_watch_preflight_check(
    tmp_path: Path,
) -> None:
    """Der eigentliche Zweck von convert(): das Ergebnis muss scan() und
    check_sample_rate() durchlaufen, nicht nur die -ar-Flagge im Kommando
    tragen. Das wird hier gegen die echten watch.py-Funktionen geprueft,
    nicht angenommen."""
    watch = tmp_path / "watch"
    watch.mkdir()
    source = _tone(tmp_path / "48k-quelle.wav", samplerate=48_000, channels=2)

    convert(source, watch / "aufnahme.wav")

    files = scan(watch)
    assert len(files) == 1
    check_sample_rate(files)  # darf nicht werfen


def test_convert_without_ffmpeg_raises_render_tool_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("djset.render.stretch.shutil.which", lambda name: None)
    source = _tone(tmp_path / "quelle.wav", samplerate=44_100)

    with pytest.raises(RenderToolMissing, match="ffmpeg"):
        convert(source, tmp_path / "out.wav")


def test_convert_of_unreadable_input_raises_capture_failed_with_ffmpegs_stderr(
    tmp_path: Path,
) -> None:
    source = tmp_path / "kaputt.wav"
    source.write_text("das ist kein Audio, nur Text")

    with pytest.raises(CaptureFailed) as excinfo:
        convert(source, tmp_path / "out.wav")

    assert "Invalid data" in str(excinfo.value)


# --- import_folder -----------------------------------------------------------


def test_import_folder_converts_every_audio_file(tmp_path: Path) -> None:
    source_dir = tmp_path / "quelle"
    source_dir.mkdir()
    _tone(source_dir / "Erster Track.wav", samplerate=48_000)
    _tone(source_dir / "Zweiter Track.wav", samplerate=44_100)
    (source_dir / "notizen.txt").write_text("kein Audio")

    watch = tmp_path / "watch"
    written = import_folder(source_dir, watch)

    assert len(written) == 2
    for path in written:
        assert path.parent == watch
        info = sf.info(path)
        assert info.samplerate == 44_100
        assert info.channels == 2


def test_import_folder_derives_stable_slugified_names(tmp_path: Path) -> None:
    source_dir = tmp_path / "quelle"
    source_dir.mkdir()
    _tone(source_dir / "First Light Over Water.wav", samplerate=44_100)

    watch = tmp_path / "watch"
    written = import_folder(source_dir, watch)

    assert [path.name for path in written] == ["first-light-over-water.wav"]


def test_import_folder_keeps_colliding_slugs_apart(tmp_path: Path) -> None:
    source_dir = tmp_path / "quelle"
    source_dir.mkdir()
    _tone(source_dir / "Morning Glass.wav", samplerate=44_100)
    _tone(source_dir / "morning_glass.wav", samplerate=44_100)

    watch = tmp_path / "watch"
    written = import_folder(source_dir, watch)

    assert sorted(path.name for path in written) == [
        "morning-glass-2.wav",
        "morning-glass.wav",
    ]


def test_import_folder_output_passes_the_watch_preflight_check(tmp_path: Path) -> None:
    source_dir = tmp_path / "quelle"
    source_dir.mkdir()
    _tone(source_dir / "a.wav", samplerate=48_000)
    _tone(source_dir / "b.wav", samplerate=44_100)

    watch = tmp_path / "watch"
    import_folder(source_dir, watch)

    files = scan(watch)
    assert len(files) == 2
    check_sample_rate(files)


def test_import_folder_of_a_missing_folder_is_empty_not_an_error(
    tmp_path: Path,
) -> None:
    assert import_folder(tmp_path / "gibtsnicht", tmp_path / "watch") == []


def test_import_folder_creates_the_watch_directory(tmp_path: Path) -> None:
    source_dir = tmp_path / "quelle"
    source_dir.mkdir()
    _tone(source_dir / "a.wav", samplerate=44_100)

    watch = tmp_path / "watch"
    import_folder(source_dir, watch)

    assert watch.is_dir()

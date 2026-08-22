from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from djset.contracts import Profile
from djset.render.stretch import UnsupportedSampleRate
from djset.watch import (
    AudioFile,
    NotEnoughMaterial,
    check_enough,
    check_sample_rate,
    scan,
)


def _touch(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_bytes(b"")
    return path


def test_scan_finds_audio_and_ignores_the_rest(tmp_path: Path) -> None:
    _touch(tmp_path, "erster.wav")
    _touch(tmp_path, "zweiter.FLAC")
    _touch(tmp_path, "dritter.mp3")
    _touch(tmp_path, "notizen.txt")
    (tmp_path / "unterordner").mkdir()

    found = scan(tmp_path)

    assert [f.path.name for f in found] == ["dritter.mp3", "erster.wav", "zweiter.FLAC"]


def test_scan_derives_stable_ids_from_filenames(tmp_path: Path) -> None:
    _touch(tmp_path, "First Light Over Water.wav")

    assert scan(tmp_path) == [
        AudioFile(
            id="first-light-over-water",
            path=tmp_path / "First Light Over Water.wav",
        )
    ]


def test_scan_keeps_colliding_ids_apart(tmp_path: Path) -> None:
    _touch(tmp_path, "Morning Glass.wav")
    _touch(tmp_path, "morning glass.mp3")

    assert sorted(f.id for f in scan(tmp_path)) == ["morning-glass", "morning-glass-2"]


def test_scan_binds_colliding_ids_to_the_same_file_on_every_platform(
    tmp_path: Path,
) -> None:
    """Nicht nur DASS die IDs auseinandergehalten werden, sondern WELCHE zu
    welcher Datei gehoert.

    sorted() auf Path vergleicht zeichenweise: unter POSIX steht "Morning
    Glass.wav" damit vor "morning glass.mp3" (Grossbuchstaben zuerst), unter
    Windows nicht. Diese Reihenfolge speist den Kollisionszaehler, also
    vergaeben die beiden Plattformen morning-glass und morning-glass-2 an
    jeweils die andere Datei - und ein zwischen Maschinen getragener
    Run-Ordner haengt jede zwischengespeicherte Analyse an den falschen Track.
    """
    _touch(tmp_path, "Morning Glass.wav")
    _touch(tmp_path, "morning glass.mp3")

    binding = {f.path.name: f.id for f in scan(tmp_path)}

    # casefold macht beide Namen gleich, danach entscheidet der rohe Name:
    # ".mp3" vor ".wav". Das ist auf jeder Plattform dieselbe Antwort.
    assert binding == {
        "morning glass.mp3": "morning-glass",
        "Morning Glass.wav": "morning-glass-2",
    }


def test_scan_of_a_missing_folder_is_empty_not_an_error(tmp_path: Path) -> None:
    assert scan(tmp_path / "gibtsnicht") == []


def test_too_few_files_abort_before_any_agent_runs(
    tmp_path: Path, profile: Profile
) -> None:
    files = [AudioFile(id=f"t{i}", path=tmp_path / f"t{i}.wav") for i in range(3)]

    with pytest.raises(NotEnoughMaterial) as caught:
        check_enough(files, profile)

    message = str(caught.value)
    assert "3" in message
    assert str(profile.track_count.needed) in message


def test_exactly_enough_files_pass(tmp_path: Path, profile: Profile) -> None:
    needed = profile.track_count.needed
    files = [AudioFile(id=f"t{i}", path=tmp_path / f"t{i}.wav") for i in range(needed)]

    check_enough(files, profile)


def _tone(path: Path, samplerate: int, seconds: float = 0.5) -> Path:
    frames = int(seconds * samplerate)
    signal = np.sin(
        2 * np.pi * 220.0 * np.arange(frames) / samplerate
    ).astype(np.float32)
    sf.write(path, signal, samplerate)
    return path


def test_a_wrong_sample_rate_is_caught_before_any_agent(tmp_path: Path) -> None:
    """Spec Abschnitt 7: der Vorab-Check laeuft, bevor Tokens fliessen.

    Eine 48-kHz-Datei kam bisher durch Scan, Analyse (librosa resampelt
    anstandslos), Kuratierung, Anordnung, Entwurf und Kritik und riss den Lauf
    erst in Stufe 8 ab - mit Traceback, ohne qa-report.md und nachdem jedes
    Token bezahlt war.
    """
    _tone(tmp_path / "gut.wav", 44_100)
    _tone(tmp_path / "zu-hoch.wav", 48_000)

    with pytest.raises(UnsupportedSampleRate) as caught:
        check_sample_rate(scan(tmp_path))

    message = str(caught.value)
    # Der Dateiname muss drinstehen: sonst weiss niemand, welche Datei zu
    # wandeln ist.
    assert "zu-hoch.wav" in message
    assert "48000" in message
    assert "gut.wav" not in message


def test_the_expected_sample_rate_passes(tmp_path: Path) -> None:
    _tone(tmp_path / "eins.wav", 44_100)
    _tone(tmp_path / "zwei.wav", 44_100)

    check_sample_rate(scan(tmp_path))


def test_an_unreadable_file_is_not_a_sample_rate_finding(tmp_path: Path) -> None:
    """Eine kaputte Einzeldatei darf den Lauf nicht sprengen (Spec Abschnitt 7).

    Sie hat gar keine lesbare Rate - sie hier abzuweisen wuerde aus einem
    verkraftbaren Ausfall (der Analyst markiert sie unbrauchbar, der Lauf
    laeuft weiter) einen harten Abbruch machen.
    """
    _touch(tmp_path, "kaputt.wav")
    _tone(tmp_path / "heil.wav", 44_100)

    check_sample_rate(scan(tmp_path))

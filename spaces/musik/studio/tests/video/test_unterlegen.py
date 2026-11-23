"""Unterlegen: Bild bleibt unberuehrt, Laenge bleibt die des Videos."""

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from djset.render.stretch import SR, ffmpeg_path, ffprobe_path
from djset.video.laenge import VideoFehler, aus_video
from djset.video.unterlegen import unterlegen
from tests.fixtures.make_video import kurzes_video


def _musik(pfad: Path, sekunden: float, hz: float = 1000.0) -> Path:
    # 1000 Hz, damit sie sich vom 440-Hz-Sinus des Testvideos trennen laesst.
    t = np.arange(round(sekunden * SR)) / SR
    ton = (0.5 * np.sin(2 * np.pi * hz * t)).astype(np.float32)
    sf.write(str(pfad), np.stack([ton, ton], axis=1), SR)
    return pfad


def _sha(pfad: Path) -> str:
    return hashlib.sha256(pfad.read_bytes()).hexdigest()


def _spuren(pfad: Path) -> list[str]:
    lauf = subprocess.run(
        [ffprobe_path(), "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(pfad)],
        capture_output=True, encoding="utf-8", errors="replace", check=True,
    )
    return [s["codec_type"] for s in json.loads(lauf.stdout)["streams"]]


def _ton_lesen(pfad: Path, tmp_path: Path) -> np.ndarray:
    wav = tmp_path / "dekodiert.wav"
    subprocess.run(
        [ffmpeg_path(), "-v", "error", "-y", "-i", str(pfad), "-vn", "-ac", "1", "-ar", str(SR), str(wav)],
        check=True, capture_output=True,
    )
    signal, _ = sf.read(str(wav), dtype="float64")
    return signal


def _anteil(signal: np.ndarray, hz: float) -> float:
    """Betrag des Spektrums bei `hz` (Mittel der Nachbarbins), relativ zum Maximum."""
    spektrum = np.abs(np.fft.rfft(signal * np.hanning(len(signal))))
    bin_ = round(hz * len(signal) / SR)
    return float(spektrum[bin_ - 2: bin_ + 3].max() / spektrum.max())


def _dominant(signal: np.ndarray) -> float:
    spektrum = np.abs(np.fft.rfft(signal * np.hanning(len(signal))))
    return float(np.argmax(spektrum) * SR / len(signal))


def test_unterlegen_behaelt_video_und_laenge(tmp_path: Path) -> None:
    video = kurzes_video(tmp_path, 2.5)
    musik = _musik(tmp_path / "m.wav", 2.5)
    vorher = _sha(video)
    ziel = tmp_path / "aus" / "mit_musik.mp4"
    unterlegen(video, musik, ziel)
    assert sorted(_spuren(ziel)) == ["audio", "video"]
    assert abs(aus_video(ziel).sekunden - 2.5) <= 1 / 25
    assert _sha(video) == vorher
    assert [p.name for p in ziel.parent.iterdir()] == ["mit_musik.mp4"]
    # Ohne Ersetzen bleibt der Originalton (440 Hz) hoerbar neben der Musik.
    ton = _ton_lesen(ziel, tmp_path)
    assert _anteil(ton, 440.0) > 0.1
    assert _anteil(ton, 1000.0) > 0.1


def test_unterlegen_ohne_originalton(tmp_path: Path) -> None:
    video = kurzes_video(tmp_path, 2.5, mit_ton=False)
    musik = _musik(tmp_path / "m.wav", 2.5)
    ziel = tmp_path / "mit_musik.mp4"
    unterlegen(video, musik, ziel)
    assert sorted(_spuren(ziel)) == ["audio", "video"]
    assert abs(_dominant(_ton_lesen(ziel, tmp_path)) - 1000.0) < 20.0


def test_original_ersetzen(tmp_path: Path) -> None:
    video = kurzes_video(tmp_path, 2.5)
    musik = _musik(tmp_path / "m.wav", 2.5)
    ziel = tmp_path / "ersetzt.mp4"
    unterlegen(video, musik, ziel, original_ersetzen=True)
    ton = _ton_lesen(ziel, tmp_path)
    # AAC verschiebt die Phase, daher zaehlt die Frequenz, nicht die Korrelation.
    assert abs(_dominant(ton) - 1000.0) < 20.0
    assert _anteil(ton, 440.0) < 0.02


def test_kurze_musik_laesst_das_video_nicht_kuerzen(tmp_path: Path) -> None:
    video = kurzes_video(tmp_path, 2.5)
    musik = _musik(tmp_path / "m.wav", 1.0)
    ziel = tmp_path / "lang.mp4"
    unterlegen(video, musik, ziel)
    assert abs(aus_video(ziel).sekunden - 2.5) <= 1 / 25


def test_fehler_sind_videofehler_ohne_halbe_datei(tmp_path: Path) -> None:
    video = kurzes_video(tmp_path, 1.0)
    ziel = tmp_path / "x.mp4"
    with pytest.raises(VideoFehler, match="fehlt.wav"):
        unterlegen(video, tmp_path / "fehlt.wav", ziel)
    with pytest.raises(VideoFehler, match="fehlt.mp4"):
        unterlegen(tmp_path / "fehlt.mp4", _musik(tmp_path / "m.wav", 1.0), ziel)
    kaputt = tmp_path / "kaputt.wav"
    kaputt.write_bytes(b"kein audio")
    with pytest.raises(VideoFehler, match="kaputt.wav"):
        unterlegen(video, kaputt, ziel)
    assert not ziel.exists()
    assert not list(tmp_path.glob("*.teil*"))


def test_schreibfehler_am_ziel_ist_videofehler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = kurzes_video(tmp_path, 1.0)
    musik = _musik(tmp_path / "m.wav", 1.0)
    ziel = tmp_path / "x.mp4"

    def _verweigert(quelle: object, ziel_: object) -> None:
        raise PermissionError("Zugriff verweigert")

    monkeypatch.setattr("djset.video.unterlegen.os.replace", _verweigert)
    with pytest.raises(VideoFehler, match="x.mp4"):
        unterlegen(video, musik, ziel)
    assert not ziel.exists()
    assert not list(tmp_path.glob("*.teil*"))


def test_zielordner_nicht_anlegbar_ist_videofehler(tmp_path: Path) -> None:
    video = kurzes_video(tmp_path, 1.0)
    musik = _musik(tmp_path / "m.wav", 1.0)
    datei = tmp_path / "ist_eine_datei"
    datei.write_bytes(b"x")
    ziel = datei / "unter" / "x.mp4"
    with pytest.raises(VideoFehler, match="x.mp4"):
        unterlegen(video, musik, ziel)
    assert not list(tmp_path.glob("*.teil*"))

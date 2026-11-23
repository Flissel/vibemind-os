"""Pruefung der Videomusik: saubere Stuecke ohne Befund, Fehler mit Art."""

from pathlib import Path

import numpy as np
import soundfile as sf

from djset.render.stretch import SR
from djset.video.klang import videomusik_rendern
from djset.video.pruefung import VideoBefund, videomusik_pruefen
from djset.video.zuschnitt import Zuschnitt


SAMPLES = round(7.3 * SR)


def _ton(pfad: Path) -> Path:
    # Ein gleichmaessiger Sinus erreicht -14 LUFS, ohne am Peak-Deckel zu haengen.
    t = np.arange(30 * SR) / SR
    signal = (0.3 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    sf.write(str(pfad), np.stack([signal, signal], axis=1), SR)
    return pfad


def _zuschnitt(schluss_s: float = 8.3) -> Zuschnitt:
    return Zuschnitt(start_s=2.0, schluss_s=schluss_s, ende_s=9.3, takte=3)


def test_ein_sauberes_stueck_hat_keine_befunde(tmp_path: Path) -> None:
    quelle = _ton(tmp_path / "q.wav")
    ziel = tmp_path / "z.wav"
    videomusik_rendern(quelle, _zuschnitt(), SAMPLES, ziel)
    assert videomusik_pruefen(ziel, SAMPLES, -14.0) == []


def test_zu_kurz_und_hart_abgeschnitten_sind_befunde(tmp_path: Path) -> None:
    quelle = _ton(tmp_path / "q.wav")
    kurz = SAMPLES - round(0.2 * SR)
    ziel = tmp_path / "z.wav"
    # Schluss weit hinter dem Ende: kein Ausklang, der Ton wird mittendrin gekappt.
    videomusik_rendern(quelle, _zuschnitt(schluss_s=60.0), kurz, ziel)
    arten = [b.art for b in videomusik_pruefen(ziel, SAMPLES, -14.0)]
    assert arten == ["laenge", "schluss"]


def test_nicht_lesbare_datei_ist_videofehler(tmp_path: Path) -> None:
    import pytest

    from djset.video.laenge import VideoFehler

    kaputt = tmp_path / "kaputt.wav"
    kaputt.write_bytes(b"kein audio")
    with pytest.raises(VideoFehler, match="kaputt.wav"):
        videomusik_pruefen(kaputt, SAMPLES, -14.0)
    with pytest.raises(VideoFehler, match="fehlt.wav"):
        videomusik_pruefen(tmp_path / "fehlt.wav", SAMPLES, -14.0)


def test_luecke_mitten_im_stueck(tmp_path: Path) -> None:
    quelle = _ton(tmp_path / "q.wav")
    ziel = tmp_path / "z.wav"
    videomusik_rendern(quelle, _zuschnitt(), SAMPLES, ziel)
    signal, _ = sf.read(str(ziel), dtype="float32")
    signal[SR * 3: SR * 3 + SR] = 0.0
    sf.write(str(ziel), signal, SR, subtype="FLOAT")
    arten = [b.art for b in videomusik_pruefen(ziel, SAMPLES, -14.0)]
    assert "luecke" in arten
    assert isinstance(videomusik_pruefen(ziel, SAMPLES, -14.0)[0], VideoBefund)


def _sinus(pfad: Path, amplitude: float, sr: int = SR) -> Path:
    t = np.arange(round(SAMPLES / SR * sr)) / sr
    signal = (amplitude * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    sf.write(str(pfad), np.stack([signal, signal], axis=1), sr, subtype="FLOAT")
    return pfad


def test_zu_leises_stueck_ist_pegelbefund(tmp_path: Path) -> None:
    # Amplitude 0.05 liegt bei rund -25 LUFS, weit unter dem Ziel von -14.
    datei = _sinus(tmp_path / "leise.wav", 0.05)
    befunde = videomusik_pruefen(datei, SAMPLES, -14.0)
    assert "pegel" in [b.art for b in befunde]
    assert "spitze" not in [b.art for b in befunde]


def test_zu_lautes_stueck_ist_spitzenbefund(tmp_path: Path) -> None:
    datei = _sinus(tmp_path / "laut.wav", 0.99)
    arten = [b.art for b in videomusik_pruefen(datei, SAMPLES, -14.0)]
    assert "spitze" in arten


def test_falsche_samplerate_ist_videofehler(tmp_path: Path) -> None:
    import pytest

    from djset.video.laenge import VideoFehler

    datei = _sinus(tmp_path / "48k.wav", 0.3, sr=48_000)
    with pytest.raises(VideoFehler, match="48k.wav"):
        videomusik_pruefen(datei, SAMPLES, -14.0)

def test_luecke_nach_dem_schluss_ist_kein_befund_davor_schon(tmp_path: Path) -> None:
    quelle = _ton(tmp_path / "q.wav")
    ziel = tmp_path / "z.wav"
    videomusik_rendern(quelle, _zuschnitt(), SAMPLES, ziel)
    signal, _ = sf.read(str(ziel), dtype="float32")
    signal[SR * 3: SR * 3 + SR] = 0.0  # Stille ab 3,0 s im Stueck
    sf.write(str(ziel), signal, SR, subtype="FLOAT")

    spaet = videomusik_pruefen(ziel, SAMPLES, -14.0, schluss_s=2.5)
    frueh = videomusik_pruefen(ziel, SAMPLES, -14.0, schluss_s=6.3)

    assert "luecke" not in [b.art for b in spaet]
    assert "luecke" in [b.art for b in frueh]
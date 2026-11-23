"""Klang der Videomusik: exakte Laenge, Ausklang, Einblendung, Zielpegel."""

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from djset.dsp.analyze import measure_loudness
from djset.qa import TRUE_PEAK_CEILING_DB
from djset.render.stretch import SR
from djset.video.klang import EINBLENDE_S, Pegel, videomusik_rendern
from djset.video.laenge import VideoFehler
from djset.video.zuschnitt import Zuschnitt
from tests.fixtures.render import pulsed_tone

SAMPLES = round(7.3 * SR)


def _zuschnitt(dauer_s: float = 7.3, ausklang_s: float = 1.0, start_s: float = 2.0) -> Zuschnitt:
    return Zuschnitt(
        start_s=start_s,
        schluss_s=start_s + dauer_s - ausklang_s,
        ende_s=start_s + dauer_s,
        takte=3,
    )


def _ton(path: Path, sekunden: float = 30.0, amplitude: float = 0.3) -> Path:
    t = np.arange(int(sekunden * SR)) / SR
    signal = (amplitude * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    sf.write(path, np.stack([signal, signal], axis=1), SR)
    return path


def _dbfs(x: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0
    return 20.0 * np.log10(rms) if rms > 0 else -200.0


def test_genau_so_viele_proben(tmp_path: Path) -> None:
    quelle = pulsed_tone(tmp_path / "q.wav", 30.0)
    ziel = tmp_path / "z.wav"
    videomusik_rendern(quelle, _zuschnitt(), SAMPLES, ziel)
    info = sf.info(str(ziel))
    assert info.frames == SAMPLES
    assert info.channels == 2
    assert info.subtype == "FLOAT"


def test_schluss_klingt_aus(tmp_path: Path) -> None:
    quelle = _ton(tmp_path / "q.wav")
    ziel = tmp_path / "z.wav"
    zs = _zuschnitt()
    videomusik_rendern(quelle, zs, SAMPLES, ziel)
    signal, _ = sf.read(str(ziel))
    ende = signal[-round(0.01 * SR):]
    assert _dbfs(ende) < -60.0
    schluss = round((zs.schluss_s - zs.start_s) * SR)
    nach_schluss = signal[schluss + round(0.05 * SR): schluss + round(0.10 * SR)]
    vor_ende = signal[-round(0.05 * SR):]
    assert _dbfs(nach_schluss) > _dbfs(vor_ende)


def test_anfang_ohne_knacken(tmp_path: Path) -> None:
    quelle = _ton(tmp_path / "q.wav")
    ziel = tmp_path / "z.wav"
    videomusik_rendern(quelle, _zuschnitt(), SAMPLES, ziel)
    signal, _ = sf.read(str(ziel))
    assert signal[0, 0] == 0.0
    nach = signal[round(EINBLENDE_S * SR): round(EINBLENDE_S * SR) + 2000]
    mitte = signal[round(1.0 * SR): round(1.0 * SR) + 2000]
    assert np.max(np.abs(nach)) == pytest.approx(np.max(np.abs(mitte)), rel=0.02)


def test_lautheit_trifft_das_ziel(tmp_path: Path) -> None:
    # Gleichmaessiger Ton: ein Impulszug waere so spitz, dass der True-Peak-
    # Deckel schon vor -14 LUFS greift (das prueft der Deckel-Test eigens).
    quelle = _ton(tmp_path / "q.wav")
    ziel = tmp_path / "z.wav"
    pegel = videomusik_rendern(quelle, _zuschnitt(), SAMPLES, ziel)
    assert measure_loudness(ziel, peak_per_channel=True).lufs_integrated == pytest.approx(-14.0, abs=0.5)
    assert pegel.lufs == pytest.approx(-14.0, abs=0.5)
    pegel2 = videomusik_rendern(quelle, _zuschnitt(), SAMPLES, ziel, lufs_ziel=-20.0)
    assert measure_loudness(ziel, peak_per_channel=True).lufs_integrated == pytest.approx(-20.0, abs=0.5)
    assert isinstance(pegel2, Pegel)


def test_true_peak_wird_gedeckelt(tmp_path: Path) -> None:
    quelle = _ton(tmp_path / "q.wav", amplitude=0.05)
    signal, _ = sf.read(str(quelle))
    mitte = round(5.0 * SR)
    signal[mitte: mitte + 20, :] = 0.9  # einzelne Spitze mitten im Stueck
    sf.write(quelle, signal.astype(np.float32), SR)
    ziel = tmp_path / "z.wav"
    pegel = videomusik_rendern(quelle, _zuschnitt(), SAMPLES, ziel, lufs_ziel=-6.0)
    assert pegel.gedeckelt is True
    gemessen = measure_loudness(ziel, peak_per_channel=True).true_peak
    assert gemessen <= TRUE_PEAK_CEILING_DB + 0.1
    assert pegel.true_peak == pytest.approx(gemessen, abs=0.05)


def test_fehlende_proben_werden_mit_stille_aufgefuellt(tmp_path: Path) -> None:
    quelle = _ton(tmp_path / "q.wav", sekunden=4.0)
    ziel = tmp_path / "z.wav"
    videomusik_rendern(quelle, _zuschnitt(start_s=1.0), SAMPLES, ziel)
    assert sf.info(str(ziel)).frames == SAMPLES


def test_falsche_samplerate_wird_abgelehnt(tmp_path: Path) -> None:
    quelle = tmp_path / "q.wav"
    sf.write(quelle, np.zeros(48000 * 10, dtype=np.float32) + 0.1, 48000)
    with pytest.raises(VideoFehler, match="q.wav"):
        videomusik_rendern(quelle, _zuschnitt(), SAMPLES, tmp_path / "z.wav")


def test_stille_quelle_wird_abgelehnt(tmp_path: Path) -> None:
    quelle = tmp_path / "stille.wav"
    sf.write(quelle, np.zeros(SR * 30, dtype=np.float32), SR)
    with pytest.raises(VideoFehler, match="stille.wav"):
        videomusik_rendern(quelle, _zuschnitt(), SAMPLES, tmp_path / "z.wav")

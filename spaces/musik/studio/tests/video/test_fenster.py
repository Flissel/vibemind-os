"""Fensterpegel: ein leises Intro mit lautem Schlag taugt nicht, Dauerton schon."""

from pathlib import Path

import numpy as np
import soundfile as sf

from djset.render.stretch import SR
from djset.video.fenster import FensterPegel, fenster_messen, tauglich

ZIEL_S = 29.5


def _db(db: float) -> float:
    return 10.0 ** (db / 20.0)


def _quelle(pfad: Path) -> Path:
    """30 s Sinus bei -50 dBFS mit lautem Klick bei 29 s, danach 40 s bei -20 dBFS."""
    t = np.arange(70 * SR) / SR
    signal = np.sin(2 * np.pi * 220.0 * t)
    signal[: 30 * SR] *= _db(-50)
    signal[30 * SR:] *= _db(-20)
    signal[29 * SR: 29 * SR + 20] = 0.9
    sf.write(str(pfad), signal.astype(np.float32), SR, subtype="FLOAT")
    return pfad


def test_leises_intro_mit_schlag_taugt_nicht_dauerton_schon(tmp_path: Path) -> None:
    quelle = _quelle(tmp_path / "q.wav")
    fenster = fenster_messen(quelle, [0.0, 35.0], ZIEL_S, 29.0)
    leise, laut = fenster
    assert not tauglich(leise, -14.0)
    assert tauglich(laut, -14.0)
    assert leise.erreichbar_lufs < -25.0
    assert laut.erreichbar_lufs > -14.0
    assert [f.start_s for f in fenster] == [0.0, 35.0]


def test_stille_in_der_fenstermitte_ist_eine_luecke(tmp_path: Path) -> None:
    t = np.arange(40 * SR) / SR
    signal = 0.1 * np.sin(2 * np.pi * 220.0 * t)
    signal[15 * SR: 16 * SR] = 0.0
    quelle = tmp_path / "luecke.wav"
    sf.write(str(quelle), signal.astype(np.float32), SR, subtype="FLOAT")
    mit, ohne = fenster_messen(quelle, [0.0, 20.0], 20.0, 18.0)
    assert mit.luecke and not tauglich(mit, -14.0)
    assert not ohne.luecke


def test_stille_nach_dem_schluss_ist_keine_luecke(tmp_path: Path) -> None:
    t = np.arange(40 * SR) / SR
    signal = 0.1 * np.sin(2 * np.pi * 220.0 * t)
    signal[15 * SR: 16 * SR] = 0.0
    quelle = tmp_path / "spaet.wav"
    sf.write(str(quelle), signal.astype(np.float32), SR, subtype="FLOAT")
    (f,) = fenster_messen(quelle, [0.0], 20.0, 14.0)
    assert not f.luecke


def test_stilles_fenster_ist_nie_tauglich(tmp_path: Path) -> None:
    quelle = tmp_path / "still.wav"
    sf.write(str(quelle), np.zeros(20 * SR, dtype=np.float32), SR, subtype="FLOAT")
    (f,) = fenster_messen(quelle, [0.0], 10.0, 8.0)
    assert isinstance(f, FensterPegel)
    assert not tauglich(f, -14.0)


def test_fenstermessung_stimmt_mit_der_dateimessung_ueberein(tmp_path: Path) -> None:
    """Die schnelle Fenstermessung darf nicht von `measure_loudness` abweichen."""
    from djset.dsp.analyze import measure_loudness

    rng = np.random.default_rng(7)
    t = np.arange(40 * SR) / SR
    links = 0.2 * np.sin(2 * np.pi * 220.0 * t) + 0.02 * rng.standard_normal(len(t))
    rechts = 0.1 * np.sin(2 * np.pi * 330.0 * t)
    links[20 * SR: 20 * SR + 10] = 0.8  # eine Spitze mitten im Fenster
    quelle = tmp_path / "q.wav"
    sf.write(str(quelle), np.stack([links, rechts], axis=1).astype(np.float32), SR, subtype="FLOAT")
    start, laenge = 7.3, 20.0
    ausschnitt = tmp_path / "a.wav"
    daten, _ = sf.read(str(quelle), dtype="float32")
    sf.write(str(ausschnitt), daten[round(start * SR): round((start + laenge) * SR)], SR, subtype="FLOAT")

    (f,) = fenster_messen(quelle, [start], laenge, 19.0)
    soll = measure_loudness(ausschnitt, peak_per_channel=True)

    assert abs(f.lufs - soll.lufs_integrated) < 0.05
    assert abs(f.true_peak - soll.true_peak) < 0.3  # Blockrand: leicht vorsichtig, nie zu optimistisch
    assert f.true_peak >= soll.true_peak - 0.05

"""Fensterpegel: ein leises Intro mit lautem Schlag taugt nicht, Dauerton schon."""

from pathlib import Path

import numpy as np
import soundfile as sf

from djset.render.stretch import SR
from djset.qa import TRUE_PEAK_CEILING_DB
from djset.video.fenster import FensterPegel, fenster_messen, tauglich
from djset.video.klang import huelle

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
    """Die schnelle Fenstermessung darf nicht von `measure_loudness` abweichen.

    Leises, mittleres und lautes Stueck im Fenster, damit beide Gates greifen;
    verglichen wird mit dem Ausschnitt samt derselben Huelle.
    """
    from djset.dsp.analyze import measure_loudness

    rng = np.random.default_rng(7)
    t = np.arange(40 * SR) / SR
    links = np.sin(2 * np.pi * 220.0 * t) + 0.1 * rng.standard_normal(len(t))
    rechts = 0.5 * np.sin(2 * np.pi * 330.0 * t)
    pegel = np.where(t < 15.0, 0.003, np.where(t < 25.0, 0.05, 0.2))
    links, rechts = links * pegel, rechts * pegel
    links[20 * SR: 20 * SR + 10] = 0.8  # eine Spitze mitten im Fenster
    quelle = tmp_path / "q.wav"
    sf.write(str(quelle), np.stack([links, rechts], axis=1).astype(np.float32), SR, subtype="FLOAT")
    start, laenge, schluss = 7.3, 20.0, 19.0
    daten, _ = sf.read(str(quelle), dtype="float32")
    ausschnitt = daten[round(start * SR): round((start + laenge) * SR)].astype(np.float64)
    ausschnitt *= huelle(len(ausschnitt), SR, schluss)[:, None]
    datei = tmp_path / "a.wav"
    sf.write(str(datei), ausschnitt.astype(np.float32), SR, subtype="FLOAT")

    (f,) = fenster_messen(quelle, [start], laenge, schluss)
    soll = measure_loudness(datei, peak_per_channel=True)

    assert abs(f.lufs - soll.lufs_integrated) < 0.05
    assert f.true_peak >= soll.true_peak - 0.05  # Blockrand: leicht vorsichtig, nie zu optimistisch
    assert f.true_peak < soll.true_peak + 0.5


def test_schaetzung_stimmt_mit_dem_gerenderten_stueck_ueberein(tmp_path: Path) -> None:
    """Ein lauter letzter Takt: der Ausklang senkt die Lautheit, die Schaetzung muss das sehen."""
    from djset.dsp.analyze import measure_loudness
    from djset.video.klang import videomusik_rendern
    from djset.video.zuschnitt import Zuschnitt

    t = np.arange(40 * SR) / SR
    signal = 0.1 * np.sin(2 * np.pi * 220.0 * t)
    signal[10 * SR: 10 * SR + 20] = 0.9  # Schlag auf dem letzten Taktanfang (2 s + 8 s)
    signal[10 * SR + 20: 12 * SR] += 0.3 * np.sin(2 * np.pi * 110.0 * t[10 * SR + 20: 12 * SR])
    quelle = tmp_path / "q.wav"
    sf.write(str(quelle), np.stack([signal, signal], axis=1).astype(np.float32), SR, subtype="FLOAT")
    zuschnitt = Zuschnitt(start_s=2.0, schluss_s=10.0, ende_s=12.0, takte=4)
    ziel = tmp_path / "z.wav"
    pegel = videomusik_rendern(quelle, zuschnitt, 10 * SR, ziel, -14.0)
    assert pegel.gedeckelt  # der Schlag haelt den Pegel am Deckel

    gemessen = measure_loudness(ziel, peak_per_channel=True)
    erreichbar = gemessen.lufs_integrated + (TRUE_PEAK_CEILING_DB - gemessen.true_peak)
    (f,) = fenster_messen(quelle, [2.0], 10.0, 8.0)

    assert abs(f.erreichbar_lufs - erreichbar) < 0.1

def test_stille_bis_zum_schluss_und_am_anfang_ist_eine_luecke(tmp_path: Path) -> None:
    t = np.arange(40 * SR) / SR
    quelle = tmp_path / "rand.wav"
    signal = 0.1 * np.sin(2 * np.pi * 220.0 * t)
    signal[10 * SR: 20 * SR] = 0.0  # Musik bis 10 s, Stille bis zum Schluss bei 18 s und darueber hinaus
    signal[20 * SR: 22 * SR] = 0.0
    sf.write(str(quelle), signal.astype(np.float32), SR, subtype="FLOAT")
    (ende,) = fenster_messen(quelle, [0.0], 20.0, 14.0)
    assert ende.luecke  # Stille von 10 s bis zum Schluss bei 14 s

    anfang = 0.1 * np.sin(2 * np.pi * 220.0 * t)
    anfang[: 3 * SR] = 0.0
    quelle2 = tmp_path / "anfang.wav"
    sf.write(str(quelle2), anfang.astype(np.float32), SR, subtype="FLOAT")
    (f,) = fenster_messen(quelle2, [0.0], 20.0, 18.0)
    assert f.luecke


def test_stille_nur_im_ausklang_bleibt_tauglich(tmp_path: Path) -> None:
    t = np.arange(40 * SR) / SR
    signal = 0.1 * np.sin(2 * np.pi * 220.0 * t)
    signal[15 * SR:] = 0.0  # Stille ab dem Schluss bei 15 s
    quelle = tmp_path / "ausklang.wav"
    sf.write(str(quelle), signal.astype(np.float32), SR, subtype="FLOAT")
    (f,) = fenster_messen(quelle, [0.0], 20.0, 15.0)
    assert not f.luecke

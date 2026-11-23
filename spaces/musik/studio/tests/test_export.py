from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from djset.export import to_mp3
from djset.render.stretch import RenderFailed


def _stereo_wav(path: Path, dauer_s: float = 2.0, sr: int = 44_100) -> Path:
    t = np.arange(int(dauer_s * sr)) / sr
    ton = 0.3 * np.sin(2 * np.pi * 440.0 * t)
    sf.write(path, np.column_stack([ton, ton]).astype("float32"), sr, subtype="FLOAT")
    return path


def test_to_mp3_behaelt_kanaele_rate_und_dauer(tmp_path: Path) -> None:
    quelle = _stereo_wav(tmp_path / "set.wav")
    ziel = tmp_path / "set.mp3"

    to_mp3(quelle, ziel)

    info = sf.info(ziel)
    assert info.channels == 2
    assert info.samplerate == 44_100
    assert info.duration == pytest.approx(2.0, abs=0.1)
    # Kein Zwischenstand bleibt neben dem Ziel liegen.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["set.mp3", "set.wav"]


def test_to_mp3_ohne_quelle_ist_render_failed(tmp_path: Path) -> None:
    ziel = tmp_path / "set.mp3"

    with pytest.raises(RenderFailed):
        to_mp3(tmp_path / "gibt-es-nicht.wav", ziel)

    assert not ziel.exists()
    assert list(tmp_path.iterdir()) == []


def test_to_mp3_bei_ffmpeg_fehler_bleibt_keine_halbe_datei(tmp_path: Path) -> None:
    quelle = tmp_path / "kaputt.wav"
    quelle.write_bytes(b"das ist kein audio")
    ziel = tmp_path / "set.mp3"

    with pytest.raises(RenderFailed):
        to_mp3(quelle, ziel)

    assert not ziel.exists()
    assert [p.name for p in tmp_path.iterdir()] == ["kaputt.wav"]

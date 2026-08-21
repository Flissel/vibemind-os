from pathlib import Path

from djset.camelot import parse
from djset.dsp.analyze import detect_key
from tests.fixtures.make_audio import chord_tone


def test_c_major_triad_lands_on_camelot_eight(tmp_path: Path):
    """C-Dur ist 8B, A-Moll ist 8A. Dur/Moll-Verwechslung ist tolerierbar,
    eine falsche Zahl waere es nicht - sie steuert die Nachbarschaft."""
    audio = chord_tone(tmp_path / "cmaj.wav", semitones=[0, 4, 7])
    key = detect_key(audio)
    number, _letter = parse(key.camelot)
    assert number == 8


def test_g_major_triad_lands_on_camelot_nine(tmp_path: Path):
    audio = chord_tone(tmp_path / "gmaj.wav", semitones=[7, 11, 14])
    key = detect_key(audio)
    number, _letter = parse(key.camelot)
    assert number == 9


def test_confidence_is_within_bounds(tmp_path: Path):
    audio = chord_tone(tmp_path / "cmaj.wav", semitones=[0, 4, 7])
    key = detect_key(audio)
    assert 0.0 <= key.confidence <= 1.0

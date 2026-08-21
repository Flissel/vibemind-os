import json
from pathlib import Path

import numpy as np
import soundfile as sf

from djset.agents.analyst import SNAP_CONFIDENCE_PENALTY, analyse_track
from djset.agents.base import Budget
from djset.contracts import Profile, TrackAnalysis, UnreadableTrack
from tests.fixtures.fake_client import FakeClient
from tests.fixtures.make_audio import click_track

# click_track nimmt bars, keine Sekunden entgegen. 14 Takte bei 174 BPM und
# 7 Takte bei 87 BPM ergeben beide rund 20 s Material - genug fuer eine
# stabile Tempo- und Energiemessung.
BARS_AT_174 = 14
BARS_AT_87 = 7

ANSWER = {
    "usable": True,
    "notes": "Sauberes Raster, passt ins Genre.",
    "energy_level": 0.62,
    "remeasure_with_bpm": None,
}


def _budget() -> Budget:
    return Budget(max_tokens=50_000, max_seconds=60.0)


def test_a_measured_track_gets_a_verdict(tmp_path: Path, profile: Profile) -> None:
    path = click_track(tmp_path / "t.wav", bpm=174.0, bars=BARS_AT_174)
    client = FakeClient([json.dumps(ANSWER)])

    result = analyse_track(client, path, "t", profile, _budget())

    assert isinstance(result, TrackAnalysis)
    assert result.id == "t"
    assert result.verdict.usable is True
    assert result.verdict.energy_level == 0.62
    assert client.calls == 1


def test_a_halftime_reading_is_snapped_before_the_model_sees_it(
    tmp_path: Path, profile: Profile
) -> None:
    """87 statt 174: die Korrektur ist Arithmetik gegen den Korridor, kein Urteil."""
    path = click_track(tmp_path / "t.wav", bpm=87.0, bars=BARS_AT_87)
    client = FakeClient([json.dumps(ANSWER)])

    result = analyse_track(client, path, "t", profile, _budget())

    assert isinstance(result, TrackAnalysis)
    assert profile.bpm.min <= result.bpm.value <= profile.bpm.max
    assert "174" in client.prompts[0]


def test_snapping_lowers_the_confidence(tmp_path: Path, profile: Profile) -> None:
    halftime = click_track(tmp_path / "t.wav", bpm=87.0, bars=BARS_AT_87)
    plain = click_track(tmp_path / "u.wav", bpm=174.0, bars=BARS_AT_174)

    snapped = analyse_track(
        FakeClient([json.dumps(ANSWER)]), halftime, "t", profile, _budget()
    )
    unsnapped = analyse_track(
        FakeClient([json.dumps(ANSWER)]), plain, "u", profile, _budget()
    )

    assert isinstance(snapped, TrackAnalysis)
    assert isinstance(unsnapped, TrackAnalysis)
    assert snapped.bpm.confidence < unsnapped.bpm.confidence


def test_the_model_may_ask_for_one_remeasure(tmp_path: Path, profile: Profile) -> None:
    path = click_track(tmp_path / "t.wav", bpm=174.0, bars=BARS_AT_174)
    asks_again = json.dumps(dict(ANSWER, remeasure_with_bpm=174.0))
    client = FakeClient([asks_again, json.dumps(ANSWER)])

    result = analyse_track(client, path, "t", profile, _budget())

    assert isinstance(result, TrackAnalysis)
    assert client.calls == 2


def test_the_remeasure_loop_is_bounded(tmp_path: Path, profile: Profile) -> None:
    """Ein Modell, das immer weiter messen will, darf den Lauf nicht aufhalten."""
    path = click_track(tmp_path / "t.wav", bpm=174.0, bars=BARS_AT_174)
    asks_again = json.dumps(dict(ANSWER, remeasure_with_bpm=174.0))
    client = FakeClient([asks_again, asks_again])

    result = analyse_track(client, path, "t", profile, _budget(), max_rounds=2)

    assert isinstance(result, TrackAnalysis)
    assert client.calls == 2


def test_an_unreadable_file_does_not_stop_the_run(
    tmp_path: Path, profile: Profile
) -> None:
    broken = tmp_path / "kaputt.wav"
    broken.write_bytes(b"das ist kein wav")
    client = FakeClient([])

    result = analyse_track(client, broken, "kaputt", profile, _budget())

    assert isinstance(result, UnreadableTrack)
    assert result.id == "kaputt"
    assert result.error
    assert client.calls == 0


def test_a_silent_file_is_unreadable_not_an_exception(
    tmp_path: Path, profile: Profile
) -> None:
    """Digitale Stille bringt librosa zum Warnen - das darf keinen Lauf reissen."""
    silent = tmp_path / "still.wav"
    sf.write(silent, np.zeros(44_100 * 5, dtype=np.float32), 44_100)
    client = FakeClient([])

    result = analyse_track(client, silent, "still", profile, _budget())

    assert isinstance(result, UnreadableTrack)
    assert client.calls == 0

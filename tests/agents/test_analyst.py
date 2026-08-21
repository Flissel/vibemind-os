import json
from pathlib import Path

import numpy as np
import soundfile as sf

from djset.agents.analyst import SNAP_CONFIDENCE_PENALTY, analyse_track
from djset.agents.base import Budget
from djset.contracts import Profile, TrackAnalysis, UnreadableTrack
from tests.fixtures.fake_client import FakeClient
from tests.fixtures.make_audio import click_track, click_track_with_weak_offbeats

# click_track nimmt bars, keine Sekunden entgegen. 14 Takte bei 174 BPM ergeben
# rund 20 s Material - genug fuer eine stabile Tempo- und Energiemessung.
BARS_AT_174 = 14

# Schwaeche der Offbeats fuer die Halftime-Simulation: identisch zum
# Regressionstest in tests/dsp/test_bpm.py, der festhaelt, dass genau dieses
# Material ohne Hinweis als 87 statt 174 gelesen wird. Ein einfacher
# click_track(bpm=87.0, ...) waere KEIN Halftime-Material: er enthaelt gar
# kein Signal bei 174 BPM, ein erneuter Messversuch mit Tempo-Hinweis koennte
# dort also nichts wiederfinden. click_track_with_weak_offbeats simuliert den
# echten Fall - die schnelleren Onsets sind da, nur leise -, und ein Hinweis
# kann sie wieder aufgreifen. Gemessen mit einem Wegwerfskript (siehe
# Fix-Report): ohne Hinweis liest dieses Material ~87 BPM, mit dem
# gesnappten Hinweis erneut gemessen wieder ~174 BPM samt passender
# Taktlaenge.
OFFBEAT_DB = -26.0

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
    path = click_track_with_weak_offbeats(
        tmp_path / "t.wav", bpm=174.0, bars=BARS_AT_174, offbeat_db=OFFBEAT_DB
    )
    client = FakeClient([json.dumps(ANSWER)])

    result = analyse_track(client, path, "t", profile, _budget())

    assert isinstance(result, TrackAnalysis)
    assert profile.bpm.min <= result.bpm.value <= profile.bpm.max
    assert "174" in client.prompts[0]


def test_a_snapped_measurement_has_a_consistent_beatgrid(
    tmp_path: Path, profile: Profile
) -> None:
    """Regressionstest: bar_length_s muss zum korrigierten Tempo passen.

    Ein frueherer Ansatz hat nur bpm.value ueberschrieben und den Rest der
    Messung unangetastet gelassen - das Ergebnis war ein Datensatz, der sich
    selbst widerspricht: 174 BPM neben einer Taktlaenge von 2.76 s (die
    Taktlaenge fuer 87 BPM). Stufe 6 setzt ihre Cue-Punkte auf bar_length_s;
    ein inkonsistentes Raster haette dort jede Uebergangsberechnung verdorben.
    """
    path = click_track_with_weak_offbeats(
        tmp_path / "t.wav", bpm=174.0, bars=BARS_AT_174, offbeat_db=OFFBEAT_DB
    )
    client = FakeClient([json.dumps(ANSWER)])

    result = analyse_track(client, path, "t", profile, _budget())

    assert isinstance(result, TrackAnalysis)
    expected_bar_length = 4 * 60.0 / result.bpm.value
    assert abs(result.beatgrid.bar_length_s - expected_bar_length) < 1e-6


def test_snapping_lowers_the_confidence(tmp_path: Path, profile: Profile) -> None:
    halftime = click_track_with_weak_offbeats(
        tmp_path / "t.wav", bpm=174.0, bars=BARS_AT_174, offbeat_db=OFFBEAT_DB
    )
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
    """Digitale Stille enthaelt kein Signal - _has_signal faengt das ab, bevor
    librosa ueberhaupt aufgerufen wird, statt eine Warnung erst zu einem Fehler
    zu machen. Das darf keinen Lauf reissen."""
    silent = tmp_path / "still.wav"
    sf.write(silent, np.zeros(44_100 * 5, dtype=np.float32), 44_100)
    client = FakeClient([])

    result = analyse_track(client, silent, "still", profile, _budget())

    assert isinstance(result, UnreadableTrack)
    assert client.calls == 0


def test_a_model_requested_remeasure_is_checked_against_the_corridor(
    tmp_path: Path, profile: Profile
) -> None:
    """Auch der Tempo-Hinweis des Modells laeuft durch snap_to_range.

    Es ist die einzige Stelle der ganzen Kette, an der eine Modellaeusserung
    eine Zahl steuert, die spaeter die Cue-Punkte formt: bar_length_s, energy
    und mix_regions haengen alle am erkannten Raster, und Stufe 6 setzt darauf
    ihre Cue-Punkte. Ein Modell, das hier danebengreift, bekam die Neumessung
    bisher ungeprueft und zu voller Konfidenz zurueck.

    Das Material laeuft sauber auf 174 BPM; das Modell fordert trotzdem eine
    Neumessung mit 87. Ohne Korridorpruefung stuende danach ein
    Halftime-Datensatz im Artefakt.
    """
    path = click_track(tmp_path / "t.wav", bpm=174.0, bars=BARS_AT_174)
    asks_for_halftime = json.dumps(dict(ANSWER, remeasure_with_bpm=87.0))
    client = FakeClient([asks_for_halftime, json.dumps(ANSWER)])

    result = analyse_track(client, path, "t", profile, _budget())

    assert isinstance(result, TrackAnalysis)
    assert profile.bpm.min <= result.bpm.value <= profile.bpm.max
    # Der Code hat die Oktave gedreht, also stammt das Tempo nicht mehr aus der
    # Messung allein - genau wie beim ersten Durchgang faellt die Konfidenz.
    assert result.bpm.confidence <= SNAP_CONFIDENCE_PENALTY
    # Und das Modell sieht in der zweiten Runde den Korridorwert, nicht 87.
    assert "174" in client.prompts[1]

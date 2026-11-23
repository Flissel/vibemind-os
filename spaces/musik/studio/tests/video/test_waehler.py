import json
from datetime import date

import pytest

from djset.agents.base import Budget
from djset.bestand import SongEintrag, Stimmen
from djset.contracts import TrackMeasurements
from djset.video.laenge import VideoFehler
from djset.video.waehler import kandidaten, video_waehlen
from tests.fixtures.analyses import make_analysis
from tests.fixtures.fake_client import FakeClient, ZaehlenderClient


def _song(
    song_id: str,
    dauer_s: float = 240.0,
    bpm: float = 124.0,
    getrennt: bool = False,
    abschnitte: list[float] | None = None,
) -> SongEintrag:
    messung = TrackMeasurements.model_validate(
        make_analysis(song_id, bpm=bpm, camelot="8A", duration_s=dauer_s).model_dump(
            exclude={"verdict"}
        )
    )
    return SongEintrag(
        id=song_id,
        titel=song_id.upper(),
        datei=f"{song_id}.wav",
        herkunft="suno-eigen",
        aufgenommen_am=date(2026, 11, 23),
        pruefsumme="0" * 64,
        messung=messung,
        tempo_abschnitte=abschnitte,
        stimmen=Stimmen(stimme="s.wav", begleitung="b.wav", modell="htdemucs")
        if getrennt
        else None,
    )


@pytest.fixture
def budget() -> Budget:
    return Budget(max_tokens=100_000, max_seconds=60.0)


def _antwort(song_id: str) -> str:
    return json.dumps(
        {"song_id": song_id, "einstieg": "anfang", "gesang": False, "begruendung": "passt"}
    )


def test_kandidaten_lassen_zu_kurze_und_unsichere_weg():
    songs = [
        _song("lang"),
        _song("kurz", dauer_s=100.0),
        _song("wackelig", abschnitte=[124.0, 90.0, 160.0, 70.0]),
        _song("stabil", abschnitte=[124.0, 124.5, 123.8]),
    ]
    assert [s.id for s in kandidaten(songs, 150.0, False)] == ["lang", "stabil"]


def test_ohne_gesang_nur_getrennte_songs():
    songs = [_song("a"), _song("b", getrennt=True), _song("c", getrennt=True)]
    assert [s.id for s in kandidaten(songs, 60.0, True)] == ["b", "c"]
    assert [s.id for s in kandidaten(songs, 60.0, False)] == ["a", "b", "c"]


def test_der_prompt_traegt_wunsch_laenge_und_tabelle(budget):
    client = FakeClient([_antwort("a")])
    songs = [_song("a", dauer_s=200.0, bpm=126.0, getrennt=True), _song("b")]
    video_waehlen(client, "Energie fuer den Trailer", 45.0, songs, budget)
    prompt = client.prompts[0]
    assert "Energie fuer den Trailer" in prompt
    assert "45 Sekunden" in prompt
    zeile = next(z for z in prompt.splitlines() if z.startswith("| a |"))
    assert "126.0" in zeile and "8A" in zeile and "3:20" in zeile and "ja" in zeile
    zeile_b = next(z for z in prompt.splitlines() if z.startswith("| b |"))
    assert "nein" in zeile_b


def test_unbekannter_song_ist_ein_videofehler(budget):
    client = FakeClient([_antwort("gibt-es-nicht")])
    with pytest.raises(VideoFehler) as fehler:
        video_waehlen(client, "x", 30.0, [_song("a"), _song("b")], budget)
    assert "a" in str(fehler.value) and "b" in str(fehler.value)


def test_ohne_kandidaten_kein_modellaufruf(budget):
    client = ZaehlenderClient([])
    with pytest.raises(VideoFehler):
        video_waehlen(client, "x", 30.0, [], budget)
    assert client.aufrufe == 0


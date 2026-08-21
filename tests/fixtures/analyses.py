"""Erfundene Track-Analysen als Eingabe fuer die Stufen 4 bis 7.

Ab Stufe 4 sieht keine Stufe mehr Audio, nur noch JSON. Diese Fixtures sind
deshalb die vollstaendige Testeingabe fuer Curator, Architect, Designer und
Critic - schnell, deterministisch, ohne eine einzige Audiodatei.
"""

from djset.contracts import (
    BeatGrid,
    Bpm,
    Key,
    Loudness,
    MixRegions,
    TrackAnalysis,
    Verdict,
)

# Ein geschlossener Gang ueber das ganze Camelot-Rad: abwechselnd die Tonart
# wechseln (gleiche Zahl, A nach B) und eine Position weiterruecken. Nach 24
# Schritten ist man wieder am Anfang, und auch der Uebergang vom letzten zum
# ersten Eintrag ist benachbart - deshalb 24 Eintraege und nicht 12. Eine
# kuerzere Liste wuerde beim Umlauf von 1A auf 8A springen, und die beiden sind
# auf dem Rad keine Nachbarn.
_WALK = [
    "8A", "8B", "9B", "9A", "10A", "10B", "11B", "11A",
    "12A", "12B", "1B", "1A", "2A", "2B", "3B", "3A",
    "4A", "4B", "5B", "5A", "6A", "6B", "7B", "7A",
]


def wheel_walk(count: int) -> list[str]:
    """count Tonarten, bei denen je zwei aufeinanderfolgende harmonisch passen.

    Gilt auch ueber den Umlauf hinaus: `_WALK` schliesst sich, also bleibt die
    Nachbarschaft auch bei mehr als 24 Tracks erhalten.
    """
    return [_WALK[index % len(_WALK)] for index in range(count)]


def make_analysis(
    track_id: str,
    *,
    bpm: float = 174.0,
    camelot: str = "8A",
    energy: float = 0.5,
    duration_s: float = 240.0,
    usable: bool = True,
) -> TrackAnalysis:
    """Eine vollstaendige, gueltige Trackanalyse mit sprechenden Vorgaben."""
    bar_length = 4 * 60.0 / bpm
    return TrackAnalysis(
        id=track_id,
        path=f"/watch/{track_id}.wav",
        duration_s=duration_s,
        bpm=Bpm(value=bpm, confidence=0.95),
        beatgrid=BeatGrid(
            first_downbeat_s=0.0, bar_length_s=bar_length, confidence=0.95
        ),
        key=Key(camelot=camelot, confidence=0.9),
        energy=[0.80, 0.84, 0.86, 0.85, 0.82],
        mix_regions=MixRegions(
            intro_end_s=bar_length * 16,
            outro_start_s=duration_s - bar_length * 16,
        ),
        loudness=Loudness(lufs_integrated=-9.5, true_peak=-1.2),
        verdict=Verdict(
            usable=usable, notes="Fixture-Track.", energy_level=energy
        ),
    )


def many(count: int) -> list[TrackAnalysis]:
    """count zusammenpassende Tracks: harmonisch benachbart, gestaffelte Energie."""
    keys = wheel_walk(count)
    return [
        make_analysis(
            f"t{index}",
            camelot=keys[index],
            energy=round(0.3 + 0.5 * index / max(1, count - 1), 2),
            bpm=170.0 + (index % 5),
        )
        for index in range(count)
    ]

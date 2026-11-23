"""Erfundene Track-Analysen als Eingabe fuer die Stufen 4 bis 7.

Ab Stufe 4 sieht keine Stufe mehr Audio, nur noch JSON. Diese Fixtures sind
deshalb die vollstaendige Testeingabe fuer Curator, Architect, Designer und
Critic - schnell, deterministisch, ohne eine einzige Audiodatei.
"""

from djset.contracts import (
    BeatGrid,
    Bpm,
    Key,
    LokaleRaster,
    LokalesRaster,
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


def make_off_grid(
    track_id: str,
    *,
    bpm: float,
    duration_s: float = 240.0,
    first_downbeat_s: float = 0.0,
    intro_end_s: float | None = None,
    outro_start_s: float | None = None,
    camelot: str = "8A",
    energy: float = 0.5,
) -> TrackAnalysis:
    """Ein Track mit voll kontrollierbarem Raster und Mix-Regionen.

    `make_analysis` bindet first_downbeat_s fest auf 0.0 und Intro/Outro immer
    an genau 16 Takte. Beides ist bequem und beides ist unrealistisch: im
    Betrieb liegt der erste Downbeat irgendwo, und find_mix_regions zaehlt
    seine 4-Takt-Bloecke ab Sample 0 statt ab first_downbeat_s, also sitzt
    outro_start_s auf einem beliebigen Bruchteil eines Takts.

    Genau die Faelle, die make_analysis nicht herstellen kann, brauchen die
    Tests hier: ein schiefes Raster, ein schiefes Outro, und Mix-Regionen ohne
    nutzbares Material (intro_end_s == 0.0, outro_start_s == duration_s - die
    gleichfoermig laute Spur).
    """
    bar_length = 4 * 60.0 / bpm
    if intro_end_s is None:
        intro_end_s = bar_length * 16
    if outro_start_s is None:
        outro_start_s = duration_s - bar_length * 16
    return TrackAnalysis(
        id=track_id,
        path=f"/watch/{track_id}.wav",
        duration_s=duration_s,
        bpm=Bpm(value=bpm, confidence=0.95),
        beatgrid=BeatGrid(
            first_downbeat_s=first_downbeat_s, bar_length_s=bar_length, confidence=0.95
        ),
        key=Key(camelot=camelot, confidence=0.9),
        energy=[0.80, 0.84, 0.86, 0.85, 0.82],
        mix_regions=MixRegions(intro_end_s=intro_end_s, outro_start_s=outro_start_s),
        loudness=Loudness(lufs_integrated=-9.5, true_peak=-1.2),
        verdict=Verdict(usable=True, notes="Fixture-Track.", energy_level=energy),
    )


def mit_lokalem_raster(
    track: TrackAnalysis,
    intro: tuple[float, float] | None = None,
    outro: tuple[float, float] | None = None,
    streuung: float = 0.02,
) -> TrackAnalysis:
    """Der Track mit lokalen Rastern, je (downbeat_s, bpm) oder None."""

    def raster(werte: tuple[float, float] | None) -> LokalesRaster | None:
        if werte is None:
            return None
        downbeat, bpm = werte
        return LokalesRaster(downbeat_s=downbeat, bar_length_s=4 * 60.0 / bpm,
                             streuung_schlaege=streuung, beats=64)

    return track.model_copy(
        update={"lokale_raster": LokaleRaster(intro=raster(intro), outro=raster(outro))}
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

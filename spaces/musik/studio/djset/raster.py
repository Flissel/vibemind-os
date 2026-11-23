"""Welches Raster an welchem Ende eines Tracks gilt.

Fund 3 der Abnahme: Songs halten ihr Tempo nicht ueber die ganze Laenge. In
den Mischbereichen gilt deshalb das lokale Raster, wenn es gemessen wurde,
sonst das globale. Designer und Critic fragen hier, damit beide dasselbe
Raster benutzen - zwei Herleitungen derselben Groesse waren in diesem Projekt
schon zweimal der Fehler (siehe Transition.overlap_s).
"""

from djset.contracts import MAX_RATIO, MIN_RATIO, BeatGrid, TrackMeasurements


def intro_raster(track: TrackMeasurements) -> BeatGrid:
    """Das Raster, auf dem der Track einsetzt."""
    lokal = track.lokale_raster
    if lokal is not None and lokal.intro is not None:
        return lokal.intro.als_beatgrid()
    return track.beatgrid


def outro_raster(track: TrackMeasurements) -> BeatGrid:
    """Das Raster, auf dem der Track ausgeblendet wird."""
    lokal = track.lokale_raster
    if lokal is not None and lokal.outro is not None:
        return lokal.outro.als_beatgrid()
    return track.beatgrid


def lokales_verhaeltnis(
    grid: BeatGrid, target_bpm: float, fallback: float
) -> float:
    """Das Stretch-Verhaeltnis, das dieses Raster ins Zieltempo bringt.

    Liegt es ausserhalb der Stretch-Grenze, gilt das Verhaeltnis des ganzen
    Tracks: ein Mischbereich, der sich nicht angleichen laesst, soll nicht
    allein ungestreckt bleiben.
    """
    ratio = target_bpm * grid.bar_length_s / (4 * 60.0)
    if MIN_RATIO <= ratio <= MAX_RATIO:
        return ratio
    return fallback

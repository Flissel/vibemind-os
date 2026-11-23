"""Ein Tempo-Bereich fuer ein Set aus dem Bestand.

Produktregel: ein Set hat EINEN Tempo-Korridor. Jeder Song darin muss sich
innerhalb der Stretch-Grenze aus contracts.py (MIN_RATIO..MAX_RATIO) auf das
Zieltempo strecken lassen. Was das nicht kann, bleibt draussen und wird im
Ergebnis benannt - ein stilles Weglassen saehe aus wie ein vergessener Song.

Reine Rechnung auf gemessenen Tempi, kein Modellurteil.
"""

from pydantic import BaseModel

from djset.contracts import MAX_RATIO, MIN_RATIO, BpmRange


class TempoWahl(BaseModel):
    # target: das Zieltempo, auf das gestreckt wird; min/max: kleinstes und
    # groesstes gemessenes Tempo der Songs in `drin`.
    bereich: BpmRange
    drin: list[str]  # Song-IDs im Bereich, in der Reihenfolge der Eingabe
    draussen: dict[str, float]  # Song-ID -> gemessenes Tempo


def _passt(target: float, tempo: float) -> bool:
    return MIN_RATIO <= target / tempo <= MAX_RATIO


def waehle_tempo(tempi: dict[str, float]) -> TempoWahl:
    """Der Bereich, in den die meisten Songs passen. Ein Song passt, wenn
    MIN_RATIO <= target / tempo <= MAX_RATIO. Bei Gleichstand gewinnt das
    Ziel, bei dem die Summe der Abweichungen am kleinsten ist. Kandidaten
    fuer target sind die gemessenen Tempi selbst und die Mitten benachbarter
    Paare. bereich.min = kleinstes Tempo in `drin`, max = groesstes."""
    if not tempi:
        raise ValueError("waehle_tempo braucht mindestens ein gemessenes Tempo")

    sortiert = sorted(set(tempi.values()))
    kandidaten = sorted(
        set(sortiert) | {(a + b) / 2 for a, b in zip(sortiert, sortiert[1:])}
    )

    beste: tuple[int, float] | None = None
    beste_wahl: TempoWahl | None = None
    for target in kandidaten:
        drin = [song_id for song_id, tempo in tempi.items() if _passt(target, tempo)]
        if not drin:
            continue
        unten = min(tempi[song_id] for song_id in drin)
        oben = max(tempi[song_id] for song_id in drin)
        # Eine Mitte, die nur Songs auf EINER Seite aufnimmt, laege ausserhalb
        # von min..max und verletzte den BpmRange-Vertrag. Gewinnen kann sie
        # ohnehin nie: das naechstgelegene Tempo dieser Seite nimmt dieselben
        # Songs auf, mit kleinerer Abweichung. Uebersprungen wird sie trotzdem
        # ausdruecklich, statt sich auf diese Ueberlegung zu verlassen.
        if not unten <= target <= oben:
            continue
        abweichung = sum(abs(target - tempi[song_id]) for song_id in drin)
        # Mehr Songs schlagen weniger, danach entscheidet die Abweichung. Bei
        # vollem Gleichstand bleibt der erste (kleinste) Kandidat - feste
        # Reihenfolge, damit dieselbe Eingabe immer dieselbe Wahl ergibt.
        wertung = (-len(drin), abweichung)
        if beste is None or wertung < beste:
            beste = wertung
            beste_wahl = TempoWahl(
                bereich=BpmRange(target=target, min=unten, max=oben),
                drin=drin,
                draussen={
                    song_id: tempo
                    for song_id, tempo in tempi.items()
                    if song_id not in drin
                },
            )

    # Jedes gemessene Tempo ist selbst Kandidat und nimmt mindestens seinen
    # eigenen Song auf (Verhaeltnis 1.0) - eine Wahl gibt es also immer.
    if beste_wahl is None:
        raise RuntimeError("kein Tempo-Kandidat nimmt einen Song auf")
    return beste_wahl

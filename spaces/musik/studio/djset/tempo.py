"""Ein Tempo-Bereich fuer ein Set aus dem Bestand.

Produktregel: ein Set hat EINEN Tempo-Korridor. Jeder Song darin muss sich
innerhalb der Stretch-Grenze aus contracts.py (MIN_RATIO..MAX_RATIO) auf das
Zieltempo strecken lassen. Was das nicht kann, bleibt draussen und wird im
Ergebnis benannt - ein stilles Weglassen saehe aus wie ein vergessener Song.

Reine Rechnung auf gemessenen Tempi, kein Modellurteil.
"""

import math

from pydantic import BaseModel

from djset.contracts import MAX_RATIO, MIN_RATIO, BpmRange


class TempoWahl(BaseModel):
    # target: das Zieltempo, auf das gestreckt wird; min/max: kleinstes und
    # groesstes gemessenes Tempo der Songs in `drin`.
    bereich: BpmRange
    drin: list[str]  # Song-IDs im Bereich, in der Reihenfolge der Eingabe
    draussen: dict[str, float]  # Song-ID -> gemessenes Tempo


def _passt(target: float, tempo: float) -> bool:
    # Genau diese Division, ohne Toleranz: dieselbe Pruefung steht in
    # agents/designer.py::_ratio, und ein Song, der hier passt, muss dort
    # gestreckt werden - nicht wegen einer Rundung ungestreckt geschnitten.
    return MIN_RATIO <= target / tempo <= MAX_RATIO


def _grenze(tempo: float, verhaeltnis: float, innen: float) -> float:
    """Das Ziel tempo * verhaeltnis, auf die Gleitkommazahl geschoben, an der
    die Pruefung _passt(ziel, tempo) gerade noch besteht.

    tempo * 0.94 faellt durch Rundung teils durch seine eigene Pruefung
    (ziel / tempo ergibt 0.9399999...). Dann wird mit math.nextafter nach
    innen geschoben, bis sie besteht. Besteht sie schon, wird umgekehrt nach
    aussen geschoben, solange sie besteht - der Kandidat ist danach genau die
    aeusserste Zahl, die den Song noch aufnimmt. Beides sind hoechstens ein
    paar Schritte von einer Einheit der letzten Stelle.
    """
    ziel = tempo * verhaeltnis
    while not _passt(ziel, tempo):
        ziel = math.nextafter(ziel, innen)
    aussen = -innen
    while _passt(math.nextafter(ziel, aussen), tempo):
        ziel = math.nextafter(ziel, aussen)
    return ziel


def _kandidaten(tempi: dict[str, float]) -> list[float]:
    """Jedes gemessene Tempo, die Mitte jedes Paars benachbarter verschiedener
    Tempi und je Tempo die beiden Grenzen tempo * MIN_RATIO und
    tempo * MAX_RATIO.

    Die Grenzen sind noetig: die meisten Songs nimmt immer ein Ziel auf, an
    dem ein Song gerade hineinkommt - und das ist tempo * MIN_RATIO, kein
    gemessenes Tempo und keine Mitte. Beispiel 100 und 112,5 BPM: gemeinsam
    geht es nur zwischen 105,75 und 106.
    """
    sortiert = sorted(set(tempi.values()))
    kandidaten = set(sortiert)
    kandidaten |= {(a + b) / 2 for a, b in zip(sortiert, sortiert[1:])}
    for tempo in sortiert:
        kandidaten.add(_grenze(tempo, MIN_RATIO, math.inf))
        kandidaten.add(_grenze(tempo, MAX_RATIO, -math.inf))
    return sorted(kandidaten)


def waehle_tempo(tempi: dict[str, float]) -> TempoWahl:
    """Der Bereich, in den die meisten Songs passen.

    Ein Song mit Tempo t passt zu einem Ziel T, wenn
    MIN_RATIO <= T / t <= MAX_RATIO - genau diese Division. Kandidaten fuer T
    stehen in _kandidaten. Gewertet wird nur ein Kandidat, der zwischen dem
    kleinsten und dem groessten Tempo der aufgenommenen Songs liegt. Rang:
    die meisten Songs, dann die kleinste Summe |T - t|, dann das kleinste T.
    bereich.min = kleinstes Tempo in `drin`, max = groesstes."""
    if not tempi:
        raise ValueError("waehle_tempo braucht mindestens ein gemessenes Tempo")

    beste: tuple[int, float, float] | None = None
    beste_wahl: TempoWahl | None = None
    for target in _kandidaten(tempi):
        drin = [song_id for song_id, tempo in tempi.items() if _passt(target, tempo)]
        if not drin:
            continue
        unten = min(tempi[song_id] for song_id in drin)
        oben = max(tempi[song_id] for song_id in drin)
        # Ein Ziel ausserhalb von min..max verletzte den BpmRange-Vertrag.
        # Gewinnen koennte es ohnehin nie: das naechstgelegene Tempo dieser
        # Seite nimmt dieselben Songs auf, mit kleinerer Abweichung.
        if not unten <= target <= oben:
            continue
        abweichung = sum(abs(target - tempi[song_id]) for song_id in drin)
        wertung = (-len(drin), abweichung, target)
        if beste is None or wertung < beste:
            beste = wertung
            draussen = {
                song_id: tempo
                for song_id, tempo in tempi.items()
                if song_id not in drin
            }
            beste_wahl = TempoWahl(
                bereich=BpmRange(target=target, min=unten, max=oben),
                drin=drin,
                draussen=draussen,
            )

    # Jedes gemessene Tempo ist selbst Kandidat und nimmt mindestens seinen
    # eigenen Song auf (Verhaeltnis 1.0) - eine Wahl gibt es also immer.
    if beste_wahl is None:
        raise RuntimeError("kein Tempo-Kandidat nimmt einen Song auf")
    return beste_wahl

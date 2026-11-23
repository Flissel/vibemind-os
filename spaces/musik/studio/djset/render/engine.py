"""Stufe 8: fuehrt den Mixplan aus.

Der Renderer trifft keine eigenen Entscheidungen - das ist die
Determinismus-Grenze der Spec: gleicher Mixplan, gleiches WAV. Jede Zahl, nach
der er handelt, steht im Plan. Findet sich beim Bauen eine Groesse, die er
selbst bestimmen muesste, ist das ein Befund ueber den Mixplan.

Die Zeitleiste ist dieselbe wie in Stufe 6: Eintrag 0 beginnt bei null, jeder
weitere bei start_at_s seines eingehenden Uebergangs, und ein Eintrag klingt
(play_to_s - play_from_s) / timestretch_ratio Sekunden lang.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from djset.contracts import MixEntry, MixPlan, Transition
from djset.dsp.analyze import BEATS_PER_BAR
from djset.render.eq import apply_eq
from djset.render.stretch import SR, UnsupportedSampleRate, stretch


class MissingSource(RuntimeError):
    """Zu einem Eintrag des Mixplans fehlt die Audiodatei."""


class ImpossibleMixPlan(RuntimeError):
    """Der Mixplan verlangt etwas, das sich nicht ausfuehren laesst."""


def bar_length_s(target_bpm: float) -> float:
    """Wie lang ein Takt im fertigen Set dauert."""
    return BEATS_PER_BAR * 60.0 / target_bpm


def _strip_peak_timestamp(path: Path) -> None:
    """Nullt den Zeitstempel im PEAK-Chunk einer WAV-Datei.

    libsndfile schreibt fuer Float-WAVs einen PEAK-Chunk, dessen zweites Feld
    time(NULL) ist. Damit unterscheiden sich zwei Renders desselben Mixplans in
    genau einem Byte-Feld, obwohl die Audiodaten bitgleich sind - und die Zusage
    des Spec ("gleicher Mixplan, gleiches WAV") waere nicht woertlich wahr.

    Die Chunk-Tabelle wird durchlaufen statt an einen festen Offset gesprungen:
    die Chunkfolge haengt vom Format ab und ist keine Konstante.
    """
    with path.open("r+b") as handle:
        if handle.read(4) != b"RIFF":
            return
        handle.seek(12)
        while True:
            header = handle.read(8)
            if len(header) < 8:
                return
            name, size = header[:4], int.from_bytes(header[4:8], "little")
            if name == b"PEAK" and size >= 8:
                handle.seek(4, 1)          # ueber das version-Feld
                handle.write(b"\x00\x00\x00\x00")
                return
            handle.seek(size + (size & 1), 1)


def _load_audio(path: Path, sr: int) -> np.ndarray:
    """Liest eine Audiodatei als Stereo, Form (n, 2), float32.

    Ein einzelner Kanal wird auf beide Seiten gelegt (ohne Pegelaenderung),
    bei mehr als zwei Kanaelen zaehlen die ersten beiden. Ein Downmix auf Mono
    findet nicht mehr statt - das Stereobild der Quelle bleibt erhalten.
    """
    signal, file_sr = sf.read(path, dtype="float32", always_2d=True)
    if signal.shape[1] == 1:
        signal = np.repeat(signal, 2, axis=1)
    elif signal.shape[1] > 2:
        signal = signal[:, :2]
    if file_sr != sr:
        # Rueckhalt, nicht die eigentliche Pruefung: die steht im Vorab-Check
        # (djset.watch.check_sample_rate) und laeuft, bevor der erste Agent
        # Tokens verbraucht. Frueher flog hier ein MissingSource - eine Datei,
        # die sehr wohl da war, wurde als fehlend gemeldet, und zwar erst nach
        # der kompletten Agentenkette.
        raise UnsupportedSampleRate(
            f"{path.name} liegt mit {file_sr} Hz vor, gebraucht werden {sr} Hz"
        )
    return np.ascontiguousarray(signal, dtype=np.float32)


# Streckabschnitte (Fund 3): so viel Quellmaterial wird zu beiden Seiten eines
# Stuecks mitgestreckt. rubberband braucht Anlauf, und die Ueberblendung an
# einer Grenze braucht Material von beiden Stuecken ueber die Grenze hinaus.
_KONTEXT_S = 0.25

# Breite der Ueberblendung an einer Abschnittsgrenze. Beide Seiten sind
# dasselbe Material im selben Tempo, die Grenze liegt auf einem Downbeat -
# zehn Millisekunden reichen, damit die zwei Phasen-Vocoder-Ergebnisse nicht
# knacken, und sind zu kurz, um zu verwischen.
_FUGE_S = 0.010


def _abschnittsweise(
    signal: np.ndarray, entry: MixEntry, sr: int, work: Path
) -> np.ndarray:
    """Ein Eintrag mit mehreren Streckabschnitten.

    Jedes Stueck wird mit etwas Kontext einzeln gestreckt und an die Stelle
    gelegt, die MixEntry.ausgabe_s ihm zuweist. An den inneren Grenzen gehen
    die Stuecke ueber eine kurze lineare Fuge ineinander ueber; am Anfang und
    Ende des Eintrags wird hart geschnitten wie beim einzelnen Verhaeltnis.
    """
    laenge = int(round(entry.ausgabe_dauer_s * sr))
    ergebnis = np.zeros((laenge, 2), dtype=np.float64)
    fuge = _FUGE_S * sr
    stuecke = entry.stuecke()
    quelle_ende = len(signal) / sr

    for nummer, (von, bis, ratio) in enumerate(stuecke):
        a = max(0.0, von - _KONTEXT_S)
        b = min(quelle_ende, bis + _KONTEXT_S)
        stueck = signal[int(round(a * sr)):int(round(b * sr))]
        if ratio != 1.0:
            cut = work / f"{entry.track_id}-{nummer}-cut.wav"
            out = work / f"{entry.track_id}-{nummer}-stretched.wav"
            sf.write(cut, stueck, sr, subtype="FLOAT")
            stretch(cut, out, ratio, sr=sr)
            stueck = _load_audio(out, sr)

        platz = int(round((entry.ausgabe_s(von) - (von - a) / ratio) * sr))
        anfang = entry.ausgabe_s(von) * sr
        ende = entry.ausgabe_s(bis) * sr
        index = np.arange(len(stueck)) + platz
        if nummer == 0:
            gewicht = (index >= round(anfang)).astype(np.float64)
        else:
            gewicht = np.clip((index - (anfang - fuge / 2)) / fuge, 0.0, 1.0)
        if nummer == len(stuecke) - 1:
            gewicht *= index < round(ende)
        else:
            gewicht *= np.clip(((ende + fuge / 2) - index) / fuge, 0.0, 1.0)

        drin = (index >= 0) & (index < laenge)
        ergebnis[index[drin]] += stueck[drin] * gewicht[drin, None]

    return ergebnis.astype(np.float32)


def _segment(source: Path, entry: MixEntry, sr: int, work: Path) -> np.ndarray:
    """Der gedehnte, verstaerkte Abschnitt eines Eintrags."""
    signal = _load_audio(source, sr)
    if entry.abschnitte:
        return _abschnittsweise(signal, entry, sr, work) * (10 ** (entry.gain_db / 20.0))
    start = int(round(entry.play_from_s * sr))
    stop = min(len(signal), int(round(entry.play_to_s * sr)))
    piece = signal[start:stop]

    if entry.timestretch_ratio != 1.0:
        # Die Schnittdatei entsteht nur, wenn wirklich gedehnt wird. Sie immer
        # zu schreiben waere bei dreizehn Tracks eines Sechzig-Minuten-Sets ein
        # voller ueberfluessiger Schreibdurchlauf ueber das gesamte Material.
        cut = work / f"{entry.track_id}-cut.wav"
        out = work / f"{entry.track_id}-stretched.wav"
        # Die Schnittdatei traegt beide Kanaele; stretch() bleibt unveraendert,
        # ffmpeg/rubberband behalten die Kanalzahl.
        # subtype="FLOAT": sf.write faellt bei WAV sonst auf PCM_16 zurueck.
        # Jeder gedehnte Eintrag waere damit durch 16 Bit gelaufen, waehrend
        # die ungedehnten in Float bleiben - eine stille Qualitaetsstufe
        # mitten im Set, allein davon abhaengig, ob ein Track angeglichen
        # werden musste.
        sf.write(cut, piece, sr, subtype="FLOAT")
        stretch(cut, out, entry.timestretch_ratio, sr=sr)
        piece = _load_audio(out, sr)

    return piece * (10 ** (entry.gain_db / 20.0))


# Plan 1c: Schrittweite der Stimmhuelle, die render() zurueckgibt. 50 ms
# loesen eine Ueberlagerung von einer halben Sekunde zehnfach auf.
STIMM_HUELLE_S = 0.05
# Pegel eines stillen Schritts, statt minus unendlich.
STILLE_DB = -120.0


@dataclass(frozen=True)
class StimmSpur:
    """Wie laut die Stimme eines Eintrags im fertigen Set klingt (Plan 1c).

    Gemessen an genau dem Signal, das der Renderer gelegt hat - nach Huelle,
    EQ, Pegel und Master. `pegel_db[i]` ist der RMS der Mono-Summe im Schritt
    ab start_s + i * schritt_s.
    """

    track_id: str
    start_s: float
    schritt_s: float
    pegel_db: np.ndarray


def _stimm_verlauf(
    laenge: int,
    entry: MixEntry,
    start_s: float,
    incoming: Transition | None,
    outgoing: Transition | None,
    sr: int,
) -> np.ndarray:
    """Verstaerkung der Stimme je Sample des Eintrags.

    Im eingehenden Uebergang schweigt sie bis zum Stimmwechsel und blendet
    danach ein, im ausgehenden blendet sie davor aus und schweigt danach.
    Gesang aus heisst null ueberall.
    """
    verlauf = np.full(laenge, 1.0 if entry.stimme else 0.0)
    index = np.arange(laenge)
    if incoming is not None and incoming.stimmwechsel is not None:
        wechsel = incoming.stimmwechsel
        bei = wechsel.bei_s * sr
        verlauf *= np.clip((index - bei) / (wechsel.blende_s * sr), 0.0, 1.0)
    if outgoing is not None and outgoing.stimmwechsel is not None:
        wechsel = outgoing.stimmwechsel
        bei = (outgoing.start_at_s + wechsel.bei_s - start_s) * sr
        verlauf *= np.clip((bei - index) / (wechsel.blende_s * sr), 0.0, 1.0)
    return verlauf


def _pegel_db(signal: np.ndarray, sr: int) -> np.ndarray:
    schritt = int(round(STIMM_HUELLE_S * sr))
    mono = signal.astype(np.float64).mean(axis=1)
    anzahl = len(mono) // schritt
    stuecke = mono[: anzahl * schritt].reshape(anzahl, schritt)
    rms = np.sqrt(np.mean(stuecke**2, axis=1))
    with np.errstate(divide="ignore"):
        return np.maximum(20.0 * np.log10(rms), STILLE_DB)


def render(
    plan: MixPlan,
    sources: dict[str, Path],
    target: Path,
    master_gain_db: float = 0.0,
    sr: int = SR,
    stimmen: dict[str, tuple[Path, Path]] | None = None,
) -> list[StimmSpur]:
    """Rendert den Mixplan in eine einzelne Audiodatei.

    `stimmen` ordnet einer track_id (Stimme, Begleitung) zu (Plan 1c). Ein
    solcher Eintrag wird aus beiden Spuren gebaut, die Stimme nach
    MixEntry.stimme und Transition.stimmwechsel geblendet. Eintraege ohne
    getrennte Stimme klingen wie bisher aus ihrer Quelle. Zurueck kommen die
    Huellen der gelegten Stimmen, fuer die QA.
    """
    missing = [e.track_id for e in plan.entries if e.track_id not in sources]
    if missing:
        raise MissingSource(
            f"Zu diesen Eintraegen fehlt die Audiodatei: {', '.join(missing)}"
        )
    stimmen = stimmen or {}

    starts = [0.0] + [t.start_at_s for t in plan.transitions]
    total = int(round(plan.target_length_s * sr))
    mix = np.zeros((total, 2), dtype=np.float32)
    master = 10 ** (master_gain_db / 20.0)
    spuren: list[StimmSpur] = []

    with tempfile.TemporaryDirectory() as raw:
        work = Path(raw)
        for index, entry in enumerate(plan.entries):
            # Der eingehende Uebergang faerbt den Kopf, der ausgehende den
            # Schwanz. Dazwischen laeuft der Track flach.
            incoming = plan.transitions[index - 1] if index > 0 else None
            outgoing = plan.transitions[index] if index < len(plan.transitions) else None

            paar = stimmen.get(entry.track_id)
            if paar is None:
                teile = [_segment(sources[entry.track_id], entry, sr, work)]
            else:
                gesang = _segment(paar[0], entry, sr, work)
                begleitung = _segment(paar[1], entry, sr, work)
                laenge = min(len(gesang), len(begleitung))
                gesang, begleitung = gesang[:laenge], begleitung[:laenge]
                verlauf = _stimm_verlauf(
                    laenge, entry, starts[index], incoming, outgoing, sr
                )
                teile = [begleitung, (gesang * verlauf[:, None]).astype(np.float32)]
            block = teile[0]

            # Die Fensterbreite steht im Plan (Transition.overlap_s) und wird
            # hier NICHT mehr aus bars und target_bpm nachgerechnet. Genau
            # diese zweite Herleitung war der Fehler: sie mass in Takten des
            # Zieltempos, waehrend Stufe 6 in Takten des Quelltracks rechnete,
            # und die Differenz landete als Fenster VOR der Naht - beim Schnitt
            # als digitale Stille, bevor der neue Track einsetzte.
            length = block.shape[0]
            head_width = (
                min(length, int(round(incoming.overlap_s * sr)))
                if incoming is not None else 0
            )
            tail_width = (
                min(length, int(round(outgoing.overlap_s * sr)))
                if outgoing is not None else 0
            )

            if head_width + tail_width > length:
                # block[:head_width] und block[-tail_width:] wuerden sich
                # ueberlappen: die zweite apply_eq-Anwendung laese dann
                # bereits von der ersten veraenderte Samples, die beiden
                # Kurven wuerden sich multiplizieren - ein Ergebnis, das in
                # keinem Plan steht. Der Renderer entscheidet das nicht,
                # sondern meldet es als Befund ueber den Mixplan.
                raise ImpossibleMixPlan(
                    f"Eintrag {entry.track_id!r} ist kuerzer als seine beiden "
                    f"Uebergangsfenster zusammen: Kopf {head_width} + Schwanz "
                    f"{tail_width} Samples > {length} Samples Blocklaenge"
                )

            # Getrennte Spuren werden jede fuer sich gefaerbt: die EQ-Kurven
            # sind linear, die Summe ist dieselbe - aber die Stimmhuelle misst
            # so genau das, was von der Stimme im Set ankommt.
            for teil in teile:
                if incoming is not None and head_width > 0:
                    teil[:head_width] = apply_eq(teil[:head_width], sr, incoming.eq_in)
                if outgoing is not None and tail_width > 0:
                    # Ohne die tail_width > 0 - Bedingung waere teil[-0:] das
                    # GANZE Array, nicht das leere - eine klassische Python-Falle.
                    teil[-tail_width:] = apply_eq(
                        teil[-tail_width:], sr, outgoing.eq_out
                    )
            block = teile[0] if len(teile) == 1 else teile[0] + teile[1]
            if len(teile) == 2:
                spuren.append(
                    StimmSpur(
                        track_id=entry.track_id,
                        start_s=starts[index],
                        schritt_s=STIMM_HUELLE_S,
                        pegel_db=_pegel_db(teile[1] * master, sr),
                    )
                )

            offset = int(round(starts[index] * sr))
            end = min(total, offset + length)
            if end > offset:
                mix[offset:end] += block[: end - offset]

    mix *= master
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(target, mix, sr, subtype="FLOAT")
    # sf.write mit subtype="FLOAT" bleibt bewusst bestehen (nicht auf ein
    # Integer-Subtype gewechselt): der Mix darf 1.0 ueberschreiten, ohne
    # geklippt zu werden, weil die QA erst danach den wahren Spitzenpegel
    # misst und ueber einen korrigierenden Nachlauf entscheidet. Der einzige
    # Nachteil des Float-Formats - der PEAK-Chunk-Zeitstempel - wird separat
    # entschaerft.
    _strip_peak_timestamp(target)
    return spuren

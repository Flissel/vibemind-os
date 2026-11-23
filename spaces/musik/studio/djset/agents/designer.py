"""Stufe 6: entwirft jeden Uebergang und rechnet daraus den Mixplan.

Aufgabenteilung: das Modell waehlt pro Uebergang Technik und Taktzahl - das ist
Geschmack und Genrekenntnis. Alles andere rechnet Code: Cue-Punkte auf
Downbeats, Stretch-Verhaeltnisse gegen das Ziel-Tempo, Positionen in der
Ausgabe-Timeline, EQ-Kurven aus Vorlagen je Technik. Damit ist der aufwendigste
Teil der Stufe deterministisch und ohne Modell testbar - und der Mixplan
erfuellt die Determinismus-Grenze der Spec, ohne dass ein Modell Zahlen erfindet.

Zeitrechnung: timestretch_ratio ist Ziel-Tempo geteilt durch Quell-Tempo, die
Ausgabedauer eines Abschnitts also Quelldauer geteilt durch das Verhaeltnis.

Tonart-Brueche: liess sich keine Reihenfolge ohne Bruch finden (siehe
architect.py), steht an manchen Naehten ein harmonisch unvertraegliches Paar.
Dort ueberblendet man nicht - zwei Tonarten uebereinander klingen schief. Ein
Bruch-Uebergang bekommt deshalb nur `cut` oder `filter_sweep`, auch wenn das
Profil sie nicht nennt (BREAK_TECHNIQUES).
"""

import math

from pydantic import BaseModel, Field

from djset.agents.base import (
    AgentClient,
    Budget,
    StageRulesViolated,
    run_agent,
)
from djset.camelot import break_indices
from djset.dsp.analyze import mischbereich
from djset.raster import intro_raster, lokales_verhaeltnis, outro_raster
from djset.contracts import (
    MAX_RATIO,
    MIN_RATIO,
    BeatGrid,
    EqCurve,
    EqPoint,
    MixEntry,
    MixPlan,
    Profile,
    Setlist,
    Stimmwechsel,
    Streckabschnitt,
    Technique,
    TrackAnalysis,
    Transition,
)
from djset.dsp.analyze import BEATS_PER_BAR

MAX_ROUNDS = 3

# Wie weit der Pegelabgleich einen Track hoechstens verschiebt. 6 dB deckt den
# Abstand ab, den normal gemastertes Material untereinander hat; alles
# darueber ist keine Lautheitsdifferenz mehr, sondern eine Fehlmessung.
MAX_GAIN_DB = 6.0

# Was an einem Tonart-Bruch erlaubt ist: ein harter Schnitt oder ein
# Filter-Sweep, der die Klangfarbe wegnimmt, bevor die neue Tonart kommt. Gilt
# unabhaengig von den Techniken des Profils - der Bericht nennt es, wenn eine
# davon dort nicht stand.
BREAK_TECHNIQUES: tuple[Technique, ...] = ("cut", "filter_sweep")


class TransitionChoice(BaseModel):
    technique: Technique
    bars: int = Field(ge=1)


class DesignerAnswer(BaseModel):
    choices: list[TransitionChoice] = Field(min_length=1)


PROMPT = """Du entwirfst die Uebergaenge eines DJ-Sets.

Das Set: {genre}, Ziel-BPM {bpm_target}. Erlaubte Techniken: {techniques}.
Erlaubte Uebergangslaenge: {bars_min} bis {bars_max} Takte.

Die Reihenfolge steht fest. Diese Uebergaenge sind zu entwerfen:

{table}

Waehle fuer jeden Uebergang eine Technik und eine Taktzahl. Gib genau
{count} Eintraege in derselben Reihenfolge zurueck.

Woran du dich orientierst:
- Ein grosser Energiesprung vertraegt einen kuerzeren, haerteren Uebergang.
- Zwei ruhige Tracks nebeneinander wollen einen langen, weichen Blend.
- Ein eq_swap taugt, wenn beide Tracks eine tragende Bassline haben.
- Ein filter_sweep ueberbrueckt einen Wechsel der Klangfarbe.
- Steht bei einem Uebergang "Tempi nicht angleichbar", wird er vom Code
  automatisch zu einem Schnitt gemacht - die Tempi liegen zu weit auseinander,
  um sie anzugleichen. Deine Technikwahl wird dort verworfen; waehle trotzdem
  eine aus der erlaubten Liste.
{break_hint}
Antworte ausschliesslich mit dem geforderten JSON."""

BREAK_HINT = """- Steht bei einem Uebergang "Tonart-Bruch", passen die beiden Tonarten
  nicht zusammen. Dort ist nur cut oder filter_sweep erlaubt, auch wenn die
  Liste oben sie nicht nennt.
"""


def break_seams(
    order: list[str], by_id: dict[str, TrackAnalysis], profile: Profile
) -> set[int]:
    """Die Naehte i (zwischen order[i] und order[i+1]), die ein Tonart-Bruch sind."""
    keys = [by_id[track_id].key.camelot for track_id in order]
    return set(break_indices(keys, profile.harmonic_strictness))


def snap_to_downbeat(time_s: float, grid: BeatGrid) -> float:
    """Die naechstgelegene Taktgrenze zu einem Zeitpunkt."""
    if time_s <= grid.first_downbeat_s:
        return grid.first_downbeat_s
    bars = round((time_s - grid.first_downbeat_s) / grid.bar_length_s)
    return grid.first_downbeat_s + bars * grid.bar_length_s


def _ratio(track: TrackAnalysis, target_bpm: float) -> float:
    """Das Verhaeltnis, mit dem der Track auf das Ziel-Tempo gezogen wird.

    Liegt es ausserhalb der Grenze, bleibt der Track ungestreckt - der Uebergang
    in ihn hinein wird dann zum cut.
    """
    ratio = target_bpm / track.bpm.value
    if MIN_RATIO <= ratio <= MAX_RATIO:
        return ratio
    return 1.0


def _is_unstretchable(track: TrackAnalysis, ratio: float, target_bpm: float) -> bool:
    """Ob der Track nicht auf das Ziel-Tempo gezogen werden konnte.

    Ratio 1.0 heisst entweder "liegt schon auf dem Ziel" oder "liess sich nicht
    angleichen". Erst der Tempovergleich trennt die beiden Faelle.
    """
    return ratio == 1.0 and track.bpm.value != target_bpm


def _gains(order: list[str], by_id: dict[str, TrackAnalysis]) -> dict[str, float]:
    """Wie weit jeder Track angehoben oder abgesenkt wird.

    Suno liefert Tracks mit deutlich verschiedener Lautheit, und die Spec
    verbietet dem Render-Engineer eigene Entscheidungen - der Pegelabgleich
    kann also nicht an ihn abgeschoben werden, er gehoert hierher. Gemessen
    ist er laengst: lufs_integrated stand bisher im Artefakt und wurde von
    niemandem benutzt.

    Referenz ist der MEDIAN der Lautheiten des Sets, nicht ein fester Zielwert
    wie -14 LUFS: das Set soll in sich gleich laut sein, und wo es insgesamt
    liegt, entscheidet die Normalisierung des Renderers in Plan 3 gegen das
    True-Peak-Kriterium. Der Median statt des Mittelwerts, weil ein einzelner
    Ausreisser - ein still gemessener oder kaputt gemasterter Track - sonst
    alle anderen mitzoege.

    Geklammert auf MAX_GAIN_DB: eine Fehlmessung (der Bodenwert -120 dB fuer
    digitale Stille steht so im Vertrag) wuerde sonst eine Anhebung um 110 dB
    verlangen und den Mix zerreissen. Ein Track, der die Klammer braucht, ist
    ohnehin ein Fall fuer den Curator.
    """
    levels = sorted(by_id[tid].loudness.lufs_integrated for tid in order)
    middle = len(levels) // 2
    reference = (
        levels[middle]
        if len(levels) % 2
        else (levels[middle - 1] + levels[middle]) / 2.0
    )
    gains: dict[str, float] = {}
    for tid in order:
        raw = reference - by_id[tid].loudness.lufs_integrated
        gains[tid] = max(-MAX_GAIN_DB, min(MAX_GAIN_DB, raw))
    return gains


# Ein Hauch Toleranz vor dem Abschneiden ganzer Takte. Die Zeiten kommen aus
# Fliesskommarechnung: acht Takte, einmal gesnappt und wieder abgezogen,
# ergeben 7,999999999999 - und int() macht daraus sieben. 1e-9 Takte sind bei
# 174 BPM anderthalb Nanosekunden.
_BAR_EPSILON = 1e-9


def _available_bars(span_s: float, bar_length_s: float) -> int:
    """Wie viele ganze Takte in eine Zeitspanne passen."""
    return max(0, int(span_s / bar_length_s + _BAR_EPSILON))


def _snapped_outro(track: TrackAnalysis) -> float:
    """Der Outro-Einsatz auf dem Taktraster des Tracks.

    Der gemessene Wert taugt dafuer nicht: find_mix_regions zaehlt seine
    4-Takt-Bloecke ab Sample 0, nicht ab first_downbeat_s, also liegt
    outro_start_s auf einem beliebigen Bruchteil eines Takts. Wird daraus die
    Position in der Ausgabe-Timeline gerechnet, faellt der Uebergang um genau
    diesen Bruchteil neben den Takt - der eingehende Track startet auf seinem
    eigenen Downbeat und damit gegen den Vorgaenger verschoben.

    Deshalb wird hier einmal je Track gesnappt, und jede weitere Benutzung
    leitet sich von diesem Wert ab: die Taktzahl, die das Outro noch hergibt,
    das Ende des Abschnitts und die Position des Uebergangs. Sonst rechnen
    Material und Timeline mit zwei verschiedenen Outros.

    Rundet der Snap ueber das Trackende hinaus, geht es einen Takt zurueck:
    ein Outro-Einsatz hinter dem Trackende ergaebe einen negativen Overlap und
    damit ein Loch in der Ausgabe.
    """
    grid = outro_raster(track)
    bereich = mischbereich(track)
    outro = snap_to_downbeat(bereich.outro_start_s, grid)
    if grid is not track.beatgrid:
        # Lokales Raster (Fund 3): der Mischbereich ist in Takten des
        # globalen Rasters bemessen. Ist der lokale Takt laenger, passen
        # dieselben Takte nicht mehr hinein - der Einsatz rueckt so weit vor,
        # dass hinter ihm wieder genauso viele lokale Takte kommen.
        takte = _available_bars(
            track.duration_s - bereich.outro_start_s, track.beatgrid.bar_length_s
        )
        spaetestens = track.duration_s - takte * grid.bar_length_s
        schritte = math.floor(
            (spaetestens - grid.first_downbeat_s) / grid.bar_length_s + _BAR_EPSILON
        )
        outro = min(outro, grid.first_downbeat_s + schritte * grid.bar_length_s)
    if outro > track.duration_s:
        outro = max(grid.first_downbeat_s, outro - grid.bar_length_s)
    return outro


def _eq_curve(technique: Technique, outgoing: bool) -> EqCurve:
    """EQ-Verlauf je Technik, getrennt fuer aus- und eingehenden Track."""
    full, silent = 0.0, -60.0
    start, end = (full, silent) if outgoing else (silent, full)

    def ramp(mid_gain: float) -> list[EqPoint]:
        return [
            EqPoint(position=0.0, gain_db=start),
            EqPoint(position=0.5, gain_db=mid_gain),
            EqPoint(position=1.0, gain_db=end),
        ]

    if technique == "cut":
        # Ein Schnitt ist ein Schnitt: beide Kurven stehen von Anfang an auf
        # ihrem Endwert, die Umschaltung passiert bei start_at_s. Das ist nach
        # der Downbeat-Korrektur ein Downbeat beider Tracks.
        #
        # Vorher lag der Umschaltpunkt bei Position 0.9 des Uebergangs: bei 16
        # Takten spielte der eingehende Track 14,4 Takte lang unhoerbar mit und
        # der Wechsel fiel auf Takt 14,4 - unter keiner Rasterung ein Downbeat.
        # Ausgerechnet beim Schnitt: den waehlt der Code genau dann, wenn die
        # Tempi mehr als sechs Prozent auseinanderliegen, also im Fall, in dem
        # Ausrichtung am schwersten ist.
        flat = [
            EqPoint(position=0.0, gain_db=end),
            EqPoint(position=1.0, gain_db=end),
        ]
        return EqCurve(low=flat, mid=flat, high=flat)

    if technique == "eq_swap":
        # Die Baesse tauschen frueh und schnell, damit sie sich nie stapeln.
        fast = [
            EqPoint(position=0.0, gain_db=start),
            EqPoint(position=0.35, gain_db=end),
            EqPoint(position=1.0, gain_db=end),
        ]
        return EqCurve(low=fast, mid=ramp(-6.0), high=ramp(-3.0))

    if technique == "filter_sweep":
        # Die Hoehen fuehren die Bewegung, der Bass folgt spaet.
        late = [
            EqPoint(position=0.0, gain_db=start),
            EqPoint(position=0.65, gain_db=start),
            EqPoint(position=1.0, gain_db=end),
        ]
        return EqCurve(low=late, mid=ramp(-6.0), high=ramp(-2.0))

    # blend und double_drop: gleichmaessige Blende ueber alle Baender.
    even = ramp(-6.0)
    return EqCurve(low=even, mid=even, high=even)


def _abschnitte(
    track: TrackAnalysis,
    play_from: float,
    play_to: float,
    intro_ende: float | None,
    outro_einsatz: float | None,
    ratio: float,
    target_bpm: float,
) -> list[Streckabschnitt]:
    """Die Streckabschnitte eines Eintrags (Fund 3 der Abnahme).

    Der Kopf bis zum Intro-Ende laeuft mit dem lokalen Tempo des Intros ins
    Zieltempo, der Schwanz ab dem Outro-Einsatz mit dem des Outros, der Koerper
    dazwischen mit dem Verhaeltnis des ganzen Tracks. So liegen beide Seiten
    einer Naht im Zieltempo uebereinander, auch wenn der Song sein Tempo nicht
    haelt. Ohne lokale Raster, oder wenn der Track sich gar nicht angleichen
    laesst (dann wird der Uebergang ohnehin ein Schnitt), bleibt es bei einem
    Verhaeltnis - leere Liste.
    """
    lokal = track.lokale_raster
    if lokal is None or _is_unstretchable(track, ratio, target_bpm):
        return []

    stuecke: list[tuple[float, float]] = []
    von = play_from
    if intro_ende is not None and lokal.intro is not None and von < intro_ende < play_to:
        stuecke.append(
            (intro_ende, lokales_verhaeltnis(lokal.intro.als_beatgrid(), target_bpm, ratio))
        )
        von = intro_ende
    if outro_einsatz is not None and lokal.outro is not None and von < outro_einsatz < play_to:
        stuecke.append((outro_einsatz, ratio))
        stuecke.append(
            (play_to, lokales_verhaeltnis(lokal.outro.als_beatgrid(), target_bpm, ratio))
        )
    else:
        stuecke.append((play_to, ratio))

    if all(r == ratio for _bis, r in stuecke):
        return []
    return [Streckabschnitt(bis_s=bis, ratio=r) for bis, r in stuecke]


def stimmwechsel(overlap_s: float, target_bpm: float) -> Stimmwechsel | None:
    """Wo im Uebergang die Stimme wechselt (Plan 1c).

    Der Uebergang beginnt auf einem Downbeat, und beide Seiten laufen dort im
    Zieltempo (Fund 3). Ganze Takte ab dem Anfang sind also Taktgrenzen. Der
    Wechsel liegt auf der Taktgrenze naechst der Mitte, die Blenden dauern je
    einen Takt. Passen zwei ganze Takte nicht hinein, liegt er genau in der
    Mitte und die Blenden teilen sich den Uebergang.
    """
    if overlap_s <= 0:
        return None
    takt = BEATS_PER_BAR * 60.0 / target_bpm
    takte = overlap_s / takt
    if takte < 2.0 - 1e-6:
        mitte = overlap_s / 2
        return Stimmwechsel(bei_s=mitte, blende_s=mitte)
    # Zwischen dem ersten und dem vorletzten ganzen Takt, damit beide Blenden
    # in den Uebergang passen.
    hinten = math.floor(takte + 1e-6) - 1
    bei = min(max(1, round(takte / 2)), hinten)
    return Stimmwechsel(bei_s=bei * takt, blende_s=takt)


def build_mixplan(
    setlist: Setlist,
    analyses: list[TrackAnalysis],
    choices: list[TransitionChoice],
    profile: Profile,
) -> MixPlan:
    """Rechnet aus Reihenfolge und Technikwahl den vollstaendigen Mixplan."""
    by_id = {a.id: a for a in analyses}
    order = setlist.order
    target_bpm = profile.bpm.target

    ratios = {tid: _ratio(by_id[tid], target_bpm) for tid in order}
    # Einmal je Track gesnappt, danach ist dieser Wert das Outro - fuer die
    # Materialrechnung, fuer play_to_s und fuer die Position des Uebergangs.
    outros = {tid: _snapped_outro(by_id[tid]) for tid in order}
    # Wo das Intro fuer den Uebergang endet: mindestens 16 Takte, auch wenn
    # der Song kein leiseres Intro hat (Fund 2 der Abnahme, siehe
    # dsp.analyze.mischbereich).
    #
    # Fund 3: an beiden Enden gilt das lokale Raster, wenn es gemessen ist
    # (djset.raster). Das Intro-Ende sitzt dann auf einem lokalen Downbeat.
    in_grids = {tid: intro_raster(by_id[tid]) for tid in order}
    out_grids = {tid: outro_raster(by_id[tid]) for tid in order}
    intros = {}
    for tid in order:
        track = by_id[tid]
        ende = mischbereich(track).intro_end_s
        grid = in_grids[tid]
        if grid is not track.beatgrid:
            # Wie beim Outro: so viele lokale Takte, wie der Bereich in
            # globalen Takten hat - auch wenn der lokale Takt laenger ist.
            takte = _available_bars(
                ende - track.beatgrid.first_downbeat_s, track.beatgrid.bar_length_s
            )
            ende = max(
                snap_to_downbeat(ende, grid),
                grid.first_downbeat_s + takte * grid.bar_length_s,
            )
        intros[tid] = ende
    gains = _gains(order, by_id)
    breaks = break_seams(order, by_id, profile)

    # Zwei Korrekturen an dem, was das Modell gewaehlt hat.
    #
    # Technik: laesst sich einer der beiden Tracks nicht auf das Ziel-Tempo
    # ziehen, wird der Uebergang ein Schnitt - egal auf welcher Seite er steht.
    # Zwei Tempi mehr als sechs Prozent auseinander laufen nach wenigen Takten
    # hoerbar auseinander, und das gilt symmetrisch. Spec Abschnitt 7 nennt Cut
    # statt Blend ausdruecklich als Ausweg und setzt sich damit bewusst ueber
    # die Technikliste des Profils hinweg.
    #
    # Taktzahl: ein Uebergang kann nicht laenger sein als das Material, das
    # beide Seiten hergeben. Seit dem Mindest-Mischbereich sind das an jedem
    # Ende mindestens 16 Takte (bei sehr kurzen Tracks ein Drittel); laengere
    # gemessene Intros und Outros geben mehr her.
    effective: list[TransitionChoice] = []
    for index, choice in enumerate(choices):
        outgoing = by_id[order[index]]
        incoming = by_id[order[index + 1]]

        technique = choice.technique
        if _is_unstretchable(
            outgoing, ratios[outgoing.id], target_bpm
        ) or _is_unstretchable(incoming, ratios[incoming.id], target_bpm):
            technique = "cut"
        # Nachbedingung am Tonart-Bruch: nur cut oder filter_sweep. Der Prompt
        # und _check verlangen das schon vom Modell; hier haengt es nicht mehr
        # an dessen Antwort.
        if index in breaks and technique not in BREAK_TECHNIQUES:
            technique = "cut"

        out_bars = _available_bars(
            outgoing.duration_s - outros[outgoing.id],
            out_grids[outgoing.id].bar_length_s,
        )
        in_bars = _available_bars(
            intros[incoming.id] - in_grids[incoming.id].first_downbeat_s,
            in_grids[incoming.id].bar_length_s,
        )
        bars = max(1, min(choice.bars, out_bars, in_bars))

        effective.append(TransitionChoice(technique=technique, bars=bars))

    entries: list[MixEntry] = []
    for index, track_id in enumerate(order):
        track = by_id[track_id]
        first = index == 0
        last = index == len(order) - 1

        if first:
            play_from = track.beatgrid.first_downbeat_s
        else:
            grid = in_grids[track_id]
            bars_in = effective[index - 1].bars
            raw = intros[track_id] - bars_in * grid.bar_length_s
            play_from = snap_to_downbeat(max(0.0, raw), grid)

        if last:
            play_to = track.duration_s
        else:
            bars_out = effective[index].bars
            # outros[track_id] sitzt schon auf dem Raster, eine ganze Taktzahl
            # dazu ebenfalls - hier ist nichts mehr zu snappen.
            play_to = min(
                track.duration_s,
                outros[track_id] + bars_out * out_grids[track_id].bar_length_s,
            )

        entries.append(
            MixEntry(
                track_id=track_id,
                play_from_s=play_from,
                play_to_s=play_to,
                timestretch_ratio=ratios[track_id],
                gain_db=gains[track_id],
                abschnitte=_abschnitte(
                    track,
                    play_from,
                    play_to,
                    intros[track_id] if not first else None,
                    outros[track_id] if not last else None,
                    ratios[track_id],
                    target_bpm,
                ),
                stimme=profile.gesang,
            )
        )

    transitions: list[Transition] = []
    out_start = 0.0
    for index, choice in enumerate(effective):
        entry = entries[index]
        following = entries[index + 1]
        # Position in der Ausgabe, an der der Vorgaenger sein Outro erreicht.
        # Das Outro sitzt auf dem Raster des Vorgaengers (lokal, wenn
        # gemessen) - und der Uebergang faellt auf einen Downbeat des
        # Vorgaengers, auf dem der eingehende Track mit seinem eigenen
        # Downbeat aufsetzt. Quell- in Ausgabezeit rechnet nur MixEntry um.
        start_at = out_start + entry.ausgabe_s(outros[order[index]])

        # Die Dauer des Uebergangs, in Sekunden der AUSGABE - und ab hier die
        # einzige Quelle dafuer (Transition.overlap_s). Beide Seiten koennen
        # verschieden viel hergeben, sobald ein Track ausserhalb der
        # Stretch-Grenze ungestreckt bleibt. Deshalb der Schnitt: was der
        # Vorgaenger hinter seinem Outro noch klingt, und was der Nachfolger
        # vor seinem Intro-Ende an Kopf mitbringt. Der Ueberschuss der
        # laengeren Seite spielt fuer sich.
        out_tail_s = entry.ausgabe_dauer_s - entry.ausgabe_s(outros[order[index]])
        in_head_s = following.ausgabe_s(intros[order[index + 1]])
        # max(0.0, ...): ohne nutzbares Intro (intro_end_s == 0.0) kann der
        # Kopf rechnerisch negativ werden, wenn der erste Downbeat dahinter
        # liegt. Eine negative Dauer gibt es nicht - das ist eine Stossnaht,
        # und die beanstandet der Critic.
        overlap_s = max(0.0, min(out_tail_s, in_head_s))

        transitions.append(
            Transition(
                from_track=order[index],
                to_track=order[index + 1],
                start_at_s=start_at,
                overlap_s=overlap_s,
                bars=choice.bars,
                technique=choice.technique,
                eq_out=_eq_curve(choice.technique, outgoing=True),
                eq_in=_eq_curve(choice.technique, outgoing=False),
                stimmwechsel=stimmwechsel(overlap_s, target_bpm),
            )
        )
        out_start = start_at

    total = out_start + entries[-1].ausgabe_dauer_s

    return MixPlan(
        target_length_s=total,
        target_bpm=target_bpm,
        entries=entries,
        transitions=transitions,
    )


def _table(setlist: Setlist, by_id: dict[str, TrackAnalysis], profile: Profile) -> str:
    breaks = break_seams(setlist.order, by_id, profile)
    lines = []
    for index in range(len(setlist.order) - 1):
        a = by_id[setlist.order[index]]
        b = by_id[setlist.order[index + 1]]
        # Beide Seiten pruefen, nicht nur die eingehende: der Schnitt-Ausweg
        # greift symmetrisch (siehe build_mixplan).
        a_stretchable = MIN_RATIO <= profile.bpm.target / a.bpm.value <= MAX_RATIO
        b_stretchable = MIN_RATIO <= profile.bpm.target / b.bpm.value <= MAX_RATIO
        note = "" if (a_stretchable and b_stretchable) else "  [Tempi nicht angleichbar]"
        if index in breaks:
            note += "  [Tonart-Bruch: nur cut oder filter_sweep]"
        lines.append(
            f"{index + 1}. {a.id} ({a.bpm.value:.1f} BPM, {a.key.camelot}, "
            f"Energie {a.verdict.energy_level:.2f}) nach {b.id} "
            f"({b.bpm.value:.1f} BPM, {b.key.camelot}, "
            f"Energie {b.verdict.energy_level:.2f}){note}"
        )
    return "\n".join(lines)


def _check(
    answer: DesignerAnswer,
    wanted: int,
    profile: Profile,
    breaks: frozenset[int] = frozenset(),
) -> str | None:
    if len(answer.choices) != wanted:
        return f"Du hast {len(answer.choices)} Uebergaenge geliefert, gebraucht werden {wanted}."
    for index, choice in enumerate(answer.choices):
        # Am Tonart-Bruch gilt nur BREAK_TECHNIQUES, sonst das Profil.
        allowed = (
            set(BREAK_TECHNIQUES)
            if index in breaks
            else set(profile.transition.techniques)
        )
        if choice.technique not in allowed:
            return (
                f"Uebergang {index + 1} benutzt {choice.technique}, erlaubt sind nur: "
                f"{', '.join(sorted(allowed))}."
            )
        if not (
            profile.transition.bars_min <= choice.bars <= profile.transition.bars_max
        ):
            return (
                f"Uebergang {index + 1} hat {choice.bars} Takte, erlaubt sind "
                f"{profile.transition.bars_min} bis {profile.transition.bars_max}."
            )
    return None


def design(
    client: AgentClient,
    setlist: Setlist,
    analyses: list[TrackAnalysis],
    profile: Profile,
    budget: Budget,
) -> MixPlan:
    """Waehlt die Uebergaenge und rechnet daraus den Mixplan."""
    by_id = {a.id: a for a in analyses}
    wanted = len(setlist.order) - 1
    breaks = frozenset(break_seams(setlist.order, by_id, profile))
    prompt = PROMPT.format(
        genre=profile.genre,
        bpm_target=profile.bpm.target,
        techniques=", ".join(profile.transition.techniques),
        bars_min=profile.transition.bars_min,
        bars_max=profile.transition.bars_max,
        table=_table(setlist, by_id, profile),
        count=wanted,
        break_hint=BREAK_HINT if breaks else "",
    )

    current = prompt
    answer = run_agent(client, current, DesignerAnswer, budget, retries=1)
    for _round in range(MAX_ROUNDS - 1):
        problem = _check(answer, wanted, profile, breaks)
        if problem is None:
            break
        current = f"{prompt}\n\nDein letzter Versuch war unbrauchbar: {problem}"
        answer = run_agent(client, current, DesignerAnswer, budget, retries=1)
    else:
        # Nicht stillschweigend durchwinken: eine Technik, die das Profil gar
        # nicht erlaubt, oder eine Taktzahl ausserhalb des Korridors kaeme sonst
        # bis in den Renderer - der Mixplan waere schemagueltig und trotzdem
        # nicht das, was das Profil verlangt.
        problem = _check(answer, wanted, profile, breaks)
        if problem is not None:
            raise StageRulesViolated(
                f"Der Transition-Designer lieferte nach {MAX_ROUNDS} Versuchen "
                f"keine regelkonformen Uebergaenge: {problem}"
            )

    return build_mixplan(setlist, analyses, answer.choices, profile)

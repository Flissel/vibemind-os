"""Stufe 6: entwirft jeden Uebergang und rechnet daraus den Mixplan.

Aufgabenteilung: das Modell waehlt pro Uebergang Technik und Taktzahl - das ist
Geschmack und Genrekenntnis. Alles andere rechnet Code: Cue-Punkte auf
Downbeats, Stretch-Verhaeltnisse gegen das Ziel-Tempo, Positionen in der
Ausgabe-Timeline, EQ-Kurven aus Vorlagen je Technik. Damit ist der aufwendigste
Teil der Stufe deterministisch und ohne Modell testbar - und der Mixplan
erfuellt die Determinismus-Grenze der Spec, ohne dass ein Modell Zahlen erfindet.

Zeitrechnung: timestretch_ratio ist Ziel-Tempo geteilt durch Quell-Tempo, die
Ausgabedauer eines Abschnitts also Quelldauer geteilt durch das Verhaeltnis.
"""

from pydantic import BaseModel, Field

from djset.agents.base import (
    AgentClient,
    Budget,
    StageRulesViolated,
    run_agent,
)
from djset.contracts import (
    BeatGrid,
    EqCurve,
    EqPoint,
    MixEntry,
    MixPlan,
    Profile,
    Setlist,
    Technique,
    TrackAnalysis,
    Transition,
)

MAX_ROUNDS = 3

# Die Stretch-Grenze aus dem Spec, hier als Rechengroesse. Der Vertrag
# MixEntry.timestretch_ratio erzwingt dieselben Werte.
MIN_RATIO = 0.94
MAX_RATIO = 1.06


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
- Steht bei einem Uebergang "Stretch nicht moeglich", muss die Technik cut
  sein: die Tempi liegen zu weit auseinander, um sie anzugleichen.

Antworte ausschliesslich mit dem geforderten JSON."""


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
        # Harter Schnitt: die Umschaltung passiert in der letzten Zehntel.
        hard = [
            EqPoint(position=0.0, gain_db=start),
            EqPoint(position=0.9, gain_db=start),
            EqPoint(position=1.0, gain_db=end),
        ]
        return EqCurve(low=hard, mid=hard, high=hard)

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

    # Ein Track, der nicht angeglichen werden kann, bekommt einen harten
    # Schnitt in sich hinein - Blenden wuerde nach Takt zwei auseinanderlaufen.
    effective: list[TransitionChoice] = []
    for index, choice in enumerate(choices):
        incoming = order[index + 1]
        if ratios[incoming] == 1.0 and by_id[incoming].bpm.value != target_bpm:
            effective.append(TransitionChoice(technique="cut", bars=choice.bars))
        else:
            effective.append(choice)

    entries: list[MixEntry] = []
    for index, track_id in enumerate(order):
        track = by_id[track_id]
        grid = track.beatgrid
        first = index == 0
        last = index == len(order) - 1

        if first:
            play_from = grid.first_downbeat_s
        else:
            bars_in = effective[index - 1].bars
            raw = track.mix_regions.intro_end_s - bars_in * grid.bar_length_s
            play_from = snap_to_downbeat(max(0.0, raw), grid)

        if last:
            play_to = track.duration_s
        else:
            bars_out = effective[index].bars
            raw = track.mix_regions.outro_start_s + bars_out * grid.bar_length_s
            play_to = min(track.duration_s, snap_to_downbeat(raw, grid))

        entries.append(
            MixEntry(
                track_id=track_id,
                play_from_s=play_from,
                play_to_s=play_to,
                timestretch_ratio=ratios[track_id],
                gain_db=0.0,
            )
        )

    transitions: list[Transition] = []
    out_start = 0.0
    for index, choice in enumerate(effective):
        entry = entries[index]
        track = by_id[order[index]]
        # Position in der Ausgabe, an der der Vorgaenger sein Outro erreicht.
        into_outro = track.mix_regions.outro_start_s - entry.play_from_s
        start_at = out_start + into_outro / entry.timestretch_ratio
        transitions.append(
            Transition(
                from_track=order[index],
                to_track=order[index + 1],
                start_at_s=start_at,
                bars=choice.bars,
                technique=choice.technique,
                eq_out=_eq_curve(choice.technique, outgoing=True),
                eq_in=_eq_curve(choice.technique, outgoing=False),
            )
        )
        out_start = start_at

    last_entry = entries[-1]
    total = out_start + (
        last_entry.play_to_s - last_entry.play_from_s
    ) / last_entry.timestretch_ratio

    return MixPlan(target_length_s=total, entries=entries, transitions=transitions)


def _table(setlist: Setlist, by_id: dict[str, TrackAnalysis], profile: Profile) -> str:
    lines = []
    for index in range(len(setlist.order) - 1):
        a = by_id[setlist.order[index]]
        b = by_id[setlist.order[index + 1]]
        stretchable = MIN_RATIO <= profile.bpm.target / b.bpm.value <= MAX_RATIO
        note = "" if stretchable else "  [Stretch nicht moeglich]"
        lines.append(
            f"{index + 1}. {a.id} ({a.bpm.value:.1f} BPM, {a.key.camelot}, "
            f"Energie {a.verdict.energy_level:.2f}) nach {b.id} "
            f"({b.bpm.value:.1f} BPM, {b.key.camelot}, "
            f"Energie {b.verdict.energy_level:.2f}){note}"
        )
    return "\n".join(lines)


def _check(answer: DesignerAnswer, wanted: int, profile: Profile) -> str | None:
    if len(answer.choices) != wanted:
        return f"Du hast {len(answer.choices)} Uebergaenge geliefert, gebraucht werden {wanted}."
    allowed = set(profile.transition.techniques)
    for index, choice in enumerate(answer.choices):
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
    prompt = PROMPT.format(
        genre=profile.genre,
        bpm_target=profile.bpm.target,
        techniques=", ".join(profile.transition.techniques),
        bars_min=profile.transition.bars_min,
        bars_max=profile.transition.bars_max,
        table=_table(setlist, by_id, profile),
        count=wanted,
    )

    current = prompt
    answer = run_agent(client, current, DesignerAnswer, budget, retries=1)
    for _round in range(MAX_ROUNDS - 1):
        problem = _check(answer, wanted, profile)
        if problem is None:
            break
        current = f"{prompt}\n\nDein letzter Versuch war unbrauchbar: {problem}"
        answer = run_agent(client, current, DesignerAnswer, budget, retries=1)
    else:
        # Nicht stillschweigend durchwinken: eine Technik, die das Profil gar
        # nicht erlaubt, oder eine Taktzahl ausserhalb des Korridors kaeme sonst
        # bis in den Renderer - der Mixplan waere schemagueltig und trotzdem
        # nicht das, was das Profil verlangt.
        problem = _check(answer, wanted, profile)
        if problem is not None:
            raise StageRulesViolated(
                f"Der Transition-Designer lieferte nach {MAX_ROUNDS} Versuchen "
                f"keine regelkonformen Uebergaenge: {problem}"
            )

    return build_mixplan(setlist, analyses, answer.choices, profile)

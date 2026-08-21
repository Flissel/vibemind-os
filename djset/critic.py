"""Stufe 7: das Gate vor dem Rendern.

Bewusst kein Agent. Die Spec listet vier Pruefungen - haelt die Energiekurve,
liegen alle Stretches in der Toleranz, sitzen die Cue-Punkte auf Downbeats,
stimmt die Gesamtlaenge - und alle vier sind Arithmetik ueber den Mixplan. Ein
Modell dafuer zu fragen waere teurer, langsamer und weniger verlaesslich als
eine Funktion. Die Spec nennt den Critic eine Stufe, keinen Agenten.

Dazu kommen zwei Pruefungen ueber die Nahtstelle statt ueber einen einzelnen
Track. Sie sind nicht neben der Spec, sondern deren eigentlicher Kern: "sitzen
die Cue-Punkte auf Downbeats" hat eine trackinterne und eine trackuebergreifende
Haelfte, und nur die zweite entscheidet, ob an der Naht der Beat durchlaeuft
(Erfolgskriterium 4). Die trackinterne Haelfte allein garantiert der Designer
schon durch seine Konstruktion - eine Pruefung, die nie ausschlagen kann,
prueft nichts.

Die Beanstandungen sind so formuliert, dass Stufe 5 damit etwas anfangen kann:
sie nennen den Track und das konkrete Problem, nicht nur die Regel. Ob Stufe 5
ueberhaupt etwas daran aendern kann, steht in Complaint.kind.
"""

from pydantic import BaseModel

from djset.contracts import (
    MAX_RATIO,
    MIN_RATIO,
    Complaint,
    ComplaintKind,
    MixPlan,
    Profile,
    TrackAnalysis,
)

# Erfolgskriterium 3 der Spec: Ziellaenge plus/minus 5 Prozent.
LENGTH_TOLERANCE = 0.05

# Wie weit ein Track von der Zielenergie des Bogens abweichen darf. Grosszuegig,
# weil der Bogen eine Absicht beschreibt und kein Messziel - er soll die grobe
# Dramaturgie erzwingen, nicht jeden Track festnageln.
ARC_TOLERANCE = 0.35

# Ein Cue-Punkt gilt als auf dem Downbeat, wenn er weniger als das davon
# entfernt liegt. Ein Zwanzigstel Takt ist unterhalb der Hoerschwelle.
DOWNBEAT_TOLERANCE_BARS = 0.05

# Weniger Ueberlappung als das ist kein Uebergang mehr, sondern ein Stoss:
# der eine Track hoert auf, der naechste faengt an. Das kann so gewollt sein
# (technique cut), aber nicht, wenn der Plan 16 Takte Blende deklariert.
MIN_OVERLAP_BARS = 1.0


class Critique(BaseModel):
    ok: bool
    complaints: list[Complaint]

    @property
    def texts(self) -> tuple[str, ...]:
        """Alle Beanstandungen als Text, fuer Ausgabe und Report."""
        return tuple(complaint.text for complaint in self.complaints)

    @property
    def reorderable(self) -> tuple[str, ...]:
        """Nur die Beanstandungen, an denen Stufe 5 etwas aendern kann."""
        return tuple(c.text for c in self.complaints if c.reorderable)


def _arc_target(profile: Profile, position: float) -> float:
    """Die Zielenergie des Bogens an einer Position, linear interpoliert."""
    points = profile.energy_arc
    if position <= points[0].position:
        return points[0].energy
    for left, right in zip(points, points[1:]):
        if position <= right.position:
            span = right.position - left.position
            if span <= 0:
                return right.energy
            share = (position - left.position) / span
            return left.energy + share * (right.energy - left.energy)
    return points[-1].energy


def critique(
    plan: MixPlan, analyses: list[TrackAnalysis], profile: Profile
) -> Critique:
    """Prueft den Mixplan gegen die harten Kriterien der Spec."""
    by_id = {a.id: a for a in analyses}
    complaints: list[Complaint] = []

    def complain(kind: ComplaintKind, text: str) -> None:
        complaints.append(Complaint(kind=kind, text=text))

    # 1. Gesamtlaenge
    target_s = profile.length_minutes * 60.0
    low = target_s * (1.0 - LENGTH_TOLERANCE)
    high = target_s * (1.0 + LENGTH_TOLERANCE)
    if not low <= plan.target_length_s <= high:
        complain(
            "length",
            f"Laenge verfehlt: {plan.target_length_s / 60:.1f} Minuten statt "
            f"{profile.length_minutes:.0f} plus/minus "
            f"{LENGTH_TOLERANCE * 100:.0f} Prozent.",
        )

    # 2. Stretch-Grenzen
    for entry in plan.entries:
        if not MIN_RATIO <= entry.timestretch_ratio <= MAX_RATIO:
            complain(
                "stretch",
                f"{entry.track_id} braucht einen Stretch von "
                f"{entry.timestretch_ratio:.3f}, erlaubt sind {MIN_RATIO} bis "
                f"{MAX_RATIO}.",
            )

    # 3. Cue-Punkte auf dem eigenen Raster
    for entry in plan.entries:
        track = by_id.get(entry.track_id)
        if track is None:
            complain(
                "unknown_track",
                f"{entry.track_id} steht im Plan, aber es gibt keine Analyse dazu.",
            )
            continue
        grid = track.beatgrid
        offset = (entry.play_from_s - grid.first_downbeat_s) / grid.bar_length_s
        if abs(offset - round(offset)) > DOWNBEAT_TOLERANCE_BARS:
            complain(
                "downbeat",
                f"Der Einstieg von {entry.track_id} bei {entry.play_from_s:.2f} s "
                f"sitzt nicht auf einem Downbeat.",
            )

    # 4. Energiekurve
    count = len(plan.entries)
    for index, entry in enumerate(plan.entries):
        track = by_id.get(entry.track_id)
        if track is None:
            continue
        position = index / max(1, count - 1)
        target = _arc_target(profile, position)
        actual = track.verdict.energy_level
        if abs(actual - target) > ARC_TOLERANCE:
            complain(
                "energy_arc",
                f"Energie verfehlt an Position {index + 1}: {entry.track_id} liegt "
                f"bei {actual:.2f}, der Bogen verlangt dort etwa {target:.2f}.",
            )

    # 5. und 6. Die Nahtstelle: sitzt der Uebergang auf einem Downbeat des
    # Vorgaengers, und liegt an ihm ueberhaupt genug uebereinander?
    #
    # Der eingehende Track startet bei start_at_s mit seinem eigenen Downbeat -
    # das garantiert Pruefung 3. Ob dort auch der Vorgaenger auf einem Downbeat
    # steht, ist eine andere Frage, und erst ihre Antwort entscheidet, ob der
    # Beat ueber die Naht durchlaeuft. Gerechnet wird in Takten der AUSGABE,
    # also Quelltaktlaenge geteilt durch das Stretch-Verhaeltnis.
    out_start = 0.0
    for index, transition in enumerate(plan.transitions):
        entry = plan.entries[index]
        track = by_id.get(entry.track_id)
        if track is None:
            continue
        out_bar_s = track.beatgrid.bar_length_s / entry.timestretch_ratio
        elapsed_bars = (transition.start_at_s - out_start) / out_bar_s
        drift = elapsed_bars - round(elapsed_bars)
        if abs(drift) > DOWNBEAT_TOLERANCE_BARS:
            complain(
                "phase",
                f"Der Uebergang von {transition.from_track} nach "
                f"{transition.to_track} bei {transition.start_at_s:.2f} s sitzt "
                f"{abs(drift) * out_bar_s * 1000:.0f} ms neben dem Downbeat von "
                f"{transition.from_track}.",
            )

        ends_at = (
            out_start
            + (entry.play_to_s - entry.play_from_s) / entry.timestretch_ratio
        )
        overlap_bars = (ends_at - transition.start_at_s) / out_bar_s
        if overlap_bars < MIN_OVERLAP_BARS - DOWNBEAT_TOLERANCE_BARS:
            complain(
                "overlap",
                f"Der Uebergang von {transition.from_track} nach "
                f"{transition.to_track} deklariert {transition.bars} Takte, "
                f"tatsaechlich liegen nur {overlap_bars:.2f} Takte "
                f"({(ends_at - transition.start_at_s):.4f} s) uebereinander - "
                f"{transition.from_track} gibt kein Outro her.",
            )
        out_start = transition.start_at_s

    return Critique(ok=not complaints, complaints=complaints)

"""Stufe 7: das Gate vor dem Rendern.

Bewusst kein Agent. Die Spec listet vier Pruefungen - haelt die Energiekurve,
liegen alle Stretches in der Toleranz, sitzen die Cue-Punkte auf Downbeats,
stimmt die Gesamtlaenge - und alle vier sind Arithmetik ueber den Mixplan. Ein
Modell dafuer zu fragen waere teurer, langsamer und weniger verlaesslich als
eine Funktion. Die Spec nennt den Critic eine Stufe, keinen Agenten.

Die Beanstandungen sind so formuliert, dass Stufe 5 damit etwas anfangen kann:
sie nennen den Track und das konkrete Problem, nicht nur die Regel.
"""

from pydantic import BaseModel

from djset.contracts import MAX_RATIO, MIN_RATIO, MixPlan, Profile, TrackAnalysis

# Erfolgskriterium 3 der Spec: Ziellaenge plus/minus 5 Prozent.
LENGTH_TOLERANCE = 0.05

# Wie weit ein Track von der Zielenergie des Bogens abweichen darf. Grosszuegig,
# weil der Bogen eine Absicht beschreibt und kein Messziel - er soll die grobe
# Dramaturgie erzwingen, nicht jeden Track festnageln.
ARC_TOLERANCE = 0.35

# Ein Cue-Punkt gilt als auf dem Downbeat, wenn er weniger als das davon
# entfernt liegt. Ein Zwanzigstel Takt ist unterhalb der Hoerschwelle.
DOWNBEAT_TOLERANCE_BARS = 0.05


class Critique(BaseModel):
    ok: bool
    complaints: list[str]


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
    """Prueft den Mixplan gegen die vier harten Kriterien der Spec."""
    by_id = {a.id: a for a in analyses}
    complaints: list[str] = []

    # 1. Gesamtlaenge
    target_s = profile.length_minutes * 60.0
    low = target_s * (1.0 - LENGTH_TOLERANCE)
    high = target_s * (1.0 + LENGTH_TOLERANCE)
    if not low <= plan.target_length_s <= high:
        complaints.append(
            f"Laenge verfehlt: {plan.target_length_s / 60:.1f} Minuten statt "
            f"{profile.length_minutes:.0f} plus/minus "
            f"{LENGTH_TOLERANCE * 100:.0f} Prozent."
        )

    # 2. Stretch-Grenzen
    for entry in plan.entries:
        if not MIN_RATIO <= entry.timestretch_ratio <= MAX_RATIO:
            complaints.append(
                f"{entry.track_id} braucht einen Stretch von "
                f"{entry.timestretch_ratio:.3f}, erlaubt sind {MIN_RATIO} bis "
                f"{MAX_RATIO}."
            )

    # 3. Cue-Punkte auf Downbeats
    for entry in plan.entries:
        track = by_id.get(entry.track_id)
        if track is None:
            complaints.append(f"{entry.track_id} steht im Plan, aber es gibt keine Analyse dazu.")
            continue
        grid = track.beatgrid
        offset = (entry.play_from_s - grid.first_downbeat_s) / grid.bar_length_s
        if abs(offset - round(offset)) > DOWNBEAT_TOLERANCE_BARS:
            complaints.append(
                f"Der Einstieg von {entry.track_id} bei {entry.play_from_s:.2f} s "
                f"sitzt nicht auf einem Downbeat."
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
            complaints.append(
                f"Energie verfehlt an Position {index + 1}: {entry.track_id} liegt "
                f"bei {actual:.2f}, der Bogen verlangt dort etwa {target:.2f}."
            )

    return Critique(ok=not complaints, complaints=complaints)

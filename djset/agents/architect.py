"""Stufe 5: legt die Reihenfolge der Tracks fest.

Drei harte Regeln aus dem Spec, alle im Code geprueft: die Reihenfolge benutzt
nur Tracks aus der Shortlist, je zwei aufeinanderfolgende passen harmonisch
zusammen, und keine zwei zu aehnlichen Tracks stehen nebeneinander. Was das
Modell liefert, wird gegen diese Regeln gehalten und bei Verstoss mit der
konkreten Beanstandung neu gefragt - ein blosses "nochmal" verschenkt den Zug.

Der Geschmack bleibt beim Modell: ob die Reihenfolge schoen ist, prueft hier
niemand. Das ist kein Testfall.
"""

from pydantic import BaseModel, Field

from djset.agents.base import (
    AgentClient,
    Budget,
    StageRulesViolated,
    run_agent,
)
from djset.camelot import is_compatible
from djset.contracts import Profile, Setlist, Shortlist, TrackAnalysis

MAX_ROUNDS = 3

# Zu aehnlich im Sinne des Spec: gleiche Camelot-Tonart UND BPM-Differenz unter
# 1 UND Energiedifferenz unter 0,1. Alle drei zusammen ergeben einen bequemen
# Uebergang und Stillstand im Set.
SIMILAR_BPM = 1.0
SIMILAR_ENERGY = 0.1


class ArchitectAnswer(BaseModel):
    order: list[str] = Field(min_length=1)
    note: str = Field(min_length=1)


PROMPT = """Du baust die Reihenfolge eines DJ-Sets.

Das Set: {genre}, harmonische Strenge {strictness}. Der Energiebogen laeuft ueber
diese Stuetzpunkte aus Position im Set und Zielenergie: {arc}.

Diese Tracks stehen zur Verfuegung, benutze genau sie alle:

{table}

Regeln, die eingehalten werden muessen:
1. Setze die Energiekurve des Bogens um: der erste Track liegt nahe der
   Startenergie, die Mitte nahe dem Hochpunkt, das Ende nahe der Schlussenergie.
2. Je zwei aufeinanderfolgende Tracks muessen harmonisch zusammenpassen. Bei
   Strenge strict heisst das: gleiche Camelot-Zahl, oder eine Position weiter
   auf dem Rad. Bei loose sind zwei Positionen erlaubt.
3. Keine zwei zu aehnlichen Tracks nebeneinander. Zu aehnlich heisst: gleiche
   Tonart UND weniger als {similar_bpm} BPM Unterschied UND weniger als
   {similar_energy} Energieunterschied. Alle drei zusammen ergeben einen
   bequemen Uebergang und Stillstand im Set.

{complaints}Antworte ausschliesslich mit dem geforderten JSON."""


def _table(analyses: list[TrackAnalysis], accepted: set[str]) -> str:
    return "\n".join(
        f"- {a.id}: {a.bpm.value:.1f} BPM, {a.key.camelot}, "
        f"Energie {a.verdict.energy_level:.2f}, {a.duration_s:.0f} s"
        for a in analyses
        if a.id in accepted
    )


def _check(
    order: list[str],
    accepted: set[str],
    by_id: dict[str, TrackAnalysis],
    profile: Profile,
) -> str | None:
    """Haelt die Reihenfolge gegen die drei harten Regeln."""
    used = set(order)
    unknown = used - accepted
    if unknown:
        return f"Diese IDs stehen nicht in der Shortlist: {', '.join(sorted(unknown))}."
    missing = accepted - used
    if missing:
        return f"Diese Tracks fehlen in der Reihenfolge: {', '.join(sorted(missing))}."
    if len(used) != len(order):
        return "Eine ID kommt mehrfach vor."

    for first_id, second_id in zip(order, order[1:]):
        first, second = by_id[first_id], by_id[second_id]
        if not is_compatible(
            first.key.camelot, second.key.camelot, profile.harmonic_strictness
        ):
            return (
                f"{first_id} ({first.key.camelot}) und {second_id} "
                f"({second.key.camelot}) passen harmonisch nicht zusammen."
            )
        same_key = first.key.camelot == second.key.camelot
        close_bpm = abs(first.bpm.value - second.bpm.value) < SIMILAR_BPM
        close_energy = (
            abs(first.verdict.energy_level - second.verdict.energy_level)
            < SIMILAR_ENERGY
        )
        if same_key and close_bpm and close_energy:
            return (
                f"{first_id} und {second_id} sind zu aehnlich: gleiche Tonart, "
                f"fast gleiches Tempo, fast gleiche Energie."
            )
    return None


def arrange(
    client: AgentClient,
    shortlist: Shortlist,
    analyses: list[TrackAnalysis],
    profile: Profile,
    budget: Budget,
    complaints: tuple[str, ...] = (),
) -> Setlist:
    """Legt die Reihenfolge fest und haelt sie gegen die harten Regeln."""
    accepted = set(shortlist.accepted)
    by_id = {a.id: a for a in analyses if a.id in accepted}
    arc = ", ".join(f"({p.position:.2f}, {p.energy:.2f})" for p in profile.energy_arc)

    complaint_block = ""
    if complaints:
        listed = "\n".join(f"- {c}" for c in complaints)
        complaint_block = (
            f"Ein frueherer Entwurf wurde beanstandet. Beheb das:\n{listed}\n\n"
        )

    prompt = PROMPT.format(
        genre=profile.genre,
        strictness=profile.harmonic_strictness,
        arc=arc,
        table=_table(analyses, accepted),
        similar_bpm=SIMILAR_BPM,
        similar_energy=SIMILAR_ENERGY,
        complaints=complaint_block,
    )

    current = prompt
    answer = run_agent(client, current, ArchitectAnswer, budget, retries=1)
    for _round in range(MAX_ROUNDS - 1):
        problem = _check(answer.order, accepted, by_id, profile)
        if problem is None:
            break
        current = f"{prompt}\n\nDein letzter Versuch war unbrauchbar: {problem}"
        answer = run_agent(client, current, ArchitectAnswer, budget, retries=1)
    else:
        # Nach der letzten Runde nicht stillschweigend durchwinken: eine
        # harmonisch gebrochene Reihenfolge flösse sonst bis in den Renderer,
        # und dort faellt sie niemandem mehr auf - der Mixplan waere
        # schemagueltig und trotzdem unbrauchbar.
        problem = _check(answer.order, accepted, by_id, profile)
        if problem is not None:
            raise StageRulesViolated(
                f"Der Architect lieferte nach {MAX_ROUNDS} Versuchen keine "
                f"regelkonforme Reihenfolge: {problem}"
            )

    return Setlist(order=answer.order, note=answer.note)

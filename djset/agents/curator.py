"""Stufe 4: sortiert aus, was nicht ins Set passt - jede Ablehnung begruendet.

Unlesbare Dateien laufen an dem Modell vorbei: dass eine Datei kaputt ist, ist
kein Urteilsfall, sondern ein Fakt. Sie landen trotzdem als Ablehnung in der
Shortlist, damit im Report nachvollziehbar bleibt, warum sie fehlen.
"""

from pydantic import BaseModel, Field

from djset.agents.base import (
    AgentClient,
    Budget,
    StageRulesViolated,
    run_agent,
)
from djset.contracts import (
    MIN_TRACKS,
    Profile,
    Rejection,
    Shortlist,
    TrackAnalysis,
    UnreadableTrack,
)

MAX_ROUNDS = 3


class NotEnoughTracks(RuntimeError):
    """Nach dem Aussortieren bleiben weniger als MIN_TRACKS uebrig.

    Bewusst getrennt von StageRulesViolated: zu wenig brauchbares Material ist
    ein Befund ueber die Musik, keine Formfehler des Modells. Der Nutzer muss
    darauf anders reagieren - mehr Tracks generieren statt einen Bug melden.
    """


class CuratorAnswer(BaseModel):
    accepted: list[str]
    rejected: list[Rejection]
    note: str = Field(min_length=1)


PROMPT = """Du stellst das Material fuer ein DJ-Set zusammen und sortierst aus,
was nicht passt.

Das Set: {genre}, Ziel-BPM {bpm_target}, erlaubt {bpm_min} bis {bpm_max},
harmonische Strenge {strictness}. Gebraucht werden {needed} Tracks; es liegen
mehr vor, damit du waehlerisch sein kannst.

Die analysierten Tracks:

{table}

Sortiere aus, was nicht taugt: falsche BPM-Familie, defekte Struktur (kein
brauchbares Intro oder Outro), zu kurz fuer einen Uebergang, am Genre vorbei,
oder ein Urteil des Analysten, das dagegen spricht.

Regeln:
- Jede track_id kommt genau einmal vor, entweder in accepted oder in rejected.
- Erfinde keine IDs; benutze nur die aus der Tabelle.
- Jede Ablehnung braucht eine konkrete Begruendung in einem Satz.
- Nimm hoechstens {needed} Tracks an. Sind weniger brauchbar, nimm die
  brauchbaren und begruende den Rest.

Antworte ausschliesslich mit dem geforderten JSON."""


def _table(analyses: list[TrackAnalysis]) -> str:
    lines = []
    for a in analyses:
        lines.append(
            f"- {a.id}: {a.bpm.value:.1f} BPM (Raster {a.beatgrid.confidence:.2f}), "
            f"{a.key.camelot}, Energie {a.verdict.energy_level:.2f}, "
            f"{a.duration_s:.0f} s, Intro bis {a.mix_regions.intro_end_s:.0f} s, "
            f"Outro ab {a.mix_regions.outro_start_s:.0f} s, "
            f"brauchbar {a.verdict.usable}, Analyst: {a.verdict.notes}"
        )
    return "\n".join(lines)


def _check(answer: CuratorAnswer, known: set[str], needed: int) -> str | None:
    """Prueft die Antwort gegen das, was es tatsaechlich gibt."""
    named = set(answer.accepted) | {r.track_id for r in answer.rejected}
    invented = named - known
    if invented:
        return f"Diese IDs gibt es nicht: {', '.join(sorted(invented))}."
    missing = known - named
    if missing:
        return f"Diese Tracks fehlen in deiner Antwort: {', '.join(sorted(missing))}."
    both = set(answer.accepted) & {r.track_id for r in answer.rejected}
    if both:
        return f"Diese IDs stehen doppelt: {', '.join(sorted(both))}."
    if len(set(answer.accepted)) != len(answer.accepted):
        return "In accepted steht eine ID mehrfach."
    if len(answer.accepted) > needed:
        return f"Du hast {len(answer.accepted)} Tracks angenommen, erlaubt sind {needed}."
    return None


def curate(
    client: AgentClient,
    analyses: list[TrackAnalysis],
    unreadable: list[UnreadableTrack],
    profile: Profile,
    budget: Budget,
) -> Shortlist:
    """Waehlt aus den analysierten Tracks die aus, mit denen gebaut wird."""
    needed = profile.track_count.needed
    known = {a.id for a in analyses}
    prompt = PROMPT.format(
        genre=profile.genre,
        bpm_target=profile.bpm.target,
        bpm_min=profile.bpm.min,
        bpm_max=profile.bpm.max,
        strictness=profile.harmonic_strictness,
        needed=needed,
        table=_table(analyses),
    )

    current = prompt
    answer = run_agent(client, current, CuratorAnswer, budget, retries=1)
    for _round in range(MAX_ROUNDS - 1):
        problem = _check(answer, known, needed)
        if problem is None:
            break
        current = f"{prompt}\n\nDein letzter Versuch war unbrauchbar: {problem}"
        answer = run_agent(client, current, CuratorAnswer, budget, retries=1)
    else:
        problem = _check(answer, known, needed)
        if problem is not None:
            raise StageRulesViolated(
                f"Der Curator lieferte nach {MAX_ROUNDS} Versuchen keine "
                f"regelkonforme Auswahl: {problem}"
            )

    rejected = list(answer.rejected) + [
        Rejection(track_id=u.id, reason=f"nicht lesbar: {u.error}") for u in unreadable
    ]

    if len(answer.accepted) < MIN_TRACKS:
        raise NotEnoughTracks(
            f"Nach dem Aussortieren bleiben {len(answer.accepted)} Tracks, "
            f"mindestens {MIN_TRACKS} werden gebraucht. Generiere mehr Material "
            f"bei Suno und lege es in watch ab."
        )

    shortened = len(answer.accepted) < needed
    note = answer.note
    if shortened:
        note = (
            f"{note} Das Set wird gekuerzt: {len(answer.accepted)} statt "
            f"{needed} Tracks."
        )

    return Shortlist(
        accepted=answer.accepted,
        rejected=rejected,
        shortened=shortened,
        note=note,
    )

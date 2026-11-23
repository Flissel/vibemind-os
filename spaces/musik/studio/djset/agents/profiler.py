"""Agent 1 - leitet aus einem freien Genre-Namen das Regelwerk des Laufs ab."""

import math

from djset.agents.base import AgentClient, Budget, run_agent
from djset.contracts import Profile, TrackCount

OVERSHOOT_FACTOR = 1.5

PROMPT = """Du bist ein erfahrener DJ und kennst die Konventionen elektronischer Genres.

Erstelle ein Regelwerk fuer ein DJ-Set im Genre: {genre}
Zielllaenge des Sets: {length_minutes:.0f} Minuten

Lege fest:
- bpm: uebliches Ziel-Tempo des Genres mit brauchbarer Toleranz nach unten und oben
- transition: uebliche Uebergangslaenge in Takten und die Techniken, die in
  diesem Genre gebraeuchlich sind. Erlaubt sind: blend, eq_swap, filter_sweep,
  cut, double_drop
- harmonic_strictness: "strict" wenn das Genre harmonisches Mixen verlangt,
  "loose" wenn Tonart nachrangig ist
- energy_arc: Stuetzpunkte der Energiekurve ueber das Set, aufsteigend nach
  position von 0.0 bis 1.0, jeweils mit Zielenergie 0.0 bis 1.0
- track_count.needed: wie viele Tracks fuer diese Laenge noetig sind, ausgehend
  von der ueblichen Tracklaenge des Genres
- style_vocab: Stilbegriffe, die Suno versteht und die dieses Genre treffen
- structure: geforderte Mindestlaenge von Intro und Outro in Takten

Die Felder genre, length_minutes und track_count.request werden nach deiner
Antwort ueberschrieben. Trage dort trotzdem schemagueltige Werte ein - also
einen nichtleeren Genrenamen, eine positive Minutenzahl und ein request
mindestens so gross wie needed. Ungueltige Platzhalter wie "" oder 0 lassen
deine Antwort scheitern, obwohl der Wert ohnehin verworfen wird.

Antworte ausschliesslich mit JSON nach dem vorgegebenen Schema."""


def build_profile(
    client: AgentClient, genre: str, length_minutes: float, budget: Budget
) -> Profile:
    """Fragt das Modell nach einem Entwurf und setzt die harten Werte selbst."""
    draft = run_agent(
        client,
        PROMPT.format(genre=genre, length_minutes=length_minutes),
        Profile,
        budget,
    )

    # Die Untergrenze von drei Tracks erzwingt bereits der Vertrag, hier bleibt
    # nur der Overshoot - und der ist Rechnung, keine Meinung des Modells.
    needed = draft.track_count.needed
    request = math.ceil(needed * OVERSHOOT_FACTOR)

    # Bewusst ueber model_validate statt model_copy(update=...): letzteres
    # validiert in Pydantic v2 nicht, womit ausgerechnet die vom Code gesetzten
    # Felder an den Vertragsconstraints vorbeikaemen.
    return Profile.model_validate(
        draft.model_dump()
        | {
            "genre": genre,
            "length_minutes": length_minutes,
            "track_count": TrackCount(needed=needed, request=request).model_dump(),
        }
    )

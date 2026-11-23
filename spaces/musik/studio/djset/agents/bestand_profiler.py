"""Agent 1, zweiter Einstieg - das Regelwerk fuer ein Set aus dem Bestand.

Anders als agents/profiler.py gibt es hier kein Genre, aus dem ein Tempo
folgt: die Songs sind schon da und gemessen. Das Modell entwirft nur, was
Geschmack ist (Energieverlauf, Uebergaenge, harmonische Strenge, Stil), aus
dem Wunsch des Nutzers und der Tabelle der Songs. Tempo und Trackzahl stehen
durch die Messung fest und werden vom Code gesetzt.
"""

from djset.agents.base import AgentClient, Budget, run_agent
from djset.bestand import SongEintrag
from djset.contracts import MIN_TRACKS, Profile
from djset.render.engine import bar_length_s
from djset.tempo import TempoWahl

PROMPT = """Du bist ein erfahrener DJ und stellst ein Set aus vorhandenen Songs zusammen.

Wunsch fuer das Set: {wunsch}
Ziellaenge des Sets: {length_minutes:.0f} Minuten
Tempo-Bereich des Sets: {bpm_min:.1f} bis {bpm_max:.1f} BPM, Ziel {bpm_target:.1f} BPM

Diese {anzahl} Songs stehen zur Verfuegung (gemessen):

| ID | Tempo (BPM) | Tonart (Camelot) | Laenge | mittlere Energie |
|----|-------------|------------------|--------|------------------|
{tabelle}

Lege fest:
- transition: passende Uebergangslaenge in Takten und die Techniken, die zu
  Wunsch und Material passen. Erlaubt sind: blend, eq_swap, filter_sweep,
  cut, double_drop
- harmonic_strictness: "strict" wenn die Tonarten der Songs harmonisches
  Mixen tragen und der Wunsch es verlangt, sonst "loose"
- energy_arc: Stuetzpunkte der Energiekurve ueber das Set, aufsteigend nach
  position von 0.0 bis 1.0, jeweils mit Zielenergie 0.0 bis 1.0 - so, wie der
  Wunsch den Verlauf beschreibt
- track_count.needed: wie viele dieser Songs das Set fuer die Ziellaenge
  braucht, hoechstens {anzahl} (wird danach vom Code aus den Songlaengen
  nachgerechnet)
- style_vocab: Stilbegriffe, die Wunsch und Material treffen
- structure: geforderte Mindestlaenge von Intro und Outro in Takten

Die Felder genre, length_minutes, bpm und track_count werden nach deiner
Antwort ueberschrieben. Trage trotzdem schemagueltige Werte ein - also einen nichtleeren
Namen, eine positive Minutenzahl, ein bpm mit target zwischen min und max und
ein request mindestens so gross wie needed. Ungueltige Platzhalter wie "" oder
0 lassen deine Antwort scheitern, obwohl der Wert ohnehin verworfen wird.

Antworte ausschliesslich mit JSON nach dem vorgegebenen Schema."""


def spielbare_laenge_s(
    dauer_s: float, takte_je_naht: float, ziel_bpm: float
) -> float:
    """Was von einem Song im Set hoerbar fuer sich steht: seine Laenge minus
    ein Uebergang von `takte_je_naht` Takten beim Zieltempo. Nie negativ - ein
    Song, der kuerzer ist als sein Uebergang, traegt nichts bei, zieht aber
    auch nichts ab."""
    return max(0.0, dauer_s - takte_je_naht * bar_length_s(ziel_bpm))


def benoetigte_songs(
    dauern: list[float], wunsch_s: float, takte_je_naht: float, ziel_bpm: float
) -> int:
    """Die kleinste Zahl n, fuer die die spielbaren Laengen der n kuerzesten
    Songs die Wunschlaenge erreichen - mindestens MIN_TRACKS, hoechstens alle.

    Die kuerzesten, nicht beliebige: so ist n eine Zahl, mit der der Kurator
    sicher hinkommt, welche Songs er auch waehlt.
    """
    summe = 0.0
    for anzahl, dauer in enumerate(sorted(dauern), start=1):
        summe += spielbare_laenge_s(dauer, takte_je_naht, ziel_bpm)
        if summe >= wunsch_s:
            return min(len(dauern), max(MIN_TRACKS, anzahl))
    return len(dauern)


def _laenge(sekunden: float) -> str:
    gesamt = round(sekunden)
    return f"{gesamt // 60}:{gesamt % 60:02d}"


def _tabelle(songs: list[SongEintrag], wahl: TempoWahl) -> str:
    """Eine Markdown-Zeile je Song im Bereich, in der Reihenfolge von wahl.drin."""
    nach_id = {song.id: song for song in songs}
    zeilen = []
    for song_id in wahl.drin:
        song = nach_id.get(song_id)
        if song is None or song.messung is None:
            raise ValueError(
                f"Song {song_id!r} liegt im Tempo-Bereich, hat aber keine Messung"
            )
        messung = song.messung
        energie = sum(messung.energy) / len(messung.energy)
        zeilen.append(
            f"| {song.id} | {messung.bpm.value:.1f} | {messung.key.camelot} | "
            f"{_laenge(messung.duration_s)} | {energie:.2f} |"
        )
    return "\n".join(zeilen)


def build_profile_from_bestand(
    client: AgentClient,
    wunsch: str,
    length_minutes: float,
    songs: list[SongEintrag],
    wahl: TempoWahl,
    budget: Budget,
) -> Profile:
    """Das Modell entwirft Energieverlauf, Uebergangsregeln, harmonische
    Strenge und Stil aus Wunsch und Songtabelle. Der Code setzt danach fest:
    genre = wunsch, bpm = wahl.bereich, track_count.needed aus den
    Songlaengen (benoetigte_songs, mit dem mittleren Uebergang des Entwurfs),
    track_count.request = len(wahl.drin) - der Kurator darf aus allen
    passenden Songs waehlen."""
    draft = run_agent(
        client,
        PROMPT.format(
            wunsch=wunsch,
            length_minutes=length_minutes,
            bpm_min=wahl.bereich.min,
            bpm_max=wahl.bereich.max,
            bpm_target=wahl.bereich.target,
            anzahl=len(wahl.drin),
            tabelle=_tabelle(songs, wahl),
        ),
        Profile,
        budget,
    )

    # needed rechnet der Code, nicht das Modell: es kennt die Songlaengen nur
    # gerundet aus der Tabelle und hat sich verzaehlt. Der mittlere Uebergang
    # kommt aus dem Entwurf selbst. request ist alles, was passt - der Bestand
    # ist, was er ist, und mehr anzufragen gibt es hier nicht. Liegen weniger
    # als MIN_TRACKS Songs im Bereich, scheitert die Validierung unten;
    # cmd_set hat diesen Fall vorher abgewiesen.
    nach_id = {song.id: song for song in songs}
    dauern = [
        messung.duration_s
        for song_id in wahl.drin
        if (messung := nach_id[song_id].messung) is not None
    ]
    mittlerer_uebergang = (draft.transition.bars_min + draft.transition.bars_max) / 2
    needed = benoetigte_songs(
        dauern, length_minutes * 60.0, mittlerer_uebergang, wahl.bereich.target
    )
    request = len(wahl.drin)

    # Bewusst ueber model_validate statt model_copy(update=...): letzteres
    # validiert in Pydantic v2 nicht, womit ausgerechnet die vom Code gesetzten
    # Felder an den Vertragsconstraints vorbeikaemen.
    return Profile.model_validate(
        draft.model_dump()
        | {
            "genre": wunsch,
            "length_minutes": length_minutes,
            "bpm": wahl.bereich.model_dump(),
            "track_count": {"needed": needed, "request": request},
        }
    )

"""Agent 2 - schreibt die Suno-Prompts.

Zwei harte Regeln setzt der Code durch, nicht der Prompttext: die Anzahl muss
der Overshoot-Zahl entsprechen, und die Zieltonarten muessen sich verteilen.
Kaemen alle Tracks in derselben Tonart, haette der Setlist-Architect spaeter
keinen harmonischen Spielraum.
"""

from pydantic import BaseModel, Field, field_validator

from djset.agents.base import AgentClient, Budget, SchemaViolation, run_agent
from djset.camelot import parse
from djset.contracts import CAMELOT_RE, Profile

MIN_DISTINCT_KEYS = 3


class SunoPrompt(BaseModel):
    index: int = Field(ge=1)
    title: str = Field(min_length=1)
    style: str = Field(min_length=1)
    target_bpm: int = Field(gt=0)
    target_camelot: str = Field(pattern=CAMELOT_RE)
    notes: str

    @field_validator("target_camelot")
    @classmethod
    def _valid_camelot(cls, value: str) -> str:
        parse(value)
        return value


class PromptSet(BaseModel):
    prompts: list[SunoPrompt] = Field(min_length=1)


PROMPT = """Du schreibst Prompts fuer Suno, damit daraus die Tracks eines
DJ-Sets entstehen.

Genre: {genre}
Ziel-BPM: {bpm_target:.0f} (erlaubt {bpm_min:.0f} bis {bpm_max:.0f})
Stilbegriffe: {style_vocab}
Mindestlaenge Intro: {min_intro} Takte, Outro: {min_outro} Takte

Schreibe genau {count} Prompts. Anforderungen:
- Jeder Prompt bekommt eine fortlaufende index-Nummer ab 1
- target_bpm liegt im erlaubten Bereich
- target_camelot verteilt sich ueber mindestens {min_keys} verschiedene
  Tonarten, damit spaeter harmonisch gemischt werden kann. Bleib dabei in
  benachbarten Bereichen des Camelot-Rads, nicht quer verstreut
- style beschreibt den Klang so, wie Suno es im Feld "Style of Music" erwartet:
  kurze, kommagetrennte Begriffe, kein Fliesstext
- Die Tracks sollen sich in Energie unterscheiden, damit ein Spannungsbogen
  moeglich wird - von zurueckhaltenden Openern bis zu treibenden Peak-Tracks
- notes haelt in einem Satz fest, welche Rolle der Track im Set spielen soll

Antworte ausschliesslich mit JSON nach dem vorgegebenen Schema."""


def write_prompts(
    client: AgentClient, profile: Profile, budget: Budget
) -> PromptSet:
    wanted = profile.track_count.request
    prompt = PROMPT.format(
        genre=profile.genre,
        bpm_target=profile.bpm.target,
        bpm_min=profile.bpm.min,
        bpm_max=profile.bpm.max,
        style_vocab=", ".join(profile.style_vocab),
        min_intro=profile.structure.min_intro_bars,
        min_outro=profile.structure.min_outro_bars,
        count=wanted,
        min_keys=MIN_DISTINCT_KEYS,
    )

    # Zwei Schleifen, zwei Zustaendigkeiten. Diese hier prueft den INHALT
    # (Anzahl, Verteilung der Tonarten) und formuliert die Beanstandung neu.
    # Ob die Antwort ueberhaupt dem Schema entspricht, klaert run_agent fuer
    # sich - mit retries=1, damit ein Modell, das dem JSON einen Satz
    # voranstellt, nicht sofort das einzige Nutzerkommando abbricht. retries=0
    # war hier die verkehrte Verteilung: drei Versuche fuer den Inhalt, aber
    # keinen einzigen fuer die Form, obwohl der Spec ausdruecklich "Retry mit
    # der Fehlermeldung" fuer Schemaverletzungen verlangt.
    #
    # Obergrenze damit 3 * 2 = 6 Modellaufrufe; darueber wacht das Budget.
    attempt_prompt = prompt
    last_problem = ""
    for _attempt in range(3):
        result = run_agent(client, attempt_prompt, PromptSet, budget, retries=1)
        problem = _check(result, wanted)
        if problem is None:
            return result
        last_problem = problem
        attempt_prompt = f"{prompt}\n\nDein letzter Versuch war unbrauchbar: {problem}"

    raise SchemaViolation(f"Prompt-Satz nach drei Versuchen unbrauchbar: {last_problem}")


def _check(result: PromptSet, wanted: int) -> str | None:
    if len(result.prompts) != wanted:
        return f"es waren genau {wanted} Prompts verlangt, geliefert wurden {len(result.prompts)}"
    distinct = {prompt.target_camelot for prompt in result.prompts}
    if len(distinct) < MIN_DISTINCT_KEYS:
        return (
            f"nur {len(distinct)} verschiedene Tonarten, verlangt sind "
            f"mindestens {MIN_DISTINCT_KEYS}"
        )
    return None


def render_markdown(profile: Profile, prompt_set: PromptSet) -> str:
    lines = [
        f"# Suno-Prompts fuer {profile.genre}",
        "",
        f"Set-Laenge: {profile.length_minutes:.0f} Minuten - "
        f"benoetigt {profile.track_count.needed} Tracks, "
        f"angefordert {profile.track_count.request}.",
        "",
        "Generiere jeden Prompt einzeln bei Suno und lege die Downloads in `watch/` ab.",
        "",
    ]
    for prompt in prompt_set.prompts:
        lines += [
            f"## Track {prompt.index}: {prompt.title}",
            "",
            f"- **Style of Music:** {prompt.style}",
            f"- **BPM:** {prompt.target_bpm}",
            f"- **Tonart (Camelot):** {prompt.target_camelot}",
            f"- **Rolle im Set:** {prompt.notes}",
            "",
        ]
    return "\n".join(lines)

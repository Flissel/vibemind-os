"""Der einzige Weg, auf dem ein Sprachmodell aufgerufen wird.

Erzwingt strukturierte Ausgabe: ungeprueftes JSON verlaesst diese Datei nie.
Bei Schemaverletzung wird mit der Fehlermeldung erneut gefragt, damit das
Modell weiss, was es falsch gemacht hat.
"""

import re
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

M = TypeVar("M", bound=BaseModel)

# Grobe Schaetzung: vier Zeichen je Token. Genau genug fuer eine Notbremse.
_CHARS_PER_TOKEN = 4
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class AgentError(Exception):
    """Oberklasse aller Agentenfehler."""


class SchemaViolation(AgentError):
    """Das Modell hat auch nach allen Versuchen kein gueltiges Ergebnis geliefert."""


class BudgetExceeded(AgentError):
    """Das Token-Budget des Laufs ist aufgebraucht."""


class AgentClient(Protocol):
    def complete(self, prompt: str, schema: dict[str, object]) -> str: ...


class Budget:
    """Notbremse fuer unbeaufsichtigte Laeufe."""

    def __init__(self, max_tokens: int) -> None:
        self.max_tokens = max_tokens
        self.spent = 0

    def charge(self, text: str) -> None:
        estimate = max(1, len(text) // _CHARS_PER_TOKEN)
        if self.spent + estimate > self.max_tokens:
            raise BudgetExceeded(
                f"Budget erschoepft: {self.spent} von {self.max_tokens} Tokens verbraucht"
            )
        self.spent += estimate


def _strip_fences(raw: str) -> str:
    match = _FENCE.match(raw)
    return match.group(1) if match else raw.strip()


def run_agent(
    client: AgentClient,
    prompt: str,
    model_type: type[M],
    budget: Budget,
    retries: int = 2,
) -> M:
    """Fragt das Modell, bis die Antwort dem Schema entspricht.

    `retries` zaehlt die Wiederholungen, nicht die Gesamtversuche: bei 2 wird
    hoechstens dreimal gefragt.
    """
    schema = model_type.model_json_schema()
    current = prompt
    last_error = ""

    for _attempt in range(retries + 1):
        budget.charge(current)
        raw = client.complete(current, schema)
        budget.charge(raw)
        try:
            return model_type.model_validate_json(_strip_fences(raw))
        except ValidationError as error:
            last_error = str(error)
            current = (
                f"{prompt}\n\n"
                f"Dein letzter Versuch war ungueltig:\n{raw}\n\n"
                f"Fehler:\n{last_error}\n\n"
                f"Antworte erneut, ausschliesslich mit gueltigem JSON nach dem Schema."
            )

    raise SchemaViolation(
        f"{model_type.__name__} nach {retries + 1} Versuchen nicht erhalten: {last_error}"
    )

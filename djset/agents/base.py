"""Der einzige Weg, auf dem ein Sprachmodell aufgerufen wird.

Erzwingt strukturierte Ausgabe: ungeprueftes JSON verlaesst diese Datei nie.
Bei Schemaverletzung wird mit der Fehlermeldung erneut gefragt, damit das
Modell weiss, was es falsch gemacht hat.

Zwei Arten von Wiederholung, bewusst getrennt gehalten:

- **Transport** (Timeout, API-Fehler, abgerissene Verbindung): das Modell hat
  gar nicht geantwortet. Wiederholung mit Backoff, gleicher Prompt.
- **Schema** (Antwort da, aber ungueltig): Wiederholung sofort, mit der
  Fehlermeldung im Prompt.

Der Spec verlangt beides (Abschnitt 7). Weil hier die einzige Stelle ist, an
der ueberhaupt ein Modell gerufen wird, ist es auch die einzige, an der es
umgesetzt werden muss.
"""

import re
import time
from typing import Callable, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

M = TypeVar("M", bound=BaseModel)

# Grobe Schaetzung: vier Zeichen je Token. Genau genug fuer eine Notbremse.
_CHARS_PER_TOKEN = 4
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)

# Transport-Wiederholungen und Wartezeit davor. Kurz genug, um einen
# unbeaufsichtigten Lauf nicht haengen zu lassen, lang genug, um eine
# Rate-Limit-Delle zu ueberstehen: 1 s, dann 2 s.
TRANSPORT_RETRIES = 2
BACKOFF_BASE_S = 1.0


class AgentError(Exception):
    """Oberklasse aller Agentenfehler."""


class SchemaViolation(AgentError):
    """Das Modell hat auch nach allen Versuchen kein gueltiges Ergebnis geliefert."""


class TransportFailure(AgentError):
    """Das Modell war auch nach allen Versuchen nicht erreichbar."""


class BudgetExceeded(AgentError):
    """Das Budget des Laufs ist aufgebraucht - Tokens oder Wanduhrzeit."""


class AgentClient(Protocol):
    def complete(self, prompt: str, schema: dict[str, object]) -> str: ...


class Budget:
    """Notbremse fuer unbeaufsichtigte Laeufe: Tokens UND Wanduhrzeit.

    Der Spec verlangt beide Grenzen (Abschnitt 7): "Ein autonomer Lauf, der
    unbemerkt eskaliert, ist schlimmer als einer, der sauber stoppt." Tokens
    allein reichen dafuer nicht - ein Lauf, der in Timeouts und Backoffs
    haengt, verbraucht kaum Tokens und laeuft trotzdem endlos.

    Beide Grenzen werden bei jedem charge() geprueft, also vor und nach jedem
    Modellaufruf. Feiner braucht es das nicht: zwischen zwei Aufrufen rechnet
    dieses Projekt nur, es wartet nicht.

    `now` ist einspeisbar, damit Tests die Uhr stellen koennen, ohne zu
    schlafen.
    """

    def __init__(
        self,
        max_tokens: int,
        max_seconds: float,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_tokens = max_tokens
        self.max_seconds = max_seconds
        self.spent = 0
        self._now = now
        self._started = now()

    @property
    def elapsed_s(self) -> float:
        return self._now() - self._started

    def charge(self, text: str) -> None:
        elapsed = self.elapsed_s
        if elapsed > self.max_seconds:
            raise BudgetExceeded(
                f"Zeitbudget erschoepft: {elapsed:.1f} s von {self.max_seconds:.1f} s"
            )
        estimate = max(1, len(text) // _CHARS_PER_TOKEN)
        if self.spent + estimate > self.max_tokens:
            raise BudgetExceeded(
                f"Budget erschoepft: {self.spent} von {self.max_tokens} Tokens verbraucht"
            )
        self.spent += estimate


def _strip_fences(raw: str) -> str:
    match = _FENCE.match(raw)
    return match.group(1) if match else raw.strip()


def _complete_with_backoff(
    client: AgentClient,
    prompt: str,
    schema: dict[str, object],
    budget: Budget,
    transport_retries: int,
    backoff_base_s: float,
    sleep: Callable[[float], None],
) -> str:
    """Ruft das Modell und wiederholt bei Transportfehlern mit Backoff.

    Das Token-Budget wird vor jedem echten Versuch belastet, nicht einmal je
    Prompt: ein Retry schickt denselben Prompt noch einmal los und kostet
    entsprechend noch einmal. So bremst das Budget auch einen Retry-Sturm.

    AgentError (also auch BudgetExceeded) wird durchgereicht - das ist kein
    Transportproblem und darf nicht wiederholt werden.
    """
    last_error: Exception | None = None
    for attempt in range(transport_retries + 1):
        budget.charge(prompt)
        try:
            return client.complete(prompt, schema)
        except AgentError:
            raise
        except Exception as error:  # noqa: BLE001 - alles, was der Transport wirft
            last_error = error
            if attempt < transport_retries:
                sleep(backoff_base_s * (2.0 ** attempt))

    raise TransportFailure(
        f"Modell nach {transport_retries + 1} Versuchen nicht erreichbar: {last_error}"
    ) from last_error


def run_agent(
    client: AgentClient,
    prompt: str,
    model_type: type[M],
    budget: Budget,
    retries: int = 2,
    transport_retries: int = TRANSPORT_RETRIES,
    backoff_base_s: float = BACKOFF_BASE_S,
    sleep: Callable[[float], None] = time.sleep,
) -> M:
    """Fragt das Modell, bis die Antwort dem Schema entspricht.

    `retries` zaehlt die Schema-Wiederholungen, nicht die Gesamtversuche: bei
    2 wird hoechstens dreimal gefragt. `transport_retries` zaehlt daneben die
    Wiederholungen je Frage, wenn der Aufruf selbst scheitert.
    """
    schema = model_type.model_json_schema()
    current = prompt
    last_error = ""

    for _attempt in range(retries + 1):
        raw = _complete_with_backoff(
            client, current, schema, budget, transport_retries, backoff_base_s, sleep
        )
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

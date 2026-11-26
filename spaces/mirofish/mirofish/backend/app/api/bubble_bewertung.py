"""Routen der Bubble-Bewertung (/api/bubble/evaluate); der Dienst steht in services/bubble_bewertung.py."""

from typing import Optional

from . import bubble_bp
from ..models.task import TaskManager, TaskStatus
from ..services.bubble_bewertung import BubbleBewertung, routen_anmelden

_DIENST: Optional[BubbleBewertung] = None


def _llm():
    # Spaet importiert: der Client liest seine Einstellung erst beim ersten Aufruf.
    from ..utils.llm_client import LLMClient
    return LLMClient()


def _dienst() -> BubbleBewertung:
    global _DIENST
    if _DIENST is None:
        _DIENST = BubbleBewertung(TaskManager(), _llm, status_laeuft=TaskStatus.PROCESSING)
    return _DIENST


routen_anmelden(bubble_bp, _dienst)

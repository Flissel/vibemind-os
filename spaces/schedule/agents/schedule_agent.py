"""Entfallen: der Redis-Backend-Agent des Schedule-Space.

Hier lag ein zweites Ereignis-Verzeichnis (``EVENT_TO_TOOL``) neben
``config/space_agent_registry.yml`` -- und es nannte je Ereignis ein anderes
Werkzeug als die Registry. Zwei Verzeichnisse fuer dieselbe Bindung heissen:
wer eine Frage nach dem Werkzeug eines ``schedule.*``-Ereignisses stellt,
bekommt zwei Antworten und kann nicht entscheiden, welche gilt.

Lauffaehig war der Agent dabei seit 04f0e195 nicht: ``swarm.backend_agents``
liegt in diesem Repo nicht, und die Werkzeuge darunter hingen am SQLite-Laden,
den e4ab34dd durch die geteilte Supabase ersetzt hat. Entschieden hat das
nicht dieser Grabstein, sondern 27a59929: seitdem fuehrt der Weg ueber die
Brain-Faehigkeiten.

Es gilt genau eine Bindung:

    config/space_agent_registry.yml  (Registry = SoT, Ereignis -> Faehigkeit)
    brain/the_brain/data/capabilities.yaml  (Faehigkeit -> Ausfuehrungsziel)
    spaces/schedule/execution.py     (das Ziel)

Die Datei bleibt nur als Hinweis stehen; sie exportiert nichts.
"""

from __future__ import annotations

_ENTFALLEN = ("ScheduleBackendAgent", "get_schedule_agent")

_HINWEIS = (
    "ist entfallen - schedule.* bindet ausschliesslich ueber "
    "config/space_agent_registry.yml an spaces/schedule/execution.py"
)


def __getattr__(name: str):
    if name in _ENTFALLEN:
        raise AttributeError(f"{name} {_HINWEIS}")
    raise AttributeError(name)


__all__: list[str] = []

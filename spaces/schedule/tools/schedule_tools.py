"""Entfallen: der SQLite-Werkzeugsatz des Schedule-Space.

Die sechs Funktionen hier (``create_scheduled_task`` und Geschwister) waren
die zweite Haelfte des alten Wegs: Electron-IPC, ``data.ScheduledTaskRepository``,
eine eigene SQLite-Datei. Beides ist weg -- das ``data``-Paket liegt in diesem
Repo nicht (04f0e195), und seit e4ab34dd schreibt schedule in die geteilte
Supabase. Importiert hat sich diese Datei seitdem nicht mehr.

Stehen geblieben war sie aber lang genug, um Werkzeugnamen zu behaupten, die
die Registry nicht kennt. Gueltig ist nur der Weg in
``spaces/schedule/execution.py``: ``create``, ``list_tasks``, ``cancel``,
``modify``, ``status``, ``snooze``, ``openclaw_cron`` -- mit Zeitzonen- und
Ausloeser-Pruefung, Idempotenz-Schluessel und Rueckfrage nach jedem Schreiben.

Die Zeitanalyse (``spaces/schedule/nlp``) hat den Umbau ueberlebt und ist
weiter einzeln benutzbar; sie steckt nur in keinem Werkzeug mehr.
"""

from __future__ import annotations

_ENTFALLEN = (
    "set_electron_sender",
    "set_schedule_worker",
    "create_scheduled_task",
    "list_scheduled_tasks",
    "cancel_scheduled_task",
    "modify_scheduled_task",
    "get_schedule_status",
    "snooze_scheduled_task",
)


def __getattr__(name: str):
    if name in _ENTFALLEN:
        raise AttributeError(
            f"{name} ist entfallen - der Schedule-Space fuehrt ueber "
            "spaces/schedule/execution.py"
        )
    raise AttributeError(name)


__all__: list[str] = []

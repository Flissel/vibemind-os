"""Entfallen: der SQLite-Worker des Schedule-Space.

``ScheduleWorker`` kommt seit 27a59929 aus ``durable_worker.py``: derselbe
APScheduler, aber die Aufgaben werden aus ``spaces/schedule/execution.py``
nachgeladen statt aus einer SQLite-Datei, und ein Lauf, dessen Ziel kein
geprueftes ``ok=true`` zurueckgibt, setzt die Aufgabe auf ``failed`` statt sie
still zu verschlucken.

Die Fassung hier hing an ``data.ScheduledTaskRepository`` -- ein Paket, das in
diesem Repo nicht liegt. Sie war also nicht nur alt, sondern nicht ladbar.
"""

from __future__ import annotations


def __getattr__(name: str):
    if name == "ScheduleWorker":
        raise AttributeError(
            "ScheduleWorker kommt aus spaces.schedule.workers.durable_worker"
        )
    raise AttributeError(name)


__all__: list[str] = []

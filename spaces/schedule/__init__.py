"""
Schedule Space — geplante Aufgaben, Erinnerungen und Timer.

Die ``schedule.*``-Ereignisse binden an genau einer Stelle:
``config/space_agent_registry.yml`` (Registry = SoT) nennt je Ereignis die
Brain-Faehigkeit, ``brain/the_brain/data/capabilities.yaml`` deren
Ausfuehrungsziel in ``spaces/schedule/execution.py``. Ein zweites
Ereignis-Verzeichnis gibt es nicht mehr; was hier frueher danebenlag (Redis-
Backend-Agent, SQLite-Werkzeuge, SQLite-Worker) steht nur noch als Hinweis in
den jeweiligen Dateien.

Usage:
    from spaces.schedule import ScheduleWorker

    # APScheduler-Worker: laedt die aktiven Aufgaben aus der Supabase
    worker = ScheduleWorker(execution_target=run_task)
    await worker.start()
"""

# Configuration
from .config import ScheduleConfig, get_config

# NLP
from .nlp import ParsedTime, parse_time_expression

# Heavy runtime integrations are intentionally lazy. Brain direct targets can
# import ``spaces.schedule.execution`` without requiring APScheduler.

__all__ = [
    # Config
    "ScheduleConfig",
    "get_config",
    # NLP
    "ParsedTime",
    "parse_time_expression",
    # Worker
    "ScheduleWorker",
]


def __getattr__(name):
    if name == "ScheduleWorker":
        from .workers import ScheduleWorker
        return ScheduleWorker
    raise AttributeError(name)

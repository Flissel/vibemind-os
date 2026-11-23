"""Kleiner Zugang zum Desktop-Dienst am eigenen Rechner.

Der Python-Teil der App fragt beim Start nur, ob der Dienst läuft
(`get_automation_client().is_available()`). Das frühere Modul ist mit dem Aufräumen
des Space entfallen; seitdem scheiterte der Import. Starten und Bedienen des Dienstes
liegen heute bei der App (`desktop-service.js`) und beim Werkzeug-Dienst
(`mcp_server.py`), nicht hier.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8007"
# Der Health-Router hängt unter /api/health und seine Route heißt /health.
HEALTH_PATH = "/api/health/health"


class AutomationUIClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("DESKTOP_AUTOMATION_URL") or DEFAULT_URL).rstrip("/")

    def is_available(self, timeout: float = 2.0) -> bool:
        try:
            with urllib.request.urlopen(self.base_url + HEALTH_PATH, timeout=timeout) as response:
                return response.status == 200
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return False


_client: AutomationUIClient | None = None


def get_automation_client() -> AutomationUIClient:
    global _client
    if _client is None:
        _client = AutomationUIClient()
    return _client

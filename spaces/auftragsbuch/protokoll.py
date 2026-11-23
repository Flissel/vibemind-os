"""Log des Auftragsbuchs und des Rowboat-Abholers: nur der Logger `auftragsbuch` schreibt (nur Standardbibliothek)."""
from __future__ import annotations

import logging
import sys
from typing import TextIO


def log_einrichten(strom: TextIO | None = None) -> None:
    """Es schreibt nur noch der Logger `auftragsbuch`.

    Bibliotheken (mcp, psycopg.pool, httpx ...) loggen ueber die Wurzel und koennten Argumente,
    Host-Koepfe oder Datenbank-Adressen nennen. Darum bekommt `auftragsbuch` einen eigenen Handler
    ohne Weitergabe, die Wurzel hat nur einen NullHandler und die Stufe CRITICAL.
    """
    eigener = logging.getLogger("auftragsbuch")
    handler = logging.StreamHandler(strom if strom is not None else sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    eigener.handlers[:] = [handler]
    eigener.setLevel(logging.INFO)
    eigener.propagate = False
    wurzel = logging.getLogger()
    wurzel.handlers[:] = [logging.NullHandler()]
    wurzel.setLevel(logging.CRITICAL)

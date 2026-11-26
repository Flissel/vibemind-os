"""Schluessel lesen, auch als Docker-Secret.

``<NAME>_FILE`` (Pfad zu einer Datei, etwa ``/run/secrets/...``) geht vor ``<NAME>`` (Umgebung).
Ist eine Datei genannt, aber nicht lesbar, gibt es keinen Schluessel: nie still auf die Umgebung
ausweichen, sonst wirkte ein vergessener alter Wert weiter. Werte erscheinen nie im Log.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Mapping, Optional

logger = logging.getLogger("brain.geheimnis")


def geheimnis(name: str, umgebung: Optional[Mapping[str, str]] = None) -> str:
    u = os.environ if umgebung is None else umgebung
    pfad = (u.get(f"{name}_FILE") or "").strip()
    if pfad:
        try:
            return Path(pfad).read_text(encoding="utf-8").strip()
        except OSError:
            logger.warning("%s_FILE nicht lesbar", name)
            return ""
    return (u.get(name) or "").strip()

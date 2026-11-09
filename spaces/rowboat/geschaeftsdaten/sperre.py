"""Gemeinsame Sperre fuer den naechtlichen und den stuendlichen Geschaeftsdaten-Lauf.

`flock` auf einer Sperrdatei: beide Laeufe schreiben nie gleichzeitig ins Wissen. Die Sperre faellt mit dem
Prozess weg (auch bei Absturz); die Datei selbst bleibt liegen und ist leer. Kann die Sperrdatei nicht
geoeffnet werden, gilt die Sperre als nicht erhalten (fail closed). Ohne `fcntl` (Windows-Entwicklung) gibt
es keine Sperre und der Aufrufer bekommt `True`.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

SPERRE_STANDARD = "/home/debian/rowboat-geschaeftsdaten/lauf.lock"
log = logging.getLogger("geschaeftsdaten")


@contextmanager
def sperre(pfad: Path, *, warten_s: float) -> Iterator[bool]:
    """Liefert True, wenn die Sperre innerhalb von `warten_s` Sekunden erhalten wurde (Versuch im Abstand von
    1 s; `warten_s=0` = ein einziger Versuch), sonst False. Die Sperre wird beim Verlassen freigegeben."""
    if fcntl is None:
        yield True
        return
    try:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        datei = open(pfad, "a+b")
    except OSError as e:
        log.error("Sperrdatei nicht zu oeffnen (%s)", type(e).__name__)
        yield False
        return
    try:
        frist = time.monotonic() + max(0.0, warten_s)
        frei = False
        while True:
            try:
                fcntl.flock(datei, fcntl.LOCK_EX | fcntl.LOCK_NB)
                frei = True
                break
            except BlockingIOError:
                if time.monotonic() >= frist:
                    break
                time.sleep(1)
            except OSError as e:
                log.error("Sperre nicht zu setzen (%s)", type(e).__name__)
                break
        yield frei
    finally:
        datei.close()

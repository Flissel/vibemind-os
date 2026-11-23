"""Herkunft einer Anfrage: Kommt sie aus einer Eingabe des Betreibers?

Eigenstaendiges Modul, nur Standardbibliothek. Das Gehirn bindet es ein, siehe
``../block-2-herkunft.md``. Der Eingang weist sich mit einem Schluessel aus; in der
Umgebung steht nur dessen SHA-256 (``BRAIN_EINGANG_<NAME>_SHA256``). Nichts hier wirft
eine Ausnahme, und kein Schluessel erscheint in einer Meldung oder einem ``repr``.
"""
from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Any

EINGAENGE_BETREIBER: frozenset[str] = frozenset({"app", "telegram-betreiber"})
UNBEKANNT = "unbekannt"
ABLEHNUNG = "Nur aus einer Eingabe des Betreibers"


def _variable(eingang: str) -> str:
    return "BRAIN_EINGANG_" + eingang.upper().replace("-", "_") + "_SHA256"


def eingang_pruefen(
    eingang: str | None, schluessel: str | None, umgebung: Mapping[str, str]
) -> str:
    """Name des Eingangs, wenn der Schluessel passt, sonst ``"unbekannt"``."""
    try:
        if not isinstance(eingang, str) or eingang not in EINGAENGE_BETREIBER:
            return UNBEKANNT
        if not isinstance(schluessel, str) or not schluessel:
            return UNBEKANNT
        erwartet = umgebung.get(_variable(eingang))
        if not isinstance(erwartet, str) or not erwartet.strip():
            return UNBEKANNT
        erwartet_b = erwartet.strip().lower().encode("ascii")
        ist_b = hashlib.sha256(schluessel.encode("utf-8")).hexdigest().encode("ascii")
        return eingang if hmac.compare_digest(ist_b, erwartet_b) else UNBEKANNT
    except Exception:  # noqa: BLE001 - Pruefung darf nie werfen, im Zweifel unbekannt
        return UNBEKANNT


def braucht_betreiber(ereignis: Mapping[str, Any]) -> bool:
    """Wahr, wenn das Verzeichnis ``required_origin: betreiber`` verlangt."""
    try:
        return ereignis.get("required_origin") == "betreiber"
    except Exception:  # noqa: BLE001
        return False


def erlaubt(herkunft: str | None, ereignis: Mapping[str, Any]) -> bool:
    """Falsch nur, wenn der Betreiber verlangt wird und die Herkunft keiner seiner Eingaenge ist."""
    if not braucht_betreiber(ereignis):
        return True
    return herkunft in EINGAENGE_BETREIBER


SERVER_AUFTRAGSBUCH = "auftragsbuch"
OHNE_EREIGNIS_ERLAUBT: frozenset[str] = frozenset({"auftrag_lesen", "auftrag_probe"})


def erlaubt_werkzeug(
    herkunft: str | None, server: str, tool: str, ereignis: Mapping[str, Any] | None
) -> bool:
    """Tor fuer ein ausdruecklich genanntes Ziel ``mcp:<agent>:<server>:<werkzeug>``.

    ``ereignis`` ist der Verzeichniseintrag, den die Suche nach (Agent, Server, Werkzeug) fand,
    sonst ``None`` oder leer. Ein Werkzeug am Auftragsbuch ohne gefundenes Ereignis wird abgelehnt
    (nie ``{}`` weiterreichen: das liesse es durch), ausser ``auftrag_lesen`` und ``auftrag_probe``.
    Andere Server verhalten sich wie ``erlaubt``. Wirft nie.
    """
    try:
        gefunden = ereignis if isinstance(ereignis, Mapping) and len(ereignis) > 0 else None
        if server == SERVER_AUFTRAGSBUCH and gefunden is None:
            return tool in OHNE_EREIGNIS_ERLAUBT
        return erlaubt(herkunft, gefunden if gefunden is not None else {})
    except Exception:  # noqa: BLE001 - im Zweifel ablehnen
        return False

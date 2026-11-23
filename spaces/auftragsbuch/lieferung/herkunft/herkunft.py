"""Herkunft einer Anfrage: Kommt sie aus einer Eingabe des Betreibers?

Eigenstaendiges Modul, nur Standardbibliothek. Das Gehirn bindet es ein, siehe
``../block-2-herkunft.md``. Der Eingang weist sich mit einem Schluessel aus; in der
Umgebung steht nur dessen SHA-256 (``BRAIN_EINGANG_<NAME>_SHA256``). Nichts hier wirft
eine Ausnahme, und kein Schluessel erscheint in einer Meldung oder einem ``repr``.
"""
from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Mapping
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


ART_GETIPPT = "getippt"
_VORSATZ_EINGANG = "eingang:"


def herkunft_bestimmen(getippt: bool, eingang: Callable[[], str]) -> dict[str, Any]:
    """Das eine Herkunfts-Objekt einer Anfrage.

    ``getippt``: ist das Ergebnis von ``herkunft_pruefen(...) == "getippt"`` (Zeichen ``X-Vibemind-Eingabe``);
    es gewinnt immer. Sonst zaehlt ein gueltiger Eingang aus Block 2: ``eingang`` ist ein Aufruf ohne
    Argumente, der den Namen aus ``eingang_pruefen`` liefert (er laeuft nur, wenn nicht getippt). Eine rohe
    Zeichenkette zaehlt nie: Sie haette keinen Echtheitsnachweis. Sonst ``{"art": None}``. Wirft nie.
    """
    try:
        if getippt is True:
            return {"art": ART_GETIPPT}
        name = eingang() if callable(eingang) else None  # nur die Aufruf-Form: rohe Namen haben keinen Echtheitsnachweis
        if isinstance(name, str) and name in EINGAENGE_BETREIBER:
            return {"art": _VORSATZ_EINGANG + name}
    except Exception:  # noqa: BLE001
        pass
    return {"art": None}


def _art(herkunft: Any) -> str | None:
    """Art der Herkunft, wenn die Form stimmt, sonst ``None``. Ein alter Eingangsname gilt als ``eingang:<name>``."""
    if isinstance(herkunft, str):
        return _VORSATZ_EINGANG + herkunft if herkunft in EINGAENGE_BETREIBER else None
    if not isinstance(herkunft, Mapping):
        return None
    art = herkunft.get("art")
    if not isinstance(art, str):
        return None
    if art == ART_GETIPPT:
        return art
    if art.startswith(_VORSATZ_EINGANG) and art[len(_VORSATZ_EINGANG):] in EINGAENGE_BETREIBER:
        return art
    return None


def _weitergegeben(herkunft: Any) -> bool | None:
    """``True``/``False``, oder ``None`` bei einer falschen Form des Feldes (nur ``True`` und ``False`` sind gueltig)."""
    if not isinstance(herkunft, Mapping) or "weitergegeben" not in herkunft:
        return False
    wert = herkunft["weitergegeben"]
    return wert if isinstance(wert, bool) else None


def ist_betreiber(herkunft: Any) -> bool:
    """Wahr bei ``getippt`` oder einem Eingang des Betreibers, auch wenn die Herkunft weitergegeben wurde."""
    try:
        return _art(herkunft) is not None and _weitergegeben(herkunft) is not None
    except Exception:  # noqa: BLE001
        return False


def eingabe_herkunft(herkunft: Any) -> str | None:
    """``"getippt"`` nur fuer einen frischen getippten Satz (nie weitergegeben); sonst ``None``.

    Das ist der Wert, den ``plan_ctx["eingabe_herkunft"]`` der Desktop-Schleife (master) erwartet.
    """
    try:
        if _art(herkunft) == ART_GETIPPT and isinstance(herkunft, Mapping) and _weitergegeben(herkunft) is False:
            return ART_GETIPPT
    except Exception:  # noqa: BLE001
        pass
    return None


def weitergeben(herkunft: Any) -> dict[str, Any]:
    """Kopie fuer Plan-Rest und Nachfasser: ``weitergegeben=True``. Eine falsche Form wird ``{"art": None, ...}``."""
    try:
        art = _art(herkunft)
        if art is None or _weitergegeben(herkunft) is None:
            return {"art": None, "weitergegeben": True}
        return {"art": art, "weitergegeben": True}
    except Exception:  # noqa: BLE001
        return {"art": None, "weitergegeben": True}


def erlaubt(herkunft: Any, ereignis: Mapping[str, Any]) -> bool:
    """Falsch nur, wenn der Betreiber verlangt wird und die Herkunft nicht die des Betreibers ist.

    ``herkunft`` ist das Objekt aus ``herkunft_bestimmen``; ein alter Eingangsname (Zeichenkette) gilt weiter.
    """
    if not braucht_betreiber(ereignis):
        return True
    return ist_betreiber(herkunft)


SERVER_AUFTRAGSBUCH = "auftragsbuch"
OHNE_EREIGNIS_ERLAUBT: frozenset[str] = frozenset({"auftrag_lesen", "auftrag_probe"})


def erlaubt_werkzeug(
    herkunft: Any, server: str, tool: str, ereignis: Mapping[str, Any] | None
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

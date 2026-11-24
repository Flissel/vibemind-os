"""Schalter und Messprotokoll für das Hintergrund-Denken (24.11.2026).

Frage des Betreibers: Was kostet eine Stunde Hintergrund-Denken auf einem kleinen Modell?
Dafür schreibt jeder Aufruf eines Mikro-Agenten ohne wartenden Menschen genau eine Zeile:
Zeit, Mikro-Agent, Dauer, Ergebnis, Zeichen rein/raus und, falls OpenFang sie liefert,
Tokens rein/raus. Nie Inhalte (kein Prompt, keine Antwort, kein Fehlertext).

Schalter (jeder Aufruf prüft neu, ohne Neustart wirksam außer der Umgebung):
  BRAIN_DENKEN=0                   aus (Umgebung, braucht Neustart des Dienstes)
  Datei <Messordner>/denken.aus    aus, solange die Datei da ist (ohne Neustart)
  BRAIN_DENKEN_BIS=<ISO-Zeit>      aus ab dieser Zeit, z. B. 2026-11-24T15:30:00+00:00
Protokoll: BRAIN_DENKEN_MESSUNG=<Pfad>, sonst data/denken_messung.jsonl im Gehirn.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

# Eigene Rolle mit eigenem Agenten; brain_planning bleibt für Sätze von Menschen.
DENKEN_ROLLE = "brain_denken"


class DenkenNichtEingerichtet(RuntimeError):
    """Die Denk-Rolle zeigt auf keinen eigenen Agenten; es wird nicht gerufen."""


_DATEN = Path(__file__).resolve().parent.parent / "data"
_SPERRE = threading.Lock()
ERGEBNISSE = ("ok", "leer", "fehler", "nicht_eingerichtet")


def protokoll_pfad() -> Path:
    eigen = os.environ.get("BRAIN_DENKEN_MESSUNG", "").strip()
    return Path(eigen) if eigen else _DATEN / "denken_messung.jsonl"


def stopp_datei() -> Path:
    return protokoll_pfad().parent / "denken.aus"


def _bis() -> Optional[datetime]:
    roh = os.environ.get("BRAIN_DENKEN_BIS", "").strip()
    if not roh:
        return None
    try:
        zeit = datetime.fromisoformat(roh)
    except ValueError:
        # Unlesbar heißt aus: lieber nicht denken als unbegrenzt.
        logger.warning("BRAIN_DENKEN_BIS unreadable; background thinking stays off")
        return datetime.min.replace(tzinfo=timezone.utc)
    return zeit if zeit.tzinfo else zeit.replace(tzinfo=timezone.utc)


def denken_an(jetzt: Optional[datetime] = None) -> bool:
    if os.environ.get("BRAIN_DENKEN", "1").strip() in ("0", "false", "False", "aus"):
        return False
    try:
        if stopp_datei().exists():
            return False
    except OSError:
        return False
    bis = _bis()
    if bis is not None and (jetzt or datetime.now(timezone.utc)) >= bis:
        return False
    return True


def _zahl(wert: Any) -> Optional[float]:
    if isinstance(wert, bool) or not isinstance(wert, (int, float)):
        return None
    return wert


def zeile(agent: str, dauer_s: float, ergebnis: str, zeichen_rein: int, zeichen_raus: int,
          nutzung: Optional[Mapping[str, Any]] = None, fehler: str = "",
          jetzt: Optional[datetime] = None) -> dict[str, Any]:
    nutzung = nutzung or {}
    eintrag: dict[str, Any] = {
        "ts": (jetzt or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "agent": str(agent),
        "dauer_ms": int(round(max(0.0, dauer_s) * 1000)),
        "ergebnis": ergebnis if ergebnis in ERGEBNISSE else "fehler",
        "zeichen_rein": int(zeichen_rein),
        "zeichen_raus": int(zeichen_raus),
        "tokens_rein": _zahl(nutzung.get("tokens_rein")),
        "tokens_raus": _zahl(nutzung.get("tokens_raus")),
        "kosten_usd": _zahl(nutzung.get("kosten_usd")),
    }
    if fehler:
        # Nur der Name der Fehlerart, nie die Meldung (sie kann Inhalte tragen).
        eintrag["fehler"] = str(fehler)[:60]
    return eintrag


def schreiben(eintrag: Mapping[str, Any]) -> bool:
    try:
        pfad = protokoll_pfad()
        pfad.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(dict(eintrag), ensure_ascii=False) + "\n"
        with _SPERRE, pfad.open("a", encoding="utf-8", newline="\n") as datei:
            datei.write(text)
        return True
    except OSError as exc:
        logger.debug("denken_messung: write failed: %s", type(exc).__name__)
        return False

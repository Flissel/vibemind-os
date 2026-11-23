"""Aufgabenbuch des Desktop-Space.

Jede Aufgabe am Bildschirm ist eine JSON-Datei: was verlangt wurde, welche Schritte
ausgeführt wurden, wie es ausging. Der Werkzeug-Dienst (`mcp_server.py`) schreibt, die
App und der Prüfer lesen. Es gibt nur eine Maus, darum läuft höchstens eine Aufgabe.

Nur Standardbibliothek, kein Modell, kein Netz.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

MAX_SCHRITTE = 40
LAUFZEIT_SEKUNDEN = 600
MAX_AUFGABE_ZEICHEN = 500
MAX_ERGEBNIS_ZEICHEN = 500
ID_MUSTER = re.compile(r"dt-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")  # immer mit fullmatch prüfen
OFFEN = "laeuft"
ENDZUSTAENDE = ("fertig", "fehlgeschlagen", "abgebrochen", "abgelaufen")


class AufgabenFehler(Exception):
    """Fachlicher Fehler; der Text darf an den Aufrufer zurück."""


def ordner(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    eigener = env.get("VIBEMIND_DESKTOP_AUFGABEN_DIR", "").strip()
    if eigener:
        return Path(eigener)
    basis = env.get("LOCALAPPDATA", "").strip()
    if basis:
        return Path(basis) / "VibeMind" / "desktop-aufgaben"
    return Path.home() / ".vibemind" / "desktop-aufgaben"


def _jetzt() -> float:
    return time.time()


def _pfad(aufgabe_id: str, env: Mapping[str, str] | None) -> Path:
    if not isinstance(aufgabe_id, str) or not ID_MUSTER.fullmatch(aufgabe_id):
        raise AufgabenFehler("aufgabe_unbekannt: task_id hat nicht die Form einer Aufgaben-Kennung")
    return ordner(env) / f"{aufgabe_id}.json"


def _schreiben(pfad: Path, aufgabe: Mapping[str, Any]) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    vorlaeufig = pfad.with_suffix(".tmp")
    vorlaeufig.write_text(json.dumps(aufgabe, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(vorlaeufig, pfad)  # Leser sehen nie eine halbe Datei.


def _abgelaufen_schliessen(aufgabe: dict[str, Any]) -> bool:
    if aufgabe.get("zustand") == OFFEN and _jetzt() - aufgabe.get("begonnen", 0) > LAUFZEIT_SEKUNDEN:
        aufgabe["zustand"] = "abgelaufen"
        aufgabe["beendet"] = _jetzt()
        aufgabe["ergebnis"] = "Die Aufgabe wurde nicht abgeschlossen und ist abgelaufen."
        return True
    return False


def lesen(aufgabe_id: str, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    pfad = _pfad(aufgabe_id, env)
    try:
        aufgabe = json.loads(pfad.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise AufgabenFehler("aufgabe_unbekannt: keine Aufgabe mit dieser Kennung") from None
    except (OSError, ValueError):
        raise AufgabenFehler("aufgabenbuch_unlesbar: die Datei der Aufgabe ist nicht lesbar") from None
    if not isinstance(aufgabe, dict) or not isinstance(aufgabe.get("schritte"), list) or "zustand" not in aufgabe:
        raise AufgabenFehler("aufgabenbuch_unlesbar: die Datei der Aufgabe hat nicht die erwartete Form")
    if _abgelaufen_schliessen(aufgabe):
        _schreiben(pfad, aufgabe)
    return aufgabe


def letzte(env: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """Die zuletzt begonnene Aufgabe oder None."""
    verzeichnis = ordner(env)
    if not verzeichnis.is_dir():
        return None
    namen = sorted(p.stem for p in verzeichnis.glob("dt-*.json") if ID_MUSTER.fullmatch(p.stem))
    return lesen(namen[-1], env) if namen else None


def not_aus(env: Mapping[str, str] | None = None) -> bool:
    """Liegt die Datei STOP im Ordner, wird kein Schritt mehr ausgeführt."""
    return (ordner(env) / "STOP").exists()


def beginnen(text: str, *, trocken: bool, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise AufgabenFehler("eingabe_ungueltig: task darf nicht leer sein")
    if not_aus(env):
        raise AufgabenFehler("not_aus: die Datei STOP liegt im Aufgaben-Ordner")
    offene = letzte(env)
    if offene and offene["zustand"] == OFFEN:
        raise AufgabenFehler(f"aufgabe_laeuft: {offene['id']} ist noch nicht abgeschlossen")
    start = _jetzt()
    kennung = time.strftime("dt-%Y%m%d-%H%M%S", time.localtime(start)) + "-" + uuid.uuid4().hex[:6]
    aufgabe = {
        "id": kennung,
        "space": "desktop",
        "aufgabe": text.strip()[:MAX_AUFGABE_ZEICHEN],
        "zustand": OFFEN,
        "trocken": bool(trocken),
        "begonnen": start,
        "beendet": None,
        "schritte": [],
        "ergebnis": None,
    }
    _schreiben(_pfad(kennung, env), aufgabe)
    return aufgabe


def _offene(aufgabe_id: str, env: Mapping[str, str] | None) -> dict[str, Any]:
    aufgabe = lesen(aufgabe_id, env)
    if aufgabe["zustand"] != OFFEN:
        raise AufgabenFehler(f"aufgabe_beendet: Zustand ist {aufgabe['zustand']}")
    return aufgabe


def schritt_pruefen(aufgabe_id: str, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Darf diese Aufgabe jetzt einen weiteren Schritt ausführen?"""
    aufgabe = _offene(aufgabe_id, env)
    if not_aus(env):
        raise AufgabenFehler("not_aus: die Datei STOP liegt im Aufgaben-Ordner")
    if len(aufgabe["schritte"]) >= MAX_SCHRITTE:
        raise AufgabenFehler(f"schritte_erschoepft: höchstens {MAX_SCHRITTE} Schritte je Aufgabe")
    return aufgabe


def schritt_buchen(
    aufgabe_id: str,
    *,
    art: str,
    angaben: Mapping[str, Any],
    ok: bool,
    meldung: str,
    dauer_ms: int,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    aufgabe = _offene(aufgabe_id, env)
    schritt = {
        "nr": len(aufgabe["schritte"]) + 1,
        "zeit": _jetzt(),
        "art": art,
        "angaben": dict(angaben),
        "ok": bool(ok),
        "meldung": meldung[:200],
        "dauer_ms": int(dauer_ms),
    }
    aufgabe["schritte"].append(schritt)
    _schreiben(_pfad(aufgabe_id, env), aufgabe)
    return schritt


def beenden(
    aufgabe_id: str, *, zustand: str, ergebnis: str, env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    if zustand not in ("fertig", "fehlgeschlagen", "abgebrochen"):
        raise AufgabenFehler("eingabe_ungueltig: zustand muss fertig, fehlgeschlagen oder abgebrochen sein")
    aufgabe = _offene(aufgabe_id, env)
    aufgabe["zustand"] = zustand
    aufgabe["beendet"] = _jetzt()
    aufgabe["ergebnis"] = (ergebnis or "").strip()[:MAX_ERGEBNIS_ZEICHEN]
    _schreiben(_pfad(aufgabe_id, env), aufgabe)
    return aufgabe

"""80-%-Warnung des Event-Shims an Felix melden (Host-Dienst, Timer budget-melden.timer).

Der Shim schreibt beim Erreichen von `warnen_ab` (deploy/agent_budget.yaml) genau eine Zeile
``{"art": "warnung", "agent", "heute", "grenze"}`` in sein Tagesprotokoll
(laufzeit/shim-budget/<JJJJ-MM-TT>.jsonl, auf dem Host gemountet). Dieses Programm liest die
Protokolle von heute und gestern und schickt jede Warnung genau einmal per Telegram. Gemerkt
wird in einer Zustandsdatei (0600); scheitert der Versand, versucht es der naechste Lauf.
So braucht der Shim keinen Telegram-Token.

    python -m spaces.event_management.system.budget_melden lauf

Umgebung (deploy/budget-melden.service, /etc/event-management/budget-melden.env):
    BUDGET_ORDNER               Ordner der Tagesprotokolle (laufzeit/shim-budget)
    BUDGET_ZUSTAND              Zustandsdatei (etwa laufzeit/budget-melden.json)
    BUDGET_TELEGRAM_TOKEN_FILE  Datei mit dem Bot-Token (0600)
    BUDGET_TELEGRAM_CHAT_ID     Chat, an den gemeldet wird

Ausgaben nennen nie Token oder Chat-ID, nur Zaehlwerte.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

TAGE_MERKEN = 14
NOETIG = ("BUDGET_ORDNER", "BUDGET_ZUSTAND", "BUDGET_TELEGRAM_TOKEN_FILE", "BUDGET_TELEGRAM_CHAT_ID")


class MeldeFehler(RuntimeError):
    """Meldung enthaelt nie Token oder Chat-ID."""


def nachricht(tag: str, agent: str, heute: int, grenze: int) -> str:
    anteil = round(100 * heute / grenze) if grenze else 0
    return (f"Event-Bot: Tagesbudget zu {anteil} % erreicht ({heute} von {grenze} Laeufen, "
            f"Agent {agent}, {tag}). Ab der Grenze antwortet der Bot heute nicht mehr. "
            f"Die Warnung kommt ab 80 %, einmal pro Tag.")


def _warnungen(ordner: Path, tag: date) -> list[tuple[str, int, int]]:
    datei = ordner / f"{tag:%Y-%m-%d}.jsonl"
    if not datei.is_file():
        return []
    gefunden: list[tuple[str, int, int]] = []
    for roh in datei.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            z = json.loads(roh)
        except ValueError:
            continue
        if not isinstance(z, dict) or z.get("art") != "warnung":
            continue
        agent, heute, grenze = z.get("agent"), z.get("heute"), z.get("grenze")
        if isinstance(agent, str) and isinstance(heute, int) and isinstance(grenze, int):
            gefunden.append((agent, heute, grenze))
    return gefunden


def _zustand_lesen(zustand: Path) -> list[str]:
    """Nur eine fehlende Datei heisst "noch nichts gemeldet"; alles andere ist ein Fehler
    (fail-closed: sonst gingen alle Warnungen erneut hinaus)."""
    if not zustand.exists():
        return []
    try:
        daten = json.loads(zustand.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise MeldeFehler("Zustand nicht lesbar") from None
    if not isinstance(daten, list) or not all(isinstance(s, str) for s in daten):
        raise MeldeFehler("Zustand nicht lesbar")
    return list(daten)


def _zustand_schreiben(zustand: Path, schluessel: list[str]) -> None:
    tmp = zustand.with_name(zustand.name + ".neu")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(schluessel, f)
        os.replace(tmp, zustand)
    except OSError as fehler:
        raise MeldeFehler(f"Zustand nicht schreibbar ({type(fehler).__name__})") from None


def neue_melden(ordner: Path, zustand: Path, senden: Callable[[str], None], heute: date) -> int:
    """Schickt jede noch nicht gemeldete Warnung von heute und gestern; gibt die Anzahl zurueck.
    Ein Fehler von `senden` bricht ab; bis dahin Gemeldetes bleibt gemerkt."""
    grenze_tag = f"{heute - timedelta(days=TAGE_MERKEN):%Y-%m-%d}"
    gemeldet = [s for s in _zustand_lesen(zustand) if s.split("|", 1)[0] >= grenze_tag]
    # Vor jedem Versand: Zustand schreibbar? (alte Tage fallen dabei weg)
    _zustand_schreiben(zustand, gemeldet)
    anzahl = 0
    for tag in (heute - timedelta(days=1), heute):
        for agent, stand, grenze in _warnungen(ordner, tag):
            schluessel = f"{tag:%Y-%m-%d}|{agent}"
            if schluessel in gemeldet:
                continue
            senden(nachricht(f"{tag:%Y-%m-%d}", agent, stand, grenze))
            gemeldet.append(schluessel)
            anzahl += 1
            _zustand_schreiben(zustand, gemeldet)  # nach jedem Versand: kein Doppel bei Abbruch
    return anzahl


def telegram_senden(token: str, chat_id: str, text: str, *,
                    oeffnen: Callable[..., Any] = urllib.request.urlopen) -> None:
    anfrage = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with oeffnen(anfrage, timeout=20) as antwort:
            daten = json.loads(antwort.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as fehler:
        raise MeldeFehler(f"Telegram antwortet mit {fehler.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as fehler:
        raise MeldeFehler(f"Telegram nicht erreichbar ({type(fehler).__name__})") from None
    if not isinstance(daten, dict) or daten.get("ok") is not True:
        raise MeldeFehler("Telegram hat die Nachricht abgelehnt")


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    if argv != ["lauf"]:
        print(__doc__, file=sys.stderr)
        return 2
    fehlt = [n for n in NOETIG if not (env.get(n) or "").strip()]
    if fehlt:
        print("budget_melden: Einstellung fehlt: " + ", ".join(fehlt), file=sys.stderr)
        return 2
    try:
        token = Path(env["BUDGET_TELEGRAM_TOKEN_FILE"]).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        print("budget_melden: BUDGET_TELEGRAM_TOKEN_FILE nicht lesbar", file=sys.stderr)
        return 2
    if not token:
        print("budget_melden: BUDGET_TELEGRAM_TOKEN_FILE ist leer", file=sys.stderr)
        return 2
    chat = env["BUDGET_TELEGRAM_CHAT_ID"].strip()
    try:
        anzahl = neue_melden(Path(env["BUDGET_ORDNER"]), Path(env["BUDGET_ZUSTAND"]),
                             lambda text: telegram_senden(token, chat, text), date.today())
    except MeldeFehler as fehler:
        print(f"budget_melden: {fehler}", file=sys.stderr)
        return 1
    print(f"budget_melden: {anzahl} Warnung(en) gemeldet", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

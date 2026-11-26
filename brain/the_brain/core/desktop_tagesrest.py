"""Wie viele Laeufe hat brain-desktop heute noch? Liest nur, bucht nichts.

Die harte Grenze setzt der Budget-Waechter am Abo-Wrapper bei jedem einzelnen Lauf. Diese
Zahl dient der Desktop-Schleife nur dazu, vor dem Start die Rundenzahl zu begrenzen, damit
eine Aufgabe nicht mitten im Lauf am Budget haengen bleibt.

Zwei Angaben aus der Umgebung, beide noetig:
- VIBEMIND_BUDGET_CONFIG: Pfad zur agent_budget.yaml (Feld agenten.<name>.tagesgrenze)
- VIBEMIND_BUDGET_DIR: Ordner mit den Tagesdateien JJJJ-MM-TT.jsonl des Waechters

Fehlt etwas oder ist etwas unlesbar, ist der Rest 0. Dann startet die Schleife nicht.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Mapping, Optional

AGENT = "brain-desktop"
ZEITZONE = "Europe/Berlin"


def _heute(umgebung: Mapping[str, str], jetzt: Optional[datetime]) -> str:
    if jetzt is not None:
        return f"{jetzt:%Y-%m-%d}"
    try:
        from zoneinfo import ZoneInfo

        return f"{datetime.now(ZoneInfo(umgebung.get('VIBEMIND_BUDGET_TZ') or ZEITZONE)):%Y-%m-%d}"
    except Exception:  # noqa: BLE001 - ohne Zeitzonendaten gilt die Uhr des Rechners
        return f"{datetime.now():%Y-%m-%d}"


def tagesrest(
    agent: str = AGENT,
    *,
    umgebung: Optional[Mapping[str, str]] = None,
    jetzt: Optional[datetime] = None,
) -> int:
    u = os.environ if umgebung is None else umgebung
    try:
        config_pfad, ordner = u.get("VIBEMIND_BUDGET_CONFIG"), u.get("VIBEMIND_BUDGET_DIR")
        if not config_pfad or not ordner:
            return 0
        import yaml

        config = yaml.safe_load(Path(config_pfad).read_text(encoding="utf-8"))
        grenze = config["agenten"][agent]["tagesgrenze"]
        if not isinstance(grenze, int) or isinstance(grenze, bool) or grenze <= 0:
            return 0
        if (Path(ordner) / "STOP").exists():
            return 0
        datei = Path(ordner) / f"{_heute(u, jetzt)}.jsonl"
        verbraucht = 0
        if datei.exists():
            for zeile in datei.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    z = json.loads(zeile)
                except ValueError:
                    continue
                if (isinstance(z, dict) and z.get("art") == "pruefung"
                        and z.get("agent") == agent and z.get("erlaubt") is True):
                    verbraucht += 1
        return max(0, grenze - verbraucht)
    except Exception:  # noqa: BLE001 - jeder Fehler heisst: kein Rest
        return 0


KOPF_EINGABE = "X-Vibemind-Eingabe"
SCHLUESSEL_NAME = "VIBEMIND_EINGABE_SCHLUESSEL"
SCHLUESSEL_MINDESTLAENGE = 32
ZEICHEN_FENSTER_S = 60


def eingabe_zeichen(schluessel: str, satz: str, zeit: int) -> str:
    """Zeichen der App fuer genau diesen Satz zu genau dieser Zeit: "<zeit>.<hmac-sha256>"."""
    import hashlib
    import hmac

    stoff = f"{int(zeit)}\n{satz}".encode("utf-8")
    return f"{int(zeit)}." + hmac.new(schluessel.encode("utf-8"), stoff, hashlib.sha256).hexdigest()


def herkunft_pruefen(
    kontext: object,
    satz: str,
    zeichen: object,
    *,
    plan_vom_aufrufer: bool,
    umgebung: Optional[Mapping[str, str]] = None,
    jetzt_ts: Optional[float] = None,
) -> Optional[str]:
    """"getippt" nur, wenn die App es fuer genau diesen Satz mit dem gemeinsamen Schluessel belegt.

    Das Feld im Kontext allein genuegt nicht: Das koennte jeder Prozess am Rechner behaupten.
    Ein mitgebrachter Plan gilt nie als getippt, auch nicht mit gueltigem Zeichen.
    """
    import hmac
    import time

    if plan_vom_aufrufer or not isinstance(kontext, dict) or kontext.get("eingabe") != "getippt":
        return None
    from .geheimnis import geheimnis

    schluessel = geheimnis(SCHLUESSEL_NAME, umgebung)
    if len(schluessel) < SCHLUESSEL_MINDESTLAENGE or not isinstance(zeichen, str) or not satz:
        return None
    zeit_text, _, _ = zeichen.partition(".")
    if not zeit_text.isdigit() or len(zeit_text) > 12:
        return None
    jetzt = time.time() if jetzt_ts is None else jetzt_ts
    if abs(jetzt - int(zeit_text)) > ZEICHEN_FENSTER_S:
        return None
    if not hmac.compare_digest(zeichen, eingabe_zeichen(schluessel, satz, int(zeit_text))):
        return None
    return "getippt"

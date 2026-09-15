"""Prüft für jede vertraglich erfasste Capability, ob ihr Ziel erreichbar ist.

Warum es das gibt
-----------------
Der Intake (`space_cli intake`) misst den VERTRAG: hat eine schreibende
Capability einen unabhängigen Validator, gibt es das Ausführungsziel
überhaupt. Er sagt nichts darüber, ob das Ziel gerade antwortet — und noch
weniger, ob dort der RICHTIGE Dienst antwortet.

Genau diese Lücke hat am 12.09. einen echten Fehler verdeckt: alle sieben
mirofish-Capabilities zeigen auf `127.0.0.1:5001`, wo aber die Brain-API
sitzt. Kein fehlendes Ziel, kein toter Port — ein lebender, fremder Dienst,
der brav 404 zurückgibt. Aus der Konfiguration ist das nicht zu sehen.

Deshalb wird hier jede Endstelle wirklich angesprochen.

Drei Zustände, die nicht verwechselt werden dürfen
--------------------------------------------------
    ok       Das Ziel antwortet.
    tot      Niemand antwortet. Das ist meist ein LAUFZUSTAND, kein Defekt:
             der Dienst ist schlicht nicht gestartet.
    falsch   Es antwortet jemand, aber der Falsche. Das ist ein Defekt, und
             der gefährlichste der drei — er sieht im Log wie ein Fehler des
             Zielsystems aus.

Aufruf:  python scripts/space_reachability.py [--json]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import socket
import sys
import urllib.parse
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

try:
    import requests
except ImportError:  # pragma: no cover - Betriebswerkzeug, kein Testpfad
    print("requests fehlt - pip install requests", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parent.parent
CAPABILITIES = ROOT / "brain" / "the_brain" / "data" / "capabilities.yaml"
CONTRACTS = ROOT / "spaces" / "_contract"

OK, TOT, FALSCH, FEHLT, UNBEKANNT = "ok", "tot", "falsch", "fehlt", "unbekannt"


def _laedt(pfad: Path) -> Any:
    return yaml.safe_load(io.open(pfad, encoding="utf-8").read())


def _port_offen(host: str | None, port: int | None, timeout: float = 1.5) -> bool:
    if not host or not port:
        return False
    verbindung = socket.socket()
    verbindung.settimeout(timeout)
    try:
        verbindung.connect((host, int(port)))
        return True
    except Exception:
        return False
    finally:
        verbindung.close()


def _erreichbar(basis: str) -> bool:
    """Ein echter Verbindungsversuch - NICHT die Windows-Portliste.

    `Get-NetTCPConnection` zeigt Dienste aus WSL im gespiegelten Modus nicht
    an, obwohl sie über den Loopback antworten. Wer dort nachsieht, hält
    laufende Dienste für tot.
    """
    teile = urllib.parse.urlsplit(basis)
    vorgabe = 443 if teile.scheme == "https" else 80
    return _port_offen(teile.hostname, teile.port or vorgabe)


_gemessen: Dict[Tuple[str, str], Tuple[str, str]] = {}


def _probe(kind: str) -> Tuple[str, str]:
    if (kind, "") in _gemessen:
        return _gemessen[(kind, "")]
    ergebnis = _messen(kind)
    _gemessen[(kind, "")] = ergebnis
    return ergebnis


def _messen(kind: str) -> Tuple[str, str]:
    if kind == "supabase":
        basis = os.environ.get("SUPABASE_URL", "http://192.168.178.65:54321").rstrip("/")
        try:
            antwort = requests.get(basis + "/rest/v1/", headers={"apikey": "anon"}, timeout=5)
        except Exception as exc:
            return TOT, f"Supabase {basis}: {type(exc).__name__}"
        if antwort.status_code < 400:
            return OK, f"Supabase {basis}"
        return TOT, f"Supabase {basis}: HTTP {antwort.status_code}"

    if kind == "direct":
        return OK, "im Prozess der Brain"

    if kind in ("openfang", "mcp", "research"):
        # research: geht über einen OpenFang-Agenten, mcp: über dessen Bindung.
        if _port_offen("127.0.0.1", 4200):
            return OK, "OpenFang :4200"
        return TOT, "OpenFang :4200 laeuft nicht"

    if kind == "mirofish":
        basis = os.environ.get("MIROFISH_BASE_URL", "http://127.0.0.1:5001").rstrip("/")
        try:
            antwort = requests.post(basis + "/api/graph/build",
                                    json={"project_id": "erreichbarkeitsprobe"}, timeout=4)
        except Exception as exc:
            return TOT, f"mirofish {basis}: {type(exc).__name__}"
        if antwort.status_code != 404:
            return OK, f"mirofish {basis}: HTTP {antwort.status_code}"
        fremd = ""
        try:
            wurzel = requests.get(basis + "/", timeout=3).text[:300]
            if "Tahlamus" in wurzel:
                fremd = " - dort antwortet die Brain-API (Tahlamus)"
        except Exception:
            pass
        return FALSCH, f"404 auf /api/graph/build unter {basis}{fremd}"

    if kind == "coding-engine":
        if _port_offen("127.0.0.1", 8140):
            return OK, "coding-engine :8140"
        return TOT, "coding-engine :8140 laeuft nicht"

    return UNBEKANNT, f"unbekannte Zielart '{kind}'"


# `direct:`-Ziele laufen im Prozess, rufen von dort aber oft weiter. Was
# wirklich zaehlt, ist die zweite Strecke.
_WEITERRUFER = {
    "spaces.minibook.tools.minibook_tools": ("MINIBOOK_URL", "http://127.0.0.1:8800"),
    "spaces.schedule.execution": ("SUPABASE_URL", None),
    "spaces.video.execution_target": (None, None),
}


def _weiterruf(ziel: str) -> Tuple[str, str] | None:
    teile = ziel.split(":")
    modul = teile[1] if len(teile) >= 3 else ""
    eintrag = _WEITERRUFER.get(modul)
    if eintrag is None:
        return None
    variable, vorgabe = eintrag
    if variable == "SUPABASE_URL":
        return _probe("supabase")
    if variable is None:
        return OK, "lokale Dateien"
    basis = (os.environ.get(variable) or vorgabe or "").rstrip("/")
    if not basis:
        return UNBEKANNT, f"{variable} nicht gesetzt und kein Vorgabewert"
    if not _erreichbar(basis):
        return TOT, f"{basis} antwortet nicht"
    return OK, basis


def messen() -> List[Dict[str, str]]:
    caps = {c["capability"]: c for c in _laedt(CAPABILITIES)
            if isinstance(c, dict) and c.get("capability")}
    zeilen: List[Dict[str, str]] = []
    for datei in sorted(CONTRACTS.glob("*.contract.yaml")):
        vertrag = _laedt(datei)
        space = vertrag["id"]
        for anspruch in vertrag.get("capabilities") or []:
            name = anspruch["name"]
            ziel = str((caps.get(name) or {}).get("execution_target") or "")
            if not ziel:
                zeilen.append({"space": space, "capability": name, "ziel": "",
                               "zustand": FEHLT,
                               "detail": "kein Ausfuehrungsziel (oft bewusst entfernt)"})
                continue
            kind = ziel.split(":", 1)[0]
            zustand, detail = _probe(kind)
            if kind == "direct":
                weiter = _weiterruf(ziel)
                if weiter:
                    zustand, detail = weiter
            zeilen.append({"space": space, "capability": name, "ziel": ziel,
                           "zustand": zustand, "detail": detail})
    return zeilen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Rohdaten statt Tabelle")
    argumente = parser.parse_args()

    zeilen = messen()
    if argumente.json:
        print(json.dumps(zeilen, indent=2, ensure_ascii=False))
        return 0

    je_space: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for zeile in zeilen:
        je_space[zeile["space"]][zeile["zustand"]] += 1

    print("Erreichbarkeit je Space")
    print("-" * 74)
    print(f"{'Space':<10} {'ok':>4} {'tot':>5} {'falsch':>7} {'fehlt':>6}   Ursache")
    for space in sorted(je_space):
        zahlen = je_space[space]
        ursache = next((z["detail"] for z in zeilen
                        if z["space"] == space and z["zustand"] in (TOT, FALSCH)), "")
        print(f"{space:<10} {zahlen[OK]:>4} {zahlen[TOT]:>5} {zahlen[FALSCH]:>7} "
              f"{zahlen[FEHLT]:>6}   {ursache}")

    falsch = [z for z in zeilen if z["zustand"] == FALSCH]
    if falsch:
        print()
        print("FALSCHES ZIEL - es antwortet jemand, aber der Falsche:")
        for zeile in falsch:
            print(f"  {zeile['space']:<9} {zeile['capability']:<24} {zeile['detail']}")

    ok = sum(1 for z in zeilen if z["zustand"] == OK)
    tot = sum(1 for z in zeilen if z["zustand"] == TOT)
    print()
    print(f"{ok} von {len(zeilen)} Capabilities haben ein erreichbares Ziel.")
    print(f"{tot} sind unerreichbar, weil ein Dienst nicht laeuft - das ist ein "
          f"Laufzustand, kein Defekt.")
    print(f"{len(falsch)} zeigen auf den FALSCHEN Dienst - das ist einer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

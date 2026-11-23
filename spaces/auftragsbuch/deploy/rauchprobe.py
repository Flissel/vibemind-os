"""deploy/rauchprobe.py -- Rauchprobe des Dienstes auftragsbuch vom PC aus, ueber die echte Bruecke.

Nur Standardbibliothek (Python 3.11). Adresse und Schluessel kommen aus der Root-.env des PC, ohne
je auf einer Kommandozeile zu stehen:

  python <aeusseres-repo>/scripts/mcp_mit_env.py --env AUFTRAGSBUCH_URL --env AUFTRAGSBUCH_SCHLUESSEL \\
    --env AUFTRAGSBUCH_CA_DATEI -- <vibemind-os>/spaces/auftragsbuch/deploy/rauchprobe.py

AUFTRAGSBUCH_CA_DATEI (optional, Pfad einer PEM-Datei mit CA-Zertifikaten) geht mit der ganzen
Umgebung an die Bruecke; die Rauchprobe liest sie nicht selbst.

Startet bruecke.py als Unterprozess (wie OpenFang) und prueft:
  1. initialize antwortet,
  2. tools/list zeigt genau die fuenf Werkzeuge,
  3. tools/call sales_post_pruefen mit gueltiger Eingabe endet mit `Unbekannte Auftragsart`
     (in Stufe 1 hat brain_arten keine Zeile fuer sales; die Datenbank legt nichts an).
Ausgabe je Pruefung nur `ok ...` oder `FEHLER ... (<fester Grund>)`, nie Adresse, Schluessel oder
Inhalte des Dienstes; als Grund erscheinen nur die festen Meldungen der Bruecke. Exit 0, wenn alle
drei ok sind, sonst 1; Exit 2, wenn eine Einstellung fehlt.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

BRUECKE = Path(__file__).resolve().parents[1] / "bruecke.py"
ERWARTETE_WERKZEUGE = ("auftrag_lesen", "auftrag_probe", "rowboat_vorschlaege_anlegen", "sales_kontakt_finden", "sales_post_pruefen")
GUELTIGE_EINGABE = {"zeitraum": "heute", "entwuerfe_anlegen": False}
ABLEHNUNG = "Unbekannte Auftragsart"
ZEITLIMIT_S = 150
# Die festen Fehlermeldungen der Bruecke (bruecke.py); alles andere wird nicht durchgereicht.
_BRUECKEN_MELDUNG = re.compile(
    r"auftragsbuch(?: nicht erreichbar|: Schlüssel abgelehnt|: unerwartete Antwort \([0-9]{3}\)"
    r"|: ungültige Zeile|: Zertifikat nicht pruefbar)"
)


def anfragen() -> list[dict]:
    """Die JSON-RPC-Zeilen der Probe, in dieser Reihenfolge (die Notification bekommt keine Antwort)."""
    return [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "auftragsbuch-rauchprobe", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "sales_post_pruefen", "arguments": {"eingabe": GUELTIGE_EINGABE}}},
    ]


def _fehlergrund(antwort: object) -> str | None:
    """None, wenn die Antwort ein Ergebnis traegt; sonst ein fester Grund."""
    if not isinstance(antwort, dict):
        return "keine Antwort"
    if "error" in antwort:
        fehler = antwort.get("error")
        text = fehler.get("message") if isinstance(fehler, dict) else None
        if isinstance(text, str) and _BRUECKEN_MELDUNG.fullmatch(text):
            return text
        return "Fehlerantwort"
    if not isinstance(antwort.get("result"), dict):
        return "Antwort ohne Ergebnis"
    return None


def _pruefe_initialize(ergebnis: dict) -> tuple[bool, str]:
    ok = isinstance(ergebnis.get("protocolVersion"), str) and isinstance(ergebnis.get("serverInfo"), dict)
    return (True, "") if ok else (False, "Antwort ohne protocolVersion/serverInfo")


def _pruefe_tools_list(ergebnis: dict) -> tuple[bool, str]:
    werkzeuge = ergebnis.get("tools")
    namen = sorted(w.get("name") for w in werkzeuge if isinstance(w, dict)) if isinstance(werkzeuge, list) else []
    if namen == sorted(ERWARTETE_WERKZEUGE):
        return True, "fuenf Werkzeuge"
    return False, "nicht genau die fuenf Werkzeuge"


def _pruefe_tools_call(ergebnis: dict) -> tuple[bool, str]:
    if ergebnis.get("isError") is not True:
        return False, "Dienst hat angenommen statt abgelehnt, sofort melden"
    inhalt = ergebnis.get("content")
    text = inhalt[0].get("text") if isinstance(inhalt, list) and inhalt and isinstance(inhalt[0], dict) else None
    if text == ABLEHNUNG:
        return True, ABLEHNUNG
    return False, f"andere Ablehnung als {ABLEHNUNG}"


_PRUEFUNGEN = (
    ("initialize", 1, _pruefe_initialize),
    ("tools/list", 2, _pruefe_tools_list),
    ("tools/call sales_post_pruefen", 3, _pruefe_tools_call),
)


def auswerten(antworten: Mapping[int, dict]) -> list[tuple[str, bool, str]]:
    """(Pruefung, ok, Grund) je Pruefung; der Grund nennt nie Inhalte des Dienstes."""
    befund: list[tuple[str, bool, str]] = []
    for name, kennung, pruefe in _PRUEFUNGEN:
        antwort = antworten.get(kennung)
        grund = _fehlergrund(antwort)
        if grund is not None:
            befund.append((name, False, grund))
            continue
        assert isinstance(antwort, dict)
        ok, grund = pruefe(antwort["result"])
        befund.append((name, ok, grund))
    return befund


def antworten_lesen(ausgabe: str) -> dict[int, dict]:
    """Antwortzeilen der Bruecke nach ihrer Kennung."""
    antworten: dict[int, dict] = {}
    for zeile in ausgabe.splitlines():
        try:
            wert = json.loads(zeile)
        except ValueError:
            continue
        if isinstance(wert, dict) and isinstance(wert.get("id"), int):
            antworten[wert["id"]] = wert
    return antworten


def bruecke_fragen(umgebung: Mapping[str, str]) -> str:
    """Startet die Bruecke, schickt alle Anfragen und liefert ihre stdout (stderr wird verworfen)."""
    eingabe = "".join(json.dumps(a) + "\n" for a in anfragen())
    lauf = subprocess.run([sys.executable, str(BRUECKE)], input=eingabe.encode("utf-8"), capture_output=True,
                          env=dict(umgebung), timeout=ZEITLIMIT_S, check=False)
    return lauf.stdout.decode("utf-8", errors="replace")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    for name in ("AUFTRAGSBUCH_URL", "AUFTRAGSBUCH_SCHLUESSEL"):
        if not os.environ.get(name, "").strip():
            print(f"rauchprobe: {name} fehlt (Start ueber scripts/mcp_mit_env.py --env {name})", file=sys.stderr)
            return 2
    try:
        ausgabe = bruecke_fragen(os.environ)
    except subprocess.TimeoutExpired:
        ausgabe = ""
    alle_ok = True
    for name, ok, grund in auswerten(antworten_lesen(ausgabe)):
        alle_ok = alle_ok and ok
        zusatz = f" ({grund})" if grund else ""
        print(f"{'ok' if ok else 'FEHLER'} {name}{zusatz}", flush=True)
    return 0 if alle_ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""Die fremden Oberflaechen der Seite: heute nur die Live-Vorschau (noVNC).

"Auftraege" und "Vibe-Coder" sind Ansichten der Seite selbst; die Live-Vorschau
ist ein Rahmen auf eine lokale Adresse. Hier steht diese Adresse und ob dort
gerade etwas zu sehen ist. Nur Adressen auf diesem Rechner sind erlaubt; alles
andere faellt auf die Vorgabe zurueck.

Die Vorschau gilt nur als laufend, wenn noVNC antwortet, die Sandbox laeuft und
ihr eingehaengter Ordner zu einem Auftrag der Liste gehoert. Sonst zeigt die
Seite eine Karte statt eines Rahmens (Pruefung 28.11.: eine Sandbox auf einem
fremden Ordner zeigte eine rohe noVNC-Seite ohne Hinweis, welche App das ist).

Eingebunden wird ``vnc_lite.html``: nur das Bild, ohne die Bedienleiste von
``vnc.html`` (Shutdown, Reboot, Reset, Zwischenablage, Einstellungen).

Bis 28.11. gab es hier noch "Vibe-Coder" und "Engine" als Rahmen auf :5180, die
fremde Oberflaeche aus ``coding-engine/web-app/front``. Sie lief nicht und wird
nicht mehr eingebunden (Entscheid des Betreibers, P6 a).
"""

from __future__ import annotations

import os
import socket
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

from spaces.coding import pipeline, vorschau

KEINE = "Gerade läuft keine Vorschau."
VORGABEN = (
    ("vorschau", "Live-Vorschau", "CODING_OBERFLAECHE_VORSCHAU",
     "http://localhost:6090/vnc_lite.html?scale=true", KEINE),
)
ERLAUBTE_HOSTS = {"localhost", "127.0.0.1"}


def _lokal(url: Optional[str]) -> Optional[urllib.parse.SplitResult]:
    if not url:
        return None
    teile = urllib.parse.urlsplit(url.strip())
    if teile.scheme != "http" or teile.hostname not in ERLAUBTE_HOSTS or not teile.port:
        return None
    return teile


def adressen() -> List[Dict[str, str]]:
    """Name, Titel, Adresse und Satz je Knopf, Umgebung vor Vorgabe."""
    ergebnis = []
    for name, titel, variable, vorgabe, satz in VORGABEN:
        eigene = os.environ.get(variable)
        url = eigene.strip() if _lokal(eigene) else vorgabe
        ergebnis.append({"name": name, "titel": titel, "url": url, "satz_aus": satz})
    return ergebnis


def antwortet(url: str, warten: float = 0.3) -> bool:
    teile = _lokal(url)
    if teile is None:
        return False
    try:
        with socket.create_connection((teile.hostname, teile.port), timeout=warten):
            return True
    except OSError:
        return False


def _gleich(a: str, b: str) -> bool:
    return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))


def _auftrag_zu(ordner: str) -> Optional[Dict[str, Any]]:
    for auftrag in pipeline._alle():
        ziel = auftrag.get("zielordner")
        if ziel and _gleich(str(ziel), ordner):
            return auftrag
    return None


def _wann() -> str:
    if vorschau.an():
        return "Sie startet von selbst, sobald ein Lauf die Abnahme besteht."
    return "Der Autostart der Vorschau ist aus; sie startet erst, wenn er eingeschaltet ist."


def _vorschau(eintrag: Dict[str, Any]) -> Dict[str, Any]:
    aus = {**eintrag, "laeuft": False, "auftrag": None, "hinweis": _wann()}
    if not antwortet(eintrag["url"]):
        return aus
    sandbox = vorschau.lage()
    if not sandbox["laeuft"] or not sandbox["ordner"]:
        return aus
    auftrag = _auftrag_zu(sandbox["ordner"])
    if auftrag is None:
        return {**aus, "hinweis": "In der Sandbox liegt keine App eines Laufs aus der Liste. " + _wann()}
    return {**eintrag, "laeuft": True, "hinweis": None,
            "auftrag": {"id": auftrag.get("id"), "titel": auftrag.get("titel") or auftrag.get("id")}}


def stand() -> List[Dict[str, Any]]:
    return [_vorschau(e) if e["name"] == "vorschau" else {**e, "laeuft": antwortet(e["url"])} for e in adressen()]


def rahmen_quellen() -> str:
    """Ursprünge für frame-src in der CSP: genau die konfigurierten Adressen."""
    quellen = []
    for eintrag in adressen():
        teile = urllib.parse.urlsplit(eintrag["url"])
        ursprung = f"{teile.scheme}://{teile.hostname}:{teile.port}"
        if ursprung not in quellen:
            quellen.append(ursprung)
    return " ".join(quellen)

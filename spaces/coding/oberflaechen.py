"""Die Oberflaechen neben den Auftraegen: Live-Vorschau, Vibe-Coder, Engine.

Die Seite des Dienstes zeigt oben vier Knoepfe. "Auftraege" ist die Seite
selbst, die anderen drei sind Rahmen auf lokale Adressen. Hier steht, welche
Adresse zu welchem Knopf gehoert und ob dort gerade etwas antwortet. Nur
Adressen auf diesem Rechner sind erlaubt; alles andere faellt auf die Vorgabe
zurueck.
"""

from __future__ import annotations

import os
import socket
import urllib.parse
from typing import Dict, List, Optional

VORGABEN = (
    ("vorschau", "Live-Vorschau", "CODING_OBERFLAECHE_VORSCHAU",
     "http://localhost:6090/vnc.html?autoconnect=true&resize=scale&reconnect=true",
     "Die Live-Vorschau läuft nicht. Sie startet, sobald ein Lauf seine App in die Sandbox legt."),
    ("vibe", "Vibe-Coder", "CODING_OBERFLAECHE_VIBE", "http://localhost:5180/",
     "Der Vibe-Coder läuft nicht."),
    ("engine", "Engine", "CODING_OBERFLAECHE_ENGINE", "http://localhost:5180/engine",
     "Das Engine-Frontend läuft nicht."),
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


def stand() -> List[Dict[str, object]]:
    return [{**eintrag, "laeuft": antwortet(eintrag["url"])} for eintrag in adressen()]


def rahmen_quellen() -> str:
    """Ursprünge für frame-src in der CSP: genau die konfigurierten Adressen."""
    quellen = []
    for eintrag in adressen():
        teile = urllib.parse.urlsplit(eintrag["url"])
        ursprung = f"{teile.scheme}://{teile.hostname}:{teile.port}"
        if ursprung not in quellen:
            quellen.append(ursprung)
    return " ".join(quellen)

"""Laura-Passthrough — Videos und Transkripte als Belegquelle. NUR LESEND.

WARUM IM SIDECAR UND NICHT IM GATEWAY: derselbe Grund wie beim
Rowboat-Passthrough in `werkzeuge._rowboat` — Container erreichen im
WSL-Mirrored-Modus kein LAN (gemessen 02.09.2026), und ein etwaiges
LAURA_TOKEN bleibt so im Host-Prozess statt im Gateway-Volume.

WARUM EIN EIGENES MODUL UND KEIN ZWEITER ZWEIG IN `_api`: Laura ist ein
fremder Dienst mit eigener Adresse, eigener Kopfzeile (`X-Laura-Token`,
security.py:9-18) und eigenem Zeitlimit. Als eigenes Modul bleibt der
Fehlertext sprechend ("Laura nicht erreichbar" statt "Marketing-API ...").

DIE API HAT KEINE GLOBALE ASSET-LISTE. Assets haengen an Projekten
(`assets.py:139`), Transkripte liegen bei der Analyse (`analysis.py:116`),
und keine der Routen traegt ein `/api`-Praefix. Alles am 04.09.2026 an der
laufenden API gemessen, nicht aus der Dokumentation abgeschrieben.

Fail-soft ist der Vertrag: keine Funktion hier wirft, und in keinem
Fehlertext steht je ein Token.
"""
import json
import os
import urllib.parse
import urllib.request

FEHLER_MAXLAENGE = 300
ZEITLIMIT_SEKUNDEN = 20


def _ohne_token(text: str) -> str:
    wert = os.environ.get("LAURA_TOKEN", "")
    return text.replace(wert, "<token>") if wert else text


def _roh_anfrage(url: str, daten, kopfzeilen: dict) -> tuple:
    """Der einzige echte Netzgriff — Tests ersetzen genau diese Funktion.

    Gleiche Signatur wie `werkzeuge._roh_anfrage`, damit derselbe Rekorder
    aus den Tests hier passt.
    """
    anfrage = urllib.request.Request(
        url, data=daten, method="POST" if daten is not None else "GET",
        headers={"Content-Type": "application/json", **kopfzeilen})
    with urllib.request.urlopen(anfrage, timeout=ZEITLIMIT_SEKUNDEN) as antwort:
        return antwort.status, antwort.read().decode("utf-8", "replace")


def hole(pfad: str) -> dict:
    """Ein GET gegen Laura. Wirft nie; Rueckgabe {"ok": ...} wie ueberall."""
    basis = os.environ.get("LAURA_API_URL", "http://127.0.0.1:8765").strip().rstrip("/")
    kopfzeilen = {}
    token = os.environ.get("LAURA_TOKEN", "").strip()
    if token:
        kopfzeilen["X-Laura-Token"] = token
    try:
        status, rumpf = _roh_anfrage(basis + pfad, None, kopfzeilen)
        if status != 200:
            return {"ok": False, "fehler": _ohne_token(
                f"Laura HTTP {status}: {rumpf[:FEHLER_MAXLAENGE]}")}
        return {"ok": True, "daten": json.loads(rumpf)}
    except Exception as e:  # noqa: BLE001 — fail-soft ist der Vertrag
        return {"ok": False, "fehler": _ohne_token(
            f"Laura nicht erreichbar ({type(e).__name__}: {e})"[:FEHLER_MAXLAENGE])}


def kennung(wert: str) -> str:
    """Eine Kennung, die sicher in einen Pfad darf.

    `urllib.parse.quote` mit leerem `safe` — sonst wandert ein `../` aus dem
    Aufruf ungefiltert in die URL und zeigt auf eine fremde Route.
    """
    return urllib.parse.quote(wert.strip(), safe="")


def projekte() -> dict:
    """Die Projekte in Laura. Nur lesend."""
    return hole("/projects")


def projekt_assets(projekt_id: str) -> dict:
    """Die Assets EINES Projekts. Nur lesend."""
    return hole(f"/projects/{kennung(projekt_id)}/assets")


def alle_videos() -> dict:
    """Alle Assets ueber alle Projekte, jedes mit seinem Projektnamen.

    Zwei Stufen, weil die API keine globale Liste kennt. Ein einzelnes
    stummes Projekt kippt die Liste NICHT — sonst macht ein halbfertiges
    Projekt den ganzen Bestand unsichtbar; die Stummen stehen stattdessen
    unter "stumme_projekte", damit der Ausfall sichtbar bleibt.
    """
    liste = projekte()
    if not liste["ok"]:
        return liste
    gefunden, stumm = [], []
    for projekt in liste["daten"]:
        projekt_id = str(projekt.get("id", ""))
        antwort = projekt_assets(projekt_id)
        if not antwort["ok"]:
            stumm.append({"projekt": projekt.get("name", projekt_id),
                          "fehler": antwort["fehler"]})
            continue
        for asset in antwort["daten"]:
            gefunden.append({**asset, "projekt": projekt.get("name", projekt_id),
                             "projekt_id": projekt_id})
    ergebnis = {"ok": True, "daten": gefunden}
    if stumm:
        ergebnis["stumme_projekte"] = stumm
    return ergebnis


def transkript(video_id: str) -> dict:
    """Das Transkript eines Assets (Segmente mit Zeitstempel). Nur lesend."""
    if not video_id or not video_id.strip():
        return {"ok": False, "fehler": "Ohne Video-Kennung gibt es kein Transkript."}
    return hole(f"/assets/{kennung(video_id)}/transcript")

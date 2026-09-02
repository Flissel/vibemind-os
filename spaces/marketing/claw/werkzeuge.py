"""marketing-claw-Werkzeuge — Staging rein, Artefakte raus, NIE senden.

Alle Netzpfade laufen ueber die Marketing-API :5510 (X-API-Key) oder den
Claude-Shim :8114. Es gibt hier bewusst keinen Weg zu einem Sendepfad:
kein send_*, kein approve_* — genehmigen tut der Mensch in der UI.

Fail-soft wie sales-claws rowboat.py: keine Funktion wirft; Rueckgabe ist
{"ok": True, ...} oder {"ok": False, "fehler": "..."} — und in keinem
Fehlertext steht je ein Schluessel.
"""
import json
import os
import urllib.request

FEHLER_MAXLAENGE = 300


def _ohne_schluessel(text: str) -> str:
    for name in ("MARKETING_API_KEY", "MARKETING_PROPOSAL_API_KEY"):
        wert = os.environ.get(name, "")
        if wert and wert in text:
            text = text.replace(wert, "<schluessel>")
    return text


def _roh_anfrage(url: str, daten, kopfzeilen: dict) -> tuple:
    """Der einzige echte Netzgriff — Tests ersetzen genau diese Funktion."""
    anfrage = urllib.request.Request(
        url, data=daten, method="POST" if daten is not None else "GET",
        headers={"Content-Type": "application/json", **kopfzeilen})
    with urllib.request.urlopen(anfrage, timeout=60) as antwort:
        return antwort.status, antwort.read().decode("utf-8", "replace")


def _api(pfad: str, nutzlast: dict | None = None) -> dict:
    """GET (nutzlast=None) oder POST gegen die Marketing-API. Wirft nie."""
    basis = os.environ.get("MARKETING_API_URL", "http://127.0.0.1:5510").rstrip("/")
    daten = None if nutzlast is None else json.dumps(nutzlast).encode("utf-8")
    try:
        status, rumpf = _roh_anfrage(
            basis + pfad, daten, {"X-API-Key": os.environ.get("MARKETING_API_KEY", "")})
        if status != 200:
            return {"ok": False, "fehler": _ohne_schluessel(
                f"Marketing-API HTTP {status}: {rumpf[:FEHLER_MAXLAENGE]}")}
        return {"ok": True, "daten": json.loads(rumpf)}
    except Exception as e:  # noqa: BLE001 — fail-soft ist der Vertrag
        return {"ok": False, "fehler": _ohne_schluessel(
            f"Marketing-API nicht erreichbar ({type(e).__name__}: {e})")}


def statistik() -> dict:
    """Kennzahlen des Marketing-Space (accounts, Kampagnen, Audit-Stand)."""
    return _api("/api/stats")

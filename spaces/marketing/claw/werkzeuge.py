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

from spaces.marketing.claw import llm, schaufenster

FEHLER_MAXLAENGE = 300


def _ohne_schluessel(text: str) -> str:
    for name in ("MARKETING_API_KEY", "MARKETING_PROPOSAL_API_KEY", "ROWBOAT_API_KEY"):
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


def _rowboat(werkzeug: str, argumente: dict) -> dict:
    """Ein Lese-Werkzeugaufruf gegen Rowboats MCP-Endpunkt auf der VM.

    PASSTHROUGH IM SIDECAR, NICHT IM GATEWAY — Absicht, kein Notbehelf:
    Container erreichen im WSL-Mirrored-Modus kein LAN (gemessen 02.09.2026),
    und so bleibt der Bearer-Schluessel im Host-Prozess statt im
    Gateway-Volume. Die Projektbindung ist fest: projectId kommt aus der
    Env, nie vom Aufrufer — der Agent kann keine fremden Projekte anfragen.
    """
    basis = os.environ.get("ROWBOAT_URL", "").strip().rstrip("/")
    schluessel = os.environ.get("ROWBOAT_API_KEY", "").strip()
    fehlt = [n for n, w in (("ROWBOAT_URL", basis), ("ROWBOAT_API_KEY", schluessel),
                            ("ROWBOAT_PROJECT_ID", os.environ.get("ROWBOAT_PROJECT_ID", "").strip()))
             if not w]
    if fehlt:
        return {"ok": False, "fehler": "Wissensbasis nicht eingerichtet, es fehlt: " + ", ".join(fehlt)}
    nutzlast = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": werkzeug, "arguments": argumente}}
    try:
        status, rumpf = _roh_anfrage(
            basis + "/api/mcp", json.dumps(nutzlast).encode("utf-8"),
            {"Authorization": f"Bearer {schluessel}"})
        if status != 200:
            return {"ok": False, "fehler": _ohne_schluessel(
                f"Wissensbasis HTTP {status}: {rumpf[:FEHLER_MAXLAENGE]}")}
        antwort = json.loads(rumpf)
        if "error" in antwort:
            return {"ok": False, "fehler": _ohne_schluessel(
                str(antwort["error"].get("message", ""))[:FEHLER_MAXLAENGE])}
        ergebnis = antwort["result"]
        text = ergebnis["content"][0]["text"]
        if ergebnis.get("isError"):
            return {"ok": False, "fehler": _ohne_schluessel(text[:FEHLER_MAXLAENGE])}
        return {"ok": True, "daten": json.loads(text)}
    except Exception as e:  # noqa: BLE001 — fail-soft ist der Vertrag
        return {"ok": False, "fehler": _ohne_schluessel(
            f"Wissensbasis nicht erreichbar ({type(e).__name__}: {e})")}


def wissensquellen() -> dict:
    """Die Wissensquellen des VibeMind-Projekts (Name, Status). Nur lesend."""
    return _rowboat("rowboat_wissensquellen",
                    {"projectId": os.environ.get("ROWBOAT_PROJECT_ID", "").strip()})


def wissensquelle(quellen_id: str) -> dict:
    """Eine Wissensquelle im Detail. Nur lesend."""
    return _rowboat("rowboat_wissensquelle", {"sourceId": quellen_id})


def dokumente(quellen_id: str, mit_inhalt: bool = False) -> dict:
    """Dokumente einer Wissensquelle, optional mit Text. Nur lesend."""
    return _rowboat("rowboat_dokumente",
                    {"sourceId": quellen_id, "mitInhalt": mit_inhalt is True})


def publikum_vorschlagen(name: str, kriterien: dict, begruendung: str = "") -> dict:
    """Publikums-VORSCHLAG in die Staging-Tuer /api/proposals — genehmigen
    tut der Mensch. source ist fest 'marketing-claw' (unbekannte sources
    normalisiert der Server zu hand:unknown — sichtbar im Audit, gewollt)."""
    if not isinstance(kriterien, dict):
        return {"ok": False, "fehler": "kriterien muss ein Objekt sein"}
    return _api("/api/proposals", {
        "api_key": os.environ.get("MARKETING_PROPOSAL_API_KEY", ""),
        "name": name,
        "filter_dsl": kriterien,
        "rationale": begruendung,
        "source": "marketing-claw",
    })


def posteingang_lesen() -> dict:
    """Eingegangene Nachrichten (marketing.inbound_messages) — nur lesen."""
    return _api("/api/inbox")


def kampagnen_auflisten() -> dict:
    """Bestehende Kampagnen — nur lesen."""
    return _api("/api/campaigns")


def _llm_json(system: str, nutzer: str) -> dict:
    """LLM fragen und die Antwort als JSON-Objekt lesen — fail-soft."""
    r = llm.frage(system, nutzer)
    if not r["ok"]:
        return r
    text = r["text"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return {"ok": True, "daten": json.loads(text)}
    except Exception:  # noqa: BLE001
        return {"ok": False, "fehler": "LLM-Antwort war kein JSON"}


def kampagne_entwerfen(ziel: str, zielgruppe: str, kanal: str, kontext: str = "") -> dict:
    """Entwirft eine Kampagne: Briefing + Text als broadcast_proposals-Draft
    (Status draft — versendet NIE) plus Dateien im Schaufenster."""
    r = _llm_json(
        "Du bist Marketing-Texter fuer VibeMind. Antworte NUR mit einem "
        'JSON-Objekt {"betreff": ..., "text": ..., "begruendung": ...}. '
        "Deutsch, konkret, keine Superlative ohne Beleg.",
        f"Kampagnenziel: {ziel}\nZielgruppe: {zielgruppe}\nKanal: {kanal}\n"
        f"Kontext:\n{kontext}")
    if not r["ok"]:
        return r
    entwurf = r["daten"]
    antwort = _api("/api/curator/broadcast_proposals", {
        "api_key": os.environ.get("MARKETING_PROPOSAL_API_KEY", ""),
        "channel": kanal,
        "draft_subject": str(entwurf.get("betreff", "")),
        "draft_body_text": str(entwurf.get("text", "")),
        "actor": "marketing-claw",
    })
    if not antwort["ok"]:
        return antwort
    # Die Route antwortet {success, data: {id, status}} — id liegt unter data.
    proposal_id = (antwort["daten"].get("data") or {}).get("id") or antwort["daten"].get("id")
    briefing = (f"# Kampagne: {ziel}\n\nZielgruppe: {zielgruppe}\nKanal: {kanal}\n"
                f"Proposal: {proposal_id or '?'} (Status draft — versendet nichts)\n\n"
                f"## Betreff\n{entwurf.get('betreff', '')}\n\n## Text\n{entwurf.get('text', '')}\n\n"
                f"## Begruendung\n{entwurf.get('begruendung', '')}\n")
    dateien = [schaufenster.ablegen(ziel, "briefing.md", briefing)]
    return {"ok": True, "proposal_id": proposal_id, "dateien": dateien}


def ad_texte_entwerfen(thema: str, n: int = 3) -> dict:
    """n Ad-Text-Varianten als Schaufenster-Dateien. Kein Draft, kein Versand."""
    n = max(1, min(int(n), 10))
    dateien = []
    for i in range(1, n + 1):
        r = llm.frage(
            "Du bist Performance-Marketing-Texter. Eine Ad-Variante, Deutsch, "
            "max 300 Zeichen Haupttext + Ueberschrift. Nur der Anzeigentext.",
            f"Thema: {thema}\nVariante {i} von {n} — deutlich anders als die vorigen.")
        if not r["ok"]:
            return {"ok": False, "fehler": r["fehler"], "dateien": dateien}
        dateien.append(schaufenster.ablegen(thema, f"ad-{i:02d}.md", r["text"]))
    return {"ok": True, "dateien": dateien}


def layout_entwerfen(thema: str, format: str = "landingpage") -> dict:
    """Ein HTML-Layout (eine Datei, keine externen Abhaengigkeiten) ins Schaufenster."""
    r = llm.frage(
        "Du bist Web-Designer. Antworte NUR mit einer vollstaendigen "
        "HTML-Datei (inline CSS, keine externen Ressourcen, Deutsch).",
        f"Format: {format}\nThema: {thema}\nZweck: Entwurf zur Qualitaetsbewertung.")
    if not r["ok"]:
        return r
    text = r["text"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    kurz = "".join(c if c.isalnum() else "-" for c in format.lower())[:30]
    return {"ok": True, "dateien": [schaufenster.ablegen(thema, f"layout-{kurz}.html", text)]}

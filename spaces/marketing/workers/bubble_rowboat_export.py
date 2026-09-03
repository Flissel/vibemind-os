"""bubble_rowboat_export — klassifizierte Bubbles als Metadaten nach Rowboat.

WOFUER. Der Klassifikator sortiert Bubbles in marketing / crowdfunding /
code_project / research / general. Diese Einordnung ist fuer beide Agenten
wertvoll: marketing-claw sieht, welche Ideen Kampagnenstoff sind, sales-claw
sieht, woran gerade gebaut wird. Rowboat ist die Metaebene, auf der beide
lesen duerfen — also spiegelt dieser Worker die Einordnung dorthin.

NUR EINE RICHTUNG. Bubbles bleiben in Supabase die Wahrheit; Rowboat traegt
eine lesbare Kopie. Nichts liest von Rowboat zurueck in die Bubbles.

IDEMPOTENT. Je Bubble ein Dokument mit festem Namen (`bubble-<id>`). Vor dem
Schreiben werden die vorhandenen Dokumente gelesen; nur was fehlt oder sich
geaendert hat, wird geschrieben. Rowboat versioniert gleichnamige Dokumente
selbst — ohne diesen Vergleich waechst die Quelle bei jedem Lauf.

Aufruf:
    python -m spaces.marketing.workers.bubble_rowboat_export --once
    python -m spaces.marketing.workers.bubble_rowboat_export --once --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path

PKG_ROOT = next(p.parent for p in Path(__file__).resolve().parents if p.name == "spaces")
REPO_ROOT = next((p for p in (PKG_ROOT, *PKG_ROOT.parents) if (p / "vibemind-os").is_dir()), PKG_ROOT)
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from spaces.marketing.sync import _db  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("marketing.bubble_rowboat_export")

_ENV_KEYS = ("ROWBOAT_URL", "ROWBOAT_PROJECT_ID", "ROWBOAT_API_KEY",
             "BUBBLE_EXPORT_SOURCE_ID")
_POLL_INTERVAL_S = float(os.environ.get("BUBBLE_EXPORT_POLL_S", "300"))
_STAPEL = int(os.environ.get("BUBBLE_EXPORT_BATCH", "50"))


def _load_env_fallback() -> None:
    """Fehlende Schluessel aus der repo-.env nachladen (nie ueberschreiben)."""
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    fehlend = [k for k in _ENV_KEYS if not os.environ.get(k)]
    if not fehlend:
        return
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        for k in fehlend:
            if line.startswith(k + "="):
                os.environ[k] = line.split("=", 1)[1].strip().strip('"').strip("'")


def _ohne_schluessel(text: str) -> str:
    wert = os.environ.get("ROWBOAT_API_KEY", "")
    return text.replace(wert, "<schluessel>") if wert and wert in text else text


def _rowboat(werkzeug: str, argumente: dict) -> dict:
    """Ein Werkzeugaufruf gegen Rowboats MCP-Endpunkt. Wirft nie."""
    basis = os.environ.get("ROWBOAT_URL", "").strip().rstrip("/")
    schluessel = os.environ.get("ROWBOAT_API_KEY", "").strip()
    if not basis or not schluessel:
        return {"ok": False, "fehler": "ROWBOAT_URL/ROWBOAT_API_KEY fehlen"}
    nutzlast = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": werkzeug, "arguments": argumente}}
    anfrage = urllib.request.Request(
        basis + "/api/mcp", data=json.dumps(nutzlast).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {schluessel}"})
    try:
        with urllib.request.urlopen(anfrage, timeout=120) as antwort:
            gelesen = json.loads(antwort.read())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "fehler": _ohne_schluessel(f"{type(e).__name__}: {e}")}
    if "error" in gelesen:
        return {"ok": False, "fehler": _ohne_schluessel(str(gelesen["error"].get("message", "")))}
    ergebnis = gelesen["result"]
    text = ergebnis["content"][0]["text"]
    if ergebnis.get("isError"):
        return {"ok": False, "fehler": _ohne_schluessel(text)}
    try:
        return {"ok": True, "daten": json.loads(text)}
    except Exception:  # noqa: BLE001 — manche Werkzeuge antworten mit Klartext
        return {"ok": True, "daten": text}


def dokument_inhalt(bubble: dict) -> str:
    """Der Text eines Bubble-Dokuments. Stabil — er ist zugleich der Vergleich,
    an dem ein spaeterer Lauf erkennt, ob sich etwas geaendert hat."""
    grund = ""
    metadaten = bubble.get("metadata") or {}
    if isinstance(metadaten, dict):
        grund = str((metadaten.get("classifier") or {}).get("reason", ""))
    konfidenz = bubble.get("confidence")
    zeilen = [
        f"Bubble: {bubble.get('id', '')}",
        f"Titel: {bubble.get('title') or '(ohne Titel)'}",
        f"Kategorie: {bubble.get('category') or '(keine)'}",
        f"Konfidenz: {konfidenz if konfidenz is not None else '(unbekannt)'}",
        f"Klassifiziert am: {bubble.get('classified_at') or '(unbekannt)'}",
        "",
        "Begruendung der Einordnung:",
        grund or "(keine)",
        "",
        "Beschreibung:",
        (bubble.get("description") or "(keine)").strip(),
    ]
    return "\n".join(zeilen)


def _bestand(quellen_id: str, griff=None) -> dict:
    """name -> {'inhalte': [...], 'wartet': bool} ueber die vorhandenen Dokumente.

    `griff` ist der Rowboat-Zugriff; wer diese Funktion aus einem anderen
    Modul benutzt (laura_rowboat_export), reicht seinen eigenen herein —
    sonst zeigte der Vorgabewert an dessen Ersatz vorbei.

    ZWEI GEMESSENE EIGENHEITEN von Rowboat (02.09.2026), die den Vergleich
    bestimmen: (1) gleichnamige Dokumente werden NICHT versioniert, sondern
    doppelt angelegt — ein blindes Neuschreiben verdoppelt die Quelle bei
    jedem Lauf. (2) Der Inhalt erscheint erst, wenn der rag-worker das
    Dokument indexiert hat (Status pending -> ready, Sekunden bis Minuten);
    unmittelbar nach dem Schreiben ist `content` noch leer. Wer nur Inhalte
    vergleicht, haelt frisch Geschriebenes fuer fehlend und schreibt es
    erneut — genau so entstanden hier 14 Dokumente aus 7 Bubbles.
    """
    r = (griff or _rowboat)("rowboat_dokumente",
                            {"sourceId": quellen_id, "mitInhalt": True})
    if not r["ok"]:
        raise RuntimeError(f"Dokumente nicht lesbar: {r['fehler']}")
    daten = r["daten"] if isinstance(r["daten"], list) else []
    bestand: dict = {}
    for d in daten:
        eintrag = bestand.setdefault(d.get("name", ""), {"inhalte": [], "wartet": False})
        inhalt = d.get("content")
        if inhalt:
            eintrag["inhalte"].append(inhalt)
        else:
            eintrag["wartet"] = True
    return bestand


def exportieren(bubbles: list, dry_run: bool = False) -> int:
    """Schreibt fehlende/geaenderte Bubbles. Gibt die Anzahl zurueck."""
    quellen_id = os.environ.get("BUBBLE_EXPORT_SOURCE_ID", "").strip()
    if not quellen_id:
        raise RuntimeError(
            "BUBBLE_EXPORT_SOURCE_ID fehlt — Kennung der Rowboat-Quelle "
            "'Bubbles/Index — Klassifizierte Ideen' (aus rowboat_wissensquellen).")
    bestand = _bestand(quellen_id)

    zu_schreiben = []
    wartend = 0
    for b in bubbles:
        name = f"bubble-{b.get('id', '')}"
        inhalt = dokument_inhalt(b)
        eintrag = bestand.get(name)
        if eintrag is None:
            zu_schreiben.append({"name": name, "inhalt": inhalt})
            continue
        if inhalt in eintrag["inhalte"]:
            continue          # steht schon so drin
        if eintrag["wartet"]:
            wartend += 1      # frisch geschrieben, noch nicht indexiert — abwarten
            continue
        zu_schreiben.append({"name": name, "inhalt": inhalt})
    if wartend:
        logger.info("%d Dokument(e) noch nicht indexiert — dieser Lauf laesst sie in Ruhe", wartend)

    if not zu_schreiben:
        logger.info("nichts zu tun: %d Bubble(s) unveraendert", len(bubbles))
        return 0
    if dry_run:
        logger.info("dry-run: %d Dokument(e) wuerden geschrieben (%s)",
                    len(zu_schreiben), ", ".join(d["name"] for d in zu_schreiben[:5]))
        return len(zu_schreiben)

    r = _rowboat("rowboat_dokumente_schreiben",
                 {"sourceId": quellen_id, "dokumente": zu_schreiben})
    if not r["ok"]:
        raise RuntimeError(f"Schreiben abgelehnt: {r['fehler']}")
    logger.info("%d Dokument(e) geschrieben", len(zu_schreiben))
    return len(zu_schreiben)


def klassifizierte_bubbles(grenze: int = _STAPEL) -> list:
    """Bubbles MIT Kategorie aus Supabase, neueste Einordnung zuerst.

    Bedingung ist die Kategorie, NICHT `classified_at`: gemessen am
    02.09.2026 tragen die sieben eingeordneten Bubbles keinen Zeitstempel
    (von Hand gesetzt), waehrend die zehn mit Zeitstempel unter der
    Konfidenzschwelle blieben und darum gar keine Kategorie haben. Wer auf
    beides bestuende, exportierte nichts.
    """
    # Die Spalte heisst classification_confidence (gemessen 02.09.2026, nicht
    # `confidence` — und `query_via_docker` meldet einen SQL-Fehler NICHT, es
    # kommt einfach eine leere Liste zurueck; ein falscher Spaltenname sieht
    # also aus wie „nichts zu tun").
    return _db.query_via_docker(
        "SELECT id, title, description, category, "
        "classification_confidence AS confidence, metadata, classified_at "
        "FROM public.ideas "
        "WHERE parent_id IS NULL AND category IS NOT NULL "
        f"ORDER BY classified_at DESC NULLS LAST LIMIT {int(grenze)}")


def run_pass(dry_run: bool = False) -> int:
    bubbles = klassifizierte_bubbles()
    if not bubbles:
        logger.info("keine klassifizierten Bubbles gefunden")
        return 0
    return exportieren(bubbles, dry_run)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="ein Durchlauf statt Schleife")
    ap.add_argument("--dry-run", action="store_true", help="nur zeigen, nichts schreiben")
    args = ap.parse_args()

    _load_env_fallback()

    if args.once:
        run_pass(args.dry_run)
        return 0

    logger.info("Schleife: alle %ss", _POLL_INTERVAL_S)
    while True:
        try:
            run_pass(args.dry_run)
        except Exception as exc:  # noqa: BLE001 — ein Ausfall darf den Worker nicht beenden
            logger.error("Durchlauf fehlgeschlagen: %s", _ohne_schluessel(str(exc)))
        time.sleep(_POLL_INTERVAL_S)


if __name__ == "__main__":
    raise SystemExit(main())

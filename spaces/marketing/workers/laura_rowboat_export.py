"""laura_rowboat_export — Videokatalog aus Laura als Metadaten nach Rowboat.

WOFUER. Marketing soll wissen, welches Material bereitliegt und worueber es
redet, ohne die Dateien zu durchsuchen: „haben wir einen O-Ton zur Freigabe-
Logik?" beantwortet ein Steckbrief, nicht ein Ordner voller MP4s.

WAS NICHT WANDERT. Die Videodatei bleibt im Dateisystem — Rowboat bekommt
nur den Steckbrief: Projekt, Name, Pfad, Dauer, Aufloesung und einen
gekuerzten Transkript-Auszug. Wer das Material schneiden will, geht ueber
Laura; Rowboat sagt nur, dass es existiert und worum es geht.

GRENZE ZU LAURA. Der Worker spricht Lauras HTTP-API (127.0.0.1:8765,
`X-Laura-Token` wenn gesetzt) — nicht ihre SQLite-Datei. Die Endpunkte und
Feldnamen sind am 02.09.2026 aus `services/local-api/src/laura/api/`
abgelesen: GET /projects, GET /projects/{id}/assets, GET
/assets/{id}/transcript; Felder id/display_name/source_path/duration_frames/
rate_num/rate_den/width/height und Segmente mit text/start_frame.

IDEMPOTENT wie der Bubble-Export: Vergleich ueber den Dokumentnamen, und ein
frisch geschriebenes Dokument ohne indexierten Inhalt wird in Ruhe gelassen
(Rowboat versioniert gleichnamige Dokumente nicht, es dupliziert sie).

Aufruf:
    python -m spaces.marketing.workers.laura_rowboat_export --once
    python -m spaces.marketing.workers.laura_rowboat_export --once --dry-run
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

from spaces.marketing.workers.bubble_rowboat_export import (  # noqa: E402
    _bestand, _ohne_schluessel, _rowboat,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("marketing.laura_rowboat_export")

_ENV_KEYS = ("ROWBOAT_URL", "ROWBOAT_API_KEY", "LAURA_EXPORT_SOURCE_ID",
             "LAURA_API_URL", "LAURA_TOKEN")
_POLL_INTERVAL_S = float(os.environ.get("LAURA_EXPORT_POLL_S", "900"))
# Ein Steckbrief soll lesbar bleiben; lange Transkripte werden gekuerzt.
_MAX_ZEICHEN = int(os.environ.get("LAURA_EXPORT_MAX_ZEICHEN", "6000"))


def _load_env_fallback() -> None:
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


def _laura(pfad: str):
    """Ein GET gegen Lauras lokale API. Wirft mit klarer Ursache."""
    basis = os.environ.get("LAURA_API_URL", "http://127.0.0.1:8765").rstrip("/")
    kopf = {"Accept": "application/json"}
    token = os.environ.get("LAURA_TOKEN", "").strip()
    if token:
        kopf["X-Laura-Token"] = token
    anfrage = urllib.request.Request(basis + pfad, headers=kopf, method="GET")
    try:
        with urllib.request.urlopen(anfrage, timeout=60) as antwort:
            return json.loads(antwort.read())
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"Laura nicht erreichbar ({type(e).__name__}: {e}) — "
            "laeuft die Laura-App bzw. ihr local-api auf "
            f"{basis}?") from e


def _dauer_sekunden(asset: dict) -> str:
    rahmen = asset.get("duration_frames")
    zaehler = asset.get("rate_num")
    nenner = asset.get("rate_den") or 1
    if not rahmen or not zaehler:
        return "(unbekannt)"
    return str(round(rahmen * nenner / zaehler))


def dokument_inhalt(projekt: dict, asset: dict, segmente: list) -> str:
    """Der Steckbrief eines Clips — zugleich der Vergleichstext des naechsten Laufs."""
    aufloesung = "(unbekannt)"
    if asset.get("width") and asset.get("height"):
        aufloesung = f"{asset['width']}x{asset['height']}"

    kopf = [
        f"Video: {asset.get('id', '')}",
        f"Name: {asset.get('display_name') or '(ohne Namen)'}",
        f"Projekt: {projekt.get('name') or '(ohne Namen)'} ({projekt.get('id', '')})",
        f"Typ: {asset.get('type') or '(unbekannt)'}",
        f"Dauer in Sekunden: {_dauer_sekunden(asset)}",
        f"Aufloesung: {aufloesung}",
        f"Video-Codec: {asset.get('codec_video') or '(unbekannt)'}",
        f"Datei: {asset.get('source_path') or '(unbekannt)'}",
        "",
        "Die Datei bleibt im Dateisystem; hier steht nur ihr Steckbrief.",
        "",
        "Gesprochener Inhalt (Transkript):",
    ]
    text = " ".join((s.get("text") or "").strip() for s in segmente).strip()
    if not text:
        text = "(kein Transkript vorhanden)"
    rumpf = "\n".join(kopf)
    frei = max(0, _MAX_ZEICHEN - len(rumpf))
    if len(text) > frei:
        text = text[:frei].rstrip() + " … (gekuerzt)"
    return rumpf + "\n" + text


def _steckbriefe() -> list:
    """Alle Assets aller Projekte als (name, inhalt) — fragt Laura ab."""
    ergebnis = []
    for projekt in _laura("/projects") or []:
        pid = projekt.get("id", "")
        for asset in _laura(f"/projects/{pid}/assets") or []:
            aid = asset.get("id", "")
            try:
                segmente = _laura(f"/assets/{aid}/transcript") or []
            except RuntimeError:
                segmente = []      # kein Transkript ist kein Grund aufzugeben
            ergebnis.append({"name": f"video-{aid}",
                             "inhalt": dokument_inhalt(projekt, asset, segmente)})
    return ergebnis


def run_pass(dry_run: bool = False) -> int:
    quellen_id = os.environ.get("LAURA_EXPORT_SOURCE_ID", "").strip()
    if not quellen_id:
        raise RuntimeError(
            "LAURA_EXPORT_SOURCE_ID fehlt — Kennung der Rowboat-Quelle "
            "'Videos/Katalog' (aus rowboat_wissensquellen).")

    steckbriefe = _steckbriefe()
    if not steckbriefe:
        logger.info("Laura kennt kein Material — nichts zu exportieren")
        return 0

    bestand = _bestand(quellen_id, _rowboat)
    zu_schreiben = []
    wartend = 0
    for s in steckbriefe:
        eintrag = bestand.get(s["name"])
        if eintrag is None:
            zu_schreiben.append(s)
            continue
        if s["inhalt"] in eintrag["inhalte"]:
            continue
        if eintrag["wartet"]:
            wartend += 1
            continue
        zu_schreiben.append(s)
    if wartend:
        logger.info("%d Steckbrief(e) noch nicht indexiert — dieser Lauf laesst sie in Ruhe", wartend)

    if not zu_schreiben:
        logger.info("nichts zu tun: %d Clip(s) unveraendert", len(steckbriefe))
        return 0
    if dry_run:
        logger.info("dry-run: %d Steckbrief(e) wuerden geschrieben (%s)",
                    len(zu_schreiben), ", ".join(d["name"] for d in zu_schreiben[:5]))
        return len(zu_schreiben)

    r = _rowboat("rowboat_dokumente_schreiben",
                 {"sourceId": quellen_id, "dokumente": zu_schreiben})
    if not r["ok"]:
        raise RuntimeError(f"Schreiben abgelehnt: {r['fehler']}")
    logger.info("%d Steckbrief(e) geschrieben", len(zu_schreiben))
    return len(zu_schreiben)


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
        except Exception as exc:  # noqa: BLE001 — ausgeschaltete Laura beendet den Worker nicht
            logger.info("Durchlauf uebersprungen: %s", _ohne_schluessel(str(exc)))
        time.sleep(_POLL_INTERVAL_S)


if __name__ == "__main__":
    raise SystemExit(main())

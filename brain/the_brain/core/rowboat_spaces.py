"""Fester Lesesatz des Rowboat-Space: "Welche Agenten gibt es in Rowboat?".

Rowboat zeigt die VibeMind-Spaces als Lese-Karten (``spaces/rowboat/vibemind-spaces/*.md``,
erzeugt von ``spaces/rowboat/scripts/agenten_verzeichnis.py`` aus der Registry). Diese Karten
liest der Satz, ohne Modell und ohne Rowboat-Agent: welche Spaces dort stehen und welche
davon heute laufen (``stand: läuft``).

Kennung: Die App fuehrt den Reiter als ``roarboot``, Registry und Karten als ``rowboat``
(Alias in core/space_contract.py und core/verstehen.py). Hier zaehlt nur der Ordner.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

WURZEL = Path(__file__).resolve().parents[3]
ORDNER = WURZEL / "spaces" / "rowboat" / "vibemind-spaces"
LAEUFT = "läuft"
IM_SATZ = 20


def _ordner() -> Path:
    return Path(os.environ.get("VIBEMIND_SPACES_KARTEN", "") or ORDNER)


def _kopf(text: str) -> Optional[dict]:
    if not text.startswith("---"):
        return None
    teile = text.split("---", 2)
    if len(teile) < 3:
        return None
    kopf = yaml.safe_load(teile[1])
    return kopf if isinstance(kopf, dict) else None


def spaces_lesen(value: Any = None, **_: Any) -> dict:
    """Ziel ``direct:core.rowboat_spaces:spaces_lesen``: Titel und Stand je Karte, nach Titel."""
    ordner = _ordner()
    if not ordner.is_dir():
        return {"success": False, "error": "karten_fehlen"}
    spaces = []
    for datei in sorted(ordner.glob("*.md")):
        if datei.name.lower() == "readme.md":
            continue
        try:
            kopf = _kopf(datei.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            kopf = None
        if not kopf or not str(kopf.get("titel") or "").strip():
            continue
        spaces.append({"space": str(kopf.get("space") or datei.stem), "titel": str(kopf["titel"]).strip(),
                       "laeuft": str(kopf.get("stand") or "").strip() == LAEUFT})
    if not spaces:
        return {"success": False, "error": "keine_karten"}
    spaces.sort(key=lambda s: s["titel"].casefold())
    return {"success": True, "spaces": spaces, "anzahl": len(spaces)}


def fehlersatz(ergebnis: Any, fehler: Any = None) -> str:
    """Genauer Satz, wenn die Karten nicht gelesen werden konnten."""
    if isinstance(ergebnis, Mapping) and isinstance(ergebnis.get("result"), Mapping) and "error" not in ergebnis:
        ergebnis = ergebnis["result"]
    art = ergebnis.get("error") if isinstance(ergebnis, Mapping) else None
    if art in ("karten_fehlen", "keine_karten"):
        return ("Welche VibeMind-Spaces in Rowboat stehen, kann ich noch nicht sagen: die Lese-Karten "
                "(spaces/rowboat/vibemind-spaces) fehlen auf diesem Stand.")
    return "Die VibeMind-Spaces in Rowboat kann ich gerade nicht lesen. Geändert habe ich nichts."


def antwort(ergebnis: Any) -> Optional[str]:
    """"In Rowboat stehen 14 VibeMind-Spaces: Bubbles, …. Läuft heute: Bubbles, Desktop, Ideen."."""
    try:
        if isinstance(ergebnis, Mapping) and isinstance(ergebnis.get("result"), Mapping) and "spaces" not in ergebnis:
            ergebnis = ergebnis["result"]
        if not isinstance(ergebnis, Mapping) or ergebnis.get("success") is not True:
            return None
        spaces = [s for s in ergebnis.get("spaces") or [] if isinstance(s, Mapping) and s.get("titel")]
        if not spaces:
            return None
        namen = [str(s["titel"]) for s in spaces]
        rest = f" und {len(namen) - IM_SATZ} weitere" if len(namen) > IM_SATZ else ""
        kopf = ("In Rowboat steht ein VibeMind-Space: " if len(namen) == 1
                else f"In Rowboat stehen {len(namen)} VibeMind-Spaces: ")
        laufend = [str(s["titel"]) for s in spaces if s.get("laeuft") is True]
        fuss = ("Läuft heute: " + ", ".join(laufend) + ".") if laufend else "Heute läuft davon noch keiner."
        return kopf + ", ".join(namen[:IM_SATZ]) + rest + ". " + fuss
    except Exception:  # noqa: BLE001
        return None

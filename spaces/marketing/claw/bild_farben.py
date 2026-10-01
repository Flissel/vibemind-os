"""Farben des Newsletter-Layouts fuer den Bild-Prompt (Betreiber-Entscheid
01.10.2026: das Bild soll sich nahtlos ins Layout fuegen, nicht in fester
VibeMind-Farbe erscheinen). Die Palette kommt aus der aktuellen Fassung -
Standardwerte wie bloecke_mjml.nach_mjml, damit Bild und Mail dieselben Farben
meinen. FLUX versteht Hex-Werte schlecht, darum Namen aus Farbton und
Helligkeit: fest, ohne Modell, pruefbar."""
from __future__ import annotations

import colorsys
import re

_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
# Obergrenzen des Farbtons in Grad (HLS), erste passende gewinnt.
_TOENE = ((15, "red"), (40, "orange"), (65, "yellow"), (90, "lime"), (150, "green"), (195, "teal"),
          (255, "blue"), (285, "indigo"), (320, "purple"), (345, "pink"), (361, "red"))
# Wunsch nennt Farbe, Licht oder Stimmung -> Palette nur als weicher Rahmen.
# Ohne \b vorn fuer deutsche Komposita (Tageslicht, Abendrot); 'rot' nur als Wort (nicht 'brotlos').
_FARBWUNSCH = re.compile(
    r"\brot(e|en|er|es)?\b|\bred\b|farb|colou?r|bunt|pastell|neon|warm|waerm|wärm|kalt|kuehl|kühl|cool|cold|"
    r"orange|gelb|yellow|gruen|grün|green|blau|blue|lila|violett|purple|pink|rosa|braun|brown|schwarz|black|"
    r"weiss|weiß|white|grau|grey|gray|gold|silber|silver|hell|dunk|bright|dark|licht|light|sonne|sunny|"
    r"sunset|sunrise|abend|morgen|nacht|night", re.IGNORECASE)


def _rgb(hexwert) -> tuple[float, float, float] | None:
    m = _HEX.match(hexwert.strip()) if isinstance(hexwert, str) else None
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def farbname(hexwert) -> str:
    rgb = _rgb(hexwert)
    if rgb is None:
        return ""
    farbton, hell, saett = colorsys.rgb_to_hls(*rgb)
    if max(rgb) - min(rgb) < 0.06:   # unbunt
        for grenze, name in ((0.08, "black"), (0.25, "charcoal"), (0.45, "dark grey"), (0.7, "grey"),
                             (0.9, "light grey"), (0.98, "off-white")):
            if hell < grenze:
                return name
        return "white"
    grad = farbton * 360
    ton = next(name for grenze, name in _TOENE if grad < grenze)
    if hell < 0.12:
        vorsatz = "near-black"
    elif hell < 0.25:
        vorsatz = "very dark"
    elif hell < 0.5:
        vorsatz = "dark"
    elif hell >= 0.75:
        vorsatz = "pale"
    elif saett > 0.5:
        vorsatz = "bright"
    else:
        vorsatz = "muted"
    return f"{vorsatz} {ton}"


def palette(dok) -> dict:
    """innen = Inhaltsflaeche (canvasColor), aussen = Rand (backdropColor), text,
    akzent = Farbe des ersten Knopfs (leer, wenn es keinen gibt)."""
    dok = dok if isinstance(dok, dict) else {}
    wurzel = ((dok.get("root") or {}).get("data") or {}) if isinstance(dok.get("root"), dict) else {}
    akzent = ""
    for b in dok.values():
        if isinstance(b, dict) and b.get("type") == "Button":
            farbe = ((b.get("data") or {}).get("props") or {}).get("buttonBackgroundColor")
            if _rgb(farbe):
                akzent = farbe
                break
    return {"innen": wurzel.get("canvasColor") or "#ffffff", "aussen": wurzel.get("backdropColor") or "#f2f5f7",
            "text": wurzel.get("textColor") or "#242424", "akzent": akzent}


def farbwunsch(hinweis: str) -> bool:
    return bool(_FARBWUNSCH.search(hinweis or ""))


def satz(pal: dict | None, flaeche: str, hinweis: str) -> str:
    """Prompt-Teil: Palette des Layouts plus weicher Rand in die Flaeche, auf der
    das Bild steht. Nennt der Wunsch Farbe oder Licht, nur 'harmonizing with'."""
    if not pal:
        return "natural colors, soft light"
    grund = farbname(flaeche) or farbname(pal.get("innen"))
    namen = []
    for n in (grund, farbname(pal.get("akzent")), farbname(pal.get("text"))):
        if n and n not in namen:
            namen.append(n)
    if not namen:
        return "natural colors, soft light"
    rand = f"edges blend softly into {grund}" if grund else ""
    if farbwunsch(hinweis):
        teile = [f"harmonizing with the layout colors {', '.join(namen)}", rand]
    else:
        rollen = [f"{grund} background"] if grund else []
        rest = [n for n in namen if n != grund]
        if rest:
            rollen.append(f"{rest[0]} accents")
        if len(rest) > 1:
            rollen.append(f"{rest[1]} highlights")
        teile = [f"color palette matching the layout: {', '.join(rollen)}", rand]
    return "; ".join(t for t in teile if t)

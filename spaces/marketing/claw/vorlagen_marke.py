"""Ladenmarke fuer Newsletter-Vorlagen (Spec 2026-10-01 §5): Farbrollen aus dem
Standard-Layout des Ladens berechnen, in die Vorlage einsetzen und das Logo
einmalig als Datei ablegen. Neutrale Toene der Vorlage bleiben unangetastet."""
from __future__ import annotations

import base64
import copy
import hashlib
import os
import re
import tempfile

from spaces.marketing.claw.schoenheit import kontrast

ERSATZ_AKZENT = "#2563eb"
FAST_SCHWARZ = "#1a1a1a"
INK = "#080b13"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_LOGO = re.compile(r"^data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)$")


def _rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _hex(r: float, g: float, b: float) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(x))) for x in (r, g, b))


def mischen(a: str, b: str, anteil_b: float) -> str:
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    return _hex(ra + (rb - ra) * anteil_b, ga + (gb - ga) * anteil_b, ba + (bb - ba) * anteil_b)


def _bis_kontrast(farbe: str, grund: str, ziel: float) -> str:
    """Farbe Richtung Schwarz (heller Grund) bzw. Weiss (dunkler Grund) schieben, bis ziel erreicht."""
    richtung = "#000000" if kontrast("#000000", grund) >= kontrast("#ffffff", grund) else "#ffffff"
    for schritt in range(0, 21):
        f = mischen(farbe, richtung, schritt / 20)
        if kontrast(f, grund) >= ziel:
            return f
    return richtung


def _auf(farbe: str) -> str:
    return max(("#ffffff", FAST_SCHWARZ), key=lambda c: kontrast(c, farbe))


def rollen(gestalt: dict | None, grund: str) -> dict[str, str]:
    g = gestalt if isinstance(gestalt, dict) else {}
    akzent = str(g.get("akzent") or "").lower()
    akzent = akzent if _HEX.match(akzent) else ERSATZ_AKZENT
    zweit = str(g.get("flaeche") or "").lower()
    if not _HEX.match(zweit) or kontrast(zweit, "#ffffff") < 3:
        zweit = _bis_kontrast(mischen(akzent, "#000000", 0.55), "#ffffff", 7)
    # If auf_zweit cannot reach 4.5:1 against zweit, darken zweit until weiss reaches 4.5:1
    if kontrast("#ffffff", zweit) < 4.5:
        zweit = _bis_kontrast(zweit, "#ffffff", 4.5)
    return {
        "akzent": akzent,
        "zweit": zweit,
        "akzent_hell": mischen("#ffffff", akzent, 0.10),
        "auf_akzent": _auf(akzent),
        "auf_zweit": "#ffffff" if kontrast("#ffffff", zweit) >= 4.5 else _auf(zweit),
        "akzent_text": _bis_kontrast(akzent, grund if _HEX.match(grund or "") else "#ffffff", 4.5),
        "akzent_ring1": mischen(akzent, INK, 0.55),
        "akzent_ring2": mischen(akzent, INK, 0.40),
        "akzent_rahmen": mischen(akzent, INK, 0.70),
    }


def logo_ablegen(gestalt: dict | None, mandant: str, ordner: str) -> str | None:
    g = gestalt if isinstance(gestalt, dict) else {}
    m = _LOGO.match(str(g.get("logo") or ""))
    if not m or not ordner or not os.path.isdir(ordner) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,40}", mandant or ""):
        return None
    try:
        roh = base64.b64decode(m.group(2), validate=True)
    except ValueError:
        return None
    endung = "png" if m.group(1) == "png" else "jpg"
    name = f"logo-{mandant}-{hashlib.sha256(roh).hexdigest()[:10]}.{endung}"
    ziel = os.path.join(ordner, name)
    if not os.path.exists(ziel):
        try:
            fd, tmp = tempfile.mkstemp(dir=ordner, suffix=".tmp")
            try:
                os.write(fd, roh)
            finally:
                os.close(fd)
            os.replace(tmp, ziel)
        except OSError:
            try:
                os.unlink(tmp)
            except (OSError, NameError):
                pass
            return None
    return f"medien:{name}"


def _setzen(dok: dict, pfad: str, wert: str) -> None:
    teile = pfad.split("/")
    knoten = dok.get(teile[0])
    for t in teile[1:-1]:
        if not isinstance(knoten, dict):
            return
        knoten = knoten.setdefault(t, {})
    if isinstance(knoten, dict) and teile[1:]:
        knoten[teile[-1]] = wert


def _entfernen(dok: dict, bid: str) -> None:
    dok.pop(bid, None)
    for b in dok.values():
        data = (b or {}).get("data") or {}
        for liste in (data.get("childrenIds"), (data.get("props") or {}).get("childrenIds")):
            if isinstance(liste, list) and bid in liste:
                liste.remove(bid)
        for spalte in (data.get("props") or {}).get("columns") or []:
            if isinstance(spalte, dict) and bid in (spalte.get("childrenIds") or []):
                spalte["childrenIds"].remove(bid)


def einsetzen(dok: dict, werte: dict[str, str]) -> dict:
    d = copy.deepcopy(dok)
    rollen_tab = ((d.get("root") or {}).get("data") or {}).get("rollen") or {}
    for pfad, rolle in rollen_tab.items():
        if rolle in werte and pfad.split("/")[0] in d:
            _setzen(d, pfad, werte[rolle])
    _entfernen(d, "marke_wort" if werte.get("logo") else "marke_logo")
    return d

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

from spaces.marketing.claw.schoenheit import kontrast, leuchtdichte

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
    if _HEX.match(grund or "") and leuchtdichte(grund) < 0.2 and kontrast(akzent, grund) < 3:
        # dunkler Vorlagengrund (tech): ein dunkler Ladenakzent verschwaende als Knopf/Rahmen -
        # Richtung Weiss aufhellen, bis er sich mit 3:1 abhebt. Heller Grund bleibt unberuehrt.
        akzent = _bis_kontrast(akzent, grund, 3)
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


DUNKEL_GRENZE = 0.2          # Hintergrund-Leuchtdichte, unter der logo_dunkel gilt (Spec 2026-10-09 §1)


def ist_dunkel(farbe) -> bool:
    return isinstance(farbe, str) and bool(_HEX.match(farbe)) and leuchtdichte(farbe) < DUNKEL_GRENZE


def _kinder(block) -> list:
    data = block.get("data") if isinstance(block, dict) and isinstance(block.get("data"), dict) else {}
    props = data.get("props") if isinstance(data.get("props"), dict) else {}
    kinder = list(data.get("childrenIds") or []) + list(props.get("childrenIds") or [])
    for spalte in props.get("columns") or []:
        if isinstance(spalte, dict):
            kinder += list(spalte.get("childrenIds") or [])
    return kinder


def logo_grund(dok: dict, bid: str = "marke_logo") -> str:
    """Grund unter dem Logo-Block: eigener Hintergrund, sonst der des naechsten Vorfahren mit Hintergrund,
    sonst canvasColor der Wurzel, sonst Weiss."""
    eltern = {kind: id_ for id_, b in dok.items() for kind in _kinder(b) if isinstance(kind, str)}
    knoten, gesehen = bid, set()
    while isinstance(knoten, str) and knoten != "root" and knoten not in gesehen:
        gesehen.add(knoten)
        b = dok.get(knoten)
        data = b.get("data") if isinstance(b, dict) and isinstance(b.get("data"), dict) else {}
        stil = data.get("style") if isinstance(data.get("style"), dict) else {}
        farbe = str(stil.get("backgroundColor") or "")
        if _HEX.match(farbe):
            return farbe.lower()
        knoten = eltern.get(knoten)
    wurzel = dok.get("root") if isinstance(dok.get("root"), dict) else {}
    daten = wurzel.get("data") if isinstance(wurzel.get("data"), dict) else {}
    farbe = str(daten.get("canvasColor") or "")
    return farbe.lower() if _HEX.match(farbe) else "#ffffff"


def logo_ablegen(gestalt: dict | None, mandant: str, ordner: str, schluessel: str = "logo") -> str | None:
    g = gestalt if isinstance(gestalt, dict) else {}
    m = _LOGO.match(str(g.get(schluessel) or ""))
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
            # Lesbar für andere Prozesse (mkstemp erzeugt 0600)
            os.chmod(tmp, 0o644)
            os.replace(tmp, ziel)
        except OSError:
            try:
                os.unlink(tmp)
            except (OSError, NameError):
                pass
            return None
    return f"medien:{name}"


_KURSIV_HUELLE = re.compile(r"^\*(?!\*).+(?<!\*)\*$", re.S)


def _lesen(dok: dict, pfad: str):
    knoten = dok
    for t in pfad.split("/"):
        if not isinstance(knoten, dict):
            return None
        knoten = knoten.get(t)
    return knoten


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
            wert = werte[rolle]
            if rolle == "laden" and _KURSIV_HUELLE.match(str(_lesen(d, pfad) or "")):
                wert = f"*{wert}*"          # "*[Laden]*" bleibt kursiv (klassik)
            _setzen(d, pfad, wert)
    _entfernen(d, "marke_wort" if werte.get("logo") else "marke_logo")
    return d


def logo_masse(quelle) -> tuple[int, int] | None:
    """Pixelmasse eines Logos (Bytes oder Dateipfad); None, wenn nicht lesbar."""
    try:
        from io import BytesIO
        from PIL import Image
        if isinstance(quelle, (bytes, bytearray)):
            bild = Image.open(BytesIO(bytes(quelle)))
        elif isinstance(quelle, str) and quelle:
            bild = Image.open(quelle)
        else:
            return None
        with bild:
            b, h = bild.size
        return (b, h) if b > 0 and h > 0 else None
    except Exception:
        return None


def logo_einpassen(dok: dict, masse: tuple[int, int] | None) -> dict:
    """Logo-Bloecke (Rolle "logo") in ihren Kasten (width/height der Vorlage) einpassen, ohne das
    Seitenverhaeltnis zu aendern ("contain"). Ohne Masse oder Kasten bleibt der Block unveraendert."""
    d = copy.deepcopy(dok)
    if not masse:
        return d
    bw, bh = masse
    rollen_tab = ((d.get("root") or {}).get("data") or {}).get("rollen") or {}
    for pfad, rolle in rollen_tab.items():
        if rolle != "logo":
            continue
        block = d.get(pfad.split("/")[0])
        data = block.get("data") if isinstance(block, dict) else None
        props = data.get("props") if isinstance(data, dict) else None
        if not isinstance(props, dict):
            continue
        kw, kh = props.get("width"), props.get("height")
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0 for x in (kw, kh)):
            continue
        faktor = min(kw / bw, kh / bh)
        props["width"] = max(1, round(bw * faktor))
        props["height"] = max(1, round(bh * faktor))
    return d

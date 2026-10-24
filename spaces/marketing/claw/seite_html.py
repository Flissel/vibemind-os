"""HTML-Darstellung der freien Newsletter-Flaeche (Collage). Eine Darstellung fuer Vorschau, PDF
(Chromium, ohne JS) und spaeter Screenshots/Mail-Scheiben: Seite 600 breit, zentriert auf BREITE_PX,
Ebenen absolut (x/y = Mittelpunkt). CSS entsteht nur aus geprueften Werten, Text wird escaped."""
from __future__ import annotations

import copy
import html as _html
import re
from urllib.parse import quote

from spaces.marketing.claw import schriften, seite
from spaces.marketing.claw.bloecke_mjml import _fuss_farbe
from spaces.marketing.claw.pdf_druck import BREITE_PX

# Die 600 x hoehe Seite im aeusseren Rahmen von html(); Screenshots (Pruefbild, Mail-Stuecke) fotografieren nur sie.
SEITE_CONTAINER = "body > div > div"

_FETT = re.compile(r"\*\*([^*\n]+?)\*\*")
_KURSIV = re.compile(r"\*([^*\n]+?)\*")
_TEXTLINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")  # gleiche Form wie seite._TEXTLINK, mit Beschriftung


def _a(wert) -> str:
    return _html.escape(str(wert if wert is not None else ""), quote=True)


def _n(zahl) -> str:
    """Zahl fuer CSS ohne ueberfluessige Nachkommastellen (250 statt 250.0)."""
    z = round(float(zahl), 4)
    return str(int(z)) if z == int(z) else str(z)


def _auszeichnung(roh: str) -> str:
    t = _FETT.sub(r"<strong>\1</strong>", _a(roh))
    return _KURSIV.sub(r"<em>\1</em>", t)


def _inline(text: str, link_farbe: str | None = None) -> str:
    """Escapen, dann **fett**, *kursiv*, [text](https://...) und Zeilenumbrueche. Ziele werden mit der
    Regel der Pruefung (seite._HTTPS) gelesen; ein Ziel ausser https wird nur als Text ausgegeben.
    link_farbe (Mail): ausdrueckliche Linkfarbe hinter inherit, damit Outlook Links nicht blau malt."""
    farbe = f"color:{_a(link_farbe)};" if link_farbe else ""
    teile, pos = [], 0
    for m in _TEXTLINK.finditer(text):
        teile.append(_auszeichnung(text[pos:m.start()]))
        label, ziel = m.group(1), m.group(2)
        if seite._HTTPS.fullmatch(ziel):
            teile.append(f'<a href="{_a(ziel)}" style="color:inherit;{farbe}text-decoration:underline">'
                         f'{_auszeichnung(label)}</a>')
        else:
            teile.append(_auszeichnung(label))
        pos = m.end()
    teile.append(_auszeichnung(text[pos:]))
    return "".join(teile).replace("\r\n", "\n").replace("\n", "<br>")


# oeffentlich fuer die Mail-Fassung (seite_mail)
inline = _inline
TEXTLINK = _TEXTLINK


def _familie(s: dict, rolle: str) -> str:
    """HAND und ANZEIGE: Webschrift plus Stapel. TEXT: nur der mail-sichere Stapel (nie Webschrift)."""
    sid = seite.rolle_schrift(s, rolle)
    if rolle == "TEXT":
        return schriften.REGISTER[sid]["stapel"]
    return schriften.css_familie(sid, schriften.EBENEN_SCHRIFTEN)


def _lage(e: dict, hoehe_als_min: bool = False) -> str:
    css = (f"position:absolute;left:{_n(e['x'] - e['breite'] / 2)}px;top:{_n(e['y'] - e['hoehe'] / 2)}px;"
           f"width:{_n(e['breite'])}px;{'min-height' if hoehe_als_min else 'height'}:{_n(e['hoehe'])}px;")
    if e["drehung"]:
        css += f"transform:rotate({_n(e['drehung'])}deg);"
    if e["deckkraft"] != 1:
        css += f"opacity:{_n(e['deckkraft'])};"
    return css


def _bild_url(basis: str, quelle: str) -> str:
    return basis + quote(quelle.split(":", 1)[1], safe="/")


def _ebene(s: dict, e: dict, z: int, bild_basis: str) -> str:
    art = e["art"]
    kennung = f' data-ebene="{_a(e["id"])}"'   # fuer das Messen der gerenderten Ebenen (seite_bilder)
    stil = _lage(e, hoehe_als_min=art == "text") + f"z-index:{z};box-sizing:border-box;"
    if art == "bild":
        stil += "overflow:hidden;"
        if e["kreis"]:
            stil += "border-radius:50%;"
        elif e["rundung"]:
            stil += f"border-radius:{_n(e['rundung'])}px;"
        if e["rahmen_breite"]:
            stil += f"border:{_n(e['rahmen_breite'])}px solid {e['rahmen_farbe']};"
        if e["schatten"]:
            stil += "box-shadow:0 4px 14px rgba(0,0,0,0.3);"
        fit = "contain" if e["passung"] == "einpassen" else "cover"
        return (f'<div style="{stil}"{kennung}><img src="{_a(_bild_url(bild_basis, e["quelle"]))}" alt="" '
                f'style="display:block;width:100%;height:100%;object-fit:{fit}"></div>')
    if art == "form":
        stil += f"background:{e['farbe']};"
        stil += "border-radius:50%;" if e["form"] == "kreis" else (
            f"border-radius:{_n(e['rundung'])}px;" if e["rundung"] else "")
        return f'<div style="{stil}"{kennung}></div>'
    if art == "knopf":
        stil += (f"background:{e['farbe']};color:{e['textfarbe']};border-radius:{_n(e['rundung'])}px;"
                 f"display:flex;align-items:center;justify-content:center;text-align:center;text-decoration:none;"
                 f"font-family:{_familie(s, e['rolle'])};font-size:{_n(e['groesse'])}px;font-weight:{e['gewicht']};")
        return f'<a href="{_a(e["link"])}" style="{stil}"{kennung}>{_a(e["text"])}</a>'
    ausr = {"links": "left", "mitte": "center", "rechts": "right"}[e["ausrichtung"]]
    stil += (f"font-family:{_familie(s, e['rolle'])};font-size:{_n(e['groesse'])}px;font-weight:{e['gewicht']};"
             f"line-height:{_n(e['zeilenabstand'])};letter-spacing:{_n(e['buchstabenabstand'])}em;"
             f"color:{e['farbe']};text-align:{ausr};overflow-wrap:break-word;")
    if e["grossbuchstaben"]:
        stil += "text-transform:uppercase;"
    if e["kursiv"]:
        stil += "font-style:italic;"
    if e["hintergrund"]:
        stil += f"background:{e['hintergrund']};"
    return f'<div style="{stil}"{kennung}>{_inline(e["text"])}</div>'


def _br(text: str) -> str:
    """Zeilenumbrueche eines (bereits escapten) Fusstexts als <br>."""
    return re.sub(r"\r\n|\r|\n", "<br>", text)


def html(s: dict, *, betreff: str, pflichtteil: dict, bild_basis: str, schrift_basis: str) -> str:
    s = copy.deepcopy(s)
    schr = s.get("schriften")
    if isinstance(schr, dict):  # unbekannte Markenschrift: Rueckfall statt Absturz
        for k in ("anzeige", "text"):
            if k in schr and schr[k] not in schriften.REGISTER:
                schr[k] = "dm-sans"
    for e in s.get("ebenen") or []:  # Link mit Ziel ausser https: nur die Beschriftung bleibt
        if isinstance(e, dict) and e.get("art") == "text" and isinstance(e.get("text"), str):
            e["text"] = _TEXTLINK.sub(lambda m: m.group(0) if seite._HTTPS.fullmatch(m.group(2)) else m.group(1),
                                      e["text"])
    s = seite.pruefen(s)
    farbe, bild = s["hintergrund"]["farbe"], s["hintergrund"]["bild"]
    seite_stil = (f"position:relative;width:{seite.BREITE}px;height:{s['hoehe']}px;overflow:hidden;"
                  f"margin:0 auto;background-color:{farbe};")
    if bild:   # bild_basis kann Entities (&apos;) tragen: der ganze Stil wird unten wie src/href escaped
        url = quote(_bild_url(bild_basis, bild), safe=":/?&=%~+@,;")
        seite_stil += f"background-image:url('{url}');background-size:cover;background-position:center;"
    ebenen = "".join(_ebene(s, e, i, bild_basis) for i, e in enumerate(s["ebenen"]))
    kopf = ""
    if schrift_basis.startswith("https://") and schrift_basis.endswith("/"):
        kopf = f'<link rel="stylesheet" href="{_a(schrift_basis)}schriften.css">'
    impressum = (pflichtteil.get("impressum") or "").strip()
    impressum_html = (_br(_a(impressum)) if impressum else
                      '<strong style="color:#ef4444">Impressum fehlt &ndash; im Mandanten hinterlegen</strong>')
    vermittler = (pflichtteil.get("vermittlerangaben") or "").strip()
    vermittler_html = f"{_br(_a(vermittler))}<br>" if vermittler else ""
    hinweis = (pflichtteil.get("abmelde_hinweis") or "").replace("{abmeldelink}", "[Abmeldelink]")
    stapel = schriften.REGISTER[s["schriften"]["text"]]["stapel"]
    fuss_farbe = _fuss_farbe("#666666", farbe)   # heller Grund: wie bisher; dunkler/kraeftiger: weiss/fast-schwarz
    fuss = (f'<div style="margin:0 auto;width:{seite.BREITE}px;padding:16px 0;text-align:center;font-size:11px;'
            f'line-height:1.5;color:{fuss_farbe};font-family:{stapel}">{impressum_html}<br>{vermittler_html}{_br(_a(hinweis))}</div>')
    return (f'<!doctype html><html lang="de"><head><meta charset="utf-8"><title>{_a(betreff)}</title>{kopf}'
            f'<style>html,body{{margin:0;padding:0}}</style></head>'
            f'<body style="margin:0;background-color:{farbe}"><div style="width:{BREITE_PX}px;margin:0 auto">'
            f'<div style="{_a(seite_stil)}">{ebenen}</div>{fuss}</div></body></html>')

"""Uebersetzer Email-Builder-Bloecke -> MJML -> HTML (sales-claw Spec
2026-09-29-newsletter-editor-design.md §3.4). EINE Render-Stelle fuer die
Vorschau und spaeter den Versand. Das Dokument ist vorher von
marketing.pult_bloecke_fehler geprueft; hier trotzdem alles Fremde escapen.
Pflichtteil des Mandanten wird immer am Ende angehaengt."""
from __future__ import annotations

import html
import re
import urllib.parse

import mjml

from spaces.marketing.claw.schoenheit import kontrast

SCHRIFTEN = {
    'MODERN_SANS': '"Helvetica Neue", "Arial Nova", "Nimbus Sans", Arial, sans-serif',
    'BOOK_SANS': 'Optima, Candara, "Noto Sans", source-sans-pro, sans-serif',
    'ORGANIC_SANS': 'Seravek, "Gill Sans Nova", Ubuntu, Calibri, "DejaVu Sans", source-sans-pro, sans-serif',
    'GEOMETRIC_SANS': 'Avenir, "Avenir Next LT Pro", Montserrat, Corbel, "URW Gothic", source-sans-pro, sans-serif',
    'HEAVY_SANS': 'Bahnschrift, "DIN Alternate", "Franklin Gothic Medium", "Nimbus Sans Narrow", sans-serif-condensed, sans-serif',
    'ROUNDED_SANS': 'ui-rounded, "Hiragino Maru Gothic ProN", Quicksand, Comfortaa, Manjari, "Arial Rounded MT Bold", Calibri, source-sans-pro, sans-serif',
    'MODERN_SERIF': 'Charter, "Bitstream Charter", "Sitka Text", Cambria, serif',
    'BOOK_SERIF': '"Iowan Old Style", "Palatino Linotype", "URW Palladio L", P052, serif',
    'MONOSPACE': '"Nimbus Mono PS", "Courier New", "Cutive Mono", monospace',
}
GROESSE_UEBERSCHRIFT = {"h1": 32, "h2": 24, "h3": 20}
KNOPF_RUNDUNG = {"rectangle": 0, "rounded": 6, "pill": 64}
_FETT = re.compile(r"\*\*(.+?)\*\*")
_KURSIV = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


class RenderFehler(Exception):
    pass


def _a(wert) -> str:
    return html.escape(str(wert if wert is not None else ""), quote=True)


def _polster(style: dict) -> str:
    p = style.get("padding") or {}
    return f'{int(p.get("top", 0))}px {int(p.get("right", 24))}px {int(p.get("bottom", 0))}px {int(p.get("left", 24))}px'


def bild_adresse(url: str, bild_basis: str) -> str | None:
    if not bild_basis or not isinstance(url, str) or not url.startswith("medien:"):
        return None
    return bild_basis + urllib.parse.quote(url[len("medien:"):])


def _text(roh: str, markdown: bool) -> str:
    sicher = _a(roh)
    if markdown:
        def link(m):
            ziel = html.unescape(m.group(2))
            if not ziel.startswith("https://"):
                return m.group(1)
            return f'<a href="{_a(ziel)}" style="color:inherit">{m.group(1)}</a>'
        sicher = _LINK.sub(link, sicher)
        sicher = _FETT.sub(r"<strong>\1</strong>", sicher)
        sicher = _KURSIV.sub(r"<em>\1</em>", sicher)
    return sicher.replace("\n", "<br>")


def _block(dok: dict, bid: str, farben: dict, bild_basis: str) -> str:
    b = dok.get(bid) or {}
    typ = b.get("type")
    data = b.get("data") or {}
    s, p = data.get("style") or {}, data.get("props") or {}
    polster = _polster(s)
    ausr = s.get("textAlign") or "left"
    farbe = s.get("color") or farben["text"]
    if typ == "Heading":
        groesse = GROESSE_UEBERSCHRIFT.get(p.get("level") or "h2", 24)
        return (f'<mj-text padding="{polster}" align="{_a(ausr)}" color="{_a(farbe)}" font-size="{groesse}px" '
                f'font-weight="{_a(s.get("fontWeight") or "bold")}" line-height="1.25">{_text(p.get("text") or "", False)}</mj-text>')
    if typ == "Text":
        groesse = int(s.get("fontSize") or 16)
        return (f'<mj-text padding="{polster}" align="{_a(ausr)}" color="{_a(farbe)}" font-size="{groesse}px" '
                f'font-weight="{_a(s.get("fontWeight") or "normal")}" line-height="1.55">'
                f'{_text(p.get("text") or "", bool(p.get("markdown")))}</mj-text>')
    if typ == "Image":
        ziel = bild_adresse(p.get("url") or "", bild_basis)
        if not ziel:
            name = (p.get("url") or "")[len("medien:"):] or "?"
            return f'<mj-text padding="{polster}" align="center" color="{_a(farben["text"])}">[Bild: {_a(name)}]</mj-text>'
        breite = f' width="{int(p["width"])}px"' if p.get("width") else ""
        link = p.get("linkHref") or ""
        href = f' href="{_a(link)}"' if link.startswith("https://") else ""
        return f'<mj-image padding="{polster}" src="{_a(ziel)}" alt="{_a(p.get("alt") or "")}"{breite}{href} />'
    if typ == "Button":
        url = p.get("url") or ""
        if not url.startswith("https://"):
            return ""
        rund = KNOPF_RUNDUNG.get(p.get("buttonStyle") or "rounded", 6)
        return (f'<mj-button padding="{polster}" href="{_a(url)}" align="{_a(ausr)}" border-radius="{rund}px" '
                f'background-color="{_a(p.get("buttonBackgroundColor") or farben["akzent"])}" '
                f'color="{_a(p.get("buttonTextColor") or "#ffffff")}" font-weight="bold">{_text(p.get("text") or "", False)}</mj-button>')
    if typ == "Divider":
        return (f'<mj-divider padding="{polster}" border-color="{_a(p.get("lineColor") or "#cccccc")}" '
                f'border-width="{int(p.get("lineHeight") or 1)}px" />')
    if typ == "Spacer":
        return f'<mj-spacer height="{int(p.get("height") or 16)}px" />'
    return ""


def _kinder_als_section(dok: dict, ids: list, farben: dict, bild_basis: str) -> str:
    teile, spalte = [], []
    def spalte_schliessen():
        if spalte:
            teile.append(f'<mj-section background-color="{_a(farben["flaeche"])}" padding="0"><mj-column>{"".join(spalte)}</mj-column></mj-section>')
            spalte.clear()
    for bid in ids or []:
        b = dok.get(bid) or {}
        typ = b.get("type")
        props = (b.get("data") or {}).get("props") or {}
        if typ == "ColumnsContainer":
            spalte_schliessen()
            anzahl = int(props.get("columnsCount") or 2)
            spalten = (props.get("columns") or [])[:anzahl]
            inhalt = "".join(
                "<mj-column>" + "".join(_block(dok, k, farben, bild_basis) for k in (c.get("childrenIds") or [])) + "</mj-column>"
                for c in spalten)
            teile.append(f'<mj-section background-color="{_a(farben["flaeche"])}" padding="0">{inhalt}</mj-section>')
        elif typ == "Container":
            spalte_schliessen()
            stil = (b.get("data") or {}).get("style") or {}
            hg = stil.get("backgroundColor") or farben["flaeche"]
            kinder = props.get("childrenIds") or []
            inhalt = "".join(_block(dok, k, farben, bild_basis) for k in kinder)
            teile.append(f'<mj-section background-color="{_a(hg)}" border-radius="{int(stil.get("borderRadius") or 0)}px" '
                         f'padding="{_polster(stil)}"><mj-column>{inhalt}</mj-column></mj-section>')
        else:
            spalte.append(_block(dok, bid, farben, bild_basis))
    spalte_schliessen()
    return "".join(teile)


def nach_mjml(dokument: dict, betreff: str, vorschautext: str, pflichtteil: dict,
              bild_basis: str = "", breite: int = 600) -> str:
    wurzel = (dokument.get("root") or {}).get("data") or {}
    farben = {"grund": wurzel.get("backdropColor") or "#f2f5f7",
              "flaeche": wurzel.get("canvasColor") or "#ffffff",
              "text": wurzel.get("textColor") or "#242424",
              "akzent": "#5eead4"}
    schrift = SCHRIFTEN.get(wurzel.get("fontFamily") or "MODERN_SANS", SCHRIFTEN["MODERN_SANS"]).replace('"', "'")
    rumpf = _kinder_als_section(dokument, wurzel.get("childrenIds") or [], farben, bild_basis)
    fuss_farbe = max((farben["text"], "#ffffff", "#111111"), key=lambda c: kontrast(c, farben["grund"]))
    impressum = (pflichtteil.get("impressum") or "").strip()
    impressum_html = (_a(impressum) if impressum else
                      '<strong style="color:#ef4444">Impressum fehlt &ndash; im Mandanten hinterlegen</strong>')
    abmelden = _a((pflichtteil.get("abmelde_hinweis") or "").replace("{abmeldelink}", "[Abmeldelink]"))
    fuss = (f'<mj-section padding="16px 0"><mj-column><mj-text align="center" font-size="11px" '
            f'color="{_a(fuss_farbe)}" line-height="1.5">{impressum_html}<br>{abmelden}</mj-text></mj-column></mj-section>')
    return (f'<mjml><mj-head><mj-title>{_a(betreff)}</mj-title><mj-preview>{_a(vorschautext)}</mj-preview>'
            f'<mj-attributes><mj-all font-family="{_a(schrift)}" /></mj-attributes></mj-head>'
            f'<mj-body background-color="{_a(farben["grund"])}" width="{int(breite)}px">{rumpf}{fuss}</mj-body></mjml>')


def rendern(dokument: dict, betreff: str, vorschautext: str, pflichtteil: dict,
            bild_basis: str = "", handy: bool = False) -> str:
    quelle = nach_mjml(dokument, betreff, vorschautext, pflichtteil, bild_basis, 380 if handy else 600)
    try:
        return mjml.mjml2html(quelle)
    except Exception as e:  # mrml meldet Englisch; nach aussen deutsch
        raise RenderFehler(f"Der Newsletter liess sich nicht setzen: {e}") from e

"""Uebersetzer Email-Builder-Bloecke -> MJML -> HTML (sales-claw Spec
2026-09-29-newsletter-editor-design.md §3.4). EINE Render-Stelle fuer die
Vorschau und spaeter den Versand. Das Dokument ist vorher von
marketing.pult_bloecke_fehler geprueft; hier trotzdem alles Fremde escapen.
Pflichtteil des Mandanten wird immer am Ende angehaengt."""
from __future__ import annotations

import html
import re
import urllib.parse

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
KNOPF_RUNDUNG = {"rectangle": 0, "rounded": 4, "pill": 64}
KNOPF_GROESSE = {"x-small": (4, 8), "small": (8, 12), "medium": (12, 20), "large": (16, 32)}
KNOPF_FARBE = "#999999"
KARTEN_RAND = 24
_FETT = re.compile(r"\*\*(.+?)\*\*")
_KURSIV = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
# Absatz: Leerzeile (auch mit Leerzeichen/Tabs, auch mehrere) -> <br><br>; einfacher Umbruch -> <br>
_ABSATZ = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)*")


class RenderFehler(Exception):
    pass


def _a(wert) -> str:
    return html.escape(str(wert if wert is not None else ""), quote=True)


def _zahl(wert, standard: int) -> int:
    """Zahl aus fremdem Wert; alles Nichtnumerische wird zum Standard."""
    if isinstance(wert, bool):
        return standard
    try:
        return int(float(wert))
    except (TypeError, ValueError, OverflowError):
        return standard


def _ausrichtung(wert) -> str:
    return wert if wert in ("left", "center", "right") else "left"


def _polster(style: dict) -> str:
    p = style.get("padding")
    p = p if isinstance(p, dict) else {}
    return (f'{_zahl(p.get("top"), 0)}px {_zahl(p.get("right"), 24)}px '
            f'{_zahl(p.get("bottom"), 0)}px {_zahl(p.get("left"), 24)}px')


def _fuss_farbe(text: str, grund: str) -> str:
    """Pflichtteil liegt auf der Aussenflaeche: textColor, wenn lesbar, sonst die bessere von weiss/fast-schwarz."""
    try:
        if kontrast(text, grund) >= 4.5:
            return text
        return max(("#ffffff", "#111111"), key=lambda c: kontrast(c, grund))
    except (ValueError, AttributeError, TypeError):
        return "#111111"


def bild_adresse(url: str, bild_basis: str) -> str | None:
    if not bild_basis or not isinstance(url, str) or not url.startswith("medien:"):
        return None
    if not bild_basis.endswith("/"):
        bild_basis += "/"
    return bild_basis + urllib.parse.quote(url[len("medien:"):])


def _text(roh: str, markdown: bool) -> str:
    sicher = _a(roh).replace(chr(0), "")
    if markdown:
        links: list[str] = []

        def link(m):
            ziel = html.unescape(m.group(2))
            if not ziel.startswith("https://"):
                return m.group(1)
            links.append(f'<a href="{_a(ziel)}" style="color:inherit">{m.group(1)}</a>')
            return f"\x00{len(links) - 1}\x00"
        sicher = _LINK.sub(link, sicher)
        sicher = _FETT.sub(r"<strong>\1</strong>", sicher)
        sicher = _KURSIV.sub(r"<em>\1</em>", sicher)
        sicher = re.sub(r"\x00(\d+)\x00", lambda m: links[int(m.group(1))], sicher)
    sicher = sicher.replace("\r\n", "\n").replace("\r", "\n")
    return _ABSATZ.sub("<br><br>", sicher).replace("\n", "<br>")


def _hg(s: dict) -> str:
    """Hintergrund eines einzelnen Blocks (style.backgroundColor) als MJML-Attribut."""
    farbe = s.get("backgroundColor")
    return f' container-background-color="{_a(farbe)}"' if isinstance(farbe, str) and farbe else ""


def _block(dok: dict, bid: str, farben: dict, bild_basis: str) -> str:
    b = dok.get(bid) or {}
    typ = b.get("type")
    data = b.get("data") or {}
    s, p = data.get("style") or {}, data.get("props") or {}
    polster = _polster(s)
    ausr = _ausrichtung(s.get("textAlign"))
    farbe = s.get("color") or farben["text"]
    gewicht = s.get("fontWeight") if s.get("fontWeight") in ("bold", "normal") else None
    hg = _hg(s)
    if typ == "Heading":
        standard = GROESSE_UEBERSCHRIFT.get(p.get("level") or "h2", 24)
        groesse = _zahl(s.get("fontSize"), standard) if s.get("fontSize") else standard
        return (f'<mj-text padding="{polster}" align="{ausr}" color="{_a(farbe)}" font-size="{groesse}px" '
                f'font-weight="{gewicht or "bold"}" line-height="1.25"{hg}>{_text(p.get("text") or "", False)}</mj-text>')
    if typ == "Text":
        groesse = _zahl(s.get("fontSize"), 16)
        return (f'<mj-text padding="{polster}" align="{ausr}" color="{_a(farbe)}" font-size="{groesse}px" '
                f'font-weight="{gewicht or "normal"}" line-height="1.55"{hg}>'
                f'{_text(p.get("text") or "", bool(p.get("markdown")))}</mj-text>')
    if typ == "Image":
        ziel = bild_adresse(p.get("url") or "", bild_basis)
        if not ziel:
            name = (p.get("url") or "")[len("medien:"):] or "?"
            return f'<mj-text padding="{polster}" align="{ausr}" color="{_a(farben["text"])}"{hg}>[Bild: {_a(name)}]</mj-text>'
        w, h = _zahl(p.get("width"), 0), _zahl(p.get("height"), 0)
        masse = (f' width="{w}px"' if w > 0 else "") + (f' height="{h}px"' if h > 0 else "")
        link = p.get("linkHref") or ""
        href = f' href="{_a(link)}"' if isinstance(link, str) and link.startswith("https://") else ""
        return (f'<mj-image padding="{polster}" align="{ausr}" src="{_a(ziel)}" alt="{_a(p.get("alt") or "")}"'
                f'{masse}{href}{hg} />')
    if typ == "Button":
        url = p.get("url") or ""
        if not isinstance(url, str) or not url.startswith("https://"):
            return ""
        rund = KNOPF_RUNDUNG.get(p.get("buttonStyle") or "rounded", 4)
        senk, waag = KNOPF_GROESSE.get(p.get("size") or "medium", KNOPF_GROESSE["medium"])
        voll = ' width="100%"' if p.get("fullWidth") else ""
        return (f'<mj-button padding="{polster}" href="{_a(url)}" align="{ausr}" border-radius="{rund}px" '
                f'inner-padding="{senk}px {waag}px" font-size="{_zahl(s.get("fontSize"), 16)}px" '
                f'background-color="{_a(p.get("buttonBackgroundColor") or KNOPF_FARBE)}" '
                f'color="{_a(p.get("buttonTextColor") or "#FFFFFF")}" font-weight="{gewicht or "bold"}"{voll}{hg}>'
                f'{_text(p.get("text") or "", False)}</mj-button>')
    if typ == "Divider":
        return (f'<mj-divider padding="{polster}" border-color="{_a(p.get("lineColor") or "#333333")}" '
                f'border-width="{_zahl(p.get("lineHeight"), 1)}px"{hg} />')
    if typ == "Spacer":
        return f'<mj-spacer height="{_zahl(p.get("height"), 16)}px" />'
    return ""


def _spalten_polster(index: int, anzahl: int, luecke: int) -> tuple[float, float]:
    """Wie getPaddingBefore/After im Email Builder: die Luecke wird auf die Spaltenraender verteilt."""
    if anzahl == 2:
        return (0 if index == 0 else luecke / 2, luecke / 2 if index == 0 else 0)
    vor = (0, luecke / 3, 2 * luecke / 3)[index]
    nach = (2 * luecke / 3, luecke / 3, 0)[index]
    return vor, nach


def _px(x: float) -> str:
    return f"{x:g}px"


def _kinder_als_section(dok: dict, ids: list, farben: dict, bild_basis: str) -> str:
    teile, spalte = [], []

    def spalte_schliessen():
        if spalte:
            teile.append(f'<mj-section background-color="{_a(farben["innen"])}" padding="0"><mj-column>{"".join(spalte)}</mj-column></mj-section>')
            spalte.clear()
    for bid in ids or []:
        b = dok.get(bid) or {}
        typ = b.get("type")
        stil = (b.get("data") or {}).get("style") or {}
        props = (b.get("data") or {}).get("props") or {}
        if typ == "ColumnsContainer":
            spalte_schliessen()
            anzahl = 3 if _zahl(props.get("columnsCount"), 2) == 3 else 2
            luecke = max(0, _zahl(props.get("columnsGap"), 0))
            senk = props.get("contentAlignment") if props.get("contentAlignment") in ("top", "middle", "bottom") else "middle"
            fest = props.get("fixedWidths") if isinstance(props.get("fixedWidths"), list) else []
            spalten = list(props.get("columns") or [])[:anzahl]
            spalten += [{}] * (anzahl - len(spalten))
            inhalt = ""
            for i, c in enumerate(spalten):
                c = c if isinstance(c, dict) else {}
                vor, nach = _spalten_polster(i, anzahl, luecke)
                breite = _zahl(fest[i], 0) if i < len(fest) and fest[i] else 0
                w = f' width="{breite}px"' if breite > 0 else ""
                inhalt += (f'<mj-column vertical-align="{senk}" padding="0 {_px(nach)} 0 {_px(vor)}"{w}>'
                           + "".join(_block(dok, k, farben, bild_basis) for k in (c.get("childrenIds") or []))
                           + "</mj-column>")
            hg = stil.get("backgroundColor") or farben["innen"]
            pol = _polster(stil) if isinstance(stil.get("padding"), dict) else "0"
            teile.append(f'<mj-section background-color="{_a(hg)}" padding="{pol}">{inhalt}</mj-section>')
        elif typ == "Container":
            spalte_schliessen()
            hg = stil.get("backgroundColor") or farben["innen"]
            kinder = props.get("childrenIds") or []
            inhalt = "".join(_block(dok, k, farben, bild_basis) for k in kinder)
            rand = stil.get("borderColor")
            rahmen = f' border="1px solid {_a(rand)}"' if isinstance(rand, str) and rand else ""
            pol = _polster(stil) if isinstance(stil.get("padding"), dict) else "0"
            # Karte: aussen die Flaeche, innen die Containerfarbe mit Rundung und Innenabstand
            teile.append(
                f'<mj-wrapper background-color="{_a(farben["innen"])}" padding="0 {KARTEN_RAND}px">'
                f'<mj-section background-color="{_a(hg)}" border-radius="{_zahl(stil.get("borderRadius"), 0)}px"{rahmen} '
                f'padding="{pol}"><mj-column>{inhalt}</mj-column></mj-section></mj-wrapper>')
        else:
            spalte.append(_block(dok, bid, farben, bild_basis))
    spalte_schliessen()
    return "".join(teile)


def nach_mjml(dokument: dict, betreff: str, vorschautext: str, pflichtteil: dict,
              bild_basis: str = "", breite: int = 600) -> str:
    wurzel = (dokument.get("root") or {}).get("data") or {}
    # aussen = Flaeche um die Mail (backdropColor), innen = Inhaltsflaeche (canvasColor)
    farben = {"aussen": wurzel.get("backdropColor") or "#f2f5f7",
              "innen": wurzel.get("canvasColor") or "#ffffff",
              "text": wurzel.get("textColor") or "#242424",
              "akzent": "#5eead4"}
    schrift = SCHRIFTEN.get(wurzel.get("fontFamily") or "MODERN_SANS", SCHRIFTEN["MODERN_SANS"]).replace('"', "'")
    rumpf = _kinder_als_section(dokument, wurzel.get("childrenIds") or [], farben, bild_basis)
    fuss_farbe = _fuss_farbe(farben["text"], farben["aussen"])
    impressum = (pflichtteil.get("impressum") or "").strip()
    impressum_html = (_a(impressum) if impressum else
                      '<strong style="color:#ef4444">Impressum fehlt &ndash; im Mandanten hinterlegen</strong>')
    abmelden = _a((pflichtteil.get("abmelde_hinweis") or "").replace("{abmeldelink}", "[Abmeldelink]"))
    fuss = (f'<mj-section padding="16px 0"><mj-column><mj-text align="center" font-size="11px" '
            f'color="{_a(fuss_farbe)}" line-height="1.5">{impressum_html}<br>{abmelden}</mj-text></mj-column></mj-section>')
    return (f'<mjml><mj-head><mj-title>{_a(betreff)}</mj-title><mj-preview>{_a(vorschautext)}</mj-preview>'
            f'<mj-attributes><mj-all font-family="{_a(schrift)}" /></mj-attributes></mj-head>'
            f'<mj-body background-color="{_a(farben["aussen"])}" width="{_zahl(breite, 600)}px">{rumpf}{fuss}</mj-body></mjml>')


def rendern(dokument: dict, betreff: str, vorschautext: str, pflichtteil: dict,
            bild_basis: str = "", handy: bool = False) -> str:
    # mjml-python erst hier laden: fehlt das Paket, faellt nur die Bloecke-
    # Vorschau aus (422 mit Grund), nicht die ganze Marketing-API.
    try:
        import mjml
    except ImportError as e:
        raise RenderFehler("mjml-python fehlt auf diesem Rechner - der Newsletter laesst sich nicht setzen "
                           "(pip install -r spaces/marketing/requirements.txt)") from e
    quelle = nach_mjml(dokument, betreff, vorschautext, pflichtteil, bild_basis, 380 if handy else 600)
    try:
        return mjml.mjml2html(quelle)
    except Exception as e:  # mrml meldet Englisch; nach aussen deutsch
        raise RenderFehler(f"Der Newsletter liess sich nicht setzen: {e}") from e

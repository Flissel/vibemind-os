"""Newsletter als PDF (Paket C3, 19.10.2026): Chromium (headless, ueber Playwright) setzt das fertige Mail-HTML
eines Editor-Newsletters. Dasselbe HTML wie in der Vorschau und beim Versand (bloecke_mjml.rendern) - das PDF sieht
also aus wie der Newsletter, mit Markenfarben, -schriften und Logo, ohne einen zweiten Setzer.

WARUM CHROMIUM UND NICHT REPORTLAB/WEASYPRINT: MJML erzeugt responsives Mail-HTML (Tabellen, inline-block,
Medienabfragen, Webschriften als woff2). Ein Browser setzt das genau wie die Vorschau; jeder andere Setzer weicht
im Detail ab, und ein PDF, das anders aussieht als der freigegebene Newsletter, waere die falsche Antwort.

NETZSPERRE: der Inhalt eines Entwurfs bestimmt, was der Browser laden wuerde. Auf der VM darf er deshalb nur die
eigenen Bild- und Schriftadressen dieses Aufrufs laden (Praefixe aus bild_basis/schrift_basis); alles andere wird
abgebrochen - keine Anfrage an interne Dienste, Metadaten-Adressen oder fremde Server. JavaScript ist aus.
Sicherheitspruefung 19.10.: Schema, Host und Port werden exakt verglichen, der Pfad normalisiert (Punkt-Segmente,
%-Kodierung); jede Anfrage holt das Werkzeug selbst OHNE Weiterleitung (eine erlaubte Adresse koennte sonst auf eine
interne umleiten, und Playwright faengt Weiterleitungen nicht ab). Welche Hosts ueberhaupt in Frage kommen, legt der
Aufrufer aus der Server-Konfiguration fest (api/pult.py: MARKETING_PDF_HOSTS), nicht aus der Anfrage.
"""
from __future__ import annotations

import math
import posixpath
from urllib.parse import unquote, urlsplit

DRUCK_CSS = ("@page{margin:0}"
             "html,body{-webkit-print-color-adjust:exact;print-color-adjust:exact}")
TIMEOUT_S = 60
BREITE_PX = 700          # Newsletter 600 px + Rand, wie in der Mail-Vorschau
# PDF-Seiten duerfen hoechstens 200 Zoll hoch sein (14400 pt = 19200 px); darueber A4-Seiten.
MAX_HOEHE_PX = 19000


class PdfDruckFehler(RuntimeError):
    pass


def druck_html(html: str) -> str:
    """Druckregeln (A4, Hintergruende mitdrucken, Bilder nicht zerschneiden) in den Kopf setzen."""
    stil = f"<style>{DRUCK_CSS}</style>"
    i = html.find("</head>")
    return html[:i] + stil + html[i:] if i >= 0 else stil + html


def _pfad(roh: str) -> str:
    return posixpath.normpath("/" + unquote(roh or "/")).rstrip("/") + "/"


def seitenmass(hoehe_px) -> dict:
    """Eine durchgehende Seite in voller Laenge (echter Lauf 19.10.: auf A4 sprangen grosse Bilder auf die naechste
    Seite und liessen leere Flaechen); nur wenn das nicht geht, A4-Seiten."""
    try:
        h = math.ceil(float(hoehe_px))
    except (TypeError, ValueError):
        return {"format": "A4"}
    if h <= 0 or h > MAX_HOEHE_PX:
        return {"format": "A4"}
    return {"width": f"{BREITE_PX}px", "height": f"{h}px"}


def darf_laden(url: str, praefixe) -> bool:
    if url.startswith("data:") or url == "about:blank":
        return True
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    if u.scheme != "https" or not u.netloc:
        return False
    pfad = _pfad(u.path)
    for p in praefixe or ():
        if not isinstance(p, str) or not p:
            continue
        q = urlsplit(p)
        if q.scheme == "https" and q.netloc.lower() == u.netloc.lower() and pfad.startswith(_pfad(q.path)):
            return True
    return False


def _weiche(erlaubt: list, fehler_typ):
    """Routen-Handler: nur erlaubte Adressen, selbst geholt ohne Weiterleitung; 3xx wird verworfen."""
    def weiche(route) -> None:
        if not darf_laden(route.request.url, erlaubt):
            route.abort()
            return
        try:
            antwort = route.fetch(max_redirects=0)
        except fehler_typ:
            route.abort()
            return
        if 300 <= antwort.status < 400:
            route.abort()
            return
        route.fulfill(response=antwort)
    return weiche


def html_zu_pdf(html: str, erlaubt: list, timeout_s: int = TIMEOUT_S) -> bytes:
    """Setzt das HTML als A4-PDF. Wirft PdfDruckFehler, wenn Playwright/Chromium fehlt oder der Druck scheitert."""
    try:
        from playwright.sync_api import Error as PlaywrightFehler
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise PdfDruckFehler("PDF-Druck ist hier nicht eingerichtet (Playwright fehlt)") from None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                # Seiten-JavaScript aus; evaluate (Hoehe messen) laeuft trotzdem ueber Playwright selbst
                seite = browser.new_page(java_script_enabled=False, viewport={"width": BREITE_PX, "height": 1000})
                seite.route("**/*", _weiche(erlaubt, PlaywrightFehler))
                seite.set_content(druck_html(html), wait_until="networkidle", timeout=timeout_s * 1000)
                hoehe = seite.evaluate("document.documentElement.scrollHeight")
                return seite.pdf(print_background=True, **seitenmass(hoehe))
            finally:
                browser.close()
    except PlaywrightFehler as e:
        raise PdfDruckFehler(f"PDF-Druck gescheitert: {str(e).splitlines()[0][:200]}") from None

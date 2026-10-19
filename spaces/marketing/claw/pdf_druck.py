"""Newsletter als PDF (Paket C3, 19.10.2026): Chromium (headless, ueber Playwright) setzt das fertige Mail-HTML
eines Editor-Newsletters. Dasselbe HTML wie in der Vorschau und beim Versand (bloecke_mjml.rendern) - das PDF sieht
also aus wie der Newsletter, mit Markenfarben, -schriften und Logo, ohne einen zweiten Setzer.

WARUM CHROMIUM UND NICHT REPORTLAB/WEASYPRINT: MJML erzeugt responsives Mail-HTML (Tabellen, inline-block,
Medienabfragen, Webschriften als woff2). Ein Browser setzt das genau wie die Vorschau; jeder andere Setzer weicht
im Detail ab, und ein PDF, das anders aussieht als der freigegebene Newsletter, waere die falsche Antwort.

NETZSPERRE: der Inhalt eines Entwurfs bestimmt, was der Browser laden wuerde. Auf der VM darf er deshalb nur die
eigenen Bild- und Schriftadressen dieses Aufrufs laden (Praefixe aus bild_basis/schrift_basis); alles andere wird
abgebrochen - keine Anfrage an interne Dienste, Metadaten-Adressen oder fremde Server. JavaScript ist aus.
"""
from __future__ import annotations

DRUCK_CSS = ("@page{size:A4;margin:0}"
             "html,body{-webkit-print-color-adjust:exact;print-color-adjust:exact}"
             "img{break-inside:avoid}")
TIMEOUT_S = 60


class PdfDruckFehler(RuntimeError):
    pass


def druck_html(html: str) -> str:
    """Druckregeln (A4, Hintergruende mitdrucken, Bilder nicht zerschneiden) in den Kopf setzen."""
    stil = f"<style>{DRUCK_CSS}</style>"
    i = html.find("</head>")
    return html[:i] + stil + html[i:] if i >= 0 else stil + html


def darf_laden(url: str, praefixe) -> bool:
    if url.startswith(("data:", "about:")):
        return True
    return any(isinstance(p, str) and p and url.startswith(p) for p in praefixe or ())


def html_zu_pdf(html: str, erlaubt: list, timeout_s: int = TIMEOUT_S) -> bytes:
    """Setzt das HTML als A4-PDF. Wirft PdfDruckFehler, wenn Playwright/Chromium fehlt oder der Druck scheitert."""
    try:
        from playwright.sync_api import Error as PlaywrightFehler
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise PdfDruckFehler("PDF-Druck ist hier nicht eingerichtet (Playwright fehlt)") from None

    def weiche(route):
        if darf_laden(route.request.url, erlaubt):
            route.continue_()
        else:
            route.abort()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                seite = browser.new_page(java_script_enabled=False, viewport={"width": 700, "height": 1000})
                seite.route("**/*", weiche)
                seite.set_content(druck_html(html), wait_until="networkidle", timeout=timeout_s * 1000)
                return seite.pdf(format="A4", print_background=True)
            finally:
                browser.close()
    except PlaywrightFehler as e:
        raise PdfDruckFehler(f"PDF-Druck gescheitert: {str(e).splitlines()[0][:200]}") from None

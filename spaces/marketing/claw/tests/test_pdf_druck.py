"""Newsletter als PDF (Paket C3, 19.10.2026): Chromium setzt das fertige Mail-HTML. Geprueft wird die Logik
(Druckregeln, Netzsperre, Fehler ohne Playwright); der echte Druck nur, wenn Playwright samt Chromium da ist."""
import sys

import pytest

from spaces.marketing.claw import pdf_druck

HTML = "<html><head><title>x</title></head><body><p>Hallo Druck</p></body></html>"


def test_druckregeln_stehen_im_kopf():
    h = pdf_druck.druck_html(HTML)
    assert h.index(pdf_druck.DRUCK_CSS) < h.index("</head>")
    assert "print-color-adjust:exact" in pdf_druck.DRUCK_CSS
    assert "size:A4" in pdf_druck.DRUCK_CSS


def test_druckregeln_ohne_kopf():
    assert pdf_druck.druck_html("<p>x</p>").startswith(f"<style>{pdf_druck.DRUCK_CSS}</style>")


@pytest.mark.parametrize("url, erlaubt", [
    ("https://h.ts.net/marketing/bild/sig/a.png", True),
    ("https://h.ts.net/marketing/schrift/oxanium-600-normal.woff2", True),
    ("data:image/png;base64,AAAA", True),
    ("about:blank", True),
    ("https://h.ts.net/admin", False),
    ("http://127.0.0.1:5510/api/health", False),
    ("http://169.254.169.254/latest/meta-data", False),
    ("https://boese.example/marketing/bild/sig/a.png", False),
    ("file:///etc/passwd", False),
])
def test_netzsperre(url, erlaubt):
    praefixe = ["https://h.ts.net/marketing/bild/sig/", "https://h.ts.net/marketing/schrift/"]
    assert pdf_druck.darf_laden(url, praefixe) is erlaubt


def test_leere_praefixe_werden_ignoriert():
    assert not pdf_druck.darf_laden("https://irgendwas/", ["", None])


def test_ohne_playwright_klarer_fehler(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with pytest.raises(pdf_druck.PdfDruckFehler, match="nicht eingerichtet"):
        pdf_druck.html_zu_pdf(HTML, [])


def _chromium_da() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:  # noqa: BLE001 - dann wird der echte Druck uebersprungen
        return False


@pytest.mark.skipif(not _chromium_da(), reason="Playwright/Chromium nicht installiert")
def test_echter_druck():
    roh = pdf_druck.html_zu_pdf(HTML, [])
    assert roh.startswith(b"%PDF-")
    assert len(roh) > 500

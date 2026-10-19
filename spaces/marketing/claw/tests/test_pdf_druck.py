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
    assert "margin:0" in pdf_druck.DRUCK_CSS


def test_eine_durchgehende_seite_wie_der_newsletter():
    """Echter Lauf 19.10.: auf A4 sprangen grosse Bilder auf die naechste Seite und liessen Luecken - jetzt eine
    Seite in voller Newsletter-Laenge; nur ueber der PDF-Hoechstgroesse wird auf A4 umbrochen."""
    assert pdf_druck.seitenmass(3456) == {"width": "700px", "height": "3456px"}
    assert pdf_druck.seitenmass(3456.2) == {"width": "700px", "height": "3457px"}
    assert pdf_druck.seitenmass(pdf_druck.MAX_HOEHE_PX + 1) == {"format": "A4"}
    assert pdf_druck.seitenmass(0) == {"format": "A4"}


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
    # Sicherheitspruefung 19.10.: Pfad normalisieren, Host/Port exakt
    ("https://h.ts.net/marketing/bild/sig/../../admin", False),
    ("https://h.ts.net/marketing/bild/sig/%2e%2e/%2e%2e/admin", False),
    ("https://h.ts.net:8443/marketing/bild/sig/a.png", False),
    ("https://h.ts.net.boese.example/marketing/bild/sig/a.png", False),
    ("http://h.ts.net/marketing/bild/sig/a.png", False),
    ("https://H.TS.NET/marketing/bild/sig/a.png", True),
    ("https://h.ts.net/marketing/bild/sigX/a.png", False),
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


class _Route:
    def __init__(self, url, status=200):
        self.request = type("R", (), {"url": url})()
        self.status, self.log = status, []

    def fetch(self, max_redirects=None):
        self.log.append(("fetch", max_redirects))
        return type("A", (), {"status": self.status})()

    def fulfill(self, response=None):
        self.log.append(("fulfill", response.status))

    def abort(self):
        self.log.append(("abort",))


def test_weiche_folgt_keiner_weiterleitung():
    """Sicherheitspruefung 19.10.: eine erlaubte Adresse darf nicht auf eine interne umleiten."""
    weiche = pdf_druck._weiche(["https://h.ts.net/marketing/bild/sig/"], Exception)
    r = _Route("https://h.ts.net/marketing/bild/sig/a.png", status=302)
    weiche(r)
    assert r.log == [("fetch", 0), ("abort",)]
    r = _Route("https://h.ts.net/marketing/bild/sig/a.png", status=200)
    weiche(r)
    assert r.log == [("fetch", 0), ("fulfill", 200)]
    r = _Route("http://127.0.0.1:5510/api/health")
    weiche(r)
    assert r.log == [("abort",)]

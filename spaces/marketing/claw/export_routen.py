"""Gemeinsame Route-Weiche fuer headless Chromium (Export, Pruefbild, Bildstuecke der Collage-Mail): beantwortet
die Anfragen einer lokal gesetzten Seite an BASIS aus Medien, Schriftdateien und schriften.css; alles andere wird
abgebrochen. Liegt in claw/, damit claw/ nie von workers/ abhaengt; export_worker fuehrt die Namen weiter."""
from __future__ import annotations

import functools
import io
import os
import urllib.parse

from spaces.marketing.claw import bildplaetze, gestaltung, schriften

BASIS = "https://export.vibemind.invalid/"


def schriften_css() -> str:
    """@font-face je Register-Datei; die URLs sind relativ zu <BASIS>schrift/schriften.css."""
    regeln = []
    for sid, eintrag in schriften.EBENEN_SCHRIFTEN.items():   # inkl. Handschrift (Rolle HAND)
        for gewicht, stil in eintrag["dateien"]:
            regeln.append("@font-face{font-family:'%s';font-weight:%s;font-style:%s;font-display:block;"
                          "src:url(%s-%s-%s.woff2) format('woff2');}" % (eintrag["familie"], gewicht, stil,
                                                                        sid, gewicht, stil))
    return "\n".join(regeln) + "\n"


KACHEL_KANTE = 600


@functools.lru_cache(maxsize=32)
def kachel_png(a: int, b: int) -> bytes:
    """Neutrale Bildkachel im Verhaeltnis a:b (lange Kante KACHEL_KANTE): hellgrau, feine Diagonalen, "Bild".
    Steht im Pruefbild fuer einen Platzhalter (Bild wird noch erzeugt) oder ein fehlendes Medium."""
    from PIL import Image, ImageDraw
    a, b = max(1, int(a)), max(1, int(b))
    breite, hoehe = ((KACHEL_KANTE, max(1, round(KACHEL_KANTE * b / a))) if a >= b
                     else (max(1, round(KACHEL_KANTE * a / b)), KACHEL_KANTE))
    bild = Image.new("RGB", (breite, hoehe), (232, 232, 232))
    zeichnen = ImageDraw.Draw(bild)
    for x in range(-hoehe, breite, 28):
        zeichnen.line([(x, hoehe), (x + hoehe, 0)], fill=(214, 214, 214), width=2)
    if breite >= 40 and hoehe >= 20:
        kasten = zeichnen.textbbox((0, 0), "Bild")
        tb, th = kasten[2] - kasten[0], kasten[3] - kasten[1]
        zeichnen.text(((breite - tb) / 2, (hoehe - th) / 2), "Bild", fill=(140, 140, 140))
    puffer = io.BytesIO()
    bild.save(puffer, "PNG")
    return puffer.getvalue()


class Routen:
    """Beantwortet die Anfragen der Seite an BASIS; alles andere wird abgebrochen.
    pruefbild=True (nur das Pruefbild der Collage): Platzhalter (medien:platzhalter-AxB.png) werden lokal als
    graue Kachel gezeichnet und nie geholt; ein fehlendes Medium wird zur grauen Kachel und steht in fehlt (der
    Aufrufer macht daraus einen Hinweis). Ohne den Schalter (Export) bleibt ein fehlendes Medium ein 404."""

    def __init__(self, api, aid, pruefbild: bool = False, medium_holen=None):
        """medium_holen(name) -> bytes | None ersetzt api.medium(aid, name) (lokale Medien, z. B. Collage-Mail)."""
        self.api, self.aid, self.medien, self.fehlt, self.fehler = api, aid, {}, [], None
        self.pruefbild = pruefbild
        self.medium_holen = medium_holen

    def __call__(self, route):
        try:
            if not route.request.url.startswith(BASIS):
                return route.abort()
            pfad = urllib.parse.unquote(urllib.parse.urlsplit(route.request.url).path)
            if pfad.startswith("/medien/"):
                name = pfad[len("/medien/"):]
                if self.pruefbild and (m := bildplaetze.PLATZHALTER.match("medien:" + name)):
                    return route.fulfill(status=200, body=kachel_png(int(m[1]), int(m[2])),
                                         content_type="image/png")
                if name not in self.medien:
                    self.medien[name] = (self.medium_holen(name) if self.medium_holen else self.api.medium(self.aid, name))
                daten = self.medien[name]
                if daten is None:
                    if self.pruefbild:
                        if name not in self.fehlt:
                            self.fehlt.append(name)
                        return route.fulfill(status=200, body=kachel_png(4, 3), content_type="image/png")
                    self.fehlt.append(name)
                    return route.fulfill(status=404, body=b"")
                return route.fulfill(status=200, body=daten, content_type=bildtyp(name))
            if pfad == "/schrift/schriften.css":
                return route.fulfill(status=200, body=schriften_css(), content_type="text/css")
            if pfad.startswith("/schrift/"):
                datei = os.path.basename(pfad)
                voll = os.path.join(gestaltung.ORDNER_SCHRIFTEN, datei)
                if datei.endswith(".woff2") and os.path.isfile(voll):
                    with open(voll, "rb") as f:
                        return route.fulfill(status=200, body=f.read(), content_type="font/woff2")
            return route.abort()
        except Exception as e:  # noqa: BLE001 - Seite nicht haengen lassen, Fehler nach set_content melden
            if self.fehler is None:
                self.fehler = e
            return route.abort()


def bildtyp(name: str) -> str:
    return {"png": "image/png", "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml"}.get(
        name.rsplit(".", 1)[-1].lower(), "image/jpeg")


class EchterBrowser:
    def __init__(self, start_timeout_ms: int = 30000):   # 30 s = Playwright-Vorgabe
        from playwright.sync_api import sync_playwright   # erst hier: Unit-Tests brauchen keinen Browser
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=True, timeout=start_timeout_ms)
        except Exception:
            self._pw.stop()
            raise

    def new_page(self, **kwargs):
        return self._browser.new_page(**kwargs)

    def close(self):
        try:
            self._browser.close()
        finally:
            self._pw.stop()

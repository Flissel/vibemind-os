import io
import os

import pytest
from PIL import Image

from spaces.marketing.workers import export_worker as ew
from spaces.marketing.workers.bild_worker import ApiFehler

DOC = {"root": {"type": "EmailLayout", "data": {"backdropColor": "#ffffff", "childrenIds": ["t"]}},
       "t": {"type": "Text", "data": {"props": {"text": "Herbst"}}}}
DOC_BILD = {"root": {"type": "EmailLayout", "data": {"childrenIds": ["b"]}},
            "b": {"type": "Image", "data": {"props": {"url": "medien:a b.jpg", "alt": "x"}}}}
AUFTRAG = {"id": "a1", "art": "export", "bloecke": DOC, "betreff": "Herbst", "vorschautext": "vt",
           "pflichtteil": {"impressum": "Impressum", "abmelde_hinweis": "Abmelden"},
           "kontext": {"geraete": ["handy", "tablet", "pc"], "slug": "herbst"}, "medien": []}


def png(farbe=(200, 30, 30), groesse=(40, 40)):
    puffer = io.BytesIO()
    Image.new("RGB", groesse, farbe).save(puffer, "PNG")
    return puffer.getvalue()


class Api:
    def __init__(self, medien=None):
        self.log, self.medien = [], medien or {}

    def weiter(self, aid):
        return True

    def medium(self, aid, name):
        self.log.append(("medium", name)); return self.medien.get(name)

    def datei(self, aid, name, roh):
        self.log.append(("datei", name, roh)); return name

    def fertig(self, aid, daten):
        self.log.append(("fertig", aid, daten)); return {"status": "fertig"}

    def zurueck(self, aid, antwort):
        self.log.append(("zurueck", aid, antwort)); return "offen"


class Anfrage:
    def __init__(self, url): self.url = url


class Route:
    def __init__(self, url): self.request, self.ergebnis = Anfrage(url), None

    def fulfill(self, **kw): self.ergebnis = ("fulfill", kw)

    def abort(self): self.ergebnis = ("abort", {})


class Seite:
    def __init__(self, browser, viewport):
        self.browser, self.viewport, self.handler = browser, viewport, None

    def route(self, muster, handler):
        self.muster, self.handler = muster, handler

    def set_content(self, html, wait_until=None):
        self.browser.inhalte.append((self.viewport["width"], html, wait_until))
        for url in self.browser.anfragen:
            r = Route(url); self.handler(r); self.browser.routen.append((url, r.ergebnis))

    def screenshot(self, full_page=False, type="png"):
        if self.browser.screenshot_fehler:
            raise RuntimeError("Chromium abgestuerzt")
        return png()

    def close(self): pass


class Browser:
    def __init__(self, anfragen=(), screenshot_fehler=False):
        self.anfragen, self.screenshot_fehler = list(anfragen), screenshot_fehler
        self.inhalte, self.routen, self.zu = [], [], False

    def new_page(self, **kw):
        return Seite(self, kw["viewport"])

    def close(self): self.zu = True


def test_schriften_css_hat_22_font_face_mit_relativen_urls():
    css = ew.schriften_css()
    assert css.count("@font-face") == 22
    assert "src:url(cormorant-400-italic.woff2)" in css and "font-style:italic" in css
    assert "font-family:'Cormorant Garamond'" in css


def test_jeder_font_face_hat_eine_datei_auf_platte():
    from spaces.marketing.claw import gestaltung, schriften
    for sid, e in schriften.REGISTER.items():
        for g, s in e["dateien"]:
            assert os.path.isfile(os.path.join(gestaltung.ORDNER_SCHRIFTEN, f"{sid}-{g}-{s}.woff2"))


def test_jpeg_passend_normal_und_zu_gross():
    out = ew.jpeg_passend(png())
    assert out[:2] == b"\xff\xd8"
    rauschen = Image.frombytes("RGB", (900, 900), os.urandom(900 * 900 * 3))
    puffer = io.BytesIO(); rauschen.save(puffer, "PNG")
    assert len(ew.jpeg_passend(puffer.getvalue(), grenze=1_200_000)) <= 1_200_000
    with pytest.raises(ValueError):
        ew.jpeg_passend(puffer.getvalue(), grenze=1000)


def test_html_bauen_nutzt_basis_fuer_medien_und_schriften():
    html = ew.html_bauen({**AUFTRAG, "bloecke": DOC_BILD})
    assert "https://export.vibemind.invalid/medien/a" in html


def test_exportieren_drei_uploads_in_reihenfolge():
    api, b = Api(), Browser()
    assert ew.exportieren(api, AUFTRAG, browser_starten=lambda: b) == "fertig"
    assert [e[1] for e in api.log if e[0] == "datei"] == ["herbst-handy.jpg", "herbst-tablet.jpg", "herbst-pc.jpg"]
    assert [i[0] for i in b.inhalte] == [375, 768, 1200]
    assert all(i[2] == "networkidle" for i in b.inhalte) and b.zu
    fertig = api.log[-1]
    assert fertig[0] == "fertig" and fertig[2]["antwort"] == "Export fertig: herbst-handy.jpg, herbst-tablet.jpg, herbst-pc.jpg"
    assert fertig[2]["bildauftraege"] == [] and fertig[2]["bloecke"] is None
    assert all(e[2][:2] == b"\xff\xd8" for e in api.log if e[0] == "datei")


def test_route_liefert_medien_css_schrift_und_bricht_fremdes_ab():
    api = Api({"a b.jpg": b"BILD"})
    b = Browser([ew.BASIS + "medien/a%20b.jpg", ew.BASIS + "schrift/schriften.css",
                 ew.BASIS + "schrift/dm-sans-400-normal.woff2", "https://fremd.example/x.png",
                 ew.BASIS + "schrift/../../etc/passwd"])
    assert ew.exportieren(api, {**AUFTRAG, "bloecke": DOC_BILD}, browser_starten=lambda: b) == "fertig"
    ergebnis = dict(b.routen)
    assert ergebnis[ew.BASIS + "medien/a%20b.jpg"][1]["body"] == b"BILD"
    assert "@font-face" in ergebnis[ew.BASIS + "schrift/schriften.css"][1]["body"]
    assert ergebnis[ew.BASIS + "schrift/dm-sans-400-normal.woff2"][1]["content_type"] == "font/woff2"
    assert ergebnis["https://fremd.example/x.png"][0] == "abort"
    assert ergebnis[ew.BASIS + "schrift/../../etc/passwd"][0] == "abort"
    assert ("medium", "a b.jpg") in api.log


def test_fehlendes_medium_bricht_ab():
    api = Api()
    b = Browser([ew.BASIS + "medien/weg.jpg"])
    assert ew.exportieren(api, AUFTRAG, browser_starten=lambda: b) == "fehler"
    assert api.log[-1][0] == "zurueck" and "fehlt in den Medien" in api.log[-1][2]
    assert not [e for e in api.log if e[0] == "datei"]


def test_screenshot_fehler_gibt_zurueck_genau_einmal():
    api, b = Api(), Browser(screenshot_fehler=True)
    assert ew.exportieren(api, AUFTRAG, browser_starten=lambda: b) == "fehler"
    zur = [e for e in api.log if e[0] == "zurueck"]
    assert len(zur) == 1 and zur[0][2].startswith("Export nicht möglich: ") and "Chromium" in zur[0][2]
    assert b.zu and not [e for e in api.log if e[0] in ("fertig", "datei")]


def test_browserstart_fehler_und_fremder_auftrag():
    api = Api()
    def start(): raise RuntimeError("kein chromium")
    assert ew.exportieren(api, AUFTRAG, browser_starten=start) == "fehler"
    assert "kein chromium" in api.log[-1][2]

    class Fremd(Api):
        def datei(self, *a): raise ApiFehler(409, "weg")
    api = Fremd()
    assert ew.exportieren(api, AUFTRAG, browser_starten=lambda: Browser()) == "fehler"
    assert not [e for e in api.log if e[0] == "zurueck"]

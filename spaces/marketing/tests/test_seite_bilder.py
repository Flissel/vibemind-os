import io
import re

import pytest
from PIL import Image

from spaces.marketing.claw import seite_bilder as sb
from spaces.marketing.claw.seite_mail import Stueck


def seite(ebenen, hoehe=800):
    return {"version": 1, "breite": 600, "hoehe": hoehe, "hintergrund": {"farbe": "#fafafa", "bild": None},
            "schriften": {"anzeige": "playfair", "text": "dm-sans"}, "ebenen": ebenen}


def bild(eid, quelle, y=200):
    return {"id": eid, "art": "bild", "x": 300, "y": y, "breite": 400, "hoehe": 200, "quelle": quelle}


def png(b, h, rauschen=False):
    bild_ = Image.effect_noise((b, h), 90).convert("RGB") if rauschen else Image.new("RGB", (b, h), (200, 30, 30))
    p = io.BytesIO()
    bild_.save(p, "PNG")
    return p.getvalue()


def masse(teile):
    return [Image.open(io.BytesIO(t)).size for t in teile]


# ---- Zuschnitt -------------------------------------------------------------------------------------------
def test_zuschnitt_hoehen_und_breite():
    st = [Stueck(1, 0, 100, "a", None), Stueck(2, 150, 450, "b", "https://x.de")]
    teile = sb.zuschneiden(png(1200, 1000), st)
    assert masse(teile) == [(1200, 200), (1200, 600)]
    assert all(Image.open(io.BytesIO(t)).format == "JPEG" for t in teile)


def test_zuschnitt_ohne_stuecke():
    assert sb.zuschneiden(png(1200, 100), []) == []


def test_zuschnitt_ausserhalb_der_seite():
    with pytest.raises(sb.StueckFehler):
        sb.zuschneiden(png(1200, 100), [Stueck(1, 200, 300, "a", None)])


def test_groessengrenze_meldet_verstaendlich():
    st = [Stueck(1, 0, 400, "a", None)]
    with pytest.raises(sb.StueckFehler) as e:
        sb.zuschneiden(png(1200, 800, rauschen=True), st, max_bytes=2**18)
    assert re.fullmatch(r"Die Mail wäre zu groß \(\d+,\d MB, höchstens 0,25 MB\) – "
                        r"weniger oder kleinere Bildbereiche", str(e.value))
    assert str(e.value).startswith("Die Mail wäre zu groß (")
    assert str(e.value).endswith("– weniger oder kleinere Bildbereiche")


def test_qualitaet_sinkt_vor_der_grenze():
    st = [Stueck(1, 0, 400, "a", None)]
    p = png(1200, 800, rauschen=True)
    q82 = len(sb.zuschneiden(p, st)[0])
    teile = sb.zuschneiden(p, st, max_bytes=int(q82 * 0.9))
    assert len(teile[0]) <= int(q82 * 0.9)


# ---- Rendern mit gefaelschtem Browser ---------------------------------------------------------------------
class _Anfrage:
    def __init__(self, url):
        self.url = url


class _Route:
    def __init__(self, url, log):
        self.request, self.log = _Anfrage(url), log

    def fulfill(self, status, body, content_type=None):
        self.log.append(("fulfill", self.request.url, status))

    def abort(self):
        self.log.append(("abort", self.request.url, None))


class _Seite:
    def __init__(self, kw, log):
        self.kw, self.log, self.handler = kw, log, None

    def route(self, muster, handler):
        self.handler = handler

    def set_content(self, html, wait_until=None):
        for url in re.findall(r"https://export\.vibemind\.invalid/[^\"')\s]+", html):
            self.handler(_Route(url, self.log))

    def locator(self, sel):
        seite_ = self

        class L:
            first = type("E", (), {"screenshot": staticmethod(lambda type="png": png(1200, 1600))})()
        seite_.sel = sel
        return L

    def close(self):
        pass


class _Browser:
    def __init__(self):
        self.log, self.seiten = [], []

    def new_page(self, **kw):
        s = _Seite(kw, self.log)
        self.seiten.append(s)
        return s

    def close(self):
        pass


def test_render_nutzt_faktor_und_container():
    b = _Browser()
    daten = {"foto.jpg": b"\xff\xd8x"}
    out = sb.render_png(seite([bild("b", "medien:foto.jpg")]), daten.get, browser_starten=lambda: b)
    assert Image.open(io.BytesIO(out)).size == (1200, 1600)
    assert b.seiten[0].kw["device_scale_factor"] == 2 and b.seiten[0].kw["java_script_enabled"] is False
    assert b.seiten[0].sel == "body > div > div"
    assert ("fulfill", "https://export.vibemind.invalid/medien/foto.jpg", 200) in b.log


def test_fehlendes_medium_blockiert():
    with pytest.raises(sb.StueckFehler, match="^Bild foto.jpg fehlt$"):
        sb.render_png(seite([bild("b", "medien:foto.jpg")]), lambda n: None, browser_starten=_Browser)


def test_platzhalter_blockiert():
    gefragt = []

    def lesen(n):
        gefragt.append(n)
        return b"x"
    with pytest.raises(sb.StueckFehler, match="^Bild platzhalter-3x2.png fehlt$"):
        sb.render_png(seite([bild("b", "medien:platzhalter-3x2.png")]), lesen, browser_starten=_Browser)
    assert gefragt == []


# ---- Echter Browser ----------------------------------------------------------------------------------------
def _chromium_da():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.browser
def test_echter_browser_rauchtest():
    if not _chromium_da():
        pytest.skip("Chromium fehlt")
    s = seite([{"id": "t", "art": "text", "x": 300, "y": 100, "breite": 400, "hoehe": 80, "text": "Hallo Welt",
                "rolle": "TEXT", "groesse": 24, "farbe": "#111111"},
               {"id": "f", "art": "form", "x": 300, "y": 300, "breite": 200, "hoehe": 100, "form": "rechteck",
                "farbe": "#5b3fd6"}], hoehe=500)
    out = sb.render_png(s, lambda n: None)
    assert Image.open(io.BytesIO(out)).size == (1200, 1000)
    teile = sb.zuschneiden(out, [Stueck(1, 250, 450, "Bild", None)])
    assert masse(teile) == [(1200, 400)]

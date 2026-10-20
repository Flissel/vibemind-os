import base64
import io

import pytest
from PIL import Image

from spaces.marketing.claw import seite
from spaces.marketing.workers import seite_pruefbild as pb


def _png(b, h):
    p = io.BytesIO()
    Image.new("RGB", (b, h), (200, 30, 30)).save(p, "PNG")
    return p.getvalue()


def _seite(hoehe=900):
    s = seite.leer(None)
    s["hoehe"] = hoehe
    return s


def _fake(b, h):
    gesehen = []

    def render(html, hoehe):
        gesehen.append((html, hoehe))
        return _png(b, h)
    render.gesehen = gesehen
    return render


def _masse(teile):
    return [Image.open(io.BytesIO(t)).size for t in teile]


def test_eine_kachel():
    r = _fake(600, 900)
    teile, faktor = pb.kacheln(_seite(900), r)
    assert _masse(teile) == [(600, 900)] and faktor == 1.0
    assert r.gesehen[0][1] == 900 and "<html" in r.gesehen[0][0]
    assert Image.open(io.BytesIO(teile[0])).format == "JPEG"


def test_drei_kacheln():
    teile, faktor = pb.kacheln(_seite(3000), _fake(600, 3000))
    assert len(teile) == 3 and faktor == 1.0
    assert all(h <= 1200 for _, h in _masse(teile))


def test_vier_kacheln_skaliert():
    teile, faktor = pb.kacheln(_seite(6000), _fake(600, 6000))
    assert len(teile) == 4
    assert all(b >= 300 and h <= 1200 for b, h in _masse(teile))
    assert faktor == pytest.approx(6000 / 4800)


def test_renderfehler():
    def kaputt(html, hoehe):
        raise OSError("kein Browser")
    with pytest.raises(pb.PruefbildFehler):
        pb.kacheln(_seite(), kaputt)


def test_bildbudget_mit_anhaengen():
    anh = [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,A{i}"}} for i in range(3)]
    teile = pb.bilder_fuer_modell(anh, [b"k1", b"k2", b"k3", b"k4"])
    assert len(teile) == 6 and teile[:3] == anh
    assert teile[3]["image_url"]["url"] == "data:image/jpeg;base64," + base64.b64encode(b"k1").decode()


def test_anhaenge_haben_vorrang():
    anh = [{"type": "image_url", "image_url": {"url": "x"}}] * 6
    assert pb.bilder_fuer_modell(anh, [b"k"]) == anh


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
    s = _seite(700)
    s["ebenen"] = [{"id": "t", "art": "text", "x": 300, "y": 100, "breite": 400, "hoehe": 80,
                    "text": "Hallo Welt", "rolle": "TEXT", "groesse": 16, "farbe": "#111111"}]
    teile, faktor = pb.kacheln(s, pb.browser_render(api=None, aid="1"))
    assert faktor == 1.0 and _masse(teile) == [(600, 700)]


# ---- Gesamtpruefung: Platzhalter und fehlende Medien im Pruefbild ----------------------------------------
import re

from spaces.marketing.workers import export_worker as ew


class MedienApi:
    def __init__(self, medien=None):
        self.medien, self.gefragt = medien or {}, []

    def medium(self, aid, name):
        self.gefragt.append(name)
        return self.medien.get(name)


class _Anfrage:
    def __init__(self, url):
        self.url = url


class _Route:
    def __init__(self, url):
        self.request, self.ergebnis = _Anfrage(url), None

    def fulfill(self, **kw):
        self.ergebnis = ("fulfill", kw)

    def abort(self):
        self.ergebnis = ("abort", {})


class _Locator:
    def __init__(self, png):
        self.first, self._png = self, png

    def screenshot(self, type="png"):
        return self._png


class _Seite:
    """Fake-Chromium: fordert jede BASIS-URL aus dem HTML ueber die Route an."""
    def __init__(self, browser, viewport):
        self.browser, self.viewport = browser, viewport

    def route(self, muster, handler):
        self.handler = handler

    def set_content(self, html, wait_until=None):
        for url in re.findall(re.escape(ew.BASIS) + r"medien/[^\"')&]+", html):
            r = _Route(url)
            self.handler(r)
            self.browser.antworten.append((url, r.ergebnis))

    def evaluate(self, ausdruck):
        pass

    def locator(self, sel):
        return _Locator(_png(self.viewport["width"], self.viewport["height"]))

    def close(self):
        pass


class _Browser:
    def __init__(self):
        self.antworten = []

    def new_page(self, viewport):
        return _Seite(self, viewport)

    def close(self):
        pass


def _seite_mit_bild(quelle, breite=300, hoehe=400):
    s = _seite(900)
    s["ebenen"].append({"id": "neu", "art": "bild", "x": 300, "y": 600, "breite": breite, "hoehe": hoehe,
                        "quelle": quelle})
    return seite.pruefen(s)


def test_platzhalter_ohne_datei_laeuft_als_graue_kachel():
    """Ebene 300 x 400 -> medien:platzhalter-3x4.png, die Datei gibt es nicht: die Runde laeuft trotzdem."""
    api, b = MedienApi(), _Browser()
    render = pb.browser_render(api, "1", browser_starten=lambda: b)
    teile, faktor = pb.kacheln(_seite_mit_bild("medien:platzhalter-3x4.png"), render)
    assert len(teile) == 1 and faktor == 1.0 and render.fehlt == []
    assert api.gefragt == []                                    # nie geholt
    ((url, (art, kw)),) = b.antworten
    assert url.endswith("medien/platzhalter-3x4.png") and art == "fulfill" and kw["status"] == 200
    bild = Image.open(io.BytesIO(kw["body"]))
    assert bild.format == "PNG" and bild.size[0] * 4 == bild.size[1] * 3


def test_fehlendes_medium_wird_hinweis_statt_fehler():
    api, b = MedienApi(), _Browser()
    render = pb.browser_render(api, "1", browser_starten=lambda: b)
    teile, _ = pb.kacheln(_seite_mit_bild("medien:weg.jpg"), render)
    assert len(teile) == 1 and render.fehlt == ["weg.jpg"]
    ((_, (art, kw)),) = b.antworten
    assert art == "fulfill" and kw["status"] == 200 and Image.open(io.BytesIO(kw["body"])).format == "PNG"
    assert pb.fehlt_hinweis("weg.jpg") == "Bild weg.jpg fehlt in den Medien – im Prüfbild als graue Kachel"
    # der naechste Aufruf beginnt ohne die alte Liste
    pb.kacheln(_seite(), render)
    assert render.fehlt == []


def test_platzhalter_mit_beliebigem_verhaeltnis():
    png = pb.kachel_png(16, 9)
    assert Image.open(io.BytesIO(png)).size == (600, 338)
    assert Image.open(io.BytesIO(pb.kachel_png(0, 5))).size[0] >= 1


def test_export_routen_bleiben_streng():
    """Der Export holt auch Platzhalter aus den Medien und meldet ein fehlendes Bild mit 404."""
    api = MedienApi()
    routen = ew.Routen(api, "1")
    r = _Route(ew.BASIS + "medien/platzhalter-3x4.png")
    routen(r)
    assert r.ergebnis == ("fulfill", {"status": 404, "body": b""}) and routen.fehlt == ["platzhalter-3x4.png"]
    assert api.gefragt == ["platzhalter-3x4.png"]

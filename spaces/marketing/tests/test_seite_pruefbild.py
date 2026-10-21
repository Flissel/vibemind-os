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


# ---- Stufe 5 Task 5: Schoenheitspruefung im Pruefbild ---------------------------------------------------
def _dunkel(b, h):
    p = io.BytesIO()
    Image.new("RGB", (b, h), (20, 20, 20)).save(p, "PNG")
    return p.getvalue()


def _fake_mit_massen(masse=None, fehler_ab=None):
    """Fake-Renderer wie browser_render: setzt render.fehlt und render.masse je Aufruf, liefert dunkle Pixel."""
    gesehen = []

    def render(html, hoehe):
        gesehen.append(html)
        if fehler_ab is not None and len(gesehen) >= fehler_ab:
            raise OSError("Chromium weg")
        render.fehlt, render.masse = [], ({} if masse is None else masse)
        return _dunkel(600, hoehe)
    render.gesehen, render.fehlt, render.masse = gesehen, [], None
    return render


def test_pruefbild_liefert_harte_kontrastbefunde():
    """Dunkle Schrift (#111/#333) auf dunklem Untergrund: harte Befunde, zuerst, aus dem Rendering ohne Text."""
    render = _fake_mit_massen()
    teile, faktor, fehlt, saetze = pb.pruefbild_mit(render)(seite.pruefen(seite.leer(None)))
    assert len(teile) == 1 and faktor == 1.0 and fehlt == []
    assert len(render.gesehen) == 2                       # Pruefbild + Rendering ohne Text
    assert "Überschrift" in render.gesehen[0] and "Überschrift" not in render.gesehen[1]
    assert "Mehr erfahren" not in render.gesehen[1]
    assert saetze[0].startswith("titel: Kontrast 1,") and "mindestens 3:1" in saetze[0]
    assert saetze[1].startswith("text: Kontrast ") and "mindestens 4,5:1" in saetze[1]


def test_pruefbild_ohne_pixelbedarf_nur_ein_rendering():
    s = seite.pruefen(seite.leer(None))
    for e in s["ebenen"]:
        if e["art"] == "text":
            e["hintergrund"] = "#ffffff"
    render = _fake_mit_massen()
    *_, saetze = pb.pruefbild_mit(render)(s)
    assert len(render.gesehen) == 1
    assert not any("Kontrast" in x for x in saetze)


def test_pruefbild_hoechstens_8_befunde_harte_zuerst():
    s = seite.pruefen(seite.leer(None))
    s["ebenen"] = [{"id": f"t{i}", "art": "text", "x": 300, "y": 100 + 100 * i, "breite": 400, "hoehe": 60,
                    "text": "x", "rolle": "TEXT", "groesse": 10 + i, "farbe": "#111111"} for i in range(10)]
    *_, saetze = pb.pruefbild_mit(_fake_mit_massen())(seite.pruefen(s))
    assert len(saetze) == pb.MAX_BEFUNDE == 8
    assert all("Kontrast" in x for x in saetze)


def test_zweites_rendering_kaputt_nur_hinweis():
    render = _fake_mit_massen(fehler_ab=2)
    teile, faktor, fehlt, saetze = pb.pruefbild_mit(render)(seite.pruefen(seite.leer(None)))
    assert len(teile) == 1 and saetze == [pb.PRUEFUNG_NICHT_MOEGLICH]
    assert "Prüfung nicht möglich" in pb.PRUEFUNG_NICHT_MOEGLICH


def test_ohne_masse_nur_hinweis():
    render = _fake_mit_massen()

    def ohne(html, hoehe):
        png = render(html, hoehe)
        ohne.fehlt, ohne.masse = [], None             # Messen im Browser gescheitert
        return png
    *_, saetze = pb.pruefbild_mit(ohne)(seite.pruefen(seite.leer(None)))
    assert saetze == [pb.PRUEFUNG_NICHT_MOEGLICH]


def test_platzhalter_ueberdeckt_text_kein_pixelkontrast():
    s = seite.pruefen(seite.leer(None))
    s["ebenen"].insert(0, {"id": "held", "art": "bild", "x": 300, "y": 200, "breite": 600, "hoehe": 400,
                           "quelle": "medien:platzhalter-3x2.png"})
    render = _fake_mit_massen()
    *_, saetze = pb.pruefbild_mit(render)(seite.pruefen(s))
    assert len(render.gesehen) == 1                      # beide Texte liegen auf dem Platzhalter
    assert not any("Kontrast" in x for x in saetze)


class _MessSeite(_Seite):
    def evaluate(self, ausdruck, arg=None):
        if arg is None:
            return None
        assert "data-ebene" in ausdruck and arg == pb.SEITE_CONTAINER
        return {"titel": [40, 60, 560, 180.123], "kaputt": [1, 2]}


class _MessBrowser(_Browser):
    def new_page(self, viewport):
        return _MessSeite(self, viewport)


def test_browser_render_misst_per_dom():
    render = pb.browser_render(MedienApi(), "1", browser_starten=_MessBrowser)
    pb.kacheln(_seite(), render)
    assert render.masse == {"titel": (40, 60, 560, 180.12)}


def test_browser_render_messfehler_ist_kein_pruefbildfehler():
    render = pb.browser_render(MedienApi(), "1", browser_starten=_Browser)   # evaluate ohne Argument
    teile, _ = pb.kacheln(_seite(), render)
    assert len(teile) == 1 and render.masse is None


def test_fehlendes_medium_kein_pixelkontrast_darueber():
    """Gesamtpruefung Stufe 5: ein echtes Medium, das beim Rendern fehlte (render.fehlt, graue Kachel), ist wie
    ein Platzhalter - der Text darueber wird nicht gegen die Kachel gemessen."""
    s = seite.pruefen(seite.leer(None))
    s["ebenen"].insert(0, {"id": "held", "art": "bild", "x": 300, "y": 200, "breite": 600, "hoehe": 400,
                           "quelle": "medien:weg.jpg"})
    s = seite.pruefen(s)
    render = _fake_mit_massen()
    *_, saetze = pb.pruefbild_mit(render)(s)
    assert any("Kontrast" in x for x in saetze)                 # ohne fehlt: dunkle Kachel misst

    def mit_fehlt(html, hoehe):
        png = render(html, hoehe)
        mit_fehlt.fehlt, mit_fehlt.masse = ["weg.jpg"], {}
        return png
    mit_fehlt.fehlt, mit_fehlt.masse = [], None
    render.gesehen.clear()
    teile, faktor, fehlt, saetze = pb.pruefbild_mit(mit_fehlt)(s)
    assert fehlt == ["weg.jpg"]
    assert not any("Kontrast" in x for x in saetze)
    assert len(render.gesehen) == 1                             # kein Rendering ohne Text noetig


class _ZeilenSeite(_Seite):
    def evaluate(self, ausdruck, arg=None):
        if arg is None:
            return None
        return {"huellen": {"titel": [40, 60, 560, 180]},
                "zeilen": {"titel": [[200, 80, 400, 110], [0, 0, 0, 0], "kaputt"], "leer": []}}


class _ZeilenBrowser(_Browser):
    def new_page(self, viewport):
        return _ZeilenSeite(self, viewport)


def test_browser_render_misst_zeilenkaesten():
    render = pb.browser_render(MedienApi(), "1", browser_starten=_ZeilenBrowser)
    pb.kacheln(_seite(), render)
    assert render.masse == {"titel": (40, 60, 560, 180)}
    assert render.zeilen == {"titel": [(200, 80, 400, 110)]}


# ---- Befund 21.10.2026: Pruefbild live gescheitert (Chromium-Start > 30 s unter Speicherdruck) ----------------

def test_pruefbild_versucht_ein_zweites_mal():
    """Ein voruebergehender Renderfehler (z. B. Playwright-Zeitlimit beim Start) kippt die Pruefung nicht sofort."""
    aufrufe = []

    def render(html, hoehe):
        aufrufe.append(hoehe)
        if len(aufrufe) == 1:
            raise TimeoutError("Timeout 30000ms exceeded.")
        return _png(600, hoehe)
    teile, faktor, fehlt, _ = pb.pruefbild_mit(render)(_seite(900))
    assert len(teile) == 1 and faktor == 1.0 and fehlt == [] and aufrufe[:2] == [900, 900]


def test_pruefbild_zweimal_kaputt_bleibt_fehler():
    aufrufe = []

    def render(html, hoehe):
        aufrufe.append(hoehe)
        raise TimeoutError("Timeout 30000ms exceeded.")
    with pytest.raises(pb.PruefbildFehler):
        pb.pruefbild_mit(render)(_seite(900))
    assert len(aufrufe) == pb.PRUEF_VERSUCHE == 2


def test_browser_render_startet_chromium_mit_langem_zeitlimit(monkeypatch):
    gestartet = []

    class Starter(_Browser):
        def __init__(self, start_timeout_ms=30000):
            super().__init__()
            gestartet.append(start_timeout_ms)
    monkeypatch.setattr(pb, "EchterBrowser", Starter)
    render = pb.browser_render(MedienApi(), "1")
    pb.kacheln(_seite(900), render)
    assert gestartet == [pb.PRUEF_START_MS] and pb.PRUEF_START_MS >= 120000


class _ZeitSeite(_Seite):
    def set_default_timeout(self, ms):
        self.browser.zeitlimit = ms


class _ZeitBrowser(_Browser):
    zeitlimit = None

    def new_page(self, viewport):
        return _ZeitSeite(self, viewport)


def test_browser_render_setzt_langes_seitenzeitlimit():
    b = _ZeitBrowser()
    pb.kacheln(_seite(900), pb.browser_render(MedienApi(), "1", browser_starten=lambda: b))
    assert b.zeitlimit == pb.PRUEF_SCHRITT_MS and pb.PRUEF_SCHRITT_MS >= 90000

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


def test_qualitaet_sinkt_vor_der_grenze(monkeypatch):
    st = [Stueck(1, 0, 400, "a", None)]
    p = png(1200, 800, rauschen=True)
    benutzt = []
    echt = sb._kodieren
    monkeypatch.setattr(sb, "_kodieren", lambda b, puf, q: (benutzt.append(q), echt(b, puf, q))[1])
    q82 = len(sb.zuschneiden(p, st)[0])
    assert benutzt == [82]
    benutzt.clear()
    teile = sb.zuschneiden(p, st, max_bytes=int(q82 * 0.9))
    assert len(teile[0]) <= int(q82 * 0.9)
    assert benutzt[0] == 82 and benutzt[-1] < 82 and benutzt == sorted(benutzt, reverse=True)
    # erste passende Stufe: die davor war zu gross
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

    def set_default_timeout(self, ms):
        self.log.append(("timeout", ms, None))

    def set_content(self, html, wait_until=None, timeout=None):
        self.log.append(("set_content", timeout, None))
        for url in re.findall(r"https://export\.vibemind\.invalid/[^\"')\s]+", html):
            self.handler(_Route(url, self.log))

    def locator(self, sel):
        seite_ = self

        class L:
            first = type("E", (), {"screenshot": staticmethod(
                lambda type="png", timeout=None: (seite_.log.append(("screenshot", None, None)), png(1200, 1600))[1])})()
        seite_.sel = sel
        return L

    def evaluate(self, skript, arg=None):
        self.log.append(("evaluate", arg, None))
        return {"b": [0, 0, 600, 400.5], "kaputt": [1, 2], "nan": [0, float("nan"), 1, 1]}

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


def test_render_und_messen_liefert_geprueft_masse():
    b = _Browser()
    out, m = sb.render_und_messen(seite([bild("b", "medien:foto.jpg")]), {"foto.jpg": b"x"}.get,
                                  browser_starten=lambda: b)
    assert Image.open(io.BytesIO(out)).size == (1200, 1600)
    assert m == {"b": (0, 0, 600, 400.5)}                      # unbrauchbare Eintraege fallen weg
    assert ("evaluate", "body > div > div", None) in b.log
    # erst messen (wartet auf die Schriften), dann fotografieren
    assert b.log.index(("evaluate", "body > div > div", None)) < b.log.index(("screenshot", None, None))
    assert sb.render_png(seite([bild("b", "medien:foto.jpg")]), {"foto.jpg": b"x"}.get,
                         browser_starten=_Browser) == out


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


def test_platzhalter_kacheln_nur_auf_wunsch():
    """Schoenheitspruefung (Fix-Runde 1): platzhalter_kacheln=True zeichnet Platzhalter als Kachel (wie das
    Pruefbild), ohne die Medien zu fragen; ein fehlendes echtes Medium blockiert weiter."""
    gefragt = []

    def lesen(n):
        gefragt.append(n)
        return b"x" if n == "foto.jpg" else None
    b = _Browser()
    out, _ = sb.render_und_messen(seite([bild("p", "medien:platzhalter-3x2.png"), bild("f", "medien:foto.jpg", 600)]),
                                  lesen, browser_starten=lambda: b, platzhalter_kacheln=True)
    assert out and gefragt == ["foto.jpg"]
    assert ("fulfill", "https://export.vibemind.invalid/medien/platzhalter-3x2.png", 200) in b.log
    with pytest.raises(sb.StueckFehler, match="^Bild weg.jpg fehlt$"):
        sb.render_und_messen(seite([bild("w", "medien:weg.jpg")]), lesen, browser_starten=_Browser,
                             platzhalter_kacheln=True)


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


def test_unzulaessiger_name_wird_nicht_gelesen(monkeypatch):
    """Das Seitenmodell lehnt solche Namen schon ab; die Sperre im Abruf gilt zusaetzlich (Tiefenverteidigung)."""
    gefragt = []
    for name in ("..%2F..%2Fx", ".versteckt.jpg", "a%5Cb.jpg"):
        monkeypatch.setattr(sb.seite_html, "html", lambda *a, _n=name, **k:
                            f'<img src="https://export.vibemind.invalid/medien/{_n}">')
        with pytest.raises(sb.StueckFehler, match="^Bild .* fehlt$"):
            sb.render_png(seite([]), lambda n: gefragt.append(n), browser_starten=_Browser)
    assert gefragt == []


def test_zeitlimits_gesetzt():
    b = _Browser()
    sb.render_png(seite([bild("b", "medien:foto.jpg")]), {"foto.jpg": b"x"}.get, browser_starten=lambda: b)
    assert ("timeout", 60000, None) in b.log and ("set_content", 60000, None) in b.log


def test_zeitlimit_wird_deutsche_meldung():
    class TimeoutError(Exception):
        pass

    class B(_Browser):
        def new_page(self, **kw):
            s = super().new_page(**kw)
            s.set_content = lambda *a, **k: (_ for _ in ()).throw(TimeoutError("x"))
            return s
    with pytest.raises(sb.StueckFehler, match=r"^Bildstücke konnten nicht gerendert werden \(Zeitlimit\)$"):
        sb.render_png(seite([bild("b", "medien:foto.jpg")]), {"foto.jpg": b"x"}.get, browser_starten=B)


def test_kein_gleichzeitiges_rendern():
    import threading
    import time
    aktiv, spitze, lock = [0], [0], threading.Lock()

    class B(_Browser):
        def new_page(self, **kw):
            with lock:
                aktiv[0] += 1
                spitze[0] = max(spitze[0], aktiv[0])
            time.sleep(0.05)
            return super().new_page(**kw)

        def close(self):
            with lock:
                aktiv[0] -= 1
    ts = [threading.Thread(target=sb.render_png, args=(seite([bild("b", "medien:foto.jpg")]), {"foto.jpg": b"x"}.get),
                           kwargs={"browser_starten": B}) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert spitze[0] == 1


@pytest.mark.browser
def test_echter_browser_gedrehter_titel_ganz_im_stueck():
    """Live-Fall: gedrehter zweizeiliger ANZEIGE-Titel ueber einem Bild waechst ueber seine deklarierte Hoehe
    hinaus. Gemessen reicht das Bildstueck bis unter die letzte Zeile ("Herbst")."""
    if not _chromium_da():
        pytest.skip("Chromium fehlt")
    from spaces.marketing.claw import seite_mail, seite_streifen
    s = seite([{"id": "b", "art": "bild", "x": 300, "y": 200, "breite": 600, "hoehe": 400,
                "quelle": "medien:foto.png"},
               {"id": "t", "art": "text", "x": 330, "y": 340, "breite": 460, "hoehe": 110,
                "text": "Neue Kurse im Herbst", "rolle": "ANZEIGE", "groesse": 46, "gewicht": 600,
                "drehung": -4, "farbe": "#ffffff"}])
    s["schriften"]["anzeige"] = "montserrat"
    s["hintergrund"]["farbe"] = "#202020"     # weisse Schrift auch unterhalb des Bildes erkennbar
    p = io.BytesIO()
    Image.new("RGB", (600, 400), (40, 90, 160)).save(p, "PNG")
    out, m = sb.render_und_messen(s, {"foto.png": p.getvalue()}.get)
    deklariert_unten = seite_streifen.rahmen(seite_streifen.seite_mod.pruefen(s)["ebenen"][1])[3]
    assert m["t"][3] > deklariert_unten + 1          # der Titel ist gerendert hoeher als deklariert
    stuecke = seite_mail.stuecke(s, m)
    assert stuecke[0].oben == 0 and stuecke[0].unten >= m["t"][3]
    # Unterkante der weissen Schrift im Foto liegt innerhalb des Stuecks
    bild_ = Image.open(io.BytesIO(out)).convert("RGB")
    px = bild_.load()
    tinte = [y for y in range(bild_.size[1]) for x in range(0, bild_.size[0], 2) if min(px[x, y]) >= 240]
    assert tinte and max(tinte) / sb.FAKTOR <= stuecke[0].unten
    teile = sb.zuschneiden(out, stuecke)
    assert masse(teile)[0] == (1200, round(stuecke[0].unten * sb.FAKTOR))

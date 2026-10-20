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

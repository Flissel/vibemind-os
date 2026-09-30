import io

import numpy as np
from PIL import Image

from spaces.marketing.claw import bild_messen as bm


def jpeg(farbe):
    b = io.BytesIO()
    Image.new("RGB", (64, 32), farbe).save(b, "JPEG")
    return b.getvalue()


class Bilder:
    def embed(self, bilder):
        for b in bilder:                                   # PIL-Bilder
            r, g, _ = b.getpixel((0, 0))
            yield np.array([r, g, 1.0], dtype=np.float32)


class Texte:
    def embed(self, texte):
        for _ in texte:
            yield np.array([0.0, 255.0, 1.0], dtype=np.float32)   # "gruen"


def test_messen_ahnlichkeit_und_richtung(monkeypatch):
    monkeypatch.setattr(bm, "_modelle", lambda: (Bilder(), Texte()))
    m = bm.messen(jpeg((255, 0, 0)), jpeg((200, 60, 0)), "mehr gruen")
    assert 0.9 < m["aehnlich_original"] < 1.0
    assert m["naeher_am_hinweis"] > 0                     # neues Bild naeher an "gruen"


def test_ohne_hinweis_keine_richtung(monkeypatch):
    monkeypatch.setattr(bm, "_modelle", lambda: (Bilder(), Texte()))
    m = bm.messen(jpeg((255, 0, 0)), jpeg((255, 0, 0)), "  ")
    assert m["aehnlich_original"] == 1.0 and m["naeher_am_hinweis"] is None


def test_ohne_altes_bild_nur_leer(monkeypatch):
    monkeypatch.setattr(bm, "_modelle", lambda: (Bilder(), Texte()))
    assert bm.messen(None, jpeg((1, 2, 3)), "x") == {"aehnlich_original": None, "naeher_am_hinweis": None}


def test_fehler_ergibt_leeres_dict(monkeypatch):
    monkeypatch.setattr(bm, "_modelle", lambda: (_ for _ in ()).throw(ImportError("fastembed fehlt")))
    assert bm.messen(jpeg((1, 2, 3)), jpeg((1, 2, 3)), "x") == {}
    assert bm.messen(b"kein bild", jpeg((1, 2, 3)), "x") == {}

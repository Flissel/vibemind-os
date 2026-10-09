"""Logo bearbeiten (Spec sales-claw 2026-10-09-marke-exakt-logo-wissen §1): reine Bildrechnung."""
import io

import numpy as np
import pytest
from PIL import Image, ImageDraw

from spaces.marketing.claw import logo_bearbeiten as lb
from spaces.marketing.claw.schoenheit import kontrast


def _bild(groesse, grund, rechtecke, modus="RGB"):
    b = Image.new(modus, groesse, grund)
    zeichnen = ImageDraw.Draw(b)
    for box, farbe in rechtecke:
        zeichnen.rectangle(box, fill=farbe)
    return b


def _png(b) -> bytes:
    puffer = io.BytesIO()
    b.save(puffer, "PNG")
    return puffer.getvalue()


def _rgba(roh) -> Image.Image:
    return Image.open(io.BytesIO(roh)).convert("RGBA")


def _alpha(b):
    return np.asarray(b)[..., 3]


# --- Farbschluessel ---------------------------------------------------------------------

def test_weisses_zeichen_auf_schwarz():
    frei = lb.farbe_freistellen(lb.oeffnen(_png(_bild((100, 80), "#000000", [((30, 20, 69, 59), "#ffffff")]))))
    a = _alpha(frei)
    assert a[0, 0] == 0 and a[40, 50] == 255


def test_dunkles_zeichen_auf_weiss():
    frei = lb.farbe_freistellen(lb.oeffnen(_png(_bild((100, 80), "#ffffff", [((30, 20, 69, 59), "#1a1a1a")]))))
    a = _alpha(frei)
    assert a[0, 0] == 0 and a[40, 50] == 255


def test_mehrfarbig_auf_weiss_bleibt_deckend():
    b = _bild((120, 80), "#ffffff", [((10, 10, 49, 69), "#c81e1e"), ((60, 10, 109, 69), "#1e3a8a")])
    a = _alpha(lb.farbe_freistellen(lb.oeffnen(_png(b))))
    assert a[0, 0] == 0 and a[40, 30] == 255 and a[40, 80] == 255


def test_weiche_kante_im_uebergangsbereich():
    b = _bild((100, 80), "#ffffff", [((30, 20, 69, 59), "#dcdcdc")])     # Abstand ~60 zur Randfarbe
    a = _alpha(lb.farbe_freistellen(lb.oeffnen(_png(b))))
    assert 100 < int(a[40, 50]) < 160


def test_randfarbe_ist_das_randmittel():
    assert lb.randfarbe(lb.oeffnen(_png(_bild((100, 80), "#f0f0f0", [((30, 20, 69, 59), "#000000")])))) == (240, 240, 240)


def test_bereits_transparentes_logo_bleibt_erhalten():
    """Review Focus 1: schon freigestelltes PNG + freistellen 'farbe' -> Zeichen bleibt, nur Zuschnitt."""
    b = _bild((200, 200), (0, 0, 0, 0), [((50, 75, 149, 124), (20, 20, 20, 255))], modus="RGBA")
    assert lb.randfarbe(lb.oeffnen(_png(b))) is None
    f = lb.fassungen(_png(b), freistellen="farbe", zuschneiden=True, textfarbe="#2b2724")
    hell = _rgba(f.hell)
    assert hell.size == (108, 58)
    assert hell.getpixel((54, 29)) == (0x2b, 0x27, 0x24, 255) and hell.getpixel((0, 0))[3] == 0


# --- Zuschnitt --------------------------------------------------------------------------

def test_zuschnitt_mit_vier_prozent_rand_ueber_alpha():
    b = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    b.paste(Image.new("RGBA", (100, 50), (0, 0, 0, 255)), (50, 75))
    z = lb.zuschnitt(b, frei=True)
    assert z.size == (108, 58) and z.getpixel((0, 0))[3] == 0 and z.getpixel((4, 4))[3] == 255


def test_zuschnitt_ohne_freistellen_ueber_den_hintergrundabstand():
    b = lb.oeffnen(_png(_bild((200, 200), "#ffffff", [((50, 75, 149, 124), "#000000")])))
    z = lb.zuschnitt(b, frei=False)
    assert z.size == (108, 58) and z.getpixel((0, 0)) == (255, 255, 255, 255)


def test_zuschnitt_ohne_inhalt_ist_fehler():
    with pytest.raises(lb.LogoFehler, match="Kein Zeichen"):
        lb.zuschnitt(Image.new("RGBA", (50, 50), (0, 0, 0, 0)), frei=True)


# --- Einfarbig --------------------------------------------------------------------------

def _anteile(rot_anteil):
    b = Image.new("RGBA", (100, 10), (0, 0, 0, 0))
    rot = int(100 * rot_anteil)
    b.paste(Image.new("RGBA", (rot, 10), (200, 30, 30, 255)), (0, 0))
    if rot < 100:
        b.paste(Image.new("RGBA", (100 - rot, 10), (30, 30, 200, 255)), (rot, 0))
    return b


@pytest.mark.parametrize("anteil,erwartet", [(1.0, True), (0.92, True), (0.85, False), (0.5, False)])
def test_einfarbig_ab_neunzig_prozent(anteil, erwartet):
    assert lb.einfarbig(_anteile(anteil)) is erwartet


def test_einfarbig_mit_toleranz():
    b = Image.new("RGBA", (100, 10), (0, 0, 0, 0))
    b.paste(Image.new("RGBA", (50, 10), (20, 20, 20, 255)), (0, 0))
    b.paste(Image.new("RGBA", (50, 10), (45, 40, 38, 255)), (50, 0))      # fast dieselbe Farbe
    assert lb.einfarbig(b) is True


# --- Fassungen --------------------------------------------------------------------------

def test_fassungen_einfarbig_hell_in_textfarbe_dunkel_weiss():
    roh = _png(_bild((200, 120), "#ffffff", [((60, 30, 139, 89), "#111111")]))
    f = lb.fassungen(roh, freistellen="farbe", zuschneiden=True, textfarbe="#2b2724")
    hell, dunkel = _rgba(f.hell), _rgba(f.dunkel)
    assert f.einfarbig and hell.size == (86, 66) and dunkel.size == (86, 66)
    assert hell.getpixel((43, 33)) == (0x2b, 0x27, 0x24, 255) and hell.getpixel((0, 0))[3] == 0
    assert dunkel.getpixel((43, 33)) == (255, 255, 255, 255)
    assert lb.logo_kontrast(dunkel, lb.DUNKEL_GRUND) >= lb.KONTRAST_DUNKEL
    assert lb.logo_kontrast(hell, "#ffffff") >= 4.5


def test_fassungen_mehrfarbig_zu_dunkel_wird_weisse_silhouette():
    roh = _png(_bild((120, 80), "#ffffff", [((10, 10, 49, 69), "#7f1d1d"), ((60, 10, 109, 69), "#1e3a8a")]))
    f = lb.fassungen(roh, freistellen="farbe", zuschneiden=False, textfarbe="#2b2724")
    hell, dunkel = _rgba(f.hell), _rgba(f.dunkel)
    assert not f.einfarbig
    assert hell.getpixel((30, 40))[:3] == (0x7f, 0x1d, 0x1d)               # hell bleibt original
    assert dunkel.getpixel((30, 40)) == (255, 255, 255, 255) and dunkel.getpixel((80, 40)) == (255, 255, 255, 255)
    assert any("Silhouette" in h for h in f.hinweise)


def test_fassungen_mehrfarbig_hell_genug_bleibt_original_auf_dunkel():
    roh = _png(_bild((120, 80), "#ffffff", [((10, 10, 49, 69), "#ffd400"), ((60, 10, 109, 69), "#22d3ee")]))
    f = lb.fassungen(roh, freistellen="farbe", zuschneiden=False, textfarbe="#2b2724")
    dunkel = _rgba(f.dunkel)
    assert dunkel.getpixel((30, 40))[:3] == (0xff, 0xd4, 0x00)
    assert lb.logo_kontrast(dunkel, lb.DUNKEL_GRUND) >= 3.0


def test_nein_mit_flaeche_bleibt_wie_es_ist():
    roh = _png(_bild((120, 80), "#336699", [((10, 10, 49, 69), "#ffffff")]))
    f = lb.fassungen(roh, freistellen="nein", zuschneiden=False, textfarbe="#2b2724")
    assert f.hell == f.dunkel and _rgba(f.hell).getpixel((0, 0)) == (0x33, 0x66, 0x99, 255)
    assert any("Fläche" in h for h in f.hinweise)


def test_ki_ergebnis_wird_genutzt():
    frei = _png(_bild((100, 80), (0, 0, 0, 0), [((30, 20, 69, 59), (10, 10, 10, 255))], modus="RGBA"))
    gesehen = []
    f = lb.fassungen(_png(_bild((100, 80), "#88aa66", [((30, 20, 69, 59), "#0a0a0a")])), freistellen="ki",
                     zuschneiden=True, textfarbe="#2b2724", ki=lambda png: gesehen.append(png) or frei)
    assert gesehen and gesehen[0].startswith(b"\x89PNG") and f.einfarbig


@pytest.mark.parametrize("ki", [None, lambda png: (_ for _ in ()).throw(OSError("ComfyUI weg")),
                                lambda png: b"kein bild"])
def test_ki_fehler_ist_logofehler(ki):
    with pytest.raises(lb.LogoFehler):
        lb.fassungen(_png(_bild((60, 40), "#ffffff", [((10, 10, 49, 29), "#000000")])), freistellen="ki",
                     zuschneiden=True, textfarbe="#2b2724", ki=ki)


def test_kein_zeichen_erkannt():
    with pytest.raises(lb.LogoFehler, match="Kein Zeichen"):
        lb.fassungen(_png(Image.new("RGB", (80, 80), "#ffffff")), freistellen="farbe", zuschneiden=True,
                     textfarbe="#2b2724")


@pytest.mark.parametrize("roh", [b"kein bild", b""])
def test_kaputtes_bild(roh):
    with pytest.raises(lb.LogoFehler):
        lb.fassungen(roh, freistellen="farbe", zuschneiden=True, textfarbe="#2b2724")


def test_unbekannte_freistell_art():
    with pytest.raises(lb.LogoFehler):
        lb.fassungen(_png(Image.new("RGB", (8, 8))), freistellen="magie", zuschneiden=False, textfarbe="#000000")


def test_grosses_logo_bleibt_unter_der_grenze():
    rng = np.random.default_rng(1)
    rauschen = Image.fromarray(rng.integers(0, 255, (1300, 1300, 3), dtype=np.uint8), "RGB")
    f = lb.fassungen(_png(rauschen), freistellen="nein", zuschneiden=False, textfarbe="#000000")
    assert len(f.hell) <= lb.PNG_MAX and max(_rgba(f.hell).size) <= lb.MAX_KANTE


def test_kontrast_rechnung_passt_zu_schoenheit():
    b = Image.new("RGBA", (10, 10), (255, 255, 255, 255))
    assert abs(lb.logo_kontrast(b, "#1a1a1a") - kontrast("#ffffff", "#1a1a1a")) < 0.01

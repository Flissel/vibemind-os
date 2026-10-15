"""Freiere Flaeche (Paket 3, 15.10.): Form-Ebene, Texthintergrund, Bildrahmen/Rundung/Kreis/Schatten,
Deckkraft, Handschrift. Neue Felder sind optional - alte Flaechen behalten Hash und Aussehen."""
import copy
import os

import pytest
from PIL import Image

from spaces.marketing.claw import gestaltung as gs


def leer(ebenen, hg="#FFFFFF", fmt="quer"):
    return {"version": 1, "format": fmt, "hintergrund": hg, "ebenen": ebenen}


def form(**k):
    e = {"id": "f", "art": "form", "form": "rechteck", "x": 300, "y": 200, "breite": 200, "hoehe": 100,
         "farbe": "#C8553D", "drehung": 0}
    e.update(k)
    return e


def bild(ordner, name="rot.png", farbe=(255, 0, 0), groesse=(200, 100)):
    Image.new("RGB", groesse, farbe).save(os.path.join(ordner, name))
    return {"id": "b", "art": "bild", "quelle": f"medien:{name}", "x": 300, "y": 200, "breite": 200, "drehung": 0}


def text(**k):
    e = {"id": "t", "art": "text", "text": "Neu!", "schrift": "dm-sans", "gewicht": 700, "kursiv": False,
         "groesse": 48, "farbe": "#FFFFFF", "ausrichtung": "mitte", "zeilenabstand": 1.2, "x": 300, "y": 200, "drehung": 0}
    e.update(k)
    return e


def nah(a, b, tol=6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# ---- pruefen -------------------------------------------------------------------------------------

def test_alte_flaeche_bleibt_gleich():
    alt = leer([{"id": "b", "art": "bild", "quelle": "medien:a.jpg", "x": 1, "y": 2, "breite": 50, "drehung": 0}])
    n = gs.pruefen(alt)
    assert set(n["ebenen"][0]) == {"id", "x", "y", "drehung", "art", "quelle", "breite"}


def test_neue_felder_werden_uebernommen():
    g = leer([form(rundung=24, deckkraft=80), form(id="k", form="kreis", hoehe=200),
              {"id": "b", "art": "bild", "quelle": "medien:a.jpg", "x": 1, "y": 2, "breite": 50, "drehung": 0,
               "rahmen": 12, "rundung": 8, "kreis": True, "schatten": True, "deckkraft": 90},
              text(hintergrund="#c8553d", schrift="caveat", gewicht=700)])
    f, k, b, t = gs.pruefen(g)["ebenen"]
    assert (f["form"], f["rundung"], f["deckkraft"], f["farbe"]) == ("rechteck", 24, 80, "#C8553D")
    assert k["form"] == "kreis" and "rundung" not in k
    assert (b["rahmen"], b["rundung"], b["kreis"], b["schatten"], b["deckkraft"]) == (12, 8, True, True, 90)
    assert t["hintergrund"] == "#C8553D" and t["schrift"] == "caveat"


def test_standardwerte_fallen_weg():
    g = leer([form(deckkraft=100, rundung=0), text(hintergrund=None)])
    f, t = gs.pruefen(g)["ebenen"]
    assert "deckkraft" not in f and "rundung" not in f and "hintergrund" not in t


@pytest.mark.parametrize("aendern, grund", [
    (lambda e: e.update(form="stern"), "Form"),
    (lambda e: e.update(hoehe=4), "Höhe"),
    (lambda e: e.update(farbe="rot"), "Farbe"),
    (lambda e: e.update(deckkraft=120), "Deckkraft"),
    (lambda e: e.update(rundung=-1), "Rundung"),
])
def test_form_fehler(aendern, grund):
    e = form()
    aendern(e)
    with pytest.raises(gs.GestaltungFehler, match=grund):
        gs.pruefen(leer([e]))


@pytest.mark.parametrize("feld, wert, grund", [
    ("rahmen", 81, "Rahmen"), ("rundung", 301, "Rundung"), ("kreis", "ja", "Kreis"), ("schatten", 1, "Schatten"),
])
def test_bild_fehler(feld, wert, grund):
    e = {"id": "b", "art": "bild", "quelle": "medien:a.jpg", "x": 1, "y": 2, "breite": 50, "drehung": 0, feld: wert}
    with pytest.raises(gs.GestaltungFehler, match=grund):
        gs.pruefen(leer([e]))


def test_text_hintergrund_fehler():
    with pytest.raises(gs.GestaltungFehler, match="Farbe"):
        gs.pruefen(leer([text(hintergrund="blau")]))


def test_handschrift_im_register():
    assert gs.schrift_datei("caveat", 400, False).endswith("caveat-400-normal.woff2")
    assert os.path.isfile(gs.schrift_datei("caveat", 700, False))


def test_umformatieren_skaliert_form():
    g = leer([form(breite=200, hoehe=100)], fmt="quer")
    neu = gs.umformatieren(g, "banner")
    f = neu["ebenen"][0]
    assert f["breite"] == 100 and f["hoehe"] == 50


# ---- rechnen -------------------------------------------------------------------------------------

def test_rechteck_und_deckkraft(tmp_path):
    im, _ = gs.bild_rechnen(leer([form()]), [str(tmp_path)])
    assert nah(im.getpixel((600, 400)), (0xC8, 0x55, 0x3D)) and im.getpixel((350, 400)) == (255, 255, 255)
    im, _ = gs.bild_rechnen(leer([form(deckkraft=50, farbe="#000000")]), [str(tmp_path)])
    assert nah(im.getpixel((600, 400)), (128, 128, 128))


def test_kreis_form_hat_leere_ecken(tmp_path):
    im, _ = gs.bild_rechnen(leer([form(form="kreis", breite=200, hoehe=200)]), [str(tmp_path)])
    assert nah(im.getpixel((600, 400)), (0xC8, 0x55, 0x3D))
    assert im.getpixel((600 - 195, 400 - 195)) == (255, 255, 255)     # Ecke des 400er-Quadrats bleibt frei


def test_abgerundetes_rechteck(tmp_path):
    im, _ = gs.bild_rechnen(leer([form(rundung=40)]), [str(tmp_path)])
    assert im.getpixel((402, 302)) == (255, 255, 255) and nah(im.getpixel((600, 302)), (0xC8, 0x55, 0x3D))


def test_bild_mit_rahmen_und_kreis(tmp_path):
    e = bild(tmp_path, groesse=(200, 200))
    im, _ = gs.bild_rechnen(leer([dict(e, rahmen=20)], hg="#000000"), [str(tmp_path)])
    # 200 breit + 2x20 Rahmen = 240 -> 480 px, Rand weiss, Mitte rot
    assert nah(im.getpixel((600 - 230, 400)), (255, 255, 255)) and im.getpixel((600, 400))[0] > 240
    im, _ = gs.bild_rechnen(leer([dict(e, kreis=True)], hg="#000000"), [str(tmp_path)])
    assert im.getpixel((600 - 195, 400 - 195)) == (0, 0, 0) and im.getpixel((600, 400))[0] > 240


def test_bild_kreis_schneidet_mitte_quadratisch(tmp_path):
    e = bild(tmp_path, groesse=(400, 200))
    im, _ = gs.bild_rechnen(leer([dict(e, kreis=True)], hg="#000000"), [str(tmp_path)])
    # Breite 200 -> Kreis 200x200 (400 px), nicht 200x100
    assert im.getpixel((600, 400 - 180))[0] > 200


def test_schatten_dunkelt_umgebung(tmp_path):
    e = bild(tmp_path, farbe=(255, 255, 255))
    ohne, _ = gs.bild_rechnen(leer([e], hg="#FFFFFF"), [str(tmp_path)])
    mit, _ = gs.bild_rechnen(leer([dict(e, schatten=True)], hg="#FFFFFF"), [str(tmp_path)])
    assert ohne.getpixel((600, 400 + 110)) == (255, 255, 255) and mit.getpixel((600, 400 + 110))[0] < 250


def test_text_hintergrund_und_kontrast(tmp_path):
    im, hinweise = gs.bild_rechnen(leer([text(hintergrund="#C8553D")], hg="#FFFFFF"), [str(tmp_path)])
    assert not any("Kontrast" in h for h in hinweise)  # weiss auf Terrakotta, nicht weiss auf weiss
    # Band reicht ueber den Text hinaus: Pixel links neben dem ersten Buchstaben ist Terrakotta
    zeile = [im.getpixel((x, 400)) for x in range(300, 900)]
    assert any(nah(p, (0xC8, 0x55, 0x3D)) for p in zeile)
    _, hinweise = gs.bild_rechnen(leer([text()], hg="#FFFFFF"), [str(tmp_path)])
    assert any("Kontrast" in h for h in hinweise)


def test_handschrift_rechnet(tmp_path):
    im, _ = gs.bild_rechnen(leer([text(schrift="caveat", gewicht=400, farbe="#000000", text="Deine Lena")]),
                            [str(tmp_path)])
    assert min(im.convert("L").getdata()) < 60


def test_alter_hash_unveraendert():
    # vor Paket 3 gemessen: alte Flaechen behalten ihren Dateinamen (kein Neurechnen, keine verwaisten Bilder)
    alt = leer([{"id": "b", "art": "bild", "quelle": "medien:a.jpg", "x": 1, "y": 2, "breite": 50, "drehung": 0},
                text()])
    assert gs.schluessel(copy.deepcopy(alt)) == "e3820dfccc55"

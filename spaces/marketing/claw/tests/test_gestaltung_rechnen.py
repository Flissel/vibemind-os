import io, os, time
import pytest
from PIL import Image
from spaces.marketing.claw import gestaltung as gs

def leer(fmt="quer", hg="#FFFFFF", ebenen=None):
    return {"version": 1, "format": fmt, "hintergrund": hg, "ebenen": ebenen or []}

def bild(ordner, name, farbe, groesse=(200, 100), modus="RGB"):
    Image.new(modus, groesse, farbe).save(os.path.join(ordner, name))

def text(**k):
    e = {"id": "t", "art": "text", "text": "Hallo", "schrift": "dm-sans", "gewicht": 700, "kursiv": False,
         "groesse": 48, "farbe": "#000000", "ausrichtung": "mitte", "zeilenabstand": 1.2, "x": 300, "y": 200, "drehung": 0}
    e.update(k); return e

def test_hintergrund_und_masse(tmp_path):
    r = gs.rechnen(leer(hg="#336699"), [str(tmp_path)], str(tmp_path))
    assert r["url"] == f"medien:{r['name']}" and r["name"].startswith("gs-") and (r["width"], r["height"]) == (600, 400)
    im = Image.open(tmp_path / r["name"])
    assert im.format == "JPEG" and im.size == (1200, 800)
    assert all(abs(a - b) <= 3 for a, b in zip(im.getpixel((600, 400)), (0x33, 0x66, 0x99)))

def test_bild_ebene_mittig(tmp_path):
    bild(tmp_path, "rot.png", (255, 0, 0))
    g = leer(ebenen=[{"id": "b", "art": "bild", "quelle": "medien:rot.png", "x": 300, "y": 200, "breite": 200, "drehung": 0}])
    im, _ = gs.bild_rechnen(g, [str(tmp_path)])
    assert im.getpixel((600, 400))[0] > 240            # Mitte rot
    assert im.getpixel((350, 400)) == (255, 255, 255)  # links ausserhalb (200 breit -> 400 px, 400..800)

def test_alpha_bleibt_durchsichtig(tmp_path):
    p = Image.new("RGBA", (100, 100), (0, 0, 0, 0)); p.paste((0, 0, 255, 255), (40, 40, 60, 60))
    p.save(tmp_path / "frei.png")
    g = leer(hg="#00FF00", ebenen=[{"id": "b", "art": "bild", "quelle": "medien:frei.png", "x": 300, "y": 200, "breite": 100, "drehung": 0}])
    im, _ = gs.bild_rechnen(g, [str(tmp_path)])
    assert im.getpixel((560, 360))[1] > 240 and im.getpixel((600, 400))[2] > 240

def test_drehung_aendert_pixel(tmp_path):
    bild(tmp_path, "s.png", (0, 0, 0), (400, 20))
    g0 = leer(ebenen=[{"id": "b", "art": "bild", "quelle": "medien:s.png", "x": 300, "y": 200, "breite": 400, "drehung": 0}])
    g90 = leer(ebenen=[{"id": "b", "art": "bild", "quelle": "medien:s.png", "x": 300, "y": 200, "breite": 400, "drehung": 90}])
    a, _ = gs.bild_rechnen(g0, [str(tmp_path)]); b, _ = gs.bild_rechnen(g90, [str(tmp_path)])
    assert a.getpixel((300, 400))[0] < 30 and b.getpixel((300, 400))[0] > 220
    assert b.getpixel((600, 150))[0] < 30

def test_text_wird_gezeichnet(tmp_path):
    im, _ = gs.bild_rechnen(leer(ebenen=[text()]), [str(tmp_path)])
    box = im.crop((400, 300, 800, 500)).convert("L")
    assert min(box.getdata()) < 60

def test_emoji_stuerzt_nicht_ab(tmp_path):
    gs.bild_rechnen(leer(ebenen=[text(text="Hallo 🎉 Ω")]), [str(tmp_path)])

def test_quelle_fehlt(tmp_path):
    g = leer(ebenen=[{"id": "b", "art": "bild", "quelle": "medien:weg.png", "x": 1, "y": 1, "breite": 10, "drehung": 0}])
    with pytest.raises(gs.GestaltungFehler, match="weg.png fehlt in den Medien"):
        gs.bild_rechnen(g, [str(tmp_path)])

def test_bombe(tmp_path):
    Image.new("L", (9000, 9000), 0).save(tmp_path / "riesig.png")
    g = leer(ebenen=[{"id": "b", "art": "bild", "quelle": "medien:riesig.png", "x": 1, "y": 1, "breite": 10, "drehung": 0}])
    with pytest.raises(gs.GestaltungFehler, match="zu groß"):
        gs.bild_rechnen(g, [str(tmp_path)])

def test_hash_stabil_und_idempotent(tmp_path):
    g = leer(ebenen=[text()])
    assert gs.schluessel(g) == gs.schluessel(dict(reversed(list(g.items()))))
    a = gs.rechnen(g, [str(tmp_path)], str(tmp_path)); t = os.path.getmtime(tmp_path / a["name"])
    time.sleep(0.05); b = gs.rechnen(g, [str(tmp_path)], str(tmp_path))
    assert a["name"] == b["name"] and os.path.getmtime(tmp_path / a["name"]) == t
    assert gs.schluessel(leer(ebenen=[text(text="Anders")])) != gs.schluessel(g)

def test_hinweise(tmp_path):
    g = leer(hg="#FFFFFF", ebenen=[text(groesse=16, farbe="#EEEEEE", x=590)])
    _, h = gs.bild_rechnen(g, [str(tmp_path)])
    assert any("unter 12 px" in x for x in h)
    assert any("Kontrast" in x for x in h)
    assert any("außerhalb" in x for x in h)

def test_unter_1mb(tmp_path):
    import random
    rauschen = Image.effect_noise((1200, 1200), 120).convert("RGB")
    rauschen.save(tmp_path / "rausch.png")
    g = leer("quadrat", ebenen=[{"id": "b", "art": "bild", "quelle": "medien:rausch.png", "x": 300, "y": 300, "breite": 600, "drehung": 0}])
    r = gs.rechnen(g, [str(tmp_path)], str(tmp_path))
    assert os.path.getsize(tmp_path / r["name"]) <= 1024 * 1024

def test_umformatieren():
    g = leer("quer", ebenen=[text(x=300, y=200, groesse=40)])
    h = gs.umformatieren(g, "hoch")
    assert h["format"] == "hoch" and h["ebenen"][0]["x"] == 300 and h["ebenen"][0]["y"] == 375
    assert h["ebenen"][0]["groesse"] == 60  # Skalierung min(600,750)/min(600,400) = 1.5

def test_aufraeumen(tmp_path):
    for n in ("gs-aaaaaaaaaaaa.jpg", "gs-bbbbbbbbbbbb.jpg", "nl-12345678-x.jpg"):
        (tmp_path / n).write_bytes(b"x")
    alt = time.time() - 8 * 86400
    for n in ("gs-aaaaaaaaaaaa.jpg", "gs-bbbbbbbbbbbb.jpg", "nl-12345678-x.jpg"):
        os.utime(tmp_path / n, (alt, alt))
    weg = gs.aufraeumen(str(tmp_path), {"gs-bbbbbbbbbbbb.jpg"})
    assert weg == ["gs-aaaaaaaaaaaa.jpg"] and (tmp_path / "nl-12345678-x.jpg").exists()

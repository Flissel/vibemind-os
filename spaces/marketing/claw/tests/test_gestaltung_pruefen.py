import hashlib, os, pathlib
import pytest
from spaces.marketing.claw import gestaltung as gs

GUT = {"version": 1, "format": "quer", "hintergrund": "#F4EFE6", "ebenen": [
    {"id": "foto", "art": "bild", "quelle": "medien:nl-12345678-held_bild.jpg", "x": 300, "y": 200, "breite": 320, "drehung": 0},
    {"id": "titel", "art": "text", "text": "Herbst\nim Laden", "schrift": "playfair", "gewicht": 900,
     "kursiv": False, "groesse": 56, "farbe": "#1C1B18", "ausrichtung": "links", "zeilenabstand": 1.1,
     "x": 160, "y": 120, "drehung": -4}]}

def test_hoehen():
    assert {f: gs.hoehe(f) for f in gs.FORMATE} == {"quer": 400, "quadrat": 600, "hoch": 750, "banner": 200}

def test_gut_wird_normalisiert():
    n = gs.pruefen(GUT)
    assert n["ebenen"][1]["text"] == "Herbst\nim Laden" and n["format"] == "quer"

@pytest.mark.parametrize("aendern, grund", [
    (lambda g: g.update(version=2), "Version"),
    (lambda g: g.update(format="a4"), "Format"),
    (lambda g: g.update(hintergrund="rot"), "Farbe"),
    (lambda g: g.update(ebenen=[dict(GUT["ebenen"][0], id=f"e{i}") for i in range(21)]), "20 Ebenen"),
    (lambda g: g["ebenen"].append(dict(GUT["ebenen"][0])), "doppelt"),
    (lambda g: g["ebenen"][0].update(quelle="https://x.de/a.jpg"), "Medien"),
    (lambda g: g["ebenen"][0].update(quelle="medien:../x.jpg"), "Medien"),
    (lambda g: g["ebenen"][0].update(breite=0), "Breite"),
    (lambda g: g["ebenen"][1].update(text="x" * 201), "200 Zeichen"),
    (lambda g: g["ebenen"][1].update(text="a\nb\nc\nd\ne\nf\ng"), "6 Zeilen"),
    (lambda g: g["ebenen"][1].update(schrift="arial"), "Schrift"),
    (lambda g: g["ebenen"][1].update(gewicht=400), "Schnitt"),
    (lambda g: g["ebenen"][1].update(groesse=9), "Größe"),
    (lambda g: g["ebenen"][1].update(zeilenabstand=3), "Zeilenabstand"),
    (lambda g: g["ebenen"][1].update(drehung=181), "Drehung"),
    (lambda g: g["ebenen"][1].update(ausrichtung="block"), "Ausrichtung"),
    (lambda g: g["ebenen"][1].update(art="form"), "Art"),
    (lambda g: g["ebenen"][1].update(x=True), "Zahl"),
])
def test_fehler(aendern, grund):
    import copy
    g = copy.deepcopy(GUT); aendern(g)
    with pytest.raises(gs.GestaltungFehler, match=grund):
        gs.pruefen(g)

def test_kein_objekt():
    with pytest.raises(gs.GestaltungFehler):
        gs.pruefen([1, 2])

def test_schriften_gleich_wie_sales_claw():
    sc = os.environ.get("SALES_CLAW_DIR")
    if not sc:
        pytest.fail("SALES_CLAW_DIR setzen")
    quelle = pathlib.Path(sc) / "sales-mcp" / "static" / "schriften"
    hier = pathlib.Path(gs.ORDNER_SCHRIFTEN)
    namen = sorted(p.name for p in quelle.glob("*.woff2"))
    assert namen == sorted(p.name for p in hier.glob("*.woff2")) and len(namen) == 22
    for n in namen:
        assert hashlib.sha256((quelle / n).read_bytes()).digest() == hashlib.sha256((hier / n).read_bytes()).digest()

def test_schrift_datei():
    assert gs.schrift_datei("playfair", 900, False).endswith("playfair-900-normal.woff2")
    assert gs.schrift_datei("bodoni", 500, True).endswith("bodoni-500-italic.woff2")
    with pytest.raises(gs.GestaltungFehler):
        gs.schrift_datei("playfair", 400, False)

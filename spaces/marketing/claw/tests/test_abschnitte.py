"""Abschnitte fuer den Gestaltungs-Agenten: dieselben Bauplaene wie im Editor (sales-claw editor/src/abschnitte.ts)."""
import json
import os
import pathlib

import pytest

from spaces.marketing.claw import abschnitte as ab

GOLDEN = json.loads(pathlib.Path(ab.DATEI).read_text(encoding="utf-8"))


def _zaehler():
    n = [0]

    def neu():
        n[0] += 1
        return f"x{n[0]}"
    return neu


@pytest.mark.parametrize("probe", GOLDEN["farben"], ids=lambda p: p["doc"]["root"]["data"].get("canvasColor"))
def test_farben_wie_im_editor(probe):
    assert ab.farben(probe["doc"]) == probe["erwartet"]


def test_dreizehn_abschnitte_in_editor_reihenfolge():
    assert ab.SCHLUESSEL[:8] == ("kopfbild", "bild_text", "zwei_spalten", "drei_spalten", "zitat", "grosse_zahl",
                                 "knopfleiste", "fussgruss")
    assert len(ab.SCHLUESSEL) == 13 and "portraet_gruss" in ab.SCHLUESSEL


@pytest.mark.parametrize("schluessel", ab.SCHLUESSEL)
def test_bauen_ohne_marken_mit_medien_adressen(schluessel):
    dok = GOLDEN["farben"][1]["doc"]
    oben, bloecke = ab.bauen(schluessel, dok, _zaehler())
    assert oben in bloecke
    roh = json.dumps(bloecke, ensure_ascii=False)
    assert "{{" not in roh and "/medien/datei/" not in roh
    for bid, b in bloecke.items():
        assert bid.startswith("x")
        if b["type"] == "Image":
            assert b["data"]["props"]["url"].startswith("medien:platzhalter-")
    # alle Verweise zeigen auf mitgelieferte Bloecke
    for b in bloecke.values():
        p = b["data"].get("props") or {}
        kinder = list(p.get("childrenIds") or []) + [k for c in p.get("columns") or [] for k in c.get("childrenIds") or []]
        assert all(k in bloecke for k in kinder)


def test_farbband_traegt_ladenfarbe():
    oben, bloecke = ab.bauen("farbband", GOLDEN["farben"][1]["doc"], _zaehler())
    assert bloecke[oben]["data"]["props"]["randlos"] is True
    assert bloecke[oben]["data"]["style"]["backgroundColor"] == "#b5562f"


def test_unbekannter_abschnitt():
    with pytest.raises(KeyError):
        ab.bauen("gibtsnicht", GOLDEN["farben"][0]["doc"], _zaehler())


def test_bauplaene_gleich_wie_sales_claw():
    sc = os.environ.get("SALES_CLAW_DIR")
    if not sc:
        pytest.fail("SALES_CLAW_DIR setzen")
    quelle = pathlib.Path(sc) / "editor" / "test" / "abschnitte.golden.json"
    assert json.loads(quelle.read_text(encoding="utf-8")) == GOLDEN

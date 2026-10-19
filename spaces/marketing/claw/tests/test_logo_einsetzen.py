"""logo_einsetzen (Paket C2, 19.10.2026): alte Entwuerfe zeigen nur den Schriftzug marke_wort, weil sie entstanden,
bevor die Marke ein Logo hatte. "Marke uebernehmen" soll dort das Logo einsetzen - an derselben Stelle, wie es die
Vorlagen beim Anlegen tun (vorlagen_marke.einsetzen)."""
import copy

import pytest

from spaces.marketing.claw import agent_prompt as ap
from spaces.marketing.claw.agent_werkzeuge import WERKZEUGE, WerkzeugFehler, anwenden

LOGO = "logo-fin2gether-5bfafcd773.png"
WORT = {"type": "Heading", "data": {
    "props": {"text": "fin2gether", "level": "h1"},
    "style": {"color": "#2b2724", "padding": {"top": 4, "left": 0, "right": 0, "bottom": 8}, "fontSize": 26,
              "textAlign": "center", "fontFamily": "ANZEIGE", "letterSpacing": 4}}}
DOK = {
    "root": {"type": "EmailLayout", "data": {
        "canvasColor": "#FFFFFF", "childrenIds": ["kopf", "text1"],
        "rollen": {"marke_logo/data/props/url": "logo", "marke_wort/data/props/text": "laden",
                   "marke_wort/data/style/color": "auf_zweit", "text1/data/style/color": "text"}}},
    "kopf": {"type": "ColumnsContainer", "data": {"style": {}, "props": {"columns": [
        {"childrenIds": ["kopf_ausgabe"]}, {"childrenIds": ["marke_wort"]}, {"childrenIds": []}]}}},
    "kopf_ausgabe": {"type": "Text", "data": {"style": {}, "props": {"text": "Ausgabe 3"}}},
    "marke_wort": WORT,
    "text1": {"type": "Text", "data": {"style": {}, "props": {"text": "Hallo"}}},
}
MEDIEN = {LOGO, "nl-12345678-held.jpg"}


def lauf(aenderungen, dok=None):
    return anwenden(DOK if dok is None else dok, aenderungen, MEDIEN)


def einsetzen(quelle=f"medien:{LOGO}", dok=None):
    return lauf([{"werkzeug": "logo_einsetzen", "quelle": quelle, "schritt": "Logo"}], dok)


def test_ersetzt_den_schriftzug_an_derselben_stelle():
    b = einsetzen().bloecke
    assert "marke_wort" not in b
    assert b["kopf"]["data"]["props"]["columns"][1]["childrenIds"] == ["marke_logo"]
    logo = b["marke_logo"]
    assert logo["type"] == "Image"
    assert logo["data"]["props"] == {"url": f"medien:{LOGO}", "alt": "Logo", "width": 132, "height": 44,
                                     "grafik": True, "contentAlignment": "middle"}
    assert logo["data"]["style"] == {"padding": {"top": 4, "left": 0, "right": 0, "bottom": 8}, "textAlign": "center"}


def test_rollen_zeigen_danach_nur_noch_auf_das_logo():
    rollen = einsetzen().bloecke["root"]["data"]["rollen"]
    assert rollen["marke_logo/data/props/url"] == "logo"
    assert not [k for k in rollen if k.startswith("marke_wort/")]
    assert rollen["text1/data/style/color"] == "text"


def test_ohne_rollentabelle_wird_sie_angelegt():
    dok = copy.deepcopy(DOK)
    del dok["root"]["data"]["rollen"]
    assert einsetzen(dok=dok).bloecke["root"]["data"]["rollen"] == {"marke_logo/data/props/url": "logo"}


def test_original_bleibt_unveraendert():
    vorher = copy.deepcopy(DOK)
    einsetzen()
    assert DOK == vorher


@pytest.mark.parametrize("groesse, breite", [(24, 132), (26, 132), (34, 150), (44, 180), (56, 180), (None, 150)])
def test_kasten_folgt_der_groesse_des_schriftzugs(groesse, breite):
    dok = copy.deepcopy(DOK)
    if groesse is None:
        del dok["marke_wort"]["data"]["style"]["fontSize"]
    else:
        dok["marke_wort"]["data"]["style"]["fontSize"] = groesse
    p = einsetzen(dok=dok).bloecke["marke_logo"]["data"]["props"]
    assert (p["width"], p["height"]) == (breite, breite // 3)


def test_schriftzug_direkt_im_newsletter():
    dok = copy.deepcopy(DOK)
    dok["root"]["data"]["childrenIds"] = ["marke_wort", "text1"]
    dok["kopf"]["data"]["props"]["columns"][1]["childrenIds"] = []
    assert einsetzen(dok=dok).bloecke["root"]["data"]["childrenIds"] == ["marke_logo", "text1"]


def test_ohne_ausrichtung_links():
    dok = copy.deepcopy(DOK)
    del dok["marke_wort"]["data"]["style"]["textAlign"]
    assert einsetzen(dok=dok).bloecke["marke_logo"]["data"]["style"]["textAlign"] == "left"


def _fehler(teil, **kw):
    with pytest.raises(WerkzeugFehler) as ei:
        einsetzen(**kw)
    assert teil in str(ei.value)


def test_ohne_schriftzug_kein_umbau():
    dok = copy.deepcopy(DOK)
    dok["kopf"]["data"]["props"]["columns"][1]["childrenIds"] = []
    del dok["marke_wort"]
    _fehler("kein Schriftzug", dok=dok)


def test_logo_gibt_es_schon():
    dok = copy.deepcopy(DOK)
    dok["marke_logo"] = {"type": "Image", "data": {"style": {}, "props": {"url": f"medien:{LOGO}"}}}
    dok["root"]["data"]["childrenIds"].append("marke_logo")
    _fehler("gibt es schon", dok=dok)


def test_quelle_muss_in_den_medien_sein():
    _fehler("gibt es nicht in den Medien", quelle="medien:fremd.png")


def test_schriftzug_ist_kein_text_block():
    dok = copy.deepcopy(DOK)
    dok["marke_wort"] = {"type": "Image", "data": {"style": {}, "props": {"url": f"medien:{LOGO}"}}}
    _fehler("kein Schriftzug", dok=dok)


def test_werkzeug_steht_im_system():
    assert "logo_einsetzen" in WERKZEUGE
    assert "- logo_einsetzen: quelle*" in ap.SYSTEM
    teil = ap.SYSTEM.split("MARKENLOGO\n")[1].split("\n\n")[0]
    assert "logo_einsetzen" in teil and "marke_wort" in teil
    assert "keinen Logo-Platz" in teil

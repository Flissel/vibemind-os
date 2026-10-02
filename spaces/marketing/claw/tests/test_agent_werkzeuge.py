import copy

import pytest

from spaces.marketing.claw import agent_werkzeuge as aw
from spaces.marketing.claw.agent_werkzeuge import WerkzeugFehler, anwenden

DOK = {"root": {"type": "EmailLayout", "data": {"backdropColor": "#F5F4F0", "canvasColor": "#FFFFFF", "textColor": "#1C1B18",
                 "childrenIds": ["kopf", "held", "text1"]}},
       "kopf": {"type": "Heading", "data": {"style": {}, "props": {"text": "Oktober", "level": "h1"}}},
       "held": {"type": "Image", "data": {"style": {}, "props": {"url": "medien:nl-12345678-held.jpg", "alt": "", "width": 600, "height": 300}}},
       "text1": {"type": "Text", "data": {"style": {}, "props": {"text": "Hallo"}}}}
MEDIEN = {"nl-12345678-held.jpg", "person-frei.png"}

TEXT_EBENE = {"art": "text", "x": 300, "y": 200, "text": "Hallo", "schrift": "dm-sans", "gewicht": 400,
              "groesse": 40, "farbe": "#111111"}


def lauf(aenderungen, dok=None):
    return anwenden(DOK if dok is None else dok, aenderungen, MEDIEN)


def fl(nach="text1", fmt="quer"):
    return {"werkzeug": "flaeche_anlegen", "nach": nach, "format": fmt, "hintergrund": "#ffffff", "alt": "Aktion"}


def mit_flaeche(*weitere):
    e = lauf([fl(), *weitere])
    return e


def flaeche_id(e):
    return [k for k, b in e.bloecke.items() if "gestaltung" in b.get("data", {}).get("props", {})][0]


def fehler(aenderungen, teil, dok=None):
    with pytest.raises(WerkzeugFehler) as ei:
        lauf(aenderungen, dok)
    assert teil in str(ei.value)
    return str(ei.value)


def test_original_bleibt_unveraendert():
    vorher = copy.deepcopy(DOK)
    e = lauf([{"werkzeug": "block_loeschen", "id": "kopf"}, fl(), {"werkzeug": "farben_setzen", "textColor": "#000000"}])
    assert DOK == vorher
    assert e.geaendert and "kopf" not in e.bloecke
    assert e.bloecke is not DOK


def test_leere_liste_ungeaendert():
    e = lauf([])
    assert not e.geaendert and e.bloecke == DOK and e.bildauftraege == [] and e.export_vorschlag is None and e.notiz == ""


def test_max_40():
    lauf([{"werkzeug": "farben_setzen", "textColor": "#000000"}] * 40)
    fehler([{"werkzeug": "farben_setzen", "textColor": "#000000"}] * 41, "Höchstens 40")


def test_unbekanntes_werkzeug_und_form():
    fehler([{"werkzeug": "kaffee"}], "kaffee: unbekanntes Werkzeug")
    fehler([{"id": "x"}], "?: unbekanntes Werkzeug")
    fehler(["block_loeschen"], "Objekt")
    fehler({"werkzeug": "block_loeschen"}, "Liste")


def test_fehlende_und_zusaetzliche_parameter():
    fehler([{"werkzeug": "block_loeschen"}], "block_loeschen: Parameter fehlt: id")
    fehler([{"werkzeug": "block_loeschen", "id": "kopf", "grund": "x"}], "block_loeschen: Unbekannter Parameter: grund")
    fehler([{"werkzeug": "block_loeschen", "id": ["kopf"]}], "block_loeschen")


# ---- Bloecke ----------------------------------------------------------------------------------
def test_block_einfuegen_ende_und_nach():
    e = lauf([{"werkzeug": "block_einfuegen", "typ": "Text", "nach": None, "daten": {"props": {"text": "Ende"}}},
              {"werkzeug": "block_einfuegen", "typ": "Divider", "nach": "kopf"}])
    ids = e.bloecke["root"]["data"]["childrenIds"]
    assert len(ids) == 5 and ids[0] == "kopf" and ids[-1].startswith("agent-") and len(ids[-1]) == len("agent-") + 6
    assert e.bloecke[ids[1]]["type"] == "Divider"
    assert e.bloecke[ids[-1]]["data"]["props"]["text"] == "Ende"


def test_block_einfuegen_in_container_und_spalte():
    dok = copy.deepcopy(DOK)
    dok["root"]["data"]["childrenIds"] += ["card", "cols"]
    dok["card"] = {"type": "Container", "data": {"style": {}, "props": {"childrenIds": ["k1"]}}}
    dok["k1"] = {"type": "Text", "data": {"style": {}, "props": {"text": "a"}}}
    dok["cols"] = {"type": "ColumnsContainer", "data": {"style": {}, "props": {"columnsCount": 2, "columns": [{"childrenIds": ["c1"]}, {"childrenIds": []}]}}}
    dok["c1"] = {"type": "Text", "data": {"style": {}, "props": {"text": "b"}}}
    e = lauf([{"werkzeug": "block_einfuegen", "typ": "Spacer", "nach": "k1"},
              {"werkzeug": "block_einfuegen", "typ": "Spacer", "nach": "c1"}], dok)
    assert len(e.bloecke["card"]["data"]["props"]["childrenIds"]) == 2
    assert e.bloecke["card"]["data"]["props"]["childrenIds"][0] == "k1"
    assert len(e.bloecke["cols"]["data"]["props"]["columns"][0]["childrenIds"]) == 2
    assert len(e.bloecke["root"]["data"]["childrenIds"]) == 5


def test_block_einfuegen_fehler():
    fehler([{"werkzeug": "block_einfuegen", "typ": "Container", "nach": None}], "typ muss")
    fehler([{"werkzeug": "block_einfuegen", "typ": "Text", "nach": "gibtsnicht"}], "gibt es nicht")
    fehler([{"werkzeug": "block_einfuegen", "typ": "Text", "nach": "root"}], "root")
    fehler([{"werkzeug": "block_einfuegen", "typ": "Text", "nach": None, "daten": {"foo": 1}}], "Unbekannte Parameter")
    fehler([{"werkzeug": "block_einfuegen", "typ": "Image", "nach": None,
             "daten": {"props": {"gestaltung": {"version": 1}}}}], "gestaltung")


def test_block_aendern_merge():
    e = lauf([{"werkzeug": "block_aendern", "id": "kopf", "props": {"text": "Neu"}, "style": {"color": "#000000"}}])
    d = e.bloecke["kopf"]["data"]
    assert d["props"] == {"text": "Neu", "level": "h1"} and d["style"] == {"color": "#000000"}
    assert e.bloecke["kopf"]["type"] == "Heading"


def test_block_aendern_fehler():
    fehler([{"werkzeug": "block_aendern", "id": "root", "props": {"x": 1}}], "farben_setzen")
    fehler([{"werkzeug": "block_aendern", "id": "kopf", "props": {"type": "Text"}}], "unveränderlich")
    fehler([{"werkzeug": "block_aendern", "id": "kopf"}], "props oder style")
    fehler([{"werkzeug": "block_aendern", "id": "kopf", "props": "x"}], "Objekte")
    fehler([{"werkzeug": "block_aendern", "id": "kopf", "props": {"gestaltung": {}}}], "gestaltung")
    fehler([{"werkzeug": "block_aendern", "id": "nix", "props": {"a": 1}}], "gibt es nicht")


def test_flaeche_ueber_block_aendern_gesperrt():
    for key in ("gestaltung", "url", "width", "height"):
        fehler([fl(), {"werkzeug": "block_aendern", "id": "neu:1", "props": {key: 1}}],
               "Fläche nur mit den Flächen-Werkzeugen ändern")
    e = lauf([fl(), {"werkzeug": "block_aendern", "id": "neu:1", "props": {"alt": "Anders"}}])
    assert e.bloecke[flaeche_id(e)]["data"]["props"]["alt"] == "Anders"


def test_block_verschieben():
    e = lauf([{"werkzeug": "block_verschieben", "id": "kopf", "nach": "text1"}])
    assert e.bloecke["root"]["data"]["childrenIds"] == ["held", "text1", "kopf"]
    e = lauf([{"werkzeug": "block_verschieben", "id": "text1", "nach": None}])
    assert e.bloecke["root"]["data"]["childrenIds"] == ["kopf", "held", "text1"]
    e = lauf([{"werkzeug": "block_verschieben", "id": "text1", "nach": "kopf"}])
    assert e.bloecke["root"]["data"]["childrenIds"] == ["kopf", "text1", "held"]


def test_block_verschieben_fehler():
    fehler([{"werkzeug": "block_verschieben", "id": "root", "nach": None}], "root")
    fehler([{"werkzeug": "block_verschieben", "id": "kopf", "nach": "kopf"}], "selbst")
    dok = copy.deepcopy(DOK)
    dok["root"]["data"]["childrenIds"].append("card")
    dok["card"] = {"type": "Container", "data": {"style": {}, "props": {"childrenIds": ["k1"]}}}
    dok["k1"] = {"type": "Text", "data": {"style": {}, "props": {"text": "a"}}}
    fehler([{"werkzeug": "block_verschieben", "id": "card", "nach": "k1"}], "Nachfahren", dok)


def test_block_loeschen_mit_nachfahren():
    dok = copy.deepcopy(DOK)
    dok["root"]["data"]["childrenIds"] += ["cols"]
    dok["cols"] = {"type": "ColumnsContainer", "data": {"style": {}, "props": {"columnsCount": 2, "columns": [{"childrenIds": ["c1"]}, {"childrenIds": ["c2"]}]}}}
    dok["c1"] = {"type": "Container", "data": {"style": {}, "props": {"childrenIds": ["c3"]}}}
    dok["c2"] = {"type": "Text", "data": {"style": {}, "props": {"text": "b"}}}
    dok["c3"] = {"type": "Text", "data": {"style": {}, "props": {"text": "c"}}}
    e = lauf([{"werkzeug": "block_loeschen", "id": "cols"}], dok)
    assert set(e.bloecke) == {"root", "kopf", "held", "text1"}
    assert e.bloecke["root"]["data"]["childrenIds"] == ["kopf", "held", "text1"]
    e = lauf([{"werkzeug": "block_loeschen", "id": "c1"}], dok)
    assert "c3" not in e.bloecke and e.bloecke["cols"]["data"]["props"]["columns"][0]["childrenIds"] == []


def test_block_loeschen_fehler():
    fehler([{"werkzeug": "block_loeschen", "id": "root"}], "root")
    fehler([{"werkzeug": "block_loeschen", "id": "nix"}], "gibt es nicht")


def test_farben_setzen():
    e = lauf([{"werkzeug": "farben_setzen", "backdropColor": "#000000", "canvasColor": "#ABCDEF"}])
    d = e.bloecke["root"]["data"]
    assert d["backdropColor"] == "#000000" and d["canvasColor"] == "#ABCDEF" and d["textColor"] == "#1C1B18"
    assert d["childrenIds"] == ["kopf", "held", "text1"]
    fehler([{"werkzeug": "farben_setzen"}], "mindestens eine")
    fehler([{"werkzeug": "farben_setzen", "textColor": "rot"}], "#RRGGBB")
    fehler([{"werkzeug": "farben_setzen", "childrenIds": []}], "Unbekannter Parameter")


# ---- Flaechen ---------------------------------------------------------------------------------
def test_flaeche_anlegen():
    e = lauf([fl("kopf", "hoch")])
    fid = flaeche_id(e)
    assert e.bloecke["root"]["data"]["childrenIds"][:2] == ["kopf", fid]
    p = e.bloecke[fid]["data"]["props"]
    assert e.bloecke[fid]["type"] == "Image" and p["url"] is None and p["height"] == 750 and p["width"] == 600
    assert p["gestaltung"] == {"version": 1, "format": "hoch", "hintergrund": "#FFFFFF", "ebenen": []}
    assert p["alt"] == "Aktion"


def test_flaeche_anlegen_fehler():
    fehler([{**fl(), "format": "rund"}], "format muss")
    fehler([{**fl(), "hintergrund": "weiss"}], "Hintergrund")
    fehler([{**fl(), "alt": ""}], "alt")
    fehler([{**fl(), "alt": "x" * 201}], "alt")
    fehler([fl("nix")], "gibt es nicht")


def test_neu_verweis():
    e = lauf([fl(), fl("neu:1", "quadrat"),
              {"werkzeug": "ebene_hinzufuegen", "flaeche": "neu:2", "ebene": TEXT_EBENE},
              {"werkzeug": "hintergrund_setzen", "flaeche": "neu:1", "farbe": "#000000"}])
    ids = [i for i in e.bloecke["root"]["data"]["childrenIds"] if i.startswith("agent-")]
    assert len(ids) == 2
    assert e.bloecke[ids[0]]["data"]["props"]["gestaltung"]["hintergrund"] == "#000000"
    assert len(e.bloecke[ids[1]]["data"]["props"]["gestaltung"]["ebenen"]) == 1
    fehler([{"werkzeug": "hintergrund_setzen", "flaeche": "neu:1", "farbe": "#000000"}], "keine bisher angelegte")
    fehler([fl(), {"werkzeug": "hintergrund_setzen", "flaeche": "neu:2", "farbe": "#000000"}], "keine bisher angelegte")
    fehler([fl(), {"werkzeug": "hintergrund_setzen", "flaeche": "neu:0", "farbe": "#000000"}], "neu:0")
    fehler([{"werkzeug": "block_loeschen", "id": "neu:1"}], "keine bisher angelegte")
    fehler([fl(), {"werkzeug": "block_loeschen", "id": "neu:1"},
            {"werkzeug": "hintergrund_setzen", "flaeche": "neu:1", "farbe": "#000000"}], "gelöscht")


def test_flaeche_muss_flaeche_sein():
    fehler([{"werkzeug": "hintergrund_setzen", "flaeche": "held", "farbe": "#000000"}], "keine Fläche")
    fehler([{"werkzeug": "ebene_loeschen", "flaeche": "kopf", "id": "a"}], "keine Fläche")


def test_ebene_hinzufuegen_erfolg():
    e = lauf([fl(), {"werkzeug": "ebene_hinzufuegen", "flaeche": "neu:1", "ebene": TEXT_EBENE},
              {"werkzeug": "ebene_hinzufuegen", "flaeche": "neu:1",
               "ebene": {"art": "bild", "id": "bg", "x": 10, "y": 10, "quelle": "medien:person-frei.png", "breite": 200}}])
    eb = e.bloecke[flaeche_id(e)]["data"]["props"]["gestaltung"]["ebenen"]
    assert eb[0]["id"].startswith("e-") and len(eb[0]["id"]) == 8 and eb[0]["text"] == "Hallo"
    assert eb[1]["id"] == "bg" and eb[1]["quelle"] == "medien:person-frei.png"


def test_ebene_hinzufuegen_quelle_ohne_praefix_wird_normalisiert():
    e = lauf([fl(), {"werkzeug": "ebene_hinzufuegen", "flaeche": "neu:1",
                     "ebene": {"art": "bild", "x": 10, "y": 10, "quelle": "person-frei.png", "breite": 200}}])
    assert e.bloecke[flaeche_id(e)]["data"]["props"]["gestaltung"]["ebenen"][0]["quelle"] == "medien:person-frei.png"


def test_ebene_hinzufuegen_fehler():
    fehler([fl(), {"werkzeug": "ebene_hinzufuegen", "flaeche": "neu:1",
                   "ebene": {"art": "bild", "x": 1, "y": 1, "quelle": "medien:fremd.png", "breite": 50}}], "fremd.png")
    fehler([fl(), {"werkzeug": "ebene_hinzufuegen", "flaeche": "neu:1", "ebene": {**TEXT_EBENE, "farbe": "rot"}}], "Farbe")
    fehler([fl(), {"werkzeug": "ebene_hinzufuegen", "flaeche": "neu:1", "ebene": {**TEXT_EBENE, "tippfehler": 1}}], "tippfehler")
    fehler([fl(), {"werkzeug": "ebene_hinzufuegen", "flaeche": "neu:1", "ebene": {**TEXT_EBENE, "art": "form"}}], "art")
    fehler([fl(), {"werkzeug": "ebene_hinzufuegen", "flaeche": "neu:1", "ebene": "x"}], "Objekt")
    doppelt = [fl()] + [{"werkzeug": "ebene_hinzufuegen", "flaeche": "neu:1", "ebene": {**TEXT_EBENE, "id": "a"}}] * 2
    fehler(doppelt, "doppelt")
    viele = [fl()] + [{"werkzeug": "ebene_hinzufuegen", "flaeche": "neu:1", "ebene": TEXT_EBENE}] * 21
    fehler(viele, "20 Ebenen")


def test_ebene_aendern():
    e = lauf([fl(), {"werkzeug": "ebene_hinzufuegen", "flaeche": "neu:1", "ebene": {**TEXT_EBENE, "id": "t"}},
              {"werkzeug": "ebene_aendern", "flaeche": "neu:1", "id": "t", "felder": {"x": 100, "text": "Neu"}}])
    eb = e.bloecke[flaeche_id(e)]["data"]["props"]["gestaltung"]["ebenen"][0]
    assert eb["x"] == 100 and eb["text"] == "Neu" and eb["id"] == "t" and eb["art"] == "text"
    basis = [fl(), {"werkzeug": "ebene_hinzufuegen", "flaeche": "neu:1", "ebene": {**TEXT_EBENE, "id": "t"}}]
    fehler(basis + [{"werkzeug": "ebene_aendern", "flaeche": "neu:1", "id": "t", "felder": {"id": "u"}}], "unveränderlich")
    fehler(basis + [{"werkzeug": "ebene_aendern", "flaeche": "neu:1", "id": "t", "felder": {"art": "bild"}}], "unveränderlich")
    fehler(basis + [{"werkzeug": "ebene_aendern", "flaeche": "neu:1", "id": "t", "felder": {"quelle": "medien:person-frei.png"}}], "Unbekannte Felder")
    fehler(basis + [{"werkzeug": "ebene_aendern", "flaeche": "neu:1", "id": "zz", "felder": {"x": 1}}], "gibt es nicht")
    fehler(basis + [{"werkzeug": "ebene_aendern", "flaeche": "neu:1", "id": "t", "felder": {"groesse": 500}}], "Größe")
    fehler(basis + [{"werkzeug": "ebene_aendern", "flaeche": "neu:1", "id": "t", "felder": {}}], "felder")


def test_ebene_aendern_bild_quelle_pruefen():
    basis = [fl(), {"werkzeug": "ebene_hinzufuegen", "flaeche": "neu:1",
                    "ebene": {"art": "bild", "id": "b", "x": 1, "y": 1, "quelle": "medien:person-frei.png", "breite": 50}}]
    fehler(basis + [{"werkzeug": "ebene_aendern", "flaeche": "neu:1", "id": "b", "felder": {"quelle": "medien:weg.png"}}], "weg.png")
    e = lauf(basis + [{"werkzeug": "ebene_aendern", "flaeche": "neu:1", "id": "b", "felder": {"quelle": "nl-12345678-held.jpg"}}])
    assert e.bloecke[flaeche_id(e)]["data"]["props"]["gestaltung"]["ebenen"][0]["quelle"] == "medien:nl-12345678-held.jpg"


def test_ebene_reihenfolge():
    basis = [fl()] + [{"werkzeug": "ebene_hinzufuegen", "flaeche": "neu:1", "ebene": {**TEXT_EBENE, "id": i}} for i in "abc"]
    e = lauf(basis + [{"werkzeug": "ebene_reihenfolge", "flaeche": "neu:1", "ids": ["c", "a", "b"]}])
    assert [x["id"] for x in e.bloecke[flaeche_id(e)]["data"]["props"]["gestaltung"]["ebenen"]] == ["c", "a", "b"]
    for falsch in (["a", "b"], ["a", "b", "b"], ["a", "b", "c", "d"], "abc", [1, 2, 3]):
        fehler(basis + [{"werkzeug": "ebene_reihenfolge", "flaeche": "neu:1", "ids": falsch}], "ids")


def test_ebene_loeschen():
    basis = [fl()] + [{"werkzeug": "ebene_hinzufuegen", "flaeche": "neu:1", "ebene": {**TEXT_EBENE, "id": i}} for i in "ab"]
    e = lauf(basis + [{"werkzeug": "ebene_loeschen", "flaeche": "neu:1", "id": "a"}])
    assert [x["id"] for x in e.bloecke[flaeche_id(e)]["data"]["props"]["gestaltung"]["ebenen"]] == ["b"]
    fehler(basis + [{"werkzeug": "ebene_loeschen", "flaeche": "neu:1", "id": "zz"}], "gibt es nicht")


def test_format_setzen_setzt_hoehe():
    e = lauf([fl(), {"werkzeug": "format_setzen", "flaeche": "neu:1", "format": "banner"}])
    p = e.bloecke[flaeche_id(e)]["data"]["props"]
    assert p["gestaltung"]["format"] == "banner" and p["height"] == 200
    fehler([fl(), {"werkzeug": "format_setzen", "flaeche": "neu:1", "format": "rund"}], "format")


def test_hintergrund_setzen():
    e = lauf([fl(), {"werkzeug": "hintergrund_setzen", "flaeche": "neu:1", "farbe": "#ff0000"}])
    assert e.bloecke[flaeche_id(e)]["data"]["props"]["gestaltung"]["hintergrund"] == "#FF0000"
    fehler([fl(), {"werkzeug": "hintergrund_setzen", "flaeche": "neu:1", "farbe": "rot"}], "Hintergrund")


def test_flaeche_in_vorhandenem_dokument_aendern():
    e = lauf([fl()])
    fid = flaeche_id(e)
    e2 = anwenden(e.bloecke, [{"werkzeug": "ebene_hinzufuegen", "flaeche": fid, "ebene": TEXT_EBENE}], MEDIEN)
    assert len(e2.bloecke[fid]["data"]["props"]["gestaltung"]["ebenen"]) == 1
    assert e.bloecke[fid]["data"]["props"]["gestaltung"]["ebenen"] == []


# ---- Bilder / Abschluss -----------------------------------------------------------------------
def test_bild_erzeugen_und_freistellen():
    e = lauf([{"werkzeug": "bild_erzeugen", "platz": "held", "hinweis": "Kerzenlicht"},
              {"werkzeug": "bild_freistellen", "platz": "held"}])
    assert e.bildauftraege == [{"platz": "held", "modus": "neu", "hinweis": "Kerzenlicht"},
                               {"platz": "held", "modus": "freistellen", "hinweis": ""}]
    assert not e.geaendert


def test_bild_erzeugen_fehler():
    fehler([{"werkzeug": "bild_erzeugen", "platz": "kopf", "hinweis": "x"}], "kein Bildplatz")
    fehler([fl(), {"werkzeug": "bild_erzeugen", "platz": "neu:1", "hinweis": "x"}], "kein Bildplatz")
    e = lauf([fl()])
    fehler([{"werkzeug": "bild_erzeugen", "platz": flaeche_id(e), "hinweis": "x"}], "kein Bildplatz", e.bloecke)
    fehler([{"werkzeug": "bild_erzeugen", "platz": ["held"], "hinweis": "x"}], "kein Bildplatz")
    fehler([{"werkzeug": "bild_erzeugen", "platz": "held", "hinweis": 5}], "hinweis")


def test_bild_platz_wird_nach_blockaenderungen_geprueft():
    fehler([{"werkzeug": "bild_freistellen", "platz": "held"}, {"werkzeug": "block_loeschen", "id": "held"}], "kein Bildplatz")
    neuer_platz = {"werkzeug": "block_einfuegen", "typ": "Image", "nach": None,
                   "daten": {"props": {"url": None, "alt": "", "width": 600, "height": 300}}}
    neu = lauf([neuer_platz]).bloecke["root"]["data"]["childrenIds"][-1]
    assert neu.startswith("agent-")
    # ids sind zufaellig: ein Platz, der erst durch eine Aenderung entsteht, ist vorab nicht ansprechbar
    fehler([{"werkzeug": "bild_erzeugen", "platz": neu, "hinweis": "x"}, neuer_platz], "kein Bildplatz")


def test_bild_aus_medien():
    e = lauf([{"werkzeug": "bild_aus_medien", "platz": "held", "quelle": "person-frei.png"}])
    assert e.bloecke["held"]["data"]["props"]["url"] == "medien:person-frei.png" and e.geaendert
    e = lauf([{"werkzeug": "bild_aus_medien", "platz": "held", "quelle": "medien:person-frei.png"}])
    assert e.bloecke["held"]["data"]["props"]["url"] == "medien:person-frei.png"
    fehler([{"werkzeug": "bild_aus_medien", "platz": "held", "quelle": "fremd.png"}], "fremd.png")
    fehler([{"werkzeug": "bild_aus_medien", "platz": "kopf", "quelle": "person-frei.png"}], "kein Bildplatz")
    fehler([fl(), {"werkzeug": "bild_aus_medien", "platz": "neu:1", "quelle": "person-frei.png"}], "kein Bildplatz")


def test_entwurf_speichern():
    assert lauf([{"werkzeug": "entwurf_speichern", "notiz": "Version A"}]).notiz == "Version A"
    fehler([{"werkzeug": "entwurf_speichern", "notiz": "x" * 201}], "200")
    fehler([{"werkzeug": "entwurf_speichern", "notiz": 3}], "notiz")
    fehler([{"werkzeug": "entwurf_speichern", "notiz": "a"}, {"werkzeug": "entwurf_speichern", "notiz": "b"}], "einmal")


def test_export_vorschlagen():
    e = lauf([fl(), fl("neu:1"), {"werkzeug": "export_vorschlagen", "newsletter": True, "flaechen": ["alle"]}])
    ids = [i for i in e.bloecke["root"]["data"]["childrenIds"] if i.startswith("agent-")]
    assert e.export_vorschlag == {"newsletter": True, "flaechen": ids} and len(ids) == 2
    e = lauf([fl(), fl("neu:1"), {"werkzeug": "export_vorschlagen", "newsletter": False, "flaechen": ["neu:2", "alle"]}])
    ids = [i for i in e.bloecke["root"]["data"]["childrenIds"] if i.startswith("agent-")]
    assert e.export_vorschlag == {"newsletter": False, "flaechen": [ids[1], ids[0]]}
    assert lauf([{"werkzeug": "export_vorschlagen", "newsletter": True, "flaechen": []}]).export_vorschlag == {"newsletter": True, "flaechen": []}
    fehler([{"werkzeug": "export_vorschlagen", "newsletter": "ja", "flaechen": []}], "newsletter")
    fehler([{"werkzeug": "export_vorschlagen", "newsletter": True, "flaechen": ["kopf"]}], "keine Fläche")
    fehler([{"werkzeug": "export_vorschlagen", "newsletter": True, "flaechen": "alle"}], "flaechen")
    fehler([{"werkzeug": "export_vorschlagen", "newsletter": True, "flaechen": []}] * 2, "einmal")


def test_export_vorschlag_ohne_flaechen_alle_leer():
    assert lauf([{"werkzeug": "export_vorschlagen", "newsletter": False, "flaechen": ["alle"]}]).export_vorschlag == \
        {"newsletter": False, "flaechen": []}


def test_fehler_bricht_alles_ab_und_original_bleibt():
    vorher = copy.deepcopy(DOK)
    fehler([{"werkzeug": "block_loeschen", "id": "kopf"}, {"werkzeug": "block_loeschen", "id": "nix"}], "block_loeschen")
    assert DOK == vorher


def test_werkzeuge_vollstaendig():
    assert len(aw.WERKZEUGE) == 17 and set(aw.WERKZEUGE) == set(aw.PARAMETER)

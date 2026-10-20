import math

import pytest

from spaces.marketing.claw import seite as seite_mod
from spaces.marketing.claw import seite_streifen as st


def seite(ebenen: list[dict], hoehe: int = 1200, bild: str | None = None) -> dict:
    return {"version": 1, "breite": 600, "hoehe": hoehe,
            "hintergrund": {"farbe": "#ffffff", "bild": bild},
            "schriften": {"anzeige": "dm-sans", "text": "dm-sans"}, "ebenen": ebenen}


def text(eid, y, hoehe=100, **mehr):
    return {"id": eid, "art": "text", "x": 300, "y": y, "breite": 480, "hoehe": hoehe,
            "text": "Hallo", "rolle": "TEXT", "groesse": 17, "farbe": "#333333", **mehr}


def knopf(eid, y, **mehr):
    return {"id": eid, "art": "knopf", "x": 300, "y": y, "breite": 220, "hoehe": 48,
            "text": "Mehr", "link": "https://example.com", "farbe": "#5b3fd6", "textfarbe": "#ffffff", **mehr}


def bild(eid, y, hoehe=300, breite=400, x=300, **mehr):
    return {"id": eid, "art": "bild", "x": x, "y": y, "breite": breite, "hoehe": hoehe,
            "quelle": "medien:foto.jpg", **mehr}


def lueckenlos(baender, hoehe):
    assert baender[0].oben == 0
    assert baender[-1].unten == hoehe
    for a, b in zip(baender, baender[1:]):
        assert a.unten == b.oben
    assert all(b.unten > b.oben for b in baender)


def arten(baender):
    return [b.art for b in baender]


def band_von(baender, eid):
    return [b for b in baender if eid in b.ebenen]


def test_rahmen_ungedreht():
    assert st.rahmen({"x": 300, "y": 200, "breite": 100, "hoehe": 50, "drehung": 0}) == (250, 175, 350, 225)


def test_rahmen_90_grad_tauscht_breite_und_hoehe():
    l, o, r, u = st.rahmen({"x": 300, "y": 200, "breite": 100, "hoehe": 50, "drehung": 90})
    assert (r - l, u - o) == (50, 100)
    assert (l, o) == (275, 150)


def test_rahmen_45_grad_ist_huelle():
    l, o, r, u = st.rahmen({"x": 0, "y": 0, "breite": 100, "hoehe": 100, "drehung": 45})
    assert r - l == pytest.approx(141.421356, abs=1e-4)
    assert u - o == pytest.approx(141.421356, abs=1e-4)


def test_nur_fliesstext_und_knopf_nur_textbaender():
    s = seite([text("a", 150), knopf("k", 400), text("b", 700)])
    baender = st.plan(s)
    lueckenlos(baender, 1200)
    assert set(arten(baender)) == {"text"}
    for eid in ("a", "k", "b"):
        assert len(band_von(baender, eid)) == 1


def test_text_tauglich_regeln():
    s = seite_mod.pruefen(seite([text("a", 150), knopf("k", 400),
                                 text("dreh", 700, drehung=5), text("anz", 900, rolle="ANZEIGE"),
                                 text("hg", 1100, hoehe=60, hintergrund="#eeeeee")]))
    e = {x["id"]: x for x in s["ebenen"]}
    assert st.text_tauglich(e["a"], s["ebenen"], s)
    assert st.text_tauglich(e["k"], s["ebenen"], s)
    assert not st.text_tauglich(e["dreh"], s["ebenen"], s)
    assert not st.text_tauglich(e["anz"], s["ebenen"], s)
    assert not st.text_tauglich(e["hg"], s["ebenen"], s)
    halb = dict(e["a"], deckkraft=0.5)
    assert not st.text_tauglich(halb, s["ebenen"], s)
    halber_knopf = dict(e["k"], deckkraft=0.5)
    assert not st.text_tauglich(halber_knopf, s["ebenen"], s)
    dreh_knopf = dict(e["k"], drehung=10)
    assert not st.text_tauglich(dreh_knopf, s["ebenen"], s)


def test_beruehrende_textebenen_bleiben_text():
    s = seite_mod.pruefen(seite([text("a", 150), text("b", 250)]))  # a 100..200, b 200..300
    assert all(st.text_tauglich(e, s["ebenen"], s) for e in s["ebenen"])


def test_zwei_texte_nebeneinander_werden_bild():
    links = dict(text("l", 300, breite=250), x=130)
    rechts = dict(text("r", 320, breite=250), x=450)
    s = seite_mod.pruefen(seite([links, rechts]))
    assert not any(st.text_tauglich(e, s["ebenen"], s) for e in s["ebenen"])
    baender = st.plan(seite([links, rechts]))
    assert band_von(baender, "l")[0].art == "bild" and band_von(baender, "l") == band_von(baender, "r")


def test_text_und_knopf_nebeneinander_werden_bild():
    baender = st.plan(seite([dict(text("t", 300, breite=250), x=130), dict(knopf("k", 300), x=470)]))
    assert band_von(baender, "t")[0].art == "bild" and band_von(baender, "k")[0].art == "bild"


def test_gestapelte_beruehrende_texte_bleiben_text():
    baender = st.plan(seite([text("a", 150), text("b", 250), knopf("k", 324)]))  # 100..200, 200..300, 300..348
    assert set(arten(baender)) == {"text"}
    assert baender[0].ebenen == ["a", "b", "k"]


def test_ebene_ausserhalb_der_seite_stoert_text_nicht():
    s = seite_mod.pruefen(seite([dict(text("t", 300), x=300), bild("weg", 300, x=900, breite=100)]))
    assert st.text_tauglich(s["ebenen"][0], s["ebenen"], s)
    assert st.plan(s)[0].art == "text"


def test_textspalte_beruehrt_bild_nur_der_letzte_kasten_wird_bild():
    # Spalte 100..200, 200..300, 300..400 beruehrend; Bild 400..700 beruehrt nur den letzten Kasten.
    s = seite([text("a", 150), text("b", 250), text("c", 350), bild("foto", 550)])
    baender = st.plan(s)
    lueckenlos(baender, 1200)
    assert band_von(baender, "a")[0].art == "text" and band_von(baender, "b")[0].art == "text"
    c = band_von(baender, "c")
    assert len(c) == 1 and c[0].art == "bild" and c[0].ebenen == ["c", "foto"] and (c[0].oben, c[0].unten) == (300, 700)


def test_text_knapp_unter_einer_einheit_vor_bild_wird_bild():
    s = seite([text("t", 149.5), bild("foto", 350)])  # t 99.5..199.5, Bild 200..500
    baender = st.plan(s)
    lueckenlos(baender, 1200)
    b = band_von(baender, "t")
    assert len(b) == 1 and b[0].art == "bild" and (b[0].oben, b[0].unten) == (99.5, 500)
    s2 = seite([text("t", 148), bild("foto", 350)])  # 2 Einheiten Abstand: bleibt Text
    assert band_von(st.plan(s2), "t")[0].art == "text"


def test_gedrehter_text_15_grad():
    t15 = text("t", 400, drehung=15)
    l, o, r, u = st.rahmen(t15)
    baender = st.plan(seite([t15]))
    lueckenlos(baender, 1200)
    b = band_von(baender, "t")
    assert len(b) == 1 and b[0].art == "bild" and (b[0].oben, b[0].unten) == (o, u)
    assert u - o == pytest.approx(480 * math.sin(math.radians(15)) + 100 * math.cos(math.radians(15)))


def test_gedrehte_ueberschrift_bildband_ueber_ihrem_rahmen():
    titel = {"id": "titel", "art": "text", "x": 300, "y": 300, "breite": 400, "hoehe": 100,
             "text": "Groß", "rolle": "ANZEIGE", "groesse": 48, "farbe": "#111111", "drehung": 90}
    baender = st.plan(seite([titel, text("t", 800)]))
    lueckenlos(baender, 1200)
    b = band_von(baender, "titel")
    assert len(b) == 1 and b[0].art == "bild"
    assert (b[0].oben, b[0].unten) == (100, 500)  # gedreht: 100 breit, 400 hoch
    assert band_von(baender, "t")[0].art == "text"


def test_bild_unter_text_beides_bild():
    baender = st.plan(seite([bild("foto", 300), text("t", 300)]))
    lueckenlos(baender, 1200)
    b = band_von(baender, "foto")
    assert len(b) == 1 and b[0].art == "bild"
    assert b[0].ebenen == ["foto", "t"]
    assert not any(x.art == "text" and x.ebenen for x in baender)


def test_text_an_bild_wird_bild():
    # Fliesstext klemmt zwischen zwei Bildern und beruehrt beide: Teil EINES Bildbands, nicht doppelt.
    s = seite([bild("oben", 200, hoehe=200), text("mitte", 350, hoehe=100), bild("unten", 500, hoehe=200)])
    baender = st.plan(s)
    lueckenlos(baender, 1200)
    for eid in ("oben", "mitte", "unten"):
        assert all(b.art == "bild" for b in band_von(baender, eid))
    assert [b.ebenen for b in baender if b.art == "text" and b.ebenen] == []
    b = band_von(baender, "mitte")
    assert len(b) == 1 and (b[0].oben, b[0].unten) == (100, 600)


def test_text_neben_bild_in_gleicher_hoehe_wird_bild():
    s = seite([bild("foto", 300, breite=200, x=120), dict(text("t", 300, breite=250), x=450)])
    baender = st.plan(s)
    assert band_von(baender, "t")[0].art == "bild"
    assert band_von(baender, "t") == band_von(baender, "foto")


def test_text_ragt_in_bildband_und_zieht_es_auf():
    # Bild 150..450, Text 400..700 neben dem Bild (schneidet es nicht): Band waechst auf 150..700.
    s = seite([bild("foto", 300, breite=200, x=120), dict(text("t", 550, hoehe=300, breite=250), x=450)])
    baender = st.plan(s)
    b = band_von(baender, "foto")
    assert len(b) == 1 and (b[0].oben, b[0].unten) == (150, 700) and "t" in b[0].ebenen


def test_langes_bildband_wird_geteilt():
    baender = st.plan(seite([bild("riese", 800, hoehe=1400)], hoehe=1600))
    lueckenlos(baender, 1600)
    bb = [b for b in baender if b.art == "bild"]
    assert len(bb) == 3
    assert all(b.unten - b.oben <= 600 for b in bb)
    assert (bb[0].oben, bb[-1].unten) == (100, 1500)
    assert all(b.ebenen == ["riese"] for b in bb)


def test_teilung_schneidet_an_freien_grenzen():
    # Drei Bilder untereinander, je 400 hoch und lueckenlos: Schnitte an den Bildkanten, nicht mittendurch.
    s = seite([bild("a", 200, hoehe=400), bild("b", 600, hoehe=400), bild("c", 1000, hoehe=400)])
    baender = st.plan(s)
    bb = [b for b in baender if b.art == "bild"]
    assert [(b.oben, b.unten) for b in bb] == [(0, 400), (400, 800), (800, 1200)]
    assert [b.ebenen for b in bb] == [["a"], ["b"], ["c"]]


def test_hintergrundbild_alles_bild():
    s = seite([text("a", 150), knopf("k", 400)], hoehe=1300, bild="medien:hg.jpg")
    baender = st.plan(s)
    lueckenlos(baender, 1300)
    assert set(arten(baender)) == {"bild"}
    assert all(b.unten - b.oben <= 600 for b in baender)
    assert len(baender) == 3
    s2 = seite_mod.pruefen(s)
    assert not any(st.text_tauglich(e, s2["ebenen"], s2) for e in s2["ebenen"])


def test_luecken_lueckenlos_abgedeckt():
    s = seite([text("a", 150), bild("foto", 600, hoehe=200), knopf("k", 1000)], hoehe=1400)
    baender = st.plan(s)
    lueckenlos(baender, 1400)
    assert arten(baender) == ["text", "bild", "text"]
    assert baender[1].ebenen == ["foto"] and (baender[1].oben, baender[1].unten) == (500, 700)
    assert baender[0].ebenen == ["a"] and baender[2].ebenen == ["k"]


def test_leere_seite_ein_abstandsband():
    baender = st.plan(seite([], hoehe=800))
    assert [(b.art, b.oben, b.unten, b.ebenen) for b in baender] == [("text", 0, 800, [])]


def test_bild_ganz_oben_und_leerer_rand_als_abstand():
    baender = st.plan(seite([bild("foto", 150, hoehe=300)], hoehe=800))
    assert [(b.art, b.oben, b.unten, b.ebenen) for b in baender] == [
        ("bild", 0, 300, ["foto"]), ("text", 300, 800, [])]


def test_ebene_ueber_den_rand_wird_beschnitten():
    baender = st.plan(seite([bild("foto", 0, hoehe=300), bild("weg", 2000, hoehe=100)], hoehe=800))
    lueckenlos(baender, 800)
    assert baender[0].art == "bild" and (baender[0].oben, baender[0].unten) == (0, 150)
    assert not band_von(baender, "weg")


def test_plan_prueft_die_seite():
    with pytest.raises(seite_mod.SeiteFehler):
        st.plan({"version": 2})


def test_lesereihenfolge_nach_oben_dann_links():
    a = {"id": "a", "x": 400, "y": 100, "breite": 100, "hoehe": 50, "drehung": 0}
    b = {"id": "b", "x": 100, "y": 100, "breite": 100, "hoehe": 50, "drehung": 0}
    c = {"id": "c", "x": 50, "y": 20, "breite": 100, "hoehe": 20, "drehung": 0}
    assert [e["id"] for e in st.lesereihenfolge([a, b, c])] == ["c", "b", "a"]


def test_zufallsseiten_halten_die_zusagen():
    import random
    zufall = random.Random(4711)
    for _ in range(300):
        hoehe = zufall.randint(400, 3000)
        ebenen = []
        for nr in range(zufall.randint(0, 12)):
            art = zufall.choice(["text", "bild", "knopf", "form"])
            basis = {"id": f"e{nr}", "x": zufall.uniform(-50, 650), "y": zufall.uniform(-100, hoehe + 100),
                     "breite": zufall.uniform(10, 600), "hoehe": zufall.uniform(10, 900),
                     "drehung": zufall.choice([0, 0, 0, 15, 90])}
            if art == "text":
                basis.update(text=f"T{nr}", rolle=zufall.choice(["TEXT", "TEXT", "ANZEIGE"]),
                             groesse=17, farbe="#333333")
            elif art == "bild":
                basis.update(quelle="medien:foto.jpg")
            elif art == "knopf":
                basis.update(text="K", link="https://example.com", farbe="#5b3fd6", textfarbe="#ffffff")
            else:
                basis.update(form="rechteck", farbe="#eeeeee")
            ebenen.append({"art": art, **basis})
        s = seite(ebenen, hoehe=hoehe, bild=zufall.choice([None, None, None, "medien:hg.jpg"]))
        baender = st.plan(s)
        lueckenlos(baender, hoehe)
        assert all(b.unten - b.oben <= 600 for b in baender if b.art == "bild")
        im_text = {eid for b in baender if b.art == "text" for eid in b.ebenen}
        im_bild = {eid for b in baender if b.art == "bild" for eid in b.ebenen}
        assert not im_text & im_bild
        for b in baender:
            if b.art == "text":
                assert all(sum(eid in x.ebenen for x in baender) == 1 for eid in b.ebenen)
        geprueft = seite_mod.pruefen(s)
        sichtbar = {}
        for e in geprueft["ebenen"]:
            l, o, r, u = st.rahmen(e)
            if min(u, hoehe) > max(o, 0) and r > 0 and l < 600:
                sichtbar[e["id"]] = (max(o, 0), min(u, hoehe))
        assert set(sichtbar) == im_text | im_bild
        bildbaender = [(b.oben, b.unten) for b in baender if b.art == "bild"]
        quellen = [sichtbar[e["id"]] for e in geprueft["ebenen"] if e["id"] in sichtbar
                   and not st.text_tauglich(e, geprueft["ebenen"], geprueft)]
        for eid in im_text:
            o, u = sichtbar[eid]
            assert st.text_tauglich(next(e for e in geprueft["ebenen"] if e["id"] == eid), geprueft["ebenen"], geprueft)
            assert not any(bo < u and o < bu for bo, bu in bildbaender)
            assert all(max(qo - u, o - qu) >= st.NAH for qo, qu in quellen)


# ---- Gemessene Huellen (Fix nach der Live-Abnahme) ----------------------------------------------------------
def titel(eid="t", **mehr):
    return {"id": eid, "art": "text", "x": 330, "y": 340, "breite": 460, "hoehe": 110,
            "text": "Neue Kurse im Herbst", "rolle": "ANZEIGE", "groesse": 46, "gewicht": 600, "drehung": -4,
            "farbe": "#ffffff", **mehr}


def test_hoeherer_titel_verlaengert_das_bildband():
    s = seite([bild("b", 200, hoehe=400, breite=600), titel()], hoehe=800)
    ohne = st.plan(s)
    assert ohne[0].art == "bild" and ohne[0].unten == pytest.approx(410.91, abs=0.01)
    mit = st.plan(s, {"t": (96.4, 269.1, 563.6, 431.5)})
    lueckenlos(mit, 800)
    assert arten(mit) == ["bild", "text"]
    assert mit[0].unten == 431.5 and mit[0].ebenen == ["b", "t"]


def test_gemessene_huelle_nie_kleiner_als_deklariert():
    e = text("a", 200)
    assert st.huelle(e, {"a": (100, 180, 500, 220)}) == st.rahmen(e)   # kleiner gemessen: deklariert gilt
    assert st.huelle(e, {"a": (60, 150, 540, 290)}) == (60, 150, 540, 290)
    assert st.huelle(e, None) == st.rahmen(e) and st.huelle(e, {"x": (0, 0, 1, 1)}) == st.rahmen(e)


def test_gewachsener_text_ueberlappt_und_wird_bild():
    s = seite([text("a", 100), text("b", 260)], hoehe=600)
    assert arten(st.plan(s)) == ["text"]
    # a waechst gerendert bis 230 - in b hinein (b beginnt bei 210): beide nicht mehr text-tauglich
    mit = st.plan(s, {"a": (60, 50, 540, 230)})
    assert arten(mit) == ["text", "bild", "text"]
    assert (mit[1].oben, mit[1].unten) == (50, 310) and mit[1].ebenen == ["a", "b"]


def test_textband_grenzen_aus_messung():
    s = seite([text("a", 100), text("b", 400)], hoehe=600)
    mit = st.plan(s, {"a": (60, 50, 540, 180)})
    assert arten(mit) == ["text"] and mit[0].ebenen == ["a", "b"]


def test_masse_werden_geprueft():
    assert st.masse_pruefen({"a": (1, 2, 3, 4), "b": (1, 2), "c": (1, float("nan"), 3, 4), "d": (3, 0, 1, 4),
                             "e": ("1", 2, 3, 4), 5: (1, 2, 3, 4), "f": [0, 0, 1, 1]}) == \
        {"a": (1, 2, 3, 4), "f": (0, 0, 1, 1)}
    assert st.masse_pruefen(None) == {}

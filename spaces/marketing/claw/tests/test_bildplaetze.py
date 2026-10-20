from spaces.marketing.claw import bildplaetze as bp


def bild(url, w, h, pad=None, alt=""):
    style = {"padding": pad} if pad is not None else {}
    return {"type": "Image", "data": {"style": style, "props": {"url": url, "width": w, "height": h, "alt": alt}}}


def text(t):
    return {"type": "Text", "data": {"props": {"text": t}}}


def dok(kinder, **bloecke):
    return {"root": {"type": "EmailLayout", "data": {"childrenIds": kinder}}, **bloecke}


def test_leer_und_platzhaltername():
    assert bp.ist_leer(None) and bp.ist_leer("") and bp.ist_leer("medien:platzhalter-2x1.png")
    assert not bp.ist_leer("medien:nl-1234abcd-kopf.jpg")
    assert bp.platzhalter_name(600, 300) == "platzhalter-2x1.png"
    assert bp.platzhalter_name(268, 201) == "platzhalter-4x3.png"
    assert bp.platzhalter_name(552, 184) == "platzhalter-3x1.png"


def test_kopfbild_volle_breite_ohne_abstand():
    d = dok(["kopf", "t"], kopf=bild("medien:platzhalter-2x1.png", 600, 300, {"top": 0, "bottom": 0, "left": 0, "right": 0}, "Team"),
            t=text("Unser Herbst"))
    [p] = bp.finde(d)
    assert (p.id, p.anzeige_breite, p.anzeige_hoehe) == ("kopf", 600, 300)
    assert (p.erzeug_breite, p.erzeug_hoehe) == (1200, 608)     # 1200 ist 16er; 1200*300/600 = 600 -> 608
    assert p.verhaeltnis == "2:1" and p.leer and p.alt == "Team"
    assert "Unser Herbst" in p.kontext


def test_standardabstand_24_links_rechts_wie_der_renderer():
    [p] = bp.finde(dok(["b"], b=bild("", 552, 276)))
    assert p.anzeige_breite == 552


def test_zu_breit_wird_gekappt_verhaeltnis_bleibt():
    [p] = bp.finde(dok(["b"], b=bild("", 600, 300)))            # 24/24 Abstand -> 552 verfuegbar
    assert (p.anzeige_breite, p.anzeige_hoehe) == (552, 276)


def test_zwei_spalten_mit_luecke():
    spalten = {"type": "ColumnsContainer", "data": {"style": {"padding": {"top": 0, "bottom": 0, "left": 24, "right": 24}},
               "props": {"columnsCount": 2, "columnsGap": 16,
                         "columns": [{"childrenIds": ["a", "ta"]}, {"childrenIds": ["b"]}, {"childrenIds": []}]}}}
    nul = {"top": 0, "bottom": 0, "left": 0, "right": 0}
    d = dok(["s"], s=spalten, a=bild("", 600, 450, nul), b=bild("medien:eigen.jpg", 268, 201, nul), ta=text("Thema eins"))
    a, b = bp.finde(d)
    assert (a.anzeige_breite, a.anzeige_hoehe) == (268, 201)     # (600-48)/2 - 8 = 268
    assert a.verhaeltnis == "4:3" and "Thema eins" in a.kontext
    assert not b.leer


def test_drei_spalten():
    spalten = {"type": "ColumnsContainer", "data": {"style": {"padding": {"top": 0, "bottom": 0, "left": 24, "right": 24}},
               "props": {"columnsCount": 3, "columnsGap": 16,
                         "columns": [{"childrenIds": ["a"]}, {"childrenIds": ["b"]}, {"childrenIds": ["c"]}]}}}
    nul = {"top": 0, "bottom": 0, "left": 0, "right": 0}
    d = dok(["s"], s=spalten, a=bild("", 172, 172, nul), b=bild("", 172, 172, nul), c=bild("", 172, 172, nul))
    breiten = [p.anzeige_breite for p in bp.finde(d)]
    assert breiten == [172, 172, 172]                              # 184 - 10,67 bzw. 2*5,33 -> >= 172


def test_im_rahmen():
    rahmen = {"type": "Container", "data": {"style": {"padding": {"top": 16, "bottom": 16, "left": 16, "right": 16}},
              "props": {"childrenIds": ["i"]}}}
    [p] = bp.finde(dok(["r"], r=rahmen, i=bild("", 600, 300, {"top": 0, "bottom": 0, "left": 0, "right": 0})))
    assert p.anzeige_breite == 600 - 2 * 24 - 32                  # Kartenrand + Rahmenabstand


def test_kein_platz_ohne_hoehe_oder_breite_und_fremde_typen():
    d = dok(["a", "b", "t"], a=bild("", 600, 0), b={"type": "Image", "data": {"props": {"url": ""}}}, t=text("x"))
    assert bp.finde(d) == []


def test_kaputtes_dokument_wirft_nicht():
    assert bp.finde({}) == []
    assert bp.finde({"root": {"type": "EmailLayout", "data": {"childrenIds": ["weg"]}}}) == []


def test_erzeugmasse_sind_16er_und_gross_genug():
    for w, h in ((600, 300), (268, 201), (172, 172), (552, 184), (552, 311)):
        [p] = bp.finde(dok(["b"], b=bild("", w, h, {"top": 0, "bottom": 0, "left": 0, "right": 0})))
        assert p.erzeug_breite % 16 == 0 and p.erzeug_hoehe % 16 == 0
        assert p.erzeug_breite >= 2 * p.anzeige_breite - 15
        assert abs(p.erzeug_breite / p.erzeug_hoehe - w / h) < 0.05


def test_container_hintergrund_ist_platz():
    d = dok(["kopf"], kopf={"type": "Container", "data": {"style": {"backgroundColor": "#2f4858"},
            "props": {"url": "medien:platzhalter-2x1.png", "width": 600, "height": 300, "childrenIds": ["t"]}}},
            t=text("Titel"))
    plaetze = bp.finde(d)
    assert [p.id for p in plaetze] == ["kopf"]
    p = plaetze[0]
    assert (p.anzeige_breite, p.anzeige_hoehe, p.verhaeltnis, p.leer, p.flaeche) == (600, 300, "2:1", True, "#2f4858")
    assert "Titel" in p.kontext


def test_container_ohne_url_ist_kein_platz():
    d = dok(["k"], k={"type": "Container", "data": {"style": {}, "props": {"width": 600, "height": 300, "childrenIds": []}}})
    assert bp.finde(d) == []


def test_grafik_ist_kein_platz():
    d = dok(["g"], g={"type": "Image", "data": {"style": {}, "props": {"url": "medien:tech-signal-b5f750.png",
                                                                       "width": 536, "height": 380, "grafik": True}}})
    assert bp.finde(d) == []


def test_gestaltungsflaeche_ist_kein_platz():
    b = bild("medien:gs-aaaaaaaaaaaa.jpg", 600, 400)
    b["data"]["props"]["gestaltung"] = {"version": 1, "format": "quer", "hintergrund": "#FFFFFF", "ebenen": []}
    assert bp.finde(dok(["f"], f=b)) == []
    assert len(bp.finde(dok(["f"], f=bild("medien:x.png", 600, 400)))) == 1


# --- Editor, Entwuerfe, Bilder (Plan 2026-10-19) Task 3: Platzhalter statt Bild ---

def test_platzhalter_nach_seitenverhaeltnis():
    d = dok(["k"], k=bild("medien:a.jpg", 552, 311))
    neu, n = bp.platzhalter_einsetzen(d, "a.jpg")
    assert n == 1 and neu["k"]["data"]["props"]["url"] == "medien:platzhalter-16x9.png"


def test_platzhalter_ohne_masse_4x3():
    d = dok(["k"], k=bild("medien:a.jpg", 0, None))
    neu, n = bp.platzhalter_einsetzen(d, "a.jpg")
    assert n == 1 and neu["k"]["data"]["props"]["url"] == "medien:platzhalter-4x3.png"


def test_zwei_plaetze_und_container_in_einem_dokument():
    import json
    box = {"type": "Container", "data": {"style": {}, "props": {"url": "medien:a.jpg", "width": 600, "height": 300,
                                                                 "childrenIds": ["t"]}}}
    d = dok(["k", "box"], k=bild("medien:a.jpg", 600, 300), box=box, t=text("Hallo"))
    neu, n = bp.platzhalter_einsetzen(d, "a.jpg")
    assert n == 2
    assert neu["k"]["data"]["props"]["url"] == "medien:platzhalter-2x1.png"
    assert "url" not in neu["box"]["data"]["props"] and neu["box"]["data"]["props"]["childrenIds"] == ["t"]
    assert "medien:a.jpg" not in json.dumps(neu)


def test_aehnlicher_name_bleibt():
    d = dok(["k", "j"], k=bild("medien:aa.jpg", 600, 300), j=bild("medien:a.jpg.bak", 600, 300))
    neu, n = bp.platzhalter_einsetzen(d, "a.jpg")
    assert n == 0 and neu == d


def test_original_unveraendert():
    d = dok(["k"], k=bild("medien:a.jpg", 600, 300))
    vorher = repr(d)
    neu, _ = bp.platzhalter_einsetzen(d, "a.jpg")
    assert repr(d) == vorher and neu is not d and neu["k"] is not d["k"]

# ─── Collage (format 'seite', Stufe 3): Bildplaetze aus Bild-Ebenen ──────────────────────────────────

SEITE = {"version": 1, "breite": 600, "hoehe": 1200, "hintergrund": {"farbe": "#fdf6e3", "bild": None},
         "schriften": {"anzeige": "dm-sans", "text": "dm-sans"},
         "ebenen": [
             {"id": "flaeche", "art": "form", "form": "rechteck", "farbe": "#123456",
              "x": 300, "y": 200, "breite": 600, "hoehe": 400},
             {"id": "held", "art": "bild", "quelle": "medien:platzhalter-2x1.png",
              "x": 300, "y": 200, "breite": 600, "hoehe": 300},
             {"id": "titel", "art": "text", "text": "**Herbstfest**", "rolle": "ANZEIGE", "groesse": 48,
              "farbe": "#111111", "x": 300, "y": 420, "breite": 500, "hoehe": 80},
             {"id": "deko", "art": "bild", "quelle": "medien:eigen.jpg", "x": 450, "y": 900, "breite": 200,
              "hoehe": 200},
             {"id": "turm", "art": "bild", "quelle": "medien:eigen.jpg", "x": 300, "y": 3000, "breite": 600,
              "hoehe": 3000},
             {"id": "kaputt", "art": "bild", "quelle": "medien:x.jpg", "x": 1, "y": 1, "breite": 0, "hoehe": 10},
             {"id": "knopf", "art": "knopf", "text": "Anmelden", "link": "https://example.com", "farbe": "#b45309",
              "textfarbe": "#ffffff", "x": 300, "y": 1100, "breite": 200, "hoehe": 48},
         ]}


def test_finde_seite_bild_ebenen_als_plaetze():
    p = {x.id: x for x in bp.finde_seite(SEITE)}
    assert list(p) == ["held", "deko", "turm"]                      # Form/Text/Knopf und Mass 0 sind keine Plaetze
    held = p["held"]
    assert (held.anzeige_breite, held.anzeige_hoehe, held.erzeug_breite, held.erzeug_hoehe) == (600, 300, 1200, 608)
    assert held.verhaeltnis == "2:1" and held.leer is True and held.url == "medien:platzhalter-2x1.png"
    assert held.flaeche == "#123456"                               # Form darunter, nicht die Seitenfarbe
    assert held.kontext.split(" | ")[0] == "Herbstfest"            # naechster Text zuerst, ohne Markdown
    assert p["deko"].leer is False and p["deko"].flaeche == "#fdf6e3" and p["deko"].verhaeltnis == "1:1"
    assert (p["deko"].erzeug_breite, p["deko"].erzeug_hoehe) == (400, 400)


def test_finde_seite_kappt_die_lange_kante():
    turm = next(x for x in bp.finde_seite(SEITE) if x.id == "turm")
    assert max(turm.erzeug_breite, turm.erzeug_hoehe) <= bp.ERZEUG_SEITE_MAX
    assert turm.erzeug_breite % 16 == 0 and turm.erzeug_hoehe % 16 == 0
    assert (turm.anzeige_breite, turm.anzeige_hoehe) == (600, 3000)


def test_finde_seite_ohne_seite_leer():
    assert bp.finde_seite(None) == [] and bp.finde_seite({}) == [] and bp.finde_seite({"ebenen": "x"}) == []


def test_finde_seite_mindestkante_64():
    """Fix-Runde 1: die Upload-Route nimmt nur Kanten >= 64 (KANTE_MIN) - flache Ebenen werden angehoben."""
    s = {"hintergrund": {"farbe": "#ffffff"}, "ebenen": [
        {"id": "streifen", "art": "bild", "quelle": "medien:x.jpg", "x": 300, "y": 100, "breite": 1200, "hoehe": 10},
        {"id": "punkt", "art": "bild", "quelle": "medien:x.jpg", "x": 300, "y": 300, "breite": 10, "hoehe": 10}]}
    streifen, punkt = bp.finde_seite(s)
    assert (streifen.anzeige_breite, streifen.anzeige_hoehe) == (1200, 10)
    assert streifen.erzeug_breite == bp.ERZEUG_SEITE_MAX and streifen.erzeug_hoehe == 64
    assert (punkt.erzeug_breite, punkt.erzeug_hoehe) == (64, 64)
    assert bp.ERZEUG_KANTE_MIN == 64 and bp.ERZEUG_KANTE_MIN % 16 == 0

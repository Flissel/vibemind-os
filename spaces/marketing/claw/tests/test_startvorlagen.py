"""Die sieben Startvorlagen (Spec 2026-10-01-newsletter-vorlagen-profi): vollstaendig, im erlaubten Format, rendern fehlerfrei.

`_fehler` spiegelt marketing.pult_bloecke_fehler (db/053 + 054) so weit, dass
eine Vorlage, die hier gruen ist, auch in der DB gueltig ist. Die echte
Pruefung macht `python -m spaces.marketing.scripts.vorlagen_einspielen`
(ohne --wirklich) gegen die DB."""
import json
import pathlib
import re

import pytest

from spaces.marketing.claw import bloecke_mjml, schoenheit, vorlagen_marke

ORDNER = pathlib.Path(__file__).resolve().parents[2] / "vorlagen" / "newsletter"
NAMEN = ["studio", "zeitung", "firmenblatt", "minimal", "klassik", "bildkopf", "tech"]
ERLAUBT = {"EmailLayout", "Heading", "Text", "Button", "Image", "Divider", "Spacer", "Container", "ColumnsContainer"}
SCHRIFTEN = {"MODERN_SANS", "BOOK_SANS", "ORGANIC_SANS", "GEOMETRIC_SANS", "HEAVY_SANS", "ROUNDED_SANS",
             "MODERN_SERIF", "BOOK_SERIF", "MONOSPACE", "ANZEIGE", "TEXT"}
FARBEN = ("color", "backgroundColor", "backdropColor", "canvasColor", "textColor",
          "buttonBackgroundColor", "buttonTextColor", "lineColor", "borderColor")
WAHL = {"textAlign": {"left", "center", "right"}, "fontWeight": {"bold", "normal"}}
PROPS_WAHL = {"buttonStyle": {"rectangle", "pill", "rounded"}, "size": {"x-small", "small", "medium", "large"},
              "contentAlignment": {"top", "middle", "bottom"}}
BILD = re.compile(r"^medien:[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.(png|jpe?g|gif|webp)$")


def _zahl_ok(v, lo, hi):
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi)


def _kinder(b):
    data = b.get("data") or {}
    props = data.get("props") or {}
    ids = list(data.get("childrenIds") or props.get("childrenIds") or [])
    for c in props.get("columns") or []:
        ids += c.get("childrenIds") or []
    return ids


def _fehler(d: dict) -> str | None:
    """Python-Spiegel von marketing.pult_bloecke_fehler (054 + 055)."""
    if not isinstance(d, dict) or (d.get("root") or {}).get("type") != "EmailLayout":
        return "Wurzel"
    if len(json.dumps(d, ensure_ascii=False).encode()) > 262144 or len(d) > 151:
        return "Groesse"
    for bid, b in d.items():
        typ = b.get("type")
        if typ not in ERLAUBT:
            return f"{bid}: Typ {typ}"
        if bid != "root" and (typ == "EmailLayout" or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", bid)):
            return f"{bid}: ID/Wurzel"
        data = b.get("data") or {}
        props = data.get("props") if "props" in data else data
        props = props or {}
        style = data.get("style") or {}
        for quelle in (style, props, data):
            for k in FARBEN:
                if quelle.get(k) is not None and not re.fullmatch(r"#[0-9a-fA-F]{6}", str(quelle[k])):
                    return f"{bid}: Farbe {k}"
        if not (_zahl_ok(style.get("fontSize"), 8, 72) and _zahl_ok(style.get("borderRadius"), 0, 32)
                and _zahl_ok(props.get("width"), 1, 600) and _zahl_ok(props.get("height"), 0, 600)
                and _zahl_ok(props.get("lineHeight"), 1, 10) and _zahl_ok(props.get("columnsGap"), 0, 48)):
            return f"{bid}: Zahl"
        if not (_zahl_ok(style.get("letterSpacing"), -2, 8) and _zahl_ok(style.get("lineHeight"), 0.9, 2.0)):
            return f"{bid}: Feinheiten"
        if style.get("textTransform") not in (None, "none", "uppercase"):
            return f"{bid}: Versalien"
        ov = style.get("overlay")
        if ov is not None and (not isinstance(ov, dict) or not re.fullmatch(r"#[0-9a-fA-F]{6}", str(ov.get("farbe")))
                               or isinstance(ov.get("deckkraft"), bool) or not isinstance(ov.get("deckkraft"), (int, float))
                               or not _zahl_ok(ov.get("deckkraft"), 0, 100)):
            return f"{bid}: Farbfeld"
        for k in ("sw", "grafik"):
            if props.get(k) is not None and not isinstance(props.get(k), bool):
                return f"{bid}: {k}"
        if bid == "root":
            sw = (b.get("data") or {}).get("schriften")
            ids = {"cormorant", "dm-sans", "playfair", "poppins", "young-serif", "manrope", "bodoni",
                   "montserrat", "josefin", "oxanium", "rajdhani"}
            if sw is not None and (not isinstance(sw, dict) or not set(sw) <= {"anzeige", "text"}
                                   or not all(v in ids for v in sw.values())):
                return "root: Schriften"
        pad = style.get("padding")
        if pad is not None and (not isinstance(pad, dict) or not all(_zahl_ok(v, 0, 80) for v in pad.values())):
            return f"{bid}: Abstand"
        for quelle in (style, props, data):
            if quelle.get("fontFamily") is not None and quelle["fontFamily"] not in SCHRIFTEN:
                return f"{bid}: Schrift"
        for k, erlaubt in WAHL.items():
            if style.get(k) is not None and style[k] not in erlaubt:
                return f"{bid}: {k}"
        for k, erlaubt in PROPS_WAHL.items():
            if props.get(k) is not None and props[k] not in erlaubt:
                return f"{bid}: {k}"
        if typ == "Heading" and props.get("level") not in (None, "h1", "h2", "h3"):
            return f"{bid}: Ebene"
        if typ == "ColumnsContainer":
            if props.get("columnsCount") not in (None, 2, 3) or len(props.get("columns") or []) > 3:
                return f"{bid}: Spalten"
        if typ == "Button" and not str(props.get("url") or "").strip():
            return f"{bid}: Knopf ohne Link"
        for k in ("url", "linkHref"):
            v = props.get(k)
            if not v:
                continue
            if typ in ("Image", "Container") and k == "url":
                if not BILD.match(v) or ".." in v:
                    return f"{bid}: Bild"
            elif not re.fullmatch(r"https://[^\s\"<>]+", v):
                return f"{bid}: Link"
        if typ == "Text":
            t = props.get("text") or ""
            if re.search(r"\]\((?!https://)", t):
                return f"{bid}: Textlink"
            if props.get("markdown") is True:
                if "<" in t or "![" in t or re.search(r"(^|\n)[ \t]*\[[^\]]+\]:", t) \
                        or re.search(r"http://", t, re.I) or re.search(r"(^|[^/A-Za-z0-9.-])www\.", t, re.I):
                    return f"{bid}: Markdown"
    # Baum: jeder Block genau einmal erreichbar, Tiefe <= 4
    gesehen, front, tiefe = [], ["root"], 0
    while front:
        nxt = []
        for bid in front:
            if bid not in d:
                return f"fehlt {bid}"
            if bid in gesehen:
                return f"mehrfach {bid}"
            gesehen.append(bid)
            kinder = _kinder(d[bid])
            if bid != "root" and any((d.get(k) or {}).get("type") in ("Container", "ColumnsContainer")
                                     for k in kinder):
                return "Rahmen und Spalten nur auf oberster Ebene"
            nxt += kinder
        front = nxt
        if front:
            tiefe += 1
        if tiefe > 4:
            return "Tiefe"
    if len(gesehen) != len(d):
        return "nicht eingebunden"
    return None


def _laden(name):
    return json.loads((ORDNER / f"{name}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", NAMEN)
def test_vorlage_gueltig_und_rendert(name):
    v = _laden(name)
    assert v["name"] == name and re.fullmatch(r"[a-z][a-z0-9-]{1,40}", name) and v["beschreibung"]
    d = v["bloecke"]
    assert d["root"]["type"] == "EmailLayout"
    for bid, b in d.items():
        assert b["type"] in ERLAUBT, (bid, b["type"])
        props = (b.get("data") or {}).get("props") or {}
        if b["type"] == "Image" or (b["type"] == "Container" and props.get("url")):
            assert re.match(r"^medien:[A-Za-z0-9._-]+$", props["url"])
        for k in ("url", "linkHref"):
            if b["type"] not in ("Image", "Container") and props.get(k):
                assert props[k].startswith("https://")
    assert _fehler(d) is None, _fehler(d)
    html = bloecke_mjml.rendern(d, v["beschreibung"], "", {"impressum": "I", "abmelde_hinweis": ""},
                                bild_basis="https://h.ts.net/marketing/bild/t/")
    assert html.lower().startswith("<!doctype html")
    (pathlib.Path(__file__).parent / "_ausgabe").mkdir(exist_ok=True)
    (pathlib.Path(__file__).parent / "_ausgabe" / f"{name}.html").write_text(html, encoding="utf-8")


RAND = 32  # gemeinsame Textlinie aller Vorlagen (Spec 2026-10-01 §3.1: Raender 32 px)
TEXTTRAEGER = ("Heading", "Text", "Button", "Divider")


def _links(b):
    return ((b.get("data") or {}).get("style") or {}).get("padding", {}).get("left", 24)


def _rechts(b):
    return ((b.get("data") or {}).get("style") or {}).get("padding", {}).get("right", 24)


@pytest.mark.parametrize("name", NAMEN)
def test_textkante_einheitlich(name):
    """Alles, was Text traegt, beginnt auf derselben Linie (32 px). Spalten: Abschnittsrand plus
    Blockrand der aeusseren Spalten = RAND. Bild-Abschnitte (Container mit url): Farbtafel an RAND,
    Text darin mit Innenabstand.
    Karten: der Uebersetzer rueckt sie um KARTEN_RAND ein; ihr Inhalt darf weiter innen liegen."""
    d = _laden(name)["bloecke"]
    for bid in d["root"]["data"]["childrenIds"]:
        b = d[bid]
        props = b["data"].get("props") or {}
        if b["type"] == "ColumnsContainer":
            n = props["columnsCount"]
            erste = [k for k in props["columns"][0]["childrenIds"] if d[k]["type"] in TEXTTRAEGER]
            letzte = [k for k in props["columns"][n - 1]["childrenIds"] if d[k]["type"] in TEXTTRAEGER]
            for k in erste:
                assert _links(b) + _links(d[k]) == RAND, (bid, k)
            for k in letzte:
                assert _rechts(b) + _rechts(d[k]) == RAND, (bid, k)
        elif b["type"] == "Container" and props.get("url"):
            for k in _kinder(b):
                if d[k]["type"] in TEXTTRAEGER:
                    assert _links(b) == RAND and _links(b) + _links(d[k]) >= RAND, (bid, k)
        elif b["type"] == "Container":
            for k in _kinder(b):
                if d[k]["type"] in TEXTTRAEGER:
                    assert bloecke_mjml.KARTEN_RAND + _links(b) + _links(d[k]) >= RAND, (bid, k)
        elif b["type"] in TEXTTRAEGER:
            assert (_links(b), _rechts(b)) == (RAND, RAND), bid


@pytest.mark.parametrize("name", NAMEN)
def test_keine_echt_wirkenden_beispielwerte(name):
    """Beispielwerte stehen in [Klammern], damit nichts Echtes versehentlich rausgeht."""
    roh = (ORDNER / f"{name}.json").read_text(encoding="utf-8")
    assert not re.search(r"(?<![#\w])(19|20)\d\d\b|\d{1,2}:\d\d|(?<![#\w])\d{4,}\b|stra(ss|ß)e|gasse\b|"
                         r"\b(Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\b|"
                         r"[\w.-]+@[\w-]+\.[a-z]{2,}|vibemind|https://(?!www\.example\.de)",
                         roh, re.I)


@pytest.mark.parametrize("kaputt", [
    {"x": {"type": "Text", "data": {"props": {"text": "<b>x</b>", "markdown": True}}}},
    {"x": {"type": "Text", "data": {"props": {"text": "siehe www.example.de", "markdown": True}}}},
    {"x": {"type": "Text", "data": {"props": {"text": "[a](http://x.de)"}}}},
    {"x": {"type": "Image", "data": {"props": {"url": "medien:logo.svg"}}}},
    {"x": {"type": "Heading", "data": {"style": {"fontFamily": "Arial"}, "props": {"text": "t"}}}},
    {"x": {"type": "Text", "data": {"style": {"padding": {"top": 81}}, "props": {"text": "t"}}}},
    {"x": {"type": "Divider", "data": {"props": {"lineColor": "#fff"}}}},
    {"x": {"type": "ColumnsContainer", "data": {"props": {"columnsCount": 4, "columns": []}}}},
    {"x": {"type": "Button", "data": {"props": {"text": "Los", "url": ""}}}},
    {"x": {"type": "Button", "data": {"props": {"text": "Los"}}}},
    {"x": {"type": "Container", "data": {"props": {"childrenIds": ["y"]}}},
     "y": {"type": "Container", "data": {"props": {"childrenIds": []}}}},
])
def test_spiegel_der_db_pruefung_greift(kaputt):
    d = {"root": {"type": "EmailLayout", "data": {"childrenIds": [next(iter(kaputt))]}}, **kaputt}
    assert _fehler(d) is not None


@pytest.mark.parametrize("name", NAMEN)
def test_spalten_haben_genau_drei_eintraege(name):
    """Das Editor-Schema (ColumnsContainerPropsSchema) verlangt genau 3 columns-Eintraege,
    auch bei columnsCount 2 - sonst oeffnet der Editor den Block nicht (final-fix I4)."""
    for bid, b in _laden(name)["bloecke"].items():
        if b["type"] == "ColumnsContainer":
            props = b["data"]["props"]
            assert len(props["columns"]) == 3, bid
            assert props["columnsCount"] in (2, 3), bid
            for c in props["columns"][props["columnsCount"]:]:
                assert c == {"childrenIds": []}, bid


from spaces.marketing.claw import bildplaetze

PLAETZE_SOLL = {"studio": ["1:1", "4:3", "4:3", "4:3"], "zeitung": ["4:3", "4:3"],
                "firmenblatt": ["2:1", "4:3"], "minimal": ["1:1", "2:1"], "klassik": ["1:1", "1:1", "1:1"],
                "bildkopf": ["2:1", "1:1", "4:3"], "tech": []}
NUR_DIESE = {"platzhalter-16x9.png", "platzhalter-1x1.png", "platzhalter-2x1.png", "platzhalter-3x1.png",
             "platzhalter-4x3.png"}
PLATZHALTER_ORDNER = ORDNER / "platzhalter"


@pytest.mark.parametrize("name", NAMEN)
def test_vorlage_hat_die_bildplaetze_der_spec(name):
    v = json.loads((ORDNER / f"{name}.json").read_text(encoding="utf-8"))
    plaetze = bildplaetze.finde(v["bloecke"])
    assert [p.verhaeltnis for p in plaetze] == PLAETZE_SOLL[name]
    assert all(p.leer for p in plaetze)
    for p in plaetze:
        datei = PLATZHALTER_ORDNER / p.url[len("medien:"):]
        assert datei.is_file() and datei.name in NUR_DIESE, f"{name}: Platzhalter {datei.name} fehlt"
        assert p.alt, f"{name}/{p.id}: Alternativtext fehlt (Hinweis fuer den Agenten)"


def test_platzhalter_sind_klein_und_im_verhaeltnis():
    from PIL import Image
    for datei in PLATZHALTER_ORDNER.glob("platzhalter-*.png"):
        a, b = (int(x) for x in datei.stem.split("-")[1].split("x"))
        with Image.open(datei) as bild:
            assert abs(bild.width / bild.height - a / b) < 0.02
        assert datei.stat().st_size < 150 * 1024


@pytest.mark.parametrize("kaputt", [
    {"style": {"letterSpacing": 9}}, {"style": {"textTransform": "lowercase"}},
    {"style": {"lineHeight": 3}}, {"style": {"overlay": {"farbe": "rot", "deckkraft": 50}}},
    {"style": {"fontFamily": "COMIC"}}, {"props": {"sw": "ja"}}])
def test_neue_felder_werden_geprueft(kaputt):
    d = {"root": {"type": "EmailLayout", "data": {"childrenIds": ["h"]}},
         "h": {"type": "Heading", "data": {"style": kaputt.get("style", {}), "props": {"text": "x", **kaputt.get("props", {})}}}}
    assert _fehler(d)


def test_container_mit_medien_hintergrund_gueltig():
    d = {"root": {"type": "EmailLayout", "data": {"childrenIds": ["k"], "schriften": {"anzeige": "josefin", "text": "josefin"}}},
         "k": {"type": "Container", "data": {"style": {"overlay": {"farbe": "#2f4858", "deckkraft": 80}},
                                              "props": {"url": "medien:platzhalter-2x1.png", "width": 600, "height": 300,
                                                        "childrenIds": []}}}}
    assert _fehler(d) is None


def _wurzel(**data):
    return {"root": {"type": "EmailLayout", "data": {"childrenIds": [], **data}}}


@pytest.mark.parametrize("schriften", ["x", [], {"anzeige": None}, {"farbe": "poppins"}])
def test_schriften_falsch_wird_abgelehnt(schriften):
    assert _fehler(_wurzel(schriften=schriften))


def test_schriften_null_ist_erlaubt():
    assert _fehler(_wurzel(schriften=None)) is None


def test_grafik_kein_bool_wird_abgelehnt():
    d = {"root": {"type": "EmailLayout", "data": {"childrenIds": ["i"]}},
         "i": {"type": "Image", "data": {"props": {"url": "medien:a.png", "grafik": "ja"}}}}
    assert _fehler(d)


def test_farbfeld_ohne_deckkraft_wird_abgelehnt():
    d = {"root": {"type": "EmailLayout", "data": {"childrenIds": ["h"]}},
         "h": {"type": "Heading", "data": {"style": {"overlay": {"farbe": "#2f4858"}}, "props": {"text": "x"}}}}
    assert _fehler(d)


def _texte(dok):
    """Alle Zeichenketten (Blatt-Werte) eines Dokuments ausser der Rollen-Tabelle."""
    def gehen(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k != "rollen":
                    yield from gehen(v)
        elif isinstance(x, list):
            for v in x:
                yield from gehen(v)
        elif isinstance(x, str):
            yield x
    return list(gehen(dok))


PALETTEN = [{"akzent": "#facc15"}, {"akzent": "#111111", "flaeche": "#111111"},
            {"akzent": "#c2410c", "flaeche": "#2f4858"}, {"akzent": "#9ca3af"}]
SCHRIFTPAAR = {"studio": ("cormorant", "dm-sans"), "zeitung": ("playfair", "poppins"),
               "firmenblatt": ("young-serif", "poppins"), "minimal": ("manrope", "manrope"),
               "klassik": ("bodoni", "montserrat"), "bildkopf": ("josefin", "josefin"), "tech": ("oxanium", "rajdhani")}


def test_alle_sieben_da():
    assert sorted(p.stem for p in ORDNER.glob("*.json")) == sorted(NAMEN)


@pytest.mark.parametrize("name", NAMEN)
def test_schriftpaar_und_marke(name):
    d = _laden(name)["bloecke"]
    w = d["root"]["data"]
    assert (w["schriften"]["anzeige"], w["schriften"]["text"]) == SCHRIFTPAAR[name]
    assert "marke_logo" in d and "marke_wort" in d
    assert w["rollen"]["marke_logo/data/props/url"] == "logo" and w["rollen"]["marke_wort/data/props/text"] == "laden"


@pytest.mark.parametrize("name", NAMEN)
def test_rollen_zeigen_auf_vorhandene_felder(name):
    """Jede Rolle trifft einen Block und ein Feld, das die Vorlage schon mit einem Musterwert traegt."""
    d = _laden(name)["bloecke"]
    for pfad in d["root"]["data"]["rollen"]:
        teile = pfad.split("/")
        knoten = d[teile[0]]
        for t in teile[1:]:
            assert isinstance(knoten, dict) and t in knoten, pfad
            knoten = knoten[t]


@pytest.mark.parametrize("name", NAMEN)
@pytest.mark.parametrize("gestalt", PALETTEN)
@pytest.mark.parametrize("logo", [False, True])
def test_jede_palette_gueltig_lesbar_und_klein(name, gestalt, logo):
    d = _laden(name)["bloecke"]
    grund = d["root"]["data"].get("canvasColor") or "#ffffff"
    werte = {**vorlagen_marke.rollen(gestalt, grund), "laden": "Radhaus Jena",
             "signal_bild": "medien:tech-signal-aaaaaa.png", "glow_bild": "medien:tech-glow-aaaaaa.jpg"}
    if logo:
        werte["logo"] = "medien:logo-radhaus-0123456789.png"
    fertig = vorlagen_marke.einsetzen(d, werte)
    assert _fehler(fertig) is None
    assert schoenheit.bloecke_pruefen(fertig) == []
    html = bloecke_mjml.rendern(fertig, "Betreff", "Vorschau", {"impressum": "Radhaus Jena, Wagnergasse 5",
                                "abmelde_hinweis": "Abmelden: {abmeldelink}"},
                                bild_basis="https://ui.example.de/marketing/bild/1.a/",
                                schrift_basis="https://ui.example.de/marketing/schrift/")
    assert len(html.encode("utf-8")) < 102 * 1024
    # Brief-Fassung `"{" not in "".join(str(v) ...)` ist immer falsch (str(dict) beginnt mit "{");
    # gemeint ist: keine Rolle bleibt als {…} in einem Wert stehen.
    assert not [t for t in _texte({k: v for k, v in fertig.items() if k != "root"}) if "{" in t]
    assert ("marke_logo" in fertig) is logo and ("marke_wort" in fertig) is not logo


@pytest.mark.parametrize("name", NAMEN)
def test_mindestens_ein_leerer_bildplatz(name):
    assert any(p.leer for p in bildplaetze.finde(_laden(name)["bloecke"])) or name == "tech"


def test_tech_traegt_grafikrollen():
    w = _laden("tech")["bloecke"]["root"]["data"]
    assert {"signal_bild", "glow_bild"} <= set(w["rollen"].values()) and w.get("dunkel") is True


@pytest.mark.parametrize("name", NAMEN)
def test_genau_ein_knopf_in_ladenfarbe(name):
    d = _laden(name)["bloecke"]
    knoepfe = [bid for bid, b in d.items() if b["type"] == "Button"]
    assert len(knoepfe) == 1
    r = d["root"]["data"]["rollen"]
    assert r[f"{knoepfe[0]}/data/props/buttonBackgroundColor"] == "akzent"
    assert r[f"{knoepfe[0]}/data/props/buttonTextColor"] == "auf_akzent"

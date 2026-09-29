"""Die fuenf Startvorlagen: vollstaendig, im erlaubten Format, rendern fehlerfrei.

`_fehler` spiegelt marketing.pult_bloecke_fehler (db/053 + 054) so weit, dass
eine Vorlage, die hier gruen ist, auch in der DB gueltig ist. Die echte
Pruefung macht `python -m spaces.marketing.scripts.vorlagen_einspielen`
(ohne --wirklich) gegen die DB."""
import json
import pathlib
import re

import pytest

from spaces.marketing.claw import bloecke_mjml

ORDNER = pathlib.Path(__file__).resolve().parents[2] / "vorlagen" / "newsletter"
NAMEN = ["newsletter", "ankuendigung", "einladung", "produkt-neuheit", "kurzer-hinweis"]
ERLAUBT = {"EmailLayout", "Heading", "Text", "Button", "Image", "Divider", "Spacer", "Container", "ColumnsContainer"}
SCHRIFTEN = {"MODERN_SANS", "BOOK_SANS", "ORGANIC_SANS", "GEOMETRIC_SANS", "HEAVY_SANS", "ROUNDED_SANS",
             "MODERN_SERIF", "BOOK_SERIF", "MONOSPACE"}
FARBEN = ("color", "backgroundColor", "backdropColor", "canvasColor", "textColor",
          "buttonBackgroundColor", "buttonTextColor", "lineColor", "borderColor")
WAHL = {"textAlign": {"left", "center", "right"}, "fontWeight": {"bold", "normal"}}
PROPS_WAHL = {"buttonStyle": {"rectangle", "pill", "rounded"}, "size": {"x-small", "small", "medium", "large"},
              "contentAlignment": {"top", "middle", "bottom"}}
BILD = re.compile(r"^medien:[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.(png|jpe?g|gif|webp)$")
LAYOUT_DUNKEL = {"backdropColor": "#1d3b39", "canvasColor": "#0f2422", "textColor": "#cfe3df"}


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
            if typ == "Image" and k == "url":
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
        if b["type"] == "Image":
            assert re.match(r"^medien:[A-Za-z0-9._-]+$", props["url"])
        for k in ("url", "linkHref"):
            if b["type"] != "Image" and props.get(k):
                assert props[k].startswith("https://")
    assert _fehler(d) is None, _fehler(d)
    html = bloecke_mjml.rendern(d, v["beschreibung"], "", {"impressum": "I", "abmelde_hinweis": ""},
                                bild_basis="https://h.ts.net/marketing/bild/t/")
    assert html.lower().startswith("<!doctype html")
    (pathlib.Path(__file__).parent / "_ausgabe").mkdir(exist_ok=True)
    (pathlib.Path(__file__).parent / "_ausgabe" / f"{name}.html").write_text(html, encoding="utf-8")


@pytest.mark.parametrize("name", NAMEN)
def test_vorlage_im_vibemind_stil(name):
    """Farbbedeutung (binding): Aussenflaeche, Inhaltsflaeche, Text; Logo; hoechstens ein Knopf in Akzent."""
    d = _laden(name)["bloecke"]
    wurzel = d["root"]["data"]
    assert {k: wurzel[k] for k in LAYOUT_DUNKEL} == LAYOUT_DUNKEL
    assert any(b["type"] == "Image" and b["data"]["props"]["url"] == "medien:vibemind-logo.png" for b in d.values())
    for b in d.values():
        if b["type"] == "Heading":
            assert b["data"]["style"]["color"] == "#e9fbf6"
    knoepfe = [b["data"]["props"] for b in d.values() if b["type"] == "Button"]
    assert len(knoepfe) <= 1
    for k in knoepfe:
        assert (k["buttonBackgroundColor"], k["buttonTextColor"]) == ("#5eead4", "#0f2422")
    assert len(knoepfe) == (0 if name == "kurzer-hinweis" else 1)


RAND = 40  # gemeinsame Textlinie aller Vorlagen


def _links(b):
    return ((b.get("data") or {}).get("style") or {}).get("padding", {}).get("left", 24)


def _rechts(b):
    return ((b.get("data") or {}).get("style") or {}).get("padding", {}).get("right", 24)


@pytest.mark.parametrize("name", NAMEN)
def test_textkante_einheitlich(name):
    """Alles, was Text traegt, beginnt auf derselben Linie - auch in Spalten und Karten.
    Spalten: Rand am ColumnsContainer, Bloecke darin 0, Abstand ueber columnsGap.
    Karten: der Uebersetzer rueckt Container um KARTEN_RAND ein, der Innenabstand gleicht aus."""
    d = _laden(name)["bloecke"]
    for bid in d["root"]["data"]["childrenIds"]:
        b = d[bid]
        if b["type"] in ("Image", "Spacer"):
            continue
        if b["type"] == "ColumnsContainer":
            assert (_links(b), _rechts(b)) == (RAND, RAND), bid
            assert b["data"]["props"]["columnsGap"] > 0, bid
            for kid in _kinder(b):
                assert (_links(d[kid]), _rechts(d[kid])) == (0, 0), kid
        elif b["type"] == "Container":
            assert bloecke_mjml.KARTEN_RAND + _links(b) == RAND, bid
            assert bloecke_mjml.KARTEN_RAND + _rechts(b) == RAND, bid
            for kid in _kinder(b):
                assert (_links(d[kid]), _rechts(d[kid])) == (0, 0), kid
        else:
            assert (_links(b), _rechts(b)) == (RAND, RAND), bid


@pytest.mark.parametrize("name", NAMEN)
def test_keine_echt_wirkenden_beispielwerte(name):
    """Beispielwerte stehen in [Klammern], damit nichts Echtes versehentlich rausgeht."""
    roh = (ORDNER / f"{name}.json").read_text(encoding="utf-8")
    assert not re.search(r"\b(19|20)\d\d\b|\d{1,2}:\d\d|\b\d{5}\b|stra(ss|ß)e|"
                         r"\b(Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\b",
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


def test_alle_fuenf_da():
    assert sorted(p.stem for p in ORDNER.glob("*.json")) == sorted(NAMEN)


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

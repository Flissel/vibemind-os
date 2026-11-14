"""Python-Nachbau von readKonzept/checkVideoKonzept (deploy/headless/auftraege/konzept_lesen.py).

Massstab sind die gemeinsamen Pruefvektoren ``konzept_vektoren.json`` (Kopie aus dem Fork,
``apps/x/packages/shared/src/__fixtures__/``; dort autoritativ, hier unveraendert).
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import random
import time
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
ORDNER = TESTS.parent / "deploy" / "headless" / "auftraege"
VEKTOREN = TESTS / "konzept_vektoren.json"

_spec = importlib.util.spec_from_file_location("auftraege_konzept_lesen", ORDNER / "konzept_lesen.py")
kl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kl)

DATEI = json.loads(VEKTOREN.read_text(encoding="utf-8"))
FAELLE = DATEI["faelle"]

# Orte der autoritativen Datei im Fork: die mitgelieferte Kopie des Forks in diesem Repo (sobald der Pin sie
# enthaelt), eine per Umgebung genannte Datei und der Arbeitsbaum des Forks auf dem Entwickler-PC.
_FORK_REL = Path("apps/x/packages/shared/src/__fixtures__/konzept_vektoren.json")
FORK_ORTE = [p for p in (TESTS.parent / "rowboat" / _FORK_REL,
                         Path(os.environ["KONZEPT_VEKTOREN_FORK"]) if os.environ.get("KONZEPT_VEKTOREN_FORK") else None,
                         Path("C:/Users/User/ClaudeWork/wt-konzept-rowboat") / _FORK_REL) if p is not None]


def test_kopie_gleicht_der_datei_im_fork():
    vorhanden = [p for p in FORK_ORTE if p.is_file()]
    if not vorhanden:
        pytest.skip("keine Fork-Kopie von konzept_vektoren.json gefunden (gesucht: "
                    + ", ".join(str(p) for p in FORK_ORTE) + "); KONZEPT_VEKTOREN_FORK setzen")
    for ort in vorhanden:
        assert json.loads(ort.read_text(encoding="utf-8")) == DATEI, f"Kopie weicht vom Fork ab: {ort}"


def test_vektoren_vollstaendig():
    assert sorted(DATEI) == ["_hinweis", "faelle"]
    assert len(FAELLE) == 236
    namen = {f["name"] for f in FAELLE}
    assert len(namen) == len(FAELLE)
    for muss in (
        "gueltig_8_szenen", "gueltig_15_szenen", "ungueltig_7_szenen", "ungueltig_16_szenen", "ungueltig_minuten_6",
        "ungueltig_9_stufen_9_szenen", "ungueltig_kurz_301_zeichen", "ungueltig_bildart_unbekannt",
        "ungueltig_block_20001_zeichen", "gueltig_zwei_bloecke_der_erste_gilt", "ungueltig_kaputtes_json_abgeschnitten",
        "ungueltig_art_konzept_ohne_video", "gueltig_auftrag_8_hex", "ungueltig_auftrag_xyz",
    ):
        assert muss in namen, muss


def _streng_gleich(a, b) -> bool:
    """Gleichheit wie JSON: true ist nicht 1, 4 und 4.0 sind dieselbe Zahl."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) or isinstance(b, (int, float)):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and a == b
    if isinstance(a, dict):
        return isinstance(b, dict) and a.keys() == b.keys() and all(_streng_gleich(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return isinstance(b, list) and len(a) == len(b) and all(_streng_gleich(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


@pytest.mark.parametrize("fall", FAELLE, ids=[f["name"] for f in FAELLE])
def test_vektor(fall):
    daten, satz = kl.lesen(fall["html"])
    fehler = satz if daten is None else kl.video_pruefen(daten)
    assert (daten is not None and fehler is None) is fall["gueltig"], (satz, fehler)
    if fall["gueltig"]:
        assert satz is None
        assert _streng_gleich(daten["video"], fall["video"]), daten["video"]
    elif isinstance(fall["fehler_enthaelt"], str):
        assert isinstance(fehler, str) and fall["fehler_enthaelt"] in fehler, fehler
    else:
        assert fall["fehler_enthaelt"] is None
        assert daten is None and satz is None


# --- JavaScript-Semantik, die die Vektoren nur teilweise abdecken ----------------------------------------

def _gueltiges_video(**anders) -> dict:
    video = {"thema": "KI im Alltag", "zielgruppe": "Handwerker", "minuten": 4, "hook": "Was wäre, wenn?",
             "kernaussage": "KI spart Zeit.", "abschluss": "Probier es aus.", "auftrag": None,
             "stufen": [{"name": "Einstieg", "szenen": [{"kurz": f"Szene {i}", "bildart": "slide"} for i in range(8)]}]}
    video.update(anders)
    return video


def _html(block: str) -> str:
    return f'<html><script type="application/json" id="rowboat-konzept">{block}</script></html>'


def _block(video: dict, **oben) -> str:
    return json.dumps({"art": "videokonzept", "bezuege": [], "video": video, **oben}, ensure_ascii=False)


def test_js_trim_menge():
    # String.prototype.trim: WhiteSpace + LineTerminator; ohne U+0085, U+200B, U+180E und U+001C-U+001F
    for z in "\t\n\x0b\x0c\r \xa0\u1680\u2000\u2005\u200a\u2028\u2029\u202f\u205f\u3000\ufeff":
        assert kl.js_trim(z + "ab" + z) == "ab", hex(ord(z))
    for z in "\x85\u200b\u180e\x1c\x1f":
        assert kl.js_trim(z + "ab" + z) == z + "ab" + z, hex(ord(z))


def test_laenge_in_utf16_einheiten():
    assert kl.utf16_laenge("a\U0001F600") == 3
    assert kl.utf16_laenge("\ud800") == 1          # einzelnes Surrogat (JSON-Escape) zaehlt eine Einheit


@pytest.mark.parametrize("roh", ["[\u0663]", "{\"a\": \u0661}", "01", "1.", ".5", "+1", "0x10", "1e", "'a'", "\"\\x41\"",
                                 "\"\\u12\"", "[1,]", "{\"a\":1,}", "tru", "nul", "\ufeff{}", "{} {}", "\"\t\"",
                                 "NaN", "Infinity", "-Infinity", "[-]", "{\"a\" 1}", "{1:2}", "\u00a0{}"])
def test_json_wie_json_parse_ungueltig(roh):
    with pytest.raises(ValueError):
        kl._json_parse(roh)


@pytest.mark.parametrize("roh, wert", [
    ("4.0", 4), ("1e2", 100), ("-0", 0), ("4.5", 4.5), ("1E+1", 10), ("\"\\ud83d\\ude00\"", "\U0001F600"),
    ("\"\\ud800x\"", "\ud800x"), ("{\"a\":1,\"b\":2,\"a\":3}", {"a": 3, "b": 2}), (" \r\n\t[true,false,null] ", [True, False, None]),
    ("\"\\/\\b\\f\\n\\r\\t\\\"\\\\\\u00E4\"", "/\b\f\n\r\t\"\\ä"), ("\"\x7f\u2028\"", "\x7f\u2028"),
])
def test_json_wie_json_parse_gueltig(roh, wert):
    assert _streng_gleich(kl._json_parse(roh), wert)


def test_json_doppelter_schluessel_behaelt_die_erste_stelle():
    assert list(kl._json_parse('{"a":1,"b":2,"a":3}')) == ["a", "b"]


def test_json_tief_verschachtelt_ohne_rekursion():
    # JSON.parse in V8 arbeitet ohne Rekursion; 9000 Ebenen passen in einen 20 000-Zeichen-Block.
    tief = "[" * 9000 + "]" * 9000
    block = _block(_gueltiges_video(), extra="")[:-1] + ',"tief":' + tief + "}"
    assert kl.utf16_laenge(block) <= 20_000
    daten, satz = kl.lesen(_html(block))
    assert satz is None and kl.video_pruefen(daten) is None


def test_lesen_deckelt_bei_1_mib_utf16():
    # Block ohne Ende reicht bis zum Deckel; ein Astralzeichen auf der Grenze wird wie in JavaScript halbiert.
    vorn = '<script type="application/json" id="rowboat-konzept">'
    rest = kl.LESE_MAX - len(vorn) - 1
    daten, satz = kl.lesen(vorn + "x" * rest + "\U0001F600" + "y" * 10)
    assert daten is None and "länger als 20000 Zeichen" in satz


def test_hinweis_zu_bezuegen_ist_kein_fehler():
    daten, satz = kl.lesen(_html(_block(_gueltiges_video(), bezuege=["ohne Klammern"])))
    assert daten is not None and satz is None


def test_video_pruefen_objekt_fuer_sales():
    assert kl.video_pruefen_objekt(_gueltiges_video()) is None
    assert kl.video_pruefen_objekt(_gueltiges_video(minuten=4.0)) is None                 # aus json.loads
    assert "ganze Zahl" in kl.video_pruefen_objekt(_gueltiges_video(minuten=4.5))
    assert "zwischen 3 und 5" in kl.video_pruefen_objekt(_gueltiges_video(minuten=True))
    assert "zwischen 3 und 5" in kl.video_pruefen_objekt(_gueltiges_video(minuten=float("nan")))
    assert "Das Videokonzept fehlt" in kl.video_pruefen_objekt(None)
    assert "Das Videokonzept fehlt" in kl.video_pruefen_objekt([])
    assert "Der Auftrag muss" in kl.video_pruefen_objekt({k: v for k, v in _gueltiges_video().items() if k != "auftrag"})


def test_video_pruefen_ohne_video():
    assert "Das Videokonzept fehlt" in kl.video_pruefen({"art": "konzept", "bezuege": []})


def test_feindliche_eingaben_werfen_nie():
    zufall = random.Random(1114)
    teile = ["<", ">", "/", "\"", "'", "=", "!", "-", "?", " ", "\n", "script", "SCRIPT", "id", "type",
             "rowboat-konzept", "application/json", "{", "}", "[", "]", ":", ",", "\\", "<!--", "-->", "\ufeff",
             "\U0001F600", "\ud800", "art", "videokonzept", "video", "1", "e", "."]
    for _ in range(3000):
        text = "".join(zufall.choice(teile) for _ in range(zufall.randint(0, 60)))
        daten, satz = kl.lesen(text)
        assert daten is None or isinstance(daten, dict)
        assert satz is None or isinstance(satz, str)
        if daten is not None:
            assert kl.video_pruefen(daten) is None or isinstance(kl.video_pruefen(daten), str)


# --- Vendoring: eine Datei, nur Standardbibliothek, keine Ein-/Ausgabe ------------------------------------

def test_modul_ist_rein_und_vendorbar():
    quelle = (ORDNER / "konzept_lesen.py").read_text(encoding="utf-8")
    baum = ast.parse(quelle)
    importe = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            importe |= {a.name.split(".")[0] for a in knoten.names}
        elif isinstance(knoten, ast.ImportFrom):
            importe.add((knoten.module or "").split(".")[0])
    assert importe <= {"__future__", "re"}, importe
    for verboten in ("open(", "print(", "logging", "os.", "sys.", "socket", "subprocess"):
        assert verboten not in quelle, verboten
    doku = ast.get_docstring(baum)
    assert "Stand: " in doku and "folgt Fork-Commit aa793dce" in doku
    assert "sales-claw" in doku and "byte" in doku.lower()
    oeffentlich = {"lesen", "video_pruefen", "video_pruefen_objekt", "js_trim", "utf16_laenge"}
    assert oeffentlich <= {n.name for n in baum.body if isinstance(n, ast.FunctionDef)}


def test_quelle_ist_reines_ascii():
    # Umlaute und Trim-Zeichen stehen als \u-Escapes; ein Editor oder eine Kopie kann sie so nicht still aendern.
    roh = (ORDNER / "konzept_lesen.py").read_bytes()
    assert roh.isascii(), [i for i, b in enumerate(roh) if b > 127][:5]


# --- Laufzeit: linear auch bei feindlichen Dateien (Grenzen grosszuegig, Messwerte im Bericht) -----------

FEINDLICH = {
    "viele_tags": "<a>" * 349_525,
    "viele_attribute": "<a" + " b" * 524_000 + ">",
    "viele_lt": "< " * 524_000,
    "attribut_ohne_ende": "<a b=\"" + "x" * 1_048_500,
    "viele_scripts": "<script>x</script>" * 58_000,
    "script_ohne_ende_viele_endversuche": "<script>" + "</scriptx" * 116_000,
    "kommentare": "<!---->" * 149_000,
    "block_ganz_1mb": '<script type="application/json" id="rowboat-konzept">' + "[" * 1_048_000,
}


@pytest.mark.parametrize("name", sorted(FEINDLICH))
def test_feindliche_1_mb_datei_ist_schnell(name):
    text = FEINDLICH[name]
    assert len(text.encode("utf-8")) <= 1_048_576 + 100
    start = time.perf_counter()
    kl.lesen(text)
    assert time.perf_counter() - start < 5.0

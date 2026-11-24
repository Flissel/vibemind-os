"""Tests fuer ergebnis_text.py. Eigenstaendig lauffaehig: pytest <diese Datei>."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ergebnis_text import fuers_protokoll, vorschau  # noqa: E402

ZIEL = "mcp:main:auftragsbuch:auftrag_lesen"
KENNUNG = "4aef2c02-1111-4222-8333-444455556666"
ANTWORT = {
    "auftrag_id": KENNUNG, "an": "rowboat", "art": "vorschlaege_anlegen", "version": 1,
    "stand": "fertig", "plan_id": "plan-1",
    "ergebnis": {"punkte": [{"titel": "Mail an Anna Beispiel"}, {"titel": "x"}, {"titel": "y"}],
                 "titel": "Mail an Anna Beispiel", "beleg": {"bestaetigt": True}},
    "fehler": None,
}


def _mcp(antwort: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(antwort)}], "isError": False}


def test_vorschau_nennt_keinen_titel():
    for form in (ANTWORT, _mcp(ANTWORT), {"result": _mcp(ANTWORT)}, json.dumps(ANTWORT)):
        text = vorschau(ZIEL, form)
        assert text == ("Auftrag 4aef2c02 \u00b7 rowboat/vorschlaege_anlegen \u00b7 fertig"
                        " \u00b7 punkte: 3 \u00b7 Beleg best\u00e4tigt")
        assert "Anna" not in text and "Beispiel" not in text


def test_vorschau_zaehlt_listen():
    a = dict(ANTWORT, ergebnis={"punkte": [1, 2], "kontakte": [], "name": "Anna", "beleg": None})
    text = vorschau(ZIEL, a)
    assert "punkte: 2" in text and "kontakte: 0" in text
    assert "Anna" not in text and "Beleg" not in text


def test_vorschau_listenschluessel_mit_namen_wird_nicht_ausgegeben():
    a = dict(ANTWORT, ergebnis={"Anna Beispiel <a@b.de>": [1]})
    assert "Anna" not in vorschau(ZIEL, a)


def test_vorschau_fremdes_ziel_none():
    assert vorschau("mcp:main:andere:auftrag_lesen", ANTWORT) is None
    assert vorschau("capability:x", ANTWORT) is None
    assert vorschau("mcp:main:auftragsbuch", ANTWORT) is None
    assert vorschau(None, ANTWORT) is None  # type: ignore[arg-type]


def test_vorschau_unlesbar():
    for kaputt in (None, 5, "kein json", {}, {"content": []}, {"stand": ["x"]},
                   {"content": [{"text": "{"}]}):
        assert vorschau(ZIEL, kaputt) == "Auftragsbuch: Antwort ohne lesbaren Stand"


def test_vorschau_probe_ohne_an_art():
    a = {"auftrag_id": KENNUNG, "stand": "fertig", "vertrag_hash": "a" * 64}
    assert vorschau("mcp:main:auftragsbuch:auftrag_probe", _mcp(a)) == "Auftrag 4aef2c02 \u00b7 fertig"


def test_vorschau_fremde_werte_werden_nicht_uebernommen():
    a = dict(ANTWORT, an="Anna Beispiel", art="Mail an Anna", stand="Anna schrieb", auftrag_id="Anna Beispiel")
    text = vorschau(ZIEL, a)
    assert "Anna" not in text


def test_protokoll_ohne_inhalt():
    for form in (ANTWORT, _mcp(ANTWORT)):
        aus = fuers_protokoll(ZIEL, form)
        roh = json.dumps(aus)
        assert "Anna" not in roh and "Beispiel" not in roh
        assert aus["auftrag_id"] == KENNUNG and aus["stand"] == "fertig"
        assert aus["listen"] == {"punkte": 3}
        assert aus["an"] == "rowboat" and aus["art"] == "vorschlaege_anlegen"


def test_protokoll_fremdes_ziel_unveraendert():
    gegeben = {"x": "Anna"}
    assert fuers_protokoll("mcp:main:andere:t", gegeben) is gegeben


def test_protokoll_unlesbar_enthaelt_nichts():
    aus = fuers_protokoll(ZIEL, {"content": [{"text": "Anna Beispiel"}]})
    assert "Anna" not in json.dumps(aus)


def test_beleg_vier_feste_woerter():
    erwartet = {"entfaellt": "Beleg entf\u00e4llt", "bestaetigt": "Beleg best\u00e4tigt",
                "nicht_bestaetigt": "Beleg nicht best\u00e4tigt", "nicht_pruefbar": "Beleg nicht pr\u00fcfbar"}
    for wert, wort in erwartet.items():
        a = {"auftrag_id": KENNUNG, "an": "rowboat", "art": "x", "stand": "fertig", "beleg": wert,
             "ergebnis": {"beleg": True}}
        assert vorschau(ZIEL, a).endswith(" \u00b7 " + wort)
        assert fuers_protokoll(ZIEL, _mcp(a))["beleg"] == wert


def test_beleg_oben_hat_vorrang_und_fremdes_wird_nicht_wiedergegeben():
    a = dict(ANTWORT, beleg="nicht_bestaetigt")
    assert vorschau(ZIEL, a).endswith("Beleg nicht best\u00e4tigt")
    for fremd in ("Anna Beispiel", 5, ["x"], {"a": 1}, ""):
        a = {"auftrag_id": KENNUNG, "stand": "fertig", "beleg": fremd}
        assert vorschau(ZIEL, a) == "Auftrag 4aef2c02 \u00b7 fertig"
        assert "beleg" not in fuers_protokoll(ZIEL, a)


def test_stand_fehler_ohne_inhalt():
    a = {"auftrag_id": KENNUNG, "an": "rowboat", "art": "vorschlaege_anlegen", "stand": "fehler",
         "fehler": "Ergebnis in falscher Form", "ergebnis": None}
    text = vorschau(ZIEL, _mcp(a))
    assert text == ("Auftrag 4aef2c02 · rowboat/vorschlaege_anlegen · fehler"
                    " · Grund: Ergebnis in falscher Form")
    assert fuers_protokoll(ZIEL, _mcp(a))["grund"] == "Ergebnis in falscher Form"

# --- Sales-Abgleich: Fehlergrund und Zahlenfelder ---

import pytest  # noqa: E402

from ergebnis_text import GRUND_NICHT_ANZEIGBAR, GRUENDE  # noqa: E402

FEHLER_ANTWORT = dict(ANTWORT, stand="fehler", ergebnis=None)


def _fehler(text):
    return dict(FEHLER_ANTWORT, fehler=text)


def test_gruende_enthalten_alle_quellen():
    for text in ("Unbekannte Auftragsart oder Version", "Eingabe passt nicht zum Vertrag",
                 "Rowboat hat einen Punkt nicht angenommen", "Ergebnis passt nicht zum Vertrag",
                 "Abholer: interner Fehler", "Rowboat nicht erreichbar", "Rowboat: Schlüssel abgelehnt",
                 "Rowboat: unerwartete Antwort", "Bearbeitung bei Sales abgebrochen",
                 "Bearbeitung bei Rowboat abgebrochen", "Fehler ohne Angabe",
                 "Auftragsart oder Version ist bei Sales nicht bekannt",
                 "Auftragsart ist bei Sales nicht freigeschaltet",
                 "Veraendernde Auftragsart ohne festgelegten Testkontakt",
                 "Auftragsart ist bei Sales noch nicht ausfuehrbar",
                 "Eingabe passt nicht zur Form der Auftragsart",
                 "Ergebnis passt nicht zur Form der Auftragsart",
                 "Ausfuehrung bei Sales gescheitert", "Ergebnis in falscher Form",
                 "Zwei Versuche ohne Ergebnis"):
        assert text in GRUENDE, text


@pytest.mark.parametrize("grund", sorted(GRUENDE))
@pytest.mark.parametrize("zusatz", ["", ": Mail an Erika Beispiel", " : x"])
def test_fehlergrund_der_liste_erscheint(grund, zusatz):
    erwartet = f"Auftrag 4aef2c02 · rowboat/vorschlaege_anlegen · fehler · Grund: {grund}"
    for form in (_fehler(grund + zusatz), _mcp(_fehler(grund.upper() + zusatz))):
        text = vorschau(ZIEL, form)
        assert text == erwartet
        assert "Erika" not in text
        d = fuers_protokoll(ZIEL, form)
        assert d["stand"] == "fehler" and d["grund"] == grund
        assert "Erika" not in json.dumps(d)


@pytest.mark.parametrize("fremd", ["Mail an Erika Beispiel", "Erika Beispiel: Rowboat nicht erreichbar",
                                   "Titel: Angebot für Beispiel GmbH", "x" * 400, "Fehler ohne Angabe!"])
def test_fremder_grund_nicht_anzeigbar(fremd):
    text = vorschau(ZIEL, _fehler(fremd))
    assert text.endswith("fehler · Grund: " + GRUND_NICHT_ANZEIGBAR)
    assert "Erika" not in text and "Beispiel" not in text and "xxx" not in text
    d = fuers_protokoll(ZIEL, _fehler(fremd))
    assert d["grund"] == GRUND_NICHT_ANZEIGBAR
    assert "Erika" not in json.dumps(d) and "Beispiel" not in json.dumps(d)


@pytest.mark.parametrize("leer", [None, "", "   ", ": nur Zusatz", 5, ["Rowboat nicht erreichbar"]])
def test_leerer_oder_fehlender_grund(leer):
    a = dict(FEHLER_ANTWORT, fehler=leer)
    assert vorschau(ZIEL, a).endswith("Grund: Grund nicht anzeigbar")
    assert fuers_protokoll(ZIEL, a)["grund"] == "Grund nicht anzeigbar"
    ohne = dict(FEHLER_ANTWORT)
    assert vorschau(ZIEL, ohne).endswith("Grund: Grund nicht anzeigbar")


def test_grund_nur_bei_stand_fehler():
    a = dict(ANTWORT, fehler="Rowboat nicht erreichbar")
    assert "Grund" not in vorschau(ZIEL, a)
    assert "grund" not in fuers_protokoll(ZIEL, a)


ZAEHL = {
    "termine_heute": {"anzahl": 3, "anzahl_kunden": 1, "quellen_stumm": 0},
    "freigaben_zaehlen": {"wartend": 4, "fehlgeschlagen": 1},
    "post_offen": {"chats_unbeantwortet": 5, "mails_offen": 2},
}


@pytest.mark.parametrize("art", sorted(ZAEHL))
def test_zahlenfelder_der_zaehlarten_erscheinen(art):
    a = dict(ANTWORT, an="sales", art=art, ergebnis=ZAEHL[art])
    text = vorschau(ZIEL, a)
    assert text.startswith(f"Auftrag 4aef2c02 · sales/{art} · fertig")
    for name, n in ZAEHL[art].items():
        assert f"{name}: {n}" in text
    d = fuers_protokoll(ZIEL, _mcp(a))
    assert d["zahlen"] == ZAEHL[art] and d["listen"] == {}


def test_nur_ganze_zahlen_ab_null_oben():
    erg = {"ok": 3, "null": 0, "negativ": -1, "komma": 1.5, "ganz_als_float": 2.0, "wahr": True,
           "falsch": False, "text": "7", "verschachtelt": {"tief": 9}, "liste": [1, 2]}
    a = dict(ANTWORT, ergebnis=erg)
    text = vorschau(ZIEL, a)
    assert "ok: 3" in text and "null: 0" in text and "liste: 2" in text
    for nicht in ("negativ", "komma", "ganz_als_float", "wahr", "falsch", "text", "tief", "verschachtelt"):
        assert nicht not in text, nicht
    assert fuers_protokoll(ZIEL, a)["zahlen"] == {"ok": 3, "null": 0}


def test_zahlenname_folgt_der_regel_der_listennamen():
    erg = {"Erika Beispiel": 5, "A": 1, "x" * 30: 2, "mit-strich": 3, "gut_1": 4}
    a = dict(ANTWORT, ergebnis=erg)
    text = vorschau(ZIEL, a)
    assert text == "Auftrag 4aef2c02 · rowboat/vorschlaege_anlegen · fertig · gut_1: 4"
    for nicht in ("Erika", "A:", "xxx", "mit-strich"):
        assert nicht not in text
    assert fuers_protokoll(ZIEL, a)["zahlen"] == {"gut_1": 4}


def test_zahlen_haben_eine_obergrenze():
    erg = {"genau": 10**9, "darueber": 10**9 + 1, "plz_nummer": 491701234567, "klein": 7}
    a = dict(ANTWORT, ergebnis=erg)
    text = vorschau(ZIEL, a)
    assert "genau: 1000000000" in text and "klein: 7" in text
    assert "darueber" not in text and "plz_nummer" not in text and "491701234567" not in text
    assert fuers_protokoll(ZIEL, a)["zahlen"] == {"genau": 10**9, "klein": 7}


def test_riesige_zahl_wirft_nicht():
    riesig = 10**5000
    a = dict(ANTWORT, ergebnis={"riesig": riesig, "klein": 1})
    assert "riesig" not in vorschau(ZIEL, a)
    assert fuers_protokoll(ZIEL, a)["zahlen"] == {"klein": 1}
    text = '{"stand": "fertig", "ergebnis": {"riesig": ' + "9" * 5000 + "}}"  # json.loads scheitert hier (4300 Stellen)
    assert vorschau(ZIEL, text) is not None
    assert fuers_protokoll(ZIEL, text) is not None

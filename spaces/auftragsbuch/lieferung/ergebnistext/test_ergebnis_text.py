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

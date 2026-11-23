"""Nach Thema gruppieren (Rueckweg "gruppieren", fester Aufruf idea.group_layout). Ohne Netz; Namen erfunden."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import ideen_dokument as dok  # noqa: E402
from core import ideen_fest_contract as vertrag  # noqa: E402
from test_ideen_dokument import ALTES_DOKUMENT, BUBBLE, KARTEN, LesenAttrappe, _auftrag, _karte  # noqa: E402

KOPF = _karte("a0000099", "Alte Gruppe", "", 0, -140, metadata={"gruppen_kopf": True})


def _vorbereiten(arg=None, karten=None):
    return dok.auftrag_vorbereiten("idea.group", json.dumps(arg or {"bubble_id": BUBBLE["id"]}),
                                   "Ordne die Notizen nach Thema", lesen=LesenAttrappe(karten=karten or KARTEN))


def _quittung(inhalt):
    return {"content": [{"type": "text", "text": json.dumps(inhalt)}], "isError": False}


# ── Vorbereiten ──────────────────────────────────────────────────────────

def test_nur_lesbare_karten_mit_kuerzeln_und_ohne_kennungen() -> None:
    nachricht, rueckweg = _vorbereiten(karten=[*KARTEN, KOPF])
    assert rueckweg["vertrag"] == "gruppieren" and rueckweg["ereignis"] == "idea.group"
    # Stadtfest Überblick, Bühnenplan, Lichtanlage, Sponsoren, Helferliste, Recherche: lesbar.
    # Strom (nur Titel) bleibt liegen; Dokument und alte Überschrift gehen nicht an das Modell.
    assert len(rueckweg["kuerzel"]) == 6 and rueckweg["leer"] == 1
    assert "a0000099" not in rueckweg["kuerzel"].values() and ALTES_DOKUMENT["id"] not in rueckweg["kuerzel"].values()
    assert "Vertrag: gruppieren" in nachricht and "[K1]" in nachricht
    assert not any(k in nachricht for k in rueckweg["kuerzel"].values())
    assert rueckweg["verbinden"] is False
    assert dok.rueckweg_aus({"uebergabe": {"rueckweg": rueckweg}}) == rueckweg


def test_verbinden_reist_im_rueckweg_mit() -> None:
    _, rueckweg = _vorbereiten({"bubble_id": BUBBLE["id"], "verbinden": True})
    assert rueckweg["verbinden"] is True


def test_zu_wenig_und_zu_viele_karten_ehrlich() -> None:
    with pytest.raises(dok.RueckwegFehler, match="mindestens zwei Karten mit Inhalt"):
        _vorbereiten(karten=[KARTEN[0], KARTEN[5]])
    viele = [_karte(f"c{i:07d}", f"Karte {i}", f"Text {i}") for i in range(101)]
    with pytest.raises(dok.RueckwegFehler, match="^Gruppieren geht bis 100 Karten; in dieser Bubble sind es 101"):
        _vorbereiten(karten=viele)


def test_ueberschriften_gehen_nicht_in_zusammenfassung_oder_white_paper() -> None:
    _, rueckweg = dok.auftrag_vorbereiten("idea.summarize", json.dumps({"bubble_id": BUBBLE["id"]}), "",
                                          lesen=LesenAttrappe(karten=[*KARTEN, KOPF]), neue_kennung=lambda: "d1")
    assert "Alte Gruppe" not in rueckweg["kuerzel_titel"].values()
    assert "Alte Gruppe" not in rueckweg["leer"]


# ── Ergebnis pruefen ─────────────────────────────────────────────────────

ERLAUBT = {"K1", "K2", "K3", "K4"}


@pytest.mark.parametrize("roh, satz", [
    ('{"gruppen": [{"name": "A", "karten": ["K1", "K9"]}]}', "nicht bekommen"),
    ('{"gruppen": [{"name": "A", "karten": ["K1"]}, {"name": "B", "karten": ["K1"]}]}', "zwei Gruppen"),
    ('{"gruppen": [{"name": "A", "karten": []}]}', "vereinbarten Form"),
    ('{"gruppen": [{"name": "A", "karten": ["K1"], "farbe": "rot"}]}', "vereinbarten Form"),
    ('{"gruppen": [], "x": 1}', "vereinbarten Form"),
    ('{"gruppen": [{"name": "<b>A</b>", "karten": ["K1"]}]}', "HTML"),
    ('{"gruppen": [{"name": "' + "A" * 61 + '", "karten": ["K1"]}]}', "länger als 60"),
    ("Ich kann das leider nicht.", None),
])
def test_ergebnis_streng(roh, satz) -> None:
    werte, grund = dok.ergebnis_pruefen("gruppieren", roh, ERLAUBT)
    assert werte is None
    if satz:
        assert satz in grund


def test_ergebnis_gut_und_ausgelassene_karten_landen_unter_weitere() -> None:
    werte, grund = dok.ergebnis_pruefen(
        "gruppieren", '```json\n{"gruppen": [{"name": "Technik", "karten": ["K1", "K3"]},'
                      ' {"name": "Geld", "karten": ["K2"]}]}\n```', ERLAUBT)
    assert grund == "" and werte["gruppen"][0] == {"name": "Technik", "karten": ["K1", "K3"]}
    rueckweg = {"kuerzel": {"K1": "a1", "K2": "a2", "K3": "a3", "K4": "a4"}}
    gruppen, rest = dok.gruppen_bauen(rueckweg, werte)
    assert gruppen == [{"name": "Technik", "ids": ["a1", "a3"]}, {"name": "Geld", "ids": ["a2"]},
                       {"name": "Weitere Karten", "ids": ["a4"]}]
    assert rest == 1


def test_zwoelf_gruppen_und_ausgelassene_karte_wird_abgelehnt() -> None:
    erlaubt = {f"K{i}" for i in range(1, 14)}
    roh = json.dumps({"gruppen": [{"name": f"G{i}", "karten": [f"K{i}"]} for i in range(1, 13)]})
    werte, grund = dok.ergebnis_pruefen("gruppieren", roh, erlaubt)
    assert werte is None and "keinen Platz" in grund


# ── Vertrag des festen Aufrufs ───────────────────────────────────────────

GRUPPEN = [{"name": "Technik", "ids": ["a0000002", "a0000003"]}, {"name": "Geld", "ids": ["a0000004"]}]


def test_feste_argumente() -> None:
    gut, _ = vertrag.feste_argumente("idea.group_layout", {"bubble_id": "b0b1e001", "gruppen": GRUPPEN})
    assert gut is not None
    for schlecht in (
        {"bubble_id": "b0b1e001", "gruppen": []},
        {"bubble_id": "b0b1e001", "gruppen": [{"name": "A", "ids": ["a1"]}, {"name": "B", "ids": ["a1"]}]},
        {"bubble_id": "b0b1e001", "gruppen": [{"name": "A\nB", "ids": ["a1"]}]},
        {"bubble_id": "b0b1e001", "gruppen": [{"name": "A", "ids": ["a 1"]}]},
        {"bubble_id": "b0b1e001", "gruppen": [{"name": "A", "ids": ["a1"], "x": 1}]},
        {"bubble_id": "b0b1e001", "gruppen": GRUPPEN, "verbinden": "ja"},
    ):
        assert vertrag.feste_argumente("idea.group_layout", schlecht)[0] is None, schlecht


def _inhalt(verbinden=False):
    kanten = ([{"from_id": "k1", "to_id": "a0000002"}, {"from_id": "k1", "to_id": "a0000003"},
               {"from_id": "k2", "to_id": "a0000004"}] if verbinden else [])
    return {"bubble_id": "b0b1e001",
            "gruppen": [{"name": "Technik", "kopf_id": "k1", "ids": GRUPPEN[0]["ids"]},
                        {"name": "Geld", "kopf_id": "k2", "ids": GRUPPEN[1]["ids"]}],
            "positionen": [{"id": "k1", "x": 0, "y": 0}, {"id": "k2", "x": 240, "y": 0},
                           {"id": "a0000002", "x": 0, "y": 140}, {"id": "a0000003", "x": 0, "y": 280},
                           {"id": "a0000004", "x": 240, "y": 140}],
            "verschoben": 3, "entfernt": [], "kanten": kanten}


def _lies(zeilen_knoten, kanten=()):
    def lies(tabelle, filter_):
        if tabelle == "canvas_edges":
            return [{"from_node_id": a, "to_node_id": b} for a, b in kanten]
        return zeilen_knoten
    return lies


KNOTEN = [{"id": "k1", "x": 0, "y": 0, "title": "Technik", "metadata": {"gruppen_kopf": True}},
          {"id": "k2", "x": 240, "y": 0, "title": "Geld", "metadata": {"gruppen_kopf": True}},
          {"id": "a0000002", "x": 0, "y": 140}, {"id": "a0000003", "x": 0, "y": 280},
          {"id": "a0000004", "x": 240, "y": 140}]


def test_nachpruefung_am_zustand() -> None:
    args = {"bubble_id": "b0b1e001", "gruppen": GRUPPEN, "aenderung_id": "plan_1-r"}
    erwartet, grund = vertrag.nachpruefung("idea.group_layout", args, _quittung(_inhalt()))
    assert grund == "" and erwartet["art"] == "gruppen"
    assert vertrag.pruefe_zustand(erwartet, _lies(KNOTEN))[0] is True
    verrutscht = [dict(KNOTEN[0]), *KNOTEN[1:4], {"id": "a0000004", "x": 999, "y": 140}]
    assert vertrag.pruefe_zustand(erwartet, _lies(verrutscht))[0] is False
    falscher_titel = [{**KNOTEN[0], "title": "Anders"}, *KNOTEN[1:]]
    assert vertrag.pruefe_zustand(erwartet, _lies(falscher_titel))[0] is False
    assert vertrag.pruefe_zustand(erwartet, lambda t, f: None)[0] is None


def test_nachpruefung_mit_verbindungen() -> None:
    args = {"bubble_id": "b0b1e001", "gruppen": GRUPPEN, "verbinden": True}
    erwartet, _ = vertrag.nachpruefung("idea.group_layout", args, _quittung(_inhalt(True)))
    alle = [("k1", "a0000002"), ("k1", "a0000003"), ("k2", "a0000004")]
    assert vertrag.pruefe_zustand(erwartet, _lies(KNOTEN, alle))[0] is True
    assert vertrag.pruefe_zustand(erwartet, _lies(KNOTEN, alle[:2]))[0] is False
    # Verbinden verlangt, die Quittung nennt aber keine Kanten: nicht nachpruefbar.
    assert vertrag.nachpruefung("idea.group_layout", args, _quittung(_inhalt(False)))[0] is None


def test_quittung_ohne_lage_einer_karte_ist_ungueltig() -> None:
    inhalt = _inhalt()
    inhalt["positionen"] = inhalt["positionen"][:-1]
    erwartet, grund = vertrag.nachpruefung("idea.group_layout", {"bubble_id": "b0b1e001", "gruppen": GRUPPEN},
                                           _quittung(inhalt))
    assert erwartet is None and "invalid" in grund


# ── Verarbeiten im Nachfasser ────────────────────────────────────────────

def _verarbeiten(ergebnis, aufruf=None, pruefe=None, arg=None):
    _, rueckweg = _vorbereiten(arg)
    aufrufe = []

    def _aufruf(ereignis, argumente):
        aufrufe.append((ereignis, argumente))
        return {"ok": True}

    pruefung = dok.verarbeiten(_auftrag(rueckweg, ergebnis), lies_karten=lambda ids: [],
                               anlegen=lambda **_: "nie", aufruf=aufruf or _aufruf,
                               pruefe=pruefe or (lambda e, a, q: (True, "ok")))
    return pruefung, aufrufe, rueckweg


def test_ein_fester_aufruf_und_ehrliche_meldung() -> None:
    roh = json.dumps({"gruppen": [{"name": "Programm", "karten": ["K1", "K2", "K3"]},
                                  {"name": "Organisation", "karten": ["K4", "K5"]}]})
    pruefung, aufrufe, rueckweg = _verarbeiten(roh)
    assert pruefung["rueckweg"] == "geschrieben" and pruefung["verified"] is True
    ((ereignis, argumente),) = aufrufe
    assert ereignis == "idea.group_layout" and argumente["verbinden"] is False
    assert argumente["gruppen"][2] == {"name": "Weitere Karten", "ids": [rueckweg["kuerzel"]["K6"]]}
    assert pruefung["meldung"] == (
        "Die Karten stehen jetzt in 3 Gruppen: „Programm“ (3), „Organisation“ (2), „Weitere Karten“ (1). "
        "1 Karte hat der Agent keiner Gruppe gegeben; sie steht unter „Weitere Karten“. "
        "1 Karte ohne Inhalt bleibt, wo sie ist. „Zurück“ nimmt das als Ganzes zurück.")


def test_abgelehntes_ergebnis_schreibt_nichts() -> None:
    pruefung, aufrufe, _ = _verarbeiten('{"gruppen": [{"name": "A", "karten": ["K77"]}]}')
    assert pruefung["rueckweg"] == "abgelehnt" and aufrufe == []


def test_freigabe_und_nicht_bestaetigt() -> None:
    def wartet(ereignis, argumente):
        raise dok.FreigabeAusstehend(ereignis)

    roh = json.dumps({"gruppen": [{"name": "Alles", "karten": [f"K{i}" for i in range(1, 7)]}]})
    pruefung, _, _ = _verarbeiten(roh, aufruf=wartet)
    assert pruefung["rueckweg"] == "wartet" and "Freigabe" in pruefung["meldung"]
    pruefung, _, _ = _verarbeiten(roh, pruefe=lambda e, a, q: (False, "1 card position(s) differ"))
    assert pruefung["rueckweg"] == "fehler" and "nicht bestätigen" in pruefung["meldung"]


def test_fehlendes_recht_heisst_erlaubnis_fehlt() -> None:
    # Lauf 8: "Tool not permitted for caller agent: ..." -> nicht "Bitte prüfe das Canvas".
    roh = json.dumps({"gruppen": [{"name": "Alles", "karten": [f"K{i}" for i in range(1, 7)]}]})

    def verboten(ereignis, argumente):
        raise RuntimeError("Tool not permitted for caller agent: mcp_beispiel_gruppen")

    pruefung, _, _ = _verarbeiten(roh, aufruf=verboten)
    assert pruefung["rueckweg"] == "fehler" and pruefung["verified"] is False
    assert pruefung["meldung"] == "Dem Agenten fehlt die Erlaubnis für das Anordnen. Geändert habe ich nichts."

    def abgelehnt(ereignis, argumente):
        return {"error": {"code": -32602, "message": "Tool not permitted for caller agent: mcp_beispiel_gruppen"}}

    pruefung, _, _ = _verarbeiten(roh, aufruf=abgelehnt, pruefe=lambda e, a, q: (None, "no result"))
    assert pruefung["meldung"] == dok.RECHT_FEHLT_GRUPPEN
    # Andere Fehler bleiben beim alten Satz.
    def kaputt(ereignis, argumente):
        raise RuntimeError("connection reset")

    pruefung, _, _ = _verarbeiten(roh, aufruf=kaputt)
    assert pruefung["meldung"] == "Das Anordnen der Gruppen ist gescheitert. Bitte prüfe das Canvas."


def test_arg_vorlage_laesst_verschachtelte_objekte_als_json() -> None:
    from core import ideen_abkuerzung as abk

    argumente = {"bubble_id": "b0b1e001", "gruppen": [{"name": "Preise {netto}", "ids": ["a1"]}]}
    vorlage = abk.arg_vorlage(argumente)
    assert "{{" not in vorlage and "\\u007bnetto\\u007d" in vorlage
    assert json.loads(vorlage) == argumente

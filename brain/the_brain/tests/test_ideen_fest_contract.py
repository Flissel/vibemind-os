"""Vertrag der festen Aufrufe des Ideen-Space: Argumente, Quittung, Nachpruefung."""

from __future__ import annotations

from pathlib import Path
import json
import sys

import pytest
import yaml

import importlib.util

ROOT = Path(__file__).resolve().parents[3]


def _lade_vertrag():
    """Den Vertrag aus der Datei laden, ohne das Paket ``core`` (und damit torch).

    Das Modul hat keine Abhaengigkeiten; so laeuft dieser Test auch bei knappem Speicher.
    """
    pfad = ROOT / "brain" / "the_brain" / "core" / "ideen_fest_contract.py"
    spec = importlib.util.spec_from_file_location("ideen_fest_contract_test", pfad)
    modul = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modul
    spec.loader.exec_module(modul)
    return modul


vertrag = _lade_vertrag()


def _quittung(inhalt, is_error=False):
    return {"content": [{"type": "text", "text": json.dumps(inhalt)}], "isError": is_error}


def _registry():
    return yaml.safe_load((ROOT / "config" / "space_agent_registry.yml").read_text(encoding="utf-8"))["spaces"]


# ── Verzeichnis ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("event_id", sorted(vertrag.FESTE_EREIGNISSE))
def test_jedes_feste_ereignis_steht_im_verzeichnis_als_fester_aufruf(event_id) -> None:
    spaces = _registry()
    space = spaces["bubbles" if event_id.startswith("bubble.") else "ideas"]
    spec = space["events"][event_id]
    werkzeug = vertrag.FESTE_EREIGNISSE[event_id]["werkzeug"]

    assert spec["tool"] == werkzeug
    assert spec["execution"] == {"kind": "mcp", "server": "spaces-ideas"}
    assert spec["required_provenance"] == ["approval_ref", "cost_ref"]
    assert spec["required_params"] == list(vertrag.FESTE_EREIGNISSE[event_id]["pflicht"])
    assert "spaces-ideas" in space["mcp_servers"]
    assert werkzeug in space["mcp_tools"]["spaces-ideas"]


def test_bubble_ereignisse_laufen_im_namen_des_bubble_agenten() -> None:
    spaces = _registry()
    assert spaces["bubbles"]["agent"] == "brain-bubbles"
    # brain-ideas braucht die beiden Werkzeuge dann nicht.
    assert not {"bubble_rate", "bubble_promote"} & set(spaces["ideas"]["mcp_tools"]["spaces-ideas"])
    # Der aeltere Weg bleibt unberuehrt.
    assert spaces["ideas"]["events"]["idea.connect"]["tool"] == "idea_connect"
    assert spaces["ideas"]["events"]["idea.aenderung_zurueck"]["tool"] == "aenderung_zurueck"


def test_vertrag_kennt_dieselben_formate_wie_der_ideen_server() -> None:
    quelle = (ROOT / "spaces" / "ideas" / "mcp_server.py").read_text(encoding="utf-8")
    for fmt in vertrag.FORMATE:
        assert f'"{fmt}"' in quelle


def test_vertrag_gilt_nur_fuer_das_werkzeug_das_das_verzeichnis_bindet() -> None:
    assert vertrag.ist_fester_aufruf("idea.create", "mcp:brain-ideas:spaces-ideas:idea_create")
    assert vertrag.ist_fester_aufruf("bubble.rate", "mcp:brain-bubbles:spaces-ideas:bubble_rate")
    # Jede Schreibweise, die der Ausfuehrer als dasselbe Werkzeug behandelt, faellt unter den Vertrag.
    assert vertrag.ist_fester_aufruf("idea.rate_reason", "mcp: brain-ideas : spaces_ideas : bewertung-begruenden ")
    # anderes Werkzeug, anderer Server, kein fester Aufruf, fremdes Ereignis: alter Weg
    assert not vertrag.ist_fester_aufruf("idea.create", "mcp:brain-ideas:spaces-ideas:db_ideas_create")
    assert not vertrag.ist_fester_aufruf("idea.create", "mcp:brain-ideas:vibemind-db:idea_create")
    assert not vertrag.ist_fester_aufruf("idea.create", "supabase:idea.create")
    assert not vertrag.ist_fester_aufruf("idea.create", None)
    assert not vertrag.ist_fester_aufruf("idea.connect", "mcp:brain-ideas:spaces-ideas:idea_connect")


# ── Argumente ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("event_id, argumente", [
    ("idea.create", {"bubble_id": "bub1", "title": "Preise"}),
    ("idea.create", {"bubble_id": "bub1", "title": "Preise", "content": "", "id": "vor-1", "x": 0, "y": -40}),
    ("idea.update", {"id": "n1", "title": "Preisliste"}),
    ("idea.update", {"id": "n1", "x": 10, "y": 20}),
    ("idea.list", {}),
    ("idea.list", {"bubble_id": "bub1", "limit": 100}),
    ("idea.format", {"id": "n1", "format": "table"}),
    ("idea.format", {"id": "n1", "format": "revert"}),
    ("idea.arrange", {"bubble_id": "bub1", "order": "score", "columns": 3}),
    ("idea.auto_link", {"bubble_id": "bub1"}),
    ("idea.rate", {"id": "n1", "score": 0}),
    ("idea.rate_reason", {"bubble_id": "bub1", "text": "traegt", "idea_ids": ["n1", "n2"], "score": 70}),
    ("bubble.rate", {"id": "bub1", "score": 100}),
    ("bubble.promote", {"bubble_id": "bub1"}),
])
def test_gueltige_argumente_gehen_unveraendert_durch(event_id, argumente) -> None:
    assert vertrag.feste_argumente(event_id, argumente) == (argumente, "")
    assert vertrag.feste_argumente(event_id, json.dumps(argumente)) == (argumente, "")


@pytest.mark.parametrize("event_id, argumente", [
    ("idea.create", {"title": "Preise"}),                            # Bubble fehlt
    ("idea.create", {"bubble_id": "", "title": "Preise"}),           # leere Kennung
    ("idea.create", {"bubble_id": "bub1", "title": "   "}),          # leerer Titel
    ("idea.create", {"bubble_id": "bub1", "title": "x", "bubble_name": "Messe"}),
    ("idea.update", {"id": "n1"}),                                   # nichts zu aendern
    ("idea.update", {"idea_id": "n1", "title": "x"}),                # alter Feldname
    ("idea.update", {"id": "Die Notiz Preise", "title": "x"}),       # Name statt Kennung
    ("idea.format", {"id": "n1", "format": "bunt"}),
    ("idea.format", {"id": "n1"}),
    ("idea.arrange", {"bubble_id": "bub1", "columns": 0}),
    ("idea.auto_link", {"bubble_id": "bub1", "threshold": 0.1}),
    ("idea.rate", {"id": "n1", "score": 101}),
    ("idea.rate", {"id": "n1", "score": True}),
    ("idea.rate", {"id": "n1", "score": "80"}),
    ("idea.rate_reason", {"bubble_id": "bub1", "text": "x", "idea_ids": ["n1", "n1"]}),
    ("idea.rate_reason", {"bubble_id": "bub1", "text": "x", "idea_ids": ["n1,n2"]}),
    ("bubble.rate", {"id": "eq.bub1", "score": 5}),
    ("bubble.promote", {"bubble_id": "bub1", "title": "Messe"}),
    ("bubble.promote", "Messe"),
    ("bubble.promote", ""),
    ("idea.list", {"query": "Preise"}),
    ("idea.delete", {"id": "n1"}),                                   # kein festes Ereignis
])
def test_ungueltige_argumente_ergeben_eine_rueckfrage_und_keinen_aufruf(event_id, argumente) -> None:
    ergebnis, rueckfrage = vertrag.feste_argumente(event_id, argumente)
    assert ergebnis is None
    assert rueckfrage.startswith("clarification required")


def test_lesen_hat_keine_nachpruefung_schreiben_immer() -> None:
    assert vertrag.validator_config("idea.list") is None
    for event_id, spec in vertrag.FESTE_EREIGNISSE.items():
        if spec.get("liest_nur"):
            continue
        config = vertrag.validator_config(event_id)
        assert config == {
            "kind": "truth:supabase_ideen_fest", "on_fail": "block", "require_verified": True,
            "postcondition": {"check": "supabase_ideen_fest", "ereignis": event_id},
        }
        assert config is not vertrag.validator_config(event_id)


# ── Fehler im Inhalt ─────────────────────────────────────────────────────

def test_fehlertext_zaehlt_auch_ohne_fehlerkennzeichen() -> None:
    assert vertrag.fehler_aus_ergebnis(_quittung({"error": "idea_not_found"})) == (
        "Ideen-Werkzeug meldet einen Fehler (idea_not_found)")
    # Das Feld kommt mit, der Wert nie; auch bei gesetztem Fehlerkennzeichen.
    assert vertrag.fehler_aus_ergebnis(
        _quittung({"error": "invalid_arguments: 'bubble_id' must be a non-empty string"}, is_error=True)
    ) == "Ideen-Werkzeug meldet einen Fehler (invalid_arguments: bubble_id)"
    assert vertrag.fehler_aus_ergebnis(_quittung({"error": "idea_not_in_bubble: n-4711"})) == (
        "Ideen-Werkzeug meldet einen Fehler (idea_not_in_bubble)")
    # freier Werkzeugtext wird nie durchgereicht
    meldung = vertrag.fehler_aus_ergebnis(_quittung({"error": "supabase_http_error: status=500 key=geheim"}))
    assert meldung == "Ideen-Werkzeug meldet einen Fehler (supabase_http_error)"
    assert "geheim" not in vertrag.fehler_aus_ergebnis(_quittung({"error": "Kaputt! key=geheim"}))
    assert vertrag.fehler_aus_ergebnis({"content": []}) == "Ideen-Werkzeug: keine lesbare Antwort"
    assert vertrag.fehler_aus_ergebnis(_quittung([{"id": "n1"}])) == ""
    assert vertrag.fehler_aus_ergebnis(_quittung([])) == ""


def test_echte_fehlerform_von_openfang_wenn_der_ideen_server_beendet_ist() -> None:
    """Form gemessen am 23.11.2026 ueber POST /mcp (idea_list, nur lesend)."""
    gemessen = {
        "_meta": {"runtime_admission": {"replayed": False}},
        "content": [{"type": "text",
                     "text": "Error: MCP tool call failed: MCP tool call failed: Transport closed"}],
        "isError": True,
    }
    assert vertrag.fehler_aus_ergebnis(gemessen) == "Ideen-Dienst nicht erreichbar (transport_closed)"
    anderer = {**gemessen, "content": [{"type": "text", "text": "Error: tool timed out; key=geheim"}]}
    assert vertrag.fehler_aus_ergebnis(anderer) == "Ideen-Dienst nicht erreichbar (openfang_tool_error)"
    assert "geheim" not in vertrag.fehler_aus_ergebnis(anderer)
    # Kein JSON ohne Fehlerkennzeichen bleibt "unlesbar" und gilt nie als gelungen.
    assert vertrag.fehler_aus_ergebnis({"content": [{"type": "text", "text": "ok"}], "isError": False}) == (
        "Ideen-Werkzeug: keine lesbare Antwort")
    # Das Zusatzfeld _meta stoert das Lesen einer gueltigen Antwort nicht.
    gut = {"_meta": {"runtime_admission": {}}, "isError": False,
           "content": [{"type": "text", "text": json.dumps([{"id": "n1"}])}]}
    assert vertrag.fehler_aus_ergebnis(gut) == ""
    assert vertrag.nachpruefung("idea.rate", {"id": "n1", "score": 5}, gut)[0] is not None
    assert vertrag.feste_argumente("idea.list", {"bubble_id": "bub1", "full": True})[0] == {
        "bubble_id": "bub1", "full": True}
    assert vertrag.feste_argumente("idea.list", {"full": "ja"})[0] is None


# ── Erwartung aus Argumenten und Quittung ────────────────────────────────

def test_erwartung_je_ereignis() -> None:
    faelle = [
        ("idea.create", {"bubble_id": "bub1", "title": "Preise", "id": "vor-1"}, [{"id": "alt-9", "title": "Preise"}],
         {"art": "zeile", "tabelle": "canvas_nodes", "id": "alt-9", "felder": {"linked_idea_id": "bub1"}}),
        ("idea.update", {"id": "n1", "title": "Neu {x}", "x": 5}, [{"id": "n1"}],
         {"art": "zeile", "tabelle": "canvas_nodes", "id": "n1", "felder": {"title": "Neu {x}", "x": 5}}),
        ("idea.format", {"id": "n1", "format": "table"}, {"id": "n1"},
         {"art": "zeile", "tabelle": "canvas_nodes", "id": "n1", "felder": {"format_schema.type": "table"}}),
        ("idea.format", {"id": "n1", "format": "revert"}, {"id": "n1", "format_schema": {"type": "swot"}},
         {"art": "zeile", "tabelle": "canvas_nodes", "id": "n1", "felder": {"format_schema.type": "swot"}}),
        ("idea.rate", {"id": "n1", "score": 80}, {"id": "n1"},
         {"art": "zeile", "tabelle": "canvas_nodes", "id": "n1", "felder": {"metadata.bewertung.wert": 80}}),
        ("bubble.rate", {"id": "bub1", "score": 70}, {"id": "bub1", "score": 70},
         {"art": "zeile", "tabelle": "ideas", "id": "bub1", "felder": {"score": 70}}),
        ("idea.rate_reason", {"bubble_id": "bub1", "text": " traegt ", "idea_ids": ["n1"]},
         {"bubble_id": "bub1", "bewertung": {"id": "e1"}},
         {"art": "eintrag", "bubble_id": "bub1", "eintrag_id": "e1", "text": "traegt", "karten": ["n1"]}),
        ("idea.arrange", {"bubble_id": "bub1"},
         {"bubble_id": "bub1", "anzahl": 1, "verschoben": 1, "positionen": [{"id": "n1", "x": 0, "y": 140}]},
         {"art": "lagen", "bubble_id": "bub1", "lagen": [{"id": "n1", "x": 0, "y": 140}]}),
        ("idea.auto_link", {"bubble_id": "bub1"},
         {"bubble_id": "bub1", "anzahl": 1, "angelegt": [{"from_id": "n1", "to_id": "n2", "ueberlappung": 0.5}]},
         {"art": "kanten", "kanten": [["n1", "n2"]]}),
        ("bubble.promote", {"bubble_id": "bub1"}, {"id": "p1", "from_idea_id": "bub1", "name": "Messe"},
         {"art": "befoerdert", "bubble_id": "bub1", "project_id": "p1"}),
    ]
    for event_id, argumente, inhalt, erwartet in faelle:
        # Der Ausfuehrer haengt die Plan-Kennung an; sie stoert die Nachpruefung nicht.
        assert vertrag.nachpruefung(event_id, {**argumente, "aenderung_id": "plan_1"}, _quittung(inhalt)) == (
            erwartet, ""), event_id


@pytest.mark.parametrize("event_id, argumente, inhalt", [
    ("idea.update", {"id": "n1", "title": "x"}, [{"id": "n2"}]),            # fremde Zeile
    ("idea.update", {"id": "n1", "title": "x"}, []),                        # nichts geaendert
    ("idea.rate", {"id": "n1", "score": 5}, {"error": "idea_not_found"}),   # Fehler als Inhalt
    ("idea.format", {"id": "n1", "format": "revert"}, {"id": "n1"}),        # Format unbekannt
    ("idea.arrange", {"bubble_id": "bub1"}, {"bubble_id": "bub2", "positionen": []}),
    ("idea.arrange", {"bubble_id": "bub1"}, {"bubble_id": "bub1", "positionen": [{"id": "n1", "x": "0", "y": 0}]}),
    ("idea.auto_link", {"bubble_id": "bub1"}, {"bubble_id": "bub1", "angelegt": [{"from_id": "n1"}]}),
    ("idea.rate_reason", {"bubble_id": "bub1", "text": "x"}, {"bubble_id": "bub1", "bewertung": {}}),
    ("bubble.promote", {"bubble_id": "bub1"}, {"id": "p1", "from_idea_id": "bub2"}),
    ("idea.list", {}, []),                                                  # Lesen: keine Nachpruefung
])
def test_unbrauchbare_quittung_ergibt_keine_erwartung(event_id, argumente, inhalt) -> None:
    erwartet, grund = vertrag.nachpruefung(event_id, argumente, _quittung(inhalt))
    assert erwartet is None and grund


# ── Nachpruefung am Zustand ──────────────────────────────────────────────

def _lies(tabellen):
    gelesen = []

    def lies(tabelle, params):
        gelesen.append((tabelle, dict(params)))
        return tabellen.get(tabelle)

    lies.gelesen = gelesen
    return lies


def test_zeile_bestaetigt_widerlegt_oder_nicht_pruefbar() -> None:
    erwartet = {"art": "zeile", "tabelle": "canvas_nodes", "id": "n1",
                "felder": {"metadata.bewertung.wert": 80, "title": "Preise"}}
    gut = {"id": "n1", "title": "Preise", "metadata": {"bewertung": {"wert": 80.0}}}

    lies = _lies({"canvas_nodes": [gut]})
    assert vertrag.pruefe_zustand(erwartet, lies)[0] is True
    assert lies.gelesen == [("canvas_nodes", {"select": "*", "id": "eq.n1", "limit": "2"})]

    falsch = {**gut, "metadata": {"bewertung": {"wert": 5}}}
    ok, signal, grund = vertrag.pruefe_zustand(erwartet, _lies({"canvas_nodes": [falsch]}))
    assert ok is False and signal["abweichend"] == ["metadata.bewertung.wert"]
    assert vertrag.pruefe_zustand(erwartet, _lies({"canvas_nodes": []}))[0] is False
    assert vertrag.pruefe_zustand(erwartet, _lies({}))[0] is None                       # nicht lesbar
    assert vertrag.pruefe_zustand(erwartet, _lies({"canvas_nodes": [gut, gut]}))[0] is None
    assert vertrag.pruefe_zustand({**erwartet, "tabelle": "projects"}, _lies({}))[0] is None
    assert vertrag.pruefe_zustand({**erwartet, "id": "n1,n2"}, _lies({"canvas_nodes": [gut]}))[0] is None


def test_eintrag_lagen_kanten_und_befoerderung() -> None:
    eintrag = {"art": "eintrag", "bubble_id": "bub1", "eintrag_id": "e1", "text": "traegt", "karten": ["n1"]}
    bubble = {"id": "bub1", "metadata": {"bewertungen": [{"id": "e1", "text": "traegt", "karten": ["n1"]}]}}
    assert vertrag.pruefe_zustand(eintrag, _lies({"ideas": [bubble]}))[0] is True
    assert vertrag.pruefe_zustand({**eintrag, "text": "anders"}, _lies({"ideas": [bubble]}))[0] is False
    assert vertrag.pruefe_zustand({**eintrag, "eintrag_id": "e2"}, _lies({"ideas": [bubble]}))[0] is False

    lagen = {"art": "lagen", "bubble_id": "bub1", "lagen": [{"id": "n1", "x": 0, "y": 140}]}
    assert vertrag.pruefe_zustand(lagen, _lies({"canvas_nodes": [{"id": "n1", "x": 0.0, "y": 140.0}]}))[0] is True
    assert vertrag.pruefe_zustand(lagen, _lies({"canvas_nodes": [{"id": "n1", "x": 1, "y": 140}]}))[0] is False
    assert vertrag.pruefe_zustand(lagen, _lies({}))[0] is None
    assert vertrag.pruefe_zustand({**lagen, "lagen": []}, _lies({}))[0] is True

    kanten = {"art": "kanten", "kanten": [["n1", "n2"]]}
    lies = _lies({"canvas_edges": [{"from_node_id": "n1", "to_node_id": "n2"}]})
    assert vertrag.pruefe_zustand(kanten, lies)[0] is True
    assert lies.gelesen[0][1]["from_node_id"] == "in.(n1)"
    assert vertrag.pruefe_zustand(kanten, _lies({"canvas_edges": []}))[0] is False
    assert vertrag.pruefe_zustand({"art": "kanten", "kanten": []}, _lies({}))[0] is True

    befoerdert = {"art": "befoerdert", "bubble_id": "bub1", "project_id": "p1"}
    zustand = {"ideas": [{"id": "bub1", "status": "promoted", "promoted_to_project_id": "p1"}],
               "projects": [{"id": "p1", "from_idea_id": "bub1"}]}
    assert vertrag.pruefe_zustand(befoerdert, _lies(zustand))[0] is True
    assert vertrag.pruefe_zustand(befoerdert, _lies({**zustand, "projects": []}))[0] is False
    nicht = {**zustand, "ideas": [{"id": "bub1", "status": "active", "promoted_to_project_id": None}]}
    assert vertrag.pruefe_zustand(befoerdert, _lies(nicht))[0] is False
    assert vertrag.pruefe_zustand({"art": "unbekannt"}, _lies(zustand))[0] is None

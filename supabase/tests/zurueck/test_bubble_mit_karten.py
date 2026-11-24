"""Bubble loeschen, die Karten gehen mit (Entscheidung des Betreibers, 24.11.2026).

Echter Ideen-Server (``spaces/ideas/mcp_server.py``, Werkzeug ``bubble_delete`` mit
``mit_karten``) gegen ein echtes Wegwerf-Postgres mit Aenderungsbuch. Nachgestellt ist
nur die REST-Schicht: jede Anfrage des Servers wird hier zu SQL, in einer eigenen
Transaktion und mit dem Kopf ``x-aenderung-id`` wie PostgREST ihn setzt. Danach:

- die Nachpruefung des Gehirns (``core/ideen_fest_contract``) liest Bubble, Karten und
  Verbindungen zurueck und bestaetigt;
- "Zurueck" (``ideen_zurueck``) holt Bubble (mit ihren Begruendungen), Karten und
  Verbindungen in einem Zug wieder;
- mehr als 200 Karten: nichts wird geloescht.

Laeuft nur mit ``ZURUECK_TEST_DSN`` (siehe conftest); ohne ist dieser Teil NICHT geprueft.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from conftest import alle, als_agent, als_hand, eine
from hilfen import bubble, idee, kante, zurueck

WURZEL = Path(__file__).resolve().parents[3]


def _lade(name: str, pfad: Path):
    spec = importlib.util.spec_from_file_location(name, pfad)
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    spec.loader.exec_module(modul)
    return modul


server = _lade("ideen_server_gegen_postgres", WURZEL / "spaces" / "ideas" / "mcp_server.py")
vertrag = _lade("ideen_vertrag_gegen_postgres", WURZEL / "brain" / "the_brain" / "core" / "ideen_fest_contract.py")

_SPALTEN = {"ideas", "canvas_nodes", "canvas_edges"}


def _bedingungen(params: dict) -> tuple[str, list]:
    teile, werte = [], []
    for feld, bedingung in params.items():
        if feld in ("select", "limit", "order"):
            continue
        spalte = f'"{feld}"'
        if bedingung == "is.null":
            teile.append(f"{spalte} IS NULL")
        elif bedingung.startswith("eq."):
            teile.append(f"{spalte} = %s")
            werte.append(bedingung[3:])
        elif bedingung.startswith("in.(") and bedingung.endswith(")"):
            teile.append(f"{spalte} = ANY(%s)")
            werte.append(bedingung[4:-1].split(","))
        else:
            raise AssertionError(f"Filter nicht nachgestellt: {feld}={bedingung}")
    return (" AND ".join(teile) or "true"), werte


class _Rest:
    """Die REST-Schicht des Servers als SQL; GET liest, DELETE loescht mit Kopf."""

    def __init__(self, db):
        self.db = db

    def __call__(self, method, path, *, params=None, body=None, env=None, error_detail=False):
        assert path in _SPALTEN, path
        params = dict(params or {})
        wo, werte = _bedingungen(params)
        if method == "GET":
            auswahl = params.get("select", "*")
            spalten = "*" if auswahl == "*" else ", ".join(f'"{s}"' for s in auswahl.split(","))
            grenze = int(params.get("limit", "1000"))
            return [dict(z) for z in alle(self.db, f"SELECT {spalten} FROM public.{path} WHERE {wo} LIMIT {grenze}",
                                          tuple(werte))]
        if method == "DELETE":
            gruppe = server._CHANGE_ID.get()
            kontext = als_agent(self.db, gruppe) if gruppe else als_hand(self.db)
            with kontext:
                with self.db.cursor(row_factory=psycopg.rows.dict_row) as cur:
                    cur.execute(f"DELETE FROM public.{path} WHERE {wo} RETURNING *", tuple(werte))
                    return [json.loads(json.dumps(dict(z), default=str)) for z in cur.fetchall()]
        raise AssertionError(method)


@pytest.fixture()
def rest(db, monkeypatch):
    adapter = _Rest(db)
    monkeypatch.setattr(server, "_request", adapter)
    return adapter


def _lies(db):
    def lies(tabelle, params):
        return _Rest(db)("GET", tabelle, params=params)
    return lies


def _anlegen(db) -> None:
    with als_hand(db):
        bubble(db, "b1", "Stadtfest")
        db.execute("UPDATE public.ideas SET metadata = %s WHERE id = 'b1'",
                   (json.dumps({"bewertungen": [{"id": "r1", "text": "Sponsoren sind da"}]}),))
        bubble(db, "b2", "Druckerei")
        idee(db, "n1", "b1", "Buehnenplan", "Inhalt eins")
        idee(db, "n2", "b1", "Sponsoren")
        idee(db, "n3", "b2", "Angebot")
        kante(db, "k1", "n1", "n2")
        kante(db, "k2", "n3", "n1")      # hinueber aus einer anderen Bubble


def test_bubble_mit_karten_loeschen_pruefen_und_zurueck(db, rest):
    _anlegen(db)
    argumente = {"id": "b1", "mit_karten": True}
    ergebnis = server.call_tool("bubble_delete", {**argumente, "aenderung_id": "plan-1"})

    assert [z["id"] for z in ergebnis] == ["b1"]
    assert sorted(ergebnis[0]["karten_geloescht"]) == ["n1", "n2"]
    assert eine(db, "SELECT count(*) AS n FROM public.ideas WHERE id = 'b1'")["n"] == 0
    assert [z["id"] for z in alle(db, "SELECT id FROM public.canvas_nodes ORDER BY id")] == ["n3"]
    assert alle(db, "SELECT id FROM public.canvas_edges") == []

    # Nachpruefung des Gehirns gegen die echte Datenbank.
    erwartung, grund = vertrag.nachpruefung("bubble.delete", argumente, ergebnis)
    assert erwartung is not None, grund
    assert (erwartung["art"], erwartung["id"], sorted(erwartung["karten"])) == ("bubble_geloescht", "b1", ["n1", "n2"])
    ok, _signal, warum = vertrag.pruefe_zustand(erwartung, _lies(db))
    assert ok is True, warum

    # "Zurueck" holt alles in einem Zug.
    antwort = zurueck(db)
    assert antwort["gruppe"] == "plan-1"
    bubble_zeile = eine(db, "SELECT title, metadata FROM public.ideas WHERE id = 'b1'")
    assert bubble_zeile["title"] == "Stadtfest"
    assert bubble_zeile["metadata"]["bewertungen"][0]["id"] == "r1"
    karten = alle(db, "SELECT id, linked_idea_id, content FROM public.canvas_nodes ORDER BY id")
    assert [(k["id"], k["linked_idea_id"]) for k in karten] == [("n1", "b1"), ("n2", "b1"), ("n3", "b2")]
    assert karten[0]["content"] == "Inhalt eins"
    kanten = alle(db, "SELECT id, from_node_id, to_node_id FROM public.canvas_edges ORDER BY id")
    assert [(k["id"], k["from_node_id"], k["to_node_id"]) for k in kanten] == [("k1", "n1", "n2"),
                                                                                 ("k2", "n3", "n1")]
    # Danach faellt dieselbe Nachpruefung durch: die Bubble ist wieder da.
    ok, _signal, _warum = vertrag.pruefe_zustand(erwartung, _lies(db))
    assert ok is False


def test_ueber_200_karten_loescht_nichts(db, rest):
    with als_hand(db):
        bubble(db, "b1", "Grosse Bubble")
        for i in range(201):
            idee(db, f"n{i:03d}", "b1", f"Karte {i}")
    with pytest.raises(server.ToolError, match="too_many_cards"):
        server.call_tool("bubble_delete", {"id": "b1", "mit_karten": True, "aenderung_id": "plan-2"})
    assert eine(db, "SELECT count(*) AS n FROM public.ideas WHERE id = 'b1'")["n"] == 1
    assert eine(db, "SELECT count(*) AS n FROM public.canvas_nodes WHERE linked_idea_id = 'b1'")["n"] == 201
    assert eine(db, "SELECT count(*) AS n FROM public.ideen_aenderungen WHERE gruppe = 'plan-2'")["n"] == 0

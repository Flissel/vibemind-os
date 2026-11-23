"""Beide Enden zusammen: Argumente des Vertrags -> Ausfuehrer -> echte Werkzeugfunktion.

Luecke bis 23.11.2026: Vertrag (Gehirn) und Ideen-Server wurden getrennt getestet. Im
Betrieb kam beim Werkzeug ``{"value": {...}}`` an, und jedes Werkzeug lehnte ab.
Dieser Test schickt die Argumente GENAU so, wie der Plan-Ausfuehrer es tut
(``McpExecutor.call_with_arg(argumente, extra_params={"_runtime_authority": ...})``),
durch die JSON-RPC-Schicht in ``spaces/ideas/mcp_server.py``. Nachgestellt sind nur
OpenFang (reicht den Aufruf durch) und die Datenbank (im Speicher).

Ohne torch: Das Paket ``core`` wird als leere Huelle angelegt, seine Module laden
einzeln. Damit das keinen anderen Test stoert, laeuft die Pruefung im eigenen Prozess.

Direkt:  python brain/the_brain/tests/test_ideen_vertrag_bis_werkzeug.py
"""

from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import re
import subprocess
import sys
import types
from pathlib import Path

_BRAIN = Path(__file__).resolve().parents[1]
_ROOT = _BRAIN.parents[1]


def test_vertrag_bis_werkzeug_in_eigenem_prozess() -> None:
    lauf = subprocess.run([sys.executable, str(Path(__file__).resolve())],
                          capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert "FAIL" not in lauf.stdout and "PASS" in lauf.stdout, lauf.stdout


class Datenbank:
    """Kleine Nachstellung der REST-Schicht: Gleichheit, ilike, in, is.null, order, limit."""

    def __init__(self) -> None:
        self.tabellen = {
            "ideas": [{"id": "b0b1e001", "title": "Messe", "description": "", "parent_id": None, "score": 0,
                       "status": "active", "promoted_to_project_id": None, "metadata": {}}],
            "canvas_nodes": [
                {"id": "a0000001", "title": "Preise Druckerei", "content": "A 1", "node_type": "note", "x": 500, "y": 90,
                 "linked_idea_id": "b0b1e001", "metadata": {}, "format_schema": None, "content_json": None,
                 "previous_content_json": None},
                {"id": "a0000002", "title": "Druckerei Preise", "content": "", "node_type": "note", "x": 10, "y": 400,
                 "linked_idea_id": "b0b1e001", "metadata": {}, "format_schema": None, "content_json": None,
                 "previous_content_json": None},
            ],
            "canvas_edges": [],
            "projects": [],
        }
        self.kopf_kennungen: list = []

    def _passt(self, zeile: dict, params: dict) -> bool:
        for feld, bedingung in params.items():
            if feld in ("select", "limit", "order"):
                continue
            wert = zeile.get(feld)
            if bedingung == "is.null":
                if wert is not None:
                    return False
            elif bedingung.startswith("eq."):
                if str(wert) != bedingung[3:]:
                    return False
            elif bedingung.startswith("ilike."):
                if str(wert or "").casefold() != bedingung[6:].casefold():
                    return False
            elif bedingung.startswith("in.("):
                if str(wert) not in bedingung[4:-1].split(","):
                    return False
            else:
                raise AssertionError(f"Filter nicht nachgestellt: {feld}={bedingung}")
        return True

    def request(self, method, path, *, params=None, body=None, env=None, error_detail=False):
        zeilen = self.tabellen[path]
        params = dict(params or {})
        if method == "GET":
            treffer = [copy.deepcopy(z) for z in zeilen if self._passt(z, params)]
            if params.get("order") == "id.asc":
                treffer.sort(key=lambda z: z["id"])
            return treffer[: int(params.get("limit", "1000"))]
        if method == "POST":
            zeile = copy.deepcopy(body)
            zeilen.append(zeile)
            return [copy.deepcopy(zeile)]
        if method == "PATCH":
            geaendert = []
            for zeile in zeilen:
                if self._passt(zeile, params):
                    zeile.update(copy.deepcopy(body))
                    geaendert.append(copy.deepcopy(zeile))
            return geaendert
        if method == "DELETE":
            self.tabellen[path] = [z for z in zeilen if not self._passt(z, params)]
            return []
        raise AssertionError(method)


def _lade_datei(name: str, pfad: Path):
    spec = importlib.util.spec_from_file_location(name, pfad)
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    spec.loader.exec_module(modul)
    return modul


def _hauptlauf() -> int:
    # Paket "core" als Huelle: Untermodule laden normal, core/__init__.py (torch) nicht.
    core = types.ModuleType("core")
    core.__path__ = [str(_BRAIN / "core")]
    sys.modules["core"] = core
    sys.path.insert(0, str(_BRAIN))
    ziele = importlib.import_module("core.capability_targets")
    vertrag = importlib.import_module("core.ideen_fest_contract")
    abk = importlib.import_module("core.ideen_abkuerzung")
    connect = importlib.import_module("core.idea_connect_contract")
    authority = importlib.import_module("core.openfang_runtime_authority")
    server = _lade_datei("ideen_server_unter_test", _ROOT / "spaces" / "ideas" / "mcp_server.py")

    fehler: list = []

    def check(name, cond, hinweis=""):
        print(("  PASS " if cond else "  FAIL ") + name + (f"  [{hinweis}]" if hinweis and not cond else ""))
        if not cond:
            fehler.append(name)

    db = Datenbank()
    server._request = db.request
    angekommen: list = []

    class _Bereit:
        agent_id = "agent-uuid"
        approval_ref = "appr-1"
        cost_ref = "cost-1"
        invocation_id = "inv-1"

    class _Authority:
        @classmethod
        def from_environment(cls):
            return cls()

        def prepare_invocation(self, context):
            return _Bereit()

    ziele.RuntimeAuthorityClient = _Authority

    def openfang(self, method, url, *, headers, payload=None, retry_transient=False, timeout_cap_s=None):
        """OpenFang reicht tools/call an den Ideen-Server durch (Name ohne Vorsilbe)."""
        assert url.endswith("/mcp"), url
        name = payload["params"]["name"]
        assert name.startswith("mcp_spaces_ideas_"), name
        argumente = payload["params"]["arguments"]
        angekommen.append((name[len("mcp_spaces_ideas_"):], copy.deepcopy(argumente)))
        antwort = server.handle_message({
            "jsonrpc": "2.0", "id": payload["id"], "method": "tools/call",
            "params": {"name": name[len("mcp_spaces_ideas_"):], "arguments": argumente},
        })
        return json.loads(json.dumps(antwort))

    ziele.McpExecutor._request_json = openfang
    ziele.McpExecutor._configuration = lambda self: ("http://openfang.test", "schluessel-nur-im-test")

    def rufe(event_id: str, argumente: dict, plan_id: str = "ideen_1795000000000"):
        ziel = ziele.resolve_registry_execution_target(event_id)
        instanz = ziele.find_registered_mcp_authority(*ziel.split(":", 3)[1:])
        kontext = authority.RuntimeInvocationContext(
            correlation_id="tr_1", plan_id=plan_id, plan_revision=1, step_id="s1",
            space_id=instanz.space_id, agent_name=instanz.agent,
            invocation_id=authority.runtime_invocation_id(plan_id, 1, "s1"),
        )
        # Genau der Aufruf des Plan-Ausfuehrers fuer mcp:-Ziele.
        return ziele.build_executor(ziel).call_with_arg(argumente, extra_params={"_runtime_authority": kontext})

    def lies(tabelle, params):
        return db.request("GET", tabelle, params=params)

    # Argumente je Ereignis: wo es einen Beispielsatz gibt, aus der Abkuerzung.
    app = {"space": "ideas", "bubble": {"id": "b0b1e001", "title": "Messe"},
           "cards": [{"id": "a0000001", "title": "Preise Druckerei"}, {"id": "a0000002", "title": "Druckerei Preise"}],
           "selection": ["a0000001"], "last_id": "a0000001", "new_id": "7f3a0000-0000-4000-8000-000000000001"}
    faelle = [
        ("idea.create", abk.loese("Halt fest: Zeitmessung bitte wieder loeschen", app)["argumente"]),
        ("idea.update", abk.loese("Benenne die Notiz Druckerei Preise in Preisliste um", app)["argumente"]),
        ("idea.update", abk.loese("Ergänze bei Preise Druckerei: B 2", app)["argumente"]),
        ("idea.list", abk.loese("Zeig die Notizen dieser Bubble", app)["argumente"]),
        ("idea.list", {}),
        # Formatieren braucht Text (leere Karte: idea_format_no_content), darum vor dem Leeren.
        ("idea.format", abk.loese("Mach aus der Notiz Preise Druckerei eine Tabelle", app)["argumente"]),
        ("idea.format", {"id": "a0000001", "format": "revert"}),
        ("idea.update", {"id": "a0000001", "content": ""}),                       # Inhalt leeren
        ("idea.rate", abk.loese("Bewerte Preise Druckerei mit 80", app)["argumente"]),
        ("idea.rate_reason", abk.loese("Begründung: traegt die Idee {wirklich}", app)["argumente"]),
        ("idea.auto_link", {"bubble_id": "b0b1e001"}),
        ("idea.arrange", abk.loese("Sortiere die Karten nach Bewertung", app)["argumente"]),
        ("bubble.rate", abk.loese("Bewerte die Bubble Messe mit 70", app)["argumente"]),
        ("bubble.promote", abk.loese("Befördere diese Bubble", app)["argumente"]),
        # Dokument-Karte (core/ideen_dokument): das Gehirn schreibt sie am Ende eines White Papers.
        ("idea.document", {"id": "00000000-0000-4000-8000-000000000d01", "bubble_id": "b0b1e001",
                           "title": "White Paper: Messe", "art": "white_paper",
                           "content": "# White Paper: Messe\n\n## Inhalt\n\n1. Preise\n", "quellen": ["a0000001"]}),
    ]
    gesehen = set()
    for event_id, argumente in faelle:
        gesehen.add(event_id)
        spec = vertrag.FESTE_EREIGNISSE[event_id]
        streng, rueckfrage = vertrag.feste_argumente(event_id, abk.arg_vorlage(argumente) if argumente else "")
        check(f"{event_id}: Vertrag nimmt die Argumente an", streng == argumente, rueckfrage)
        vorher = len(angekommen)
        antwort = rufe(event_id, streng)
        check(f"{event_id}: Werkzeug nimmt die Argumente an", antwort.get("ok") is True, str(antwort.get("error")))
        if len(angekommen) == vorher:
            check(f"{event_id}: Aufruf kam beim Werkzeug an", False)
            continue
        werkzeug, beim_werkzeug = angekommen[-1]
        erwartet = dict(streng)
        if not spec.get("liest_nur"):
            erwartet["aenderung_id"] = "ideen_1795000000000"
        check(f"{event_id}: beim Werkzeug kommen genau die Argumente an (kein 'value', keine Herkunft)",
              werkzeug == spec["werkzeug"] and beim_werkzeug == erwartet, json.dumps(beim_werkzeug)[:200])
        if event_id == "idea.list" and antwort.get("ok") is True:
            # Die Form der Liste (Karten plus gezaehlte Gesamtzahl) muss beim festen Satz ankommen.
            satz = abk.antwort_fuer_schritt(event_id, True, antwort["result"], None, "", app)
            check("idea.list: echte Antwort ergibt den festen Satz mit Zahl",
                  isinstance(satz, str) and (satz[:1].isdigit() or satz.startswith("In „Messe“")), str(satz))
        if antwort.get("ok") is not True or spec.get("liest_nur"):
            continue
        # Die Quittung des echten Werkzeugs muss zur Nachpruefung des Gehirns passen.
        erwartung, grund = vertrag.nachpruefung(event_id, beim_werkzeug, antwort["result"])
        check(f"{event_id}: Quittung ergibt eine Erwartung", erwartung is not None, grund)
        if erwartung is not None:
            ok, _signal, warum = vertrag.pruefe_zustand(erwartung, lies)
            check(f"{event_id}: Zustand bestaetigt", ok is True, warum)
    check("alle zehn Ereignisse geprueft", gesehen == set(vertrag.FESTE_EREIGNISSE),
          str(set(vertrag.FESTE_EREIGNISSE) - gesehen))

    # Der aeltere Weg profitiert von derselben Korrektur.
    treffer = abk.loese("Verbinde Preisliste mit Zeitmessung bitte wieder loeschen",
                        {**app, "cards": [{"id": "a0000002", "title": "Preisliste"},
                                          {"id": "7f3a0000-0000-4000-8000-000000000001", "title": "Zeitmessung bitte wieder loeschen"}]})
    streng, _ = connect.canonical_idea_connect_arguments(treffer["argumente"])
    antwort = rufe("idea.connect", streng)
    check("idea.connect: Werkzeug nimmt die Argumente an", antwort.get("ok") is True, str(antwort.get("error")))
    check("idea.connect: ohne Kennung der Aenderung (aelterer Vertrag)",
          angekommen[-1] == ("idea_connect", streng), json.dumps(angekommen[-1][1]))
    quittung, _ = connect.extract_idea_connect_mcp_receipt(antwort.get("result"))
    check("idea.connect: Quittung lesbar", quittung is not None)

    # Der Fehlertext des Werkzeugs kommt bis in die Antwort: Fehlername und Feld, nie ein Wert.
    kaputt = ziele.build_executor(ziele.resolve_registry_execution_target("idea.rate"))
    ziel = ziele.resolve_registry_execution_target("idea.rate")
    instanz = ziele.find_registered_mcp_authority(*ziel.split(":", 3)[1:])
    kontext = authority.RuntimeInvocationContext(
        correlation_id="tr_2", plan_id="ideen_2", plan_revision=1, step_id="s1",
        space_id=instanz.space_id, agent_name=instanz.agent,
        invocation_id=authority.runtime_invocation_id("ideen_2", 1, "s1"))
    antwort = kaputt.call_with_arg({"id": "a0000001", "score": 999, "geheim": "wert-4711"},
                                   extra_params={"_runtime_authority": kontext})
    meldung = str(antwort.get("error"))
    check("Fehler nennt Fehlername und Feld", antwort.get("ok") is False
          and re.search(r"invalid_arguments: (geheim|score)\)", meldung) is not None, meldung)
    check("Fehler nennt keine Werte", "999" not in meldung and "wert-4711" not in meldung, meldung)
    antwort = kaputt.call_with_arg({"id": "gibt-es-nicht", "score": 5}, extra_params={"_runtime_authority": kontext})
    check("Fehler ohne Feld nennt nur den Fehlernamen",
          str(antwort.get("error")).endswith("Ideen-Werkzeug meldet einen Fehler (idea_not_found)"),
          str(antwort.get("error")))

    print(f"{'FEHLER' if fehler else 'OK'}: {len(fehler)} fehlgeschlagen")
    return 1 if fehler else 0


if __name__ == "__main__":
    raise SystemExit(_hauptlauf())

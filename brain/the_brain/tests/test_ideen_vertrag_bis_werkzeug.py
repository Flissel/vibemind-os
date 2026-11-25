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
                       "status": "active", "promoted_to_project_id": None, "metadata": {}},
                      {"id": "b0b1e002", "title": "Alte Messe", "description": "", "parent_id": None, "score": 0,
                       "status": "active", "promoted_to_project_id": None, "metadata": {}}],
            "canvas_nodes": [
                {"id": "a0000001", "title": "Preise Druckerei", "content": "A: 1", "node_type": "note", "x": 500, "y": 90,
                 "linked_idea_id": "b0b1e001", "metadata": {}, "format_schema": None, "content_json": None,
                 "previous_content_json": None},
                {"id": "a0000002", "title": "Druckerei Preise", "content": "", "node_type": "note", "x": 10, "y": 400,
                 "linked_idea_id": "b0b1e001", "metadata": {}, "format_schema": None, "content_json": None,
                 "previous_content_json": None},
                {"id": "a0000009", "title": "Standplan", "content": "", "node_type": "note", "x": 0, "y": 0,
                 "linked_idea_id": "b0b1e002", "metadata": {}, "format_schema": None, "content_json": None,
                 "previous_content_json": None},
                {"id": "a000000a", "title": "Zeltplan", "content": "", "node_type": "note", "x": 0, "y": 0,
                 "linked_idea_id": "b0b1e002", "metadata": {}, "format_schema": None, "content_json": None,
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
            # Wie PostgREST mit "Prefer: return=representation": die geloeschten Zeilen.
            weg = [copy.deepcopy(z) for z in zeilen if self._passt(z, params)]
            self.tabellen[path] = [z for z in zeilen if not self._passt(z, params)]
            if path == "canvas_nodes":
                # canvas_edges.from_node_id/to_node_id: ON DELETE CASCADE
                ids = {z["id"] for z in weg}
                self.tabellen["canvas_edges"] = [k for k in self.tabellen["canvas_edges"]
                                                 if k["from_node_id"] not in ids and k["to_node_id"] not in ids]
            if path == "ideas":
                # canvas_nodes.linked_idea_id: ON DELETE SET NULL
                for karte in self.tabellen["canvas_nodes"]:
                    if karte.get("linked_idea_id") in {z["id"] for z in weg}:
                        karte["linked_idea_id"] = None
            return weg
        raise AssertionError(method)


def _lade_datei(name: str, pfad: Path):
    spec = importlib.util.spec_from_file_location(name, pfad)
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    spec.loader.exec_module(modul)
    return modul


def stufe1(check, db, server, rufe, lies, abk, vertrag, gesehen) -> None:
    """Bewerten -> speichern -> Befoerdern sagt Score und To-dos -> To-do erledigen -> veraltet."""
    bew = importlib.import_module("core.ideen_bewertung")
    dok = importlib.import_module("core.ideen_dokument")
    bf = importlib.import_module("core.befoerdern_bestaetigung")

    handwert = next(b for b in db.tabellen["ideas"] if b["id"] == "b0b1e001")["metadata"].get("handwert") or {}
    check("bubble.rate: der Handwert steht getrennt in metadata.handwert", handwert.get("score") == 70,
          json.dumps(handwert))

    marke = "--- MiroFish Evaluation: Offene Punkte ---"
    db.tabellen["ideas"].append({"id": "b0b1e006", "title": "Stadtfest Süd", "parent_id": None, "score": 47,
                                 "description": f"Fest im Park.\n\n{marke}\n1. Bühnenplan fehlt",
                                 "status": "active", "promoted_to_project_id": None, "metadata": {}})
    for kennung, titel, inhalt, y in (("a0000061", "Bühnenplan", "Zwei Bühnen, Samstag und Sonntag ab 14 Uhr.", 0),
                                      ("a0000062", "Sponsoren", "", 200),
                                      ("a0000063", "Druckerei", "Plakate in A1, Lieferung eine Woche vorher.", 400)):
        db.tabellen["canvas_nodes"].append({
            "id": kennung, "title": titel, "content": inhalt, "node_type": "note", "x": 0, "y": y,
            "linked_idea_id": "b0b1e006", "metadata": {}, "format_schema": None, "content_json": None,
            "previous_content_json": None, "summary": None, "updated_at": "2026-11-25T08:00:00+00:00"})
    db.tabellen["canvas_edges"].append({"id": "e0000061", "from_node_id": "a0000061", "to_node_id": "a0000063",
                                        "edge_type": "related"})

    class TestLesen(dok.Lesen):
        def bubble(self, kennung):
            zeilen = db.request("GET", "ideas", params={"id": f"eq.{kennung}", "parent_id": "is.null"})
            return zeilen[0] if len(zeilen) == 1 else None

        def bubbles_mit_titel(self, titel):
            return db.request("GET", "ideas", params={"title": f"ilike.{titel}", "parent_id": "is.null"})

        def karten_der_bubble(self, kennung):
            return db.request("GET", "canvas_nodes", params={"linked_idea_id": f"eq.{kennung}"})

        def karten(self, kennungen):
            return db.request("GET", "canvas_nodes", params={"id": f"in.({','.join(kennungen)})"})

        def kanten(self, kennungen):
            aus = []
            for richtung in ("from_node_id", "to_node_id"):
                for k in db.request("GET", "canvas_edges", params={richtung: f"in.({','.join(kennungen)})"}):
                    if k not in aus:
                        aus.append(k)
            return aus

    lesen = TestLesen()
    nachricht, rw = dok.auftrag_vorbereiten("idea.bewerten", {"bubble_id": "b0b1e006"}, "", lesen=lesen)
    _bubble, server_stand, _ids = server._bubble_mit_stand("b0b1e006", "id")
    check("Bewerten: Gehirn und Server rechnen denselben kartenstand", rw["kartenstand"] == server_stand,
          f"{rw['kartenstand']} != {server_stand}")
    check("Bewerten: die Nachricht traegt vollen Kartentext, Verbindung und alle fuenf Sichten",
          "Zwei Bühnen" in nachricht and "K1 -> K3 (related)" in nachricht
          and all(n in nachricht for n in ("coding", "swe_design", "research", "rowboat", "ideas")), nachricht[:300])
    check("Bewerten: Rueckweg ist gueltig", dok.rueckweg_aus({"uebergabe": {"rueckweg": rw}}) == rw)

    antwort_modell = json.dumps({"sichten": {
        "coding": {"punkte": 10, "urteil": "Technik fehlt fast ganz.", "todos": [
            {"titel": "Stromplan", "fehlt": "Woher der Strom für [K1] kommt, steht nirgends.", "karten": ["K1"],
             "punkte": 4, "art": "karte_ergaenzen"}]},
        "swe_design": {"punkte": 15, "urteil": "Umfang ist grob klar.", "todos": []},
        "research": {"punkte": 12, "urteil": "Keine Quellen.", "todos": [
            {"titel": "Sponsoren füllen", "fehlt": "K2 ist leer.", "karten": ["K2"], "punkte": 6,
             "art": "karte_fuellen"}]},
        "rowboat": {"punkte": 14, "urteil": "Vorwissen teils da.", "todos": [
            {"titel": "Termin entscheiden", "fehlt": "Ob es einen Regentermin gibt.", "karten": [], "punkte": 3,
             "art": "entscheidung"}]},
        "ideas": {"punkte": 18, "urteil": "Gut verknüpft.", "todos": []}}}, ensure_ascii=False)
    lauf, satz = bew.bewertung_pruefen(antwort_modell, rw["kuerzel"], rw["kuerzel_titel"])
    check("Bewerten: die Antwort des Modells wird angenommen", lauf is not None, satz)
    check("Bewerten: Kuerzel werden zu Kennungen und im Text zu Titeln",
          lauf is not None and lauf["todos"][1]["karten"] == ["a0000062"]
          and lauf["todos"][0]["fehlt"].startswith("Woher der Strom für „Bühnenplan“"), json.dumps(lauf)[:300])
    kaputt = antwort_modell.replace('"K2"]', '"K9"]')
    check("Bewerten: eine erfundene Karte wird abgelehnt", bew.bewertung_pruefen(
        kaputt, rw["kuerzel"], rw["kuerzel_titel"])[0] is None)

    def aufruf(ereignis, argumente, plan_id="ideen_1795000000010"):
        antwort = rufe(ereignis, argumente, plan_id=plan_id)
        if not antwort.get("ok"):
            raise RuntimeError(str(antwort.get("error")))
        return antwort["result"]

    def pruefe(ereignis, argumente, quittung):
        erwartung, grund = vertrag.nachpruefung(ereignis, argumente, quittung)
        if erwartung is None:
            return None, grund
        ok, _signal, grund = vertrag.pruefe_zustand(erwartung, lies)
        return ok, grund

    status, ok, grund, meldung, mehr = bew.verarbeiten({"ergebnis": antwort_modell}, rw, lies_karten=lesen.karten,
                                                       aufruf=aufruf, pruefe=pruefe)
    gesehen.add("idea.bewertung_speichern")
    check("Bewerten: gespeichert und nachgeprueft", (status, ok) == ("geschrieben", True), f"{grund} {meldung}")
    check("Bewerten: die Meldung nennt Score, Sichten und das To-do mit dem groessten Gewinn",
          meldung.startswith("Bewertung: 69 von 100 (Coding 10, SWE Design 15, Research 12, Wissenslage 14, "
                             "Ideas 18).") and "2. Sponsoren füllen (+6" in meldung, meldung)
    bubble = next(b for b in db.tabellen["ideas"] if b["id"] == "b0b1e006")
    gespeichert = bubble["metadata"].get("bewertung_lauf") or {}
    check("Bewerten: ideas.score ist die Summe des Laufs", bubble["score"] == 69 == gespeichert.get("score"),
          str(bubble["score"]))
    check("Bewerten: der alte MiroFish-Block ist aus der Beschreibung in lauf.alt gewandert",
          bubble["description"] == "Fest im Park." and str(gespeichert.get("alt", "")).startswith(marke),
          json.dumps(bubble["description"]))
    check("Bewerten: eval_history traegt Zeit, Score und Sichten",
          [(e.get("art"), e.get("score"), (e.get("sichten") or {}).get("rowboat"))
           for e in bubble["metadata"].get("eval_history") or []] == [("lauf", 69, 14)],
          json.dumps(bubble["metadata"].get("eval_history")))

    def befoerdern_satz():
        karten = [k for k in db.tabellen["canvas_nodes"] if k.get("linked_idea_id") == "b0b1e006"]
        ids = {k["id"] for k in karten}
        kanten = [k for k in db.tabellen["canvas_edges"] if k["from_node_id"] in ids and k["to_node_id"] in ids]
        return bf.pruefen(next(b for b in db.tabellen["ideas"] if b["id"] == "b0b1e006"), karten, kanten)

    geprueft = befoerdern_satz()
    check("Befoerdern: unter der Schwelle nennt die Absage Score, Schwelle und die Top-To-dos",
          geprueft["bestaetigbar"] is False and "69 von 100" in geprueft["satz"] and "ab 95" in geprueft["satz"]
          and "To-do 2 „Sponsoren füllen“ (+6)" in geprueft["satz"], geprueft["satz"])

    # To-do erledigen: Rueckfrage, dann Auftrag, Ergebnis schreibt Karten und hakt ab.
    frage = bew.todo_frage(bubble, 2)
    check("To-do: die Rueckfrage nennt To-do, Gewinn und den Abo-Aufruf",
          "To-do 2 „Sponsoren füllen“" in frage and "+6" in frage and "Ja, erledigen" in frage, frage)
    try:
        bew.todo_frage(bubble, 3)
        entscheidung = ""
    except Exception as e:  # noqa: BLE001
        entscheidung = str(e)
    check("To-do: eine Entscheidung geht nicht an das Modell", "Entscheidung, die triffst du" in entscheidung,
          entscheidung)
    nachricht, rw2 = dok.auftrag_vorbereiten("idea.todo_erledigen", {"bubble_id": "b0b1e006", "nr": 2}, "",
                                             lesen=lesen)
    check("To-do: die betroffene Karte steht als K1 vorn", rw2["kuerzel"]["K1"] == "a0000062", json.dumps(rw2["kuerzel"]))
    ergebnis = json.dumps({"aenderungen": [
        {"art": "fuellen", "karte": "K1", "text": "Zwei Sponsoren zahlen die Bühne; ein dritter ist angefragt."},
        {"art": "neu", "titel": "Getränkestand", "text": "Ein Stand am Eingang, betreut vom Verein.",
         "verbinden": ["K1"]}]}, ensure_ascii=False)
    status, ok, grund, meldung, _mehr = bew.verarbeiten({"ergebnis": ergebnis}, rw2, lies_karten=lesen.karten,
                                                        aufruf=lambda e, a: aufruf(e, a, "ideen_1795000000011"),
                                                        pruefe=pruefe)
    gesehen.add("idea.todo_abhaken")
    check("To-do: Karten geschrieben, abgehakt und nachgeprueft", (status, ok) == ("geschrieben", True),
          f"{grund} {meldung}")
    neu = [k for k in db.tabellen["canvas_nodes"] if k.get("title") == "Getränkestand"]
    check("To-do: die neue Karte ist mit der betroffenen verbunden (nicht lose)",
          len(neu) == 1 and any(k["from_node_id"] == neu[0]["id"] and k["to_node_id"] == "a0000062"
                                for k in db.tabellen["canvas_edges"]), json.dumps(neu))
    todos = next(b for b in db.tabellen["ideas"] if b["id"] == "b0b1e006")["metadata"]["bewertung_lauf"]["todos"]
    check("To-do: das To-do ist vom Agenten abgehakt",
          [(t["nr"], t.get("erledigt"), t.get("erledigt_durch")) for t in todos][1] == (2, True, "agent"),
          json.dumps(todos))
    check("To-do: die Meldung schlaegt neu bewerten vor", "Bewerte diese Bubble" in meldung, meldung)
    check("Befoerdern: nach geaenderten Karten ist die Bewertung veraltet",
          "veraltet" in befoerdern_satz()["satz"], befoerdern_satz()["satz"])

    # Von Hand abhaken (fester Satz) und die Speicherung eines veralteten Stands.
    kontext = {"space": "ideas", "bubble": {"id": "b0b1e006", "title": "Stadtfest Süd"}, "cards": []}
    treffer = abk.loese("To-do 1 erledigt", kontext)
    antwort = rufe("idea.todo_abhaken", treffer["argumente"], plan_id="ideen_1795000000012")
    erwartung, grund = vertrag.nachpruefung("idea.todo_abhaken", treffer["argumente"], antwort.get("result"))
    check("To-do 1 erledigt: von Hand abgehakt und nachgeprueft",
          antwort.get("ok") is True and erwartung is not None and vertrag.pruefe_zustand(erwartung, lies)[0] is True,
          str(antwort.get("error")) + grund)
    antwort = rufe("idea.bewertung_speichern", {"bubble_id": "b0b1e006", "lauf": lauf,
                                                 "kartenstand": rw["kartenstand"]}, plan_id="ideen_1795000000013")
    check("Bewerten: ein veralteter kartenstand wird nicht gespeichert",
          antwort.get("ok") is False and "kartenstand_changed" in str(antwort.get("error")), str(antwort.get("error")))


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

    # Befoerdern (24.11.2026): nur eine Bubble, die die Sinnpruefung besteht (gemeinsame Kartenregeln):
    # zwei verbundene Karten mit Inhalt, eine Bewertung, ein White Paper.
    db.tabellen["ideas"].append({"id": "b0b1e004", "title": "Stadtfest", "description": "", "parent_id": None,
                                 "score": 70, "status": "active", "promoted_to_project_id": None, "metadata": {}})
    for kennung, titel, inhalt, art in (
            ("a0000041", "Bühnenplan", "Der Bühnenplan steht für Samstag fest.", "note"),
            ("a0000042", "Sponsoren", "Zwei Sponsoren zahlen die Bühne.", "note"),
            ("a0000043", "White Paper: Stadtfest", "# White Paper\n\nDas Stadtfest braucht einen Bühnenplan.",
             "dokument")):
        db.tabellen["canvas_nodes"].append({
            "id": kennung, "title": titel, "content": inhalt, "node_type": art, "x": 0, "y": 0,
            "linked_idea_id": "b0b1e004", "format_schema": None, "content_json": None,
            "previous_content_json": None, "updated_at": "2026-11-22T08:30:00+00:00",
            "metadata": {"art": "white_paper", "quellen": ["a0000041", "a0000042"]} if art == "dokument" else {}})
    db.tabellen["canvas_edges"].append({"id": "e0000041", "from_node_id": "a0000041",
                                        "to_node_id": "a0000042", "edge_type": "related"})

    # Argumente je Ereignis: wo es einen Beispielsatz gibt, aus der Abkuerzung.
    app = {"space": "ideas", "bubble": {"id": "b0b1e001", "title": "Messe"},
           "cards": [{"id": "a0000001", "title": "Preise Druckerei"}, {"id": "a0000002", "title": "Druckerei Preise"}],
           "selection": ["a0000001"], "last_id": "a0000001", "new_id": "7f3a0000-0000-4000-8000-000000000001"}
    faelle = [
        ("idea.create", abk.loese("Halt fest: Zeitmessung bitte wieder loeschen", app)["argumente"]),
        ("idea.update", abk.loese("Benenne die Notiz Druckerei Preise in Preisliste um", app)["argumente"]),
        ("idea.update", abk.loese("Ergänze bei Preise Druckerei: B: 2", app)["argumente"]),
        ("idea.list", abk.loese("Zeig die Notizen dieser Bubble", app)["argumente"]),
        ("idea.list", {}),
        # Formatieren braucht Text (leere Karte: idea_format_no_content), darum vor dem Leeren.
        ("idea.format", abk.loese("Mach aus der Notiz Preise Druckerei eine Tabelle", app)["argumente"]),
        ("idea.format", {"id": "a0000001", "format": "revert"}),
        ("idea.update", {"id": "a0000001", "content": ""}),                       # Inhalt leeren
        ("idea.rate", abk.loese("Bewerte Preise Druckerei mit 80", app)["argumente"]),
        ("idea.rate_reason", abk.loese("Begründung: traegt die Idee {wirklich}", app)["argumente"]),
        # ... und wieder weg: die neueste Begruendung (Nummer 1), wie angezeigt gezaehlt.
        ("idea.rate_reason_delete", abk.loese("Lösche die Begründung 1", app)["argumente"]),
        ("idea.auto_link", {"bubble_id": "b0b1e001"}),
        ("idea.arrange", abk.loese("Sortiere die Karten nach Bewertung", app)["argumente"]),
        ("bubble.rate", abk.loese("Bewerte die Bubble Messe mit 70", app)["argumente"]),
        # Gruppen nach Thema (Rueckweg "gruppieren"): Ueberschrift-Karte, Lage und Verbindung in einer Aenderung.
        ("idea.group_layout", {"bubble_id": "b0b1e001", "verbinden": True,
                               "gruppen": [{"name": "Druckerei", "ids": ["a0000001", "a0000002"]}]}),
        # Wurzel festlegen und wieder loeschen (metadata.wurzel der Bubble).
        ("idea.root", abk.loese("Mach Preise Druckerei zur Wurzel", app)["argumente"]),
        ("idea.root", abk.loese("Lösche die festgelegte Wurzel", app)["argumente"]),
        ("bubble.promote", abk.loese("Befördere diese Bubble",
                                     {**app, "bubble": {"id": "b0b1e004", "title": "Stadtfest"}})["argumente"]),
        # Dokument-Karte (core/ideen_dokument): das Gehirn schreibt sie am Ende eines White Papers.
        ("idea.document", {"id": "00000000-0000-4000-8000-000000000d01", "bubble_id": "b0b1e001",
                           "title": "White Paper: Messe", "art": "white_paper",
                           "content": "# White Paper: Messe\n\n## Inhalt\n\n1. Preise\n", "quellen": ["a0000001"]}),
        # Karte loeschen (nach "Ja, löschen"): Karte und ihre Verbindungen gehen, eine Aenderung.
        ("idea.delete", abk.loese("Lösche die Karte White Paper: Messe",
                                  {**app, "cards": [{"id": "00000000-0000-4000-8000-000000000d01",
                                                     "title": "White Paper: Messe"}]})["argumente"]),
        # Bubble loeschen (nach "Ja, löschen"): nur die offene, ihre Karten und Verbindungen gehen mit.
        ("bubble.delete", abk.loese("Lösche diese Bubble",
                                    {"space": "ideas", "bubble": {"id": "b0b1e002", "title": "Alte Messe"},
                                     "cards": [{"id": "a0000009", "title": "Standplan"},
                                               {"id": "a000000a", "title": "Zeltplan"}],
                                     "cards_total": 2})["argumente"]),
    ]
    db.tabellen["canvas_edges"].append({"id": "e0000001", "from_node_id": "00000000-0000-4000-8000-000000000d01",
                                        "to_node_id": "a0000001", "edge_type": "related"})
    # Verbindungen der Karten von "Alte Messe": untereinander und hinueber zu "Messe".
    db.tabellen["canvas_edges"].append({"id": "e0000002", "from_node_id": "a0000009",
                                        "to_node_id": "a000000a", "edge_type": "related"})
    db.tabellen["canvas_edges"].append({"id": "e0000003", "from_node_id": "a0000002",
                                        "to_node_id": "a000000a", "edge_type": "related"})
    erwartungen: dict = {}
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
            erwartungen[event_id] = erwartung
            ok, _signal, warum = vertrag.pruefe_zustand(erwartung, lies)
            check(f"{event_id}: Zustand bestaetigt", ok is True, warum)
    check("bubble.delete: die Karten der Bubble sind mit geloescht",
          not [k for k in db.tabellen["canvas_nodes"] if k["id"] in ("a0000009", "a000000a")])
    check("bubble.delete: ihre Verbindungen sind weg, auch die hinueber zu einer anderen Bubble",
          not [k for k in db.tabellen["canvas_edges"] if k["id"] in ("e0000002", "e0000003")])
    check("bubble.delete: die Nachpruefung liest genau die mitgeloeschten Karten zurueck",
          sorted(erwartungen.get("bubble.delete", {}).get("karten") or []) == ["a0000009", "a000000a"],
          json.dumps(erwartungen.get("bubble.delete")))
    # Die Nachpruefung merkt es, wenn eine der Karten doch noch da ist (nur losgeloest).
    db.tabellen["canvas_nodes"].append({"id": "a000000a", "title": "Zeltplan", "linked_idea_id": None})
    ok, _signal, warum = vertrag.pruefe_zustand(erwartungen.get("bubble.delete") or {}, lies)
    check("bubble.delete: eine liegengebliebene Karte faellt in der Nachpruefung auf", ok is False, warum)
    db.tabellen["canvas_nodes"] = [k for k in db.tabellen["canvas_nodes"] if k["id"] != "a000000a"]

    # Grenze: mehr als 200 Karten loescht der Weg nicht in einem Zug, auch nicht teilweise.
    db.tabellen["ideas"].append({"id": "b0b1e003", "title": "Grosse Messe", "description": "", "parent_id": None,
                                 "score": 0, "status": "active", "promoted_to_project_id": None, "metadata": {}})
    for i in range(201):
        db.tabellen["canvas_nodes"].append({"id": f"c{i:07d}", "title": f"Karte {i}", "linked_idea_id": "b0b1e003"})
    gross = {"space": "ideas", "bubble": {"id": "b0b1e003", "title": "Grosse Messe"}, "cards": [], "cards_total": 201}
    treffer = abk.loese("Lösche diese Bubble", gross)
    check("Grenze: ueber 200 Karten kommt ein ehrlicher Satz statt der Rueckfrage",
          "loeschen" not in (treffer or {}) and "201 Karten" in str((treffer or {}).get("rueckfrage"))
          and "gelöscht ist nichts" in str((treffer or {}).get("rueckfrage")), json.dumps(treffer, ensure_ascii=False))
    # Der Server prueft selbst noch einmal (die App kann zu wenig gezaehlt haben).
    antwort = rufe("bubble.delete", {"id": "b0b1e003", "mit_karten": True}, plan_id="ideen_1795000000001")
    check("Grenze: der Server loescht nichts und nennt too_many_cards",
          antwort.get("ok") is False and str(antwort.get("error")).endswith("(too_many_cards)"),
          str(antwort.get("error")))
    check("Grenze: Bubble und alle 201 Karten stehen noch",
          [b for b in db.tabellen["ideas"] if b["id"] == "b0b1e003"] != []
          and len([k for k in db.tabellen["canvas_nodes"] if k.get("linked_idea_id") == "b0b1e003"]) == 201)
    check("Grenze: der Satz im Chat ist ehrlich",
          "gelöscht ist nichts" in abk.fehlersatz(antwort.get("error"), "bubble.delete"),
          abk.fehlersatz(antwort.get("error"), "bubble.delete"))
    projekt = next((p for p in db.tabellen["projects"] if p.get("from_idea_id") == "b0b1e004"), {})
    stand = (projekt.get("metadata") or {}).get("uebergabe") or {}
    check("bubble.promote: das Projekt traegt den festen Stand (White Paper, Karten, Verbindung, Pruefsumme)",
          (stand.get("version"), (stand.get("white_paper") or {}).get("node_id"), len(stand.get("karten") or []),
           len(stand.get("kanten") or []), str(stand.get("pruefsumme", "")).startswith("sha256:"))
          == (1, "a0000043", 2, 1, True), json.dumps(stand, ensure_ascii=False)[:300])
    # ── Stufe 1 (25.11.2026): Bewertungslauf, Handwert, To-do, Schwelle fuers Befoerdern ──
    stufe1(check, db, server, rufe, lies, abk, vertrag, gesehen)

    check("alle festen Ereignisse geprueft", gesehen == set(vertrag.FESTE_EREIGNISSE),
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

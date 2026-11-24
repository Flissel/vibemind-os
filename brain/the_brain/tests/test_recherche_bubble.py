"""Recherche zu einer Bubble (core/recherche_bubble). Ohne Netz, ohne echten Lauf; Namen erfunden.

Teil 1: Bausteine mit Attrappen (Vorschau, Bestaetigung, Start, Nachfassen, Nachfasser-Runde).
Teil 2: beide Enden zusammen. Der echte ``McpExecutor`` ruft ueber eine nachgestellte OpenFang-
Schicht den echten Recherche-Server (spaces/research/mcp_server.py). Nachgestellt sind nur
OpenFang (reicht tools/call durch), die Datenbank (im Speicher) und der Start des Agenten
(``_spawn_agent_call`` schreibt nichts und startet nichts). Den Bericht legt der Test selbst ab.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_BRAIN = Path(__file__).resolve().parents[1]
_ROOT = _BRAIN.parents[1]
sys.path.insert(0, str(_BRAIN))

from core import recherche_bubble as rb  # noqa: E402
from core.agent_auftraege import AuftragsTabelle  # noqa: E402

BUBBLE = "00000000-0000-4000-8000-0000000000b1"
JOB = "job_v1_" + "0" * 26
ZEICHEN = "z" * 43
ARG = {"bubble_id": BUBBLE, "bubble_titel": "Stadtfest", "thema": "Ticketpreise anderer Stadtfeste"}
GRENZEN = {"seiten": 5, "modell_laeufe": 2, "modell": "Claude-Abo", "budget_waechter": True}


@pytest.fixture(autouse=True)
def _leer():
    rb.verwerfen()
    yield
    rb.verwerfen()


class _Tabelle:
    """Auftragstabelle im Speicher, mit denselben Methoden wie AuftragsTabelle."""

    def __init__(self, anlegen_geht=True):
        self.zeilen = {}
        self.anlegen_geht = anlegen_geht

    def anlegen(self, **kw):
        if not self.anlegen_geht:
            raise RuntimeError("weg")
        auftrag_id = f"00000000-0000-4000-8000-00000000000{len(self.zeilen) + 1}"
        self.zeilen[auftrag_id] = {"id": auftrag_id, **kw, "pruefung": None}
        return auftrag_id

    def uebergabe_setzen(self, auftrag_id, uebergabe):
        self.zeilen[auftrag_id]["uebergabe"] = uebergabe

    def recherchen_laufend(self):
        return [copy.deepcopy(z) for z in self.zeilen.values()
                if z["status"] == "laeuft" and (z.get("uebergabe") or {}).get("recherche")]

    def recherche_abschliessen(self, auftrag_id, status, pruefung):
        zeile = self.zeilen[auftrag_id]
        if zeile["status"] != "laeuft":
            return False
        zeile.update({"status": status, "pruefung": pruefung})
        return True


def _rufe(antworten, aufrufe):
    def rufe(werkzeug, argumente, **kw):
        aufrufe.append((werkzeug, copy.deepcopy(argumente)))
        antwort = antworten.pop(0)
        if isinstance(antwort, Exception):
            raise antwort
        return antwort
    return rufe


# ── Teil 1: Bausteine ────────────────────────────────────────────────────────

def test_vorschau_ist_rueckfrage_ohne_lauf_und_wird_gemerkt() -> None:
    aufrufe = []
    satz = rb.vorschau(ARG, rufe=_rufe([{"status": "preview", "brief": "BRIEF research_x.md", "grenzen": GRENZEN}], aufrufe))
    zeichen = aufrufe[0][1].pop("start_token")
    assert isinstance(zeichen, str) and len(zeichen) >= 43       # vom Gehirn ausgestellt, je Vorschau neu
    assert aufrufe == [("research_start", {"bubble_id": BUBBLE, "brief": "Ticketpreise anderer Stadtfeste",
                                           "depth": "thorough", "language": "german"})]
    assert "confirm" not in aufrufe[0][1]
    assert satz.startswith("Ich würde zu „Stadtfest“ recherchieren: Ticketpreise anderer Stadtfeste.")
    assert "höchstens 2 Modell-Läufe über das Claude-Abo und höchstens 5 geholte Seiten" in satz
    assert satz.endswith("Sag „Ja, recherchieren“, dann starte ich.")
    offen = rb.satz_pruefen("Ja, recherchieren")
    assert offen["offen"]["brief"] == "BRIEF research_x.md" and offen["offen"]["argumente"] == ARG
    assert offen["offen"]["zeichen"] == zeichen
    # genau einmal
    assert rb.satz_pruefen("Ja, recherchieren") == {"satz": rb.OHNE_FRAGE}


@pytest.mark.parametrize("grenzen", [None, {}, {"seiten": 5}, {"seiten": 5, "modell_laeufe": 0},
                                     {"seiten": True, "modell_laeufe": 2, "budget_waechter": True},
                                     {"seiten": 5, "modell_laeufe": 2, "budget_waechter": False}])
def test_ohne_kostengrenze_keine_rueckfrage(grenzen) -> None:
    # (R4) 24.11.2026: Die Vorschau nennt die Grenze je Recherche; ohne sie gibt es nichts zu bestaetigen.
    antwort = {"status": "preview", "brief": "B"}
    if grenzen is not None:
        antwort["grenzen"] = grenzen
    assert rb.vorschau(ARG, rufe=_rufe([antwort], [])) == rb.OHNE_GRENZE
    assert rb.satz_pruefen("Ja, recherchieren") == {"satz": rb.OHNE_FRAGE}


def test_anderer_satz_verwirft_die_vorschau() -> None:
    rb.vorschau(ARG, rufe=_rufe([{"status": "preview", "brief": "B", "grenzen": GRENZEN}], []))
    assert rb.satz_pruefen("Zeig die Notizen dieser Bubble") is None
    assert rb.satz_pruefen("Ja, recherchieren") == {"satz": rb.OHNE_FRAGE}


def test_vorschau_verfaellt_nach_120_s(monkeypatch) -> None:
    jetzt = [1000.0]
    monkeypatch.setattr(rb, "_jetzt", lambda: jetzt[0])
    rb.vorschau(ARG, rufe=_rufe([{"status": "preview", "brief": "B", "grenzen": GRENZEN}], []))
    jetzt[0] += 121
    assert rb.satz_pruefen("ja, recherchiere") == {"satz": rb.OHNE_FRAGE}


@pytest.mark.parametrize("satz", ["Ja, recherchieren", "ja recherchiere", "Ja, bitte recherchieren!",
                                  "Ja, starte die Recherche"])
def test_bestaetigungssaetze(satz) -> None:
    assert rb.ist_bestaetigung(satz)


@pytest.mark.parametrize("satz", ["Ja", "Ja, erstellen", "recherchiere", "Ja, recherchiere zu Sponsoren"])
def test_keine_bestaetigung(satz) -> None:
    assert not rb.ist_bestaetigung(satz)


def test_vorschau_dienst_weg_sagt_es_ehrlich() -> None:
    assert rb.vorschau(ARG, rufe=_rufe([RuntimeError("OpenFang unavailable")], [])) == rb.NICHT_ERREICHBAR
    assert rb.satz_pruefen("Ja, recherchieren") == {"satz": rb.OHNE_FRAGE}


def test_start_legt_erst_die_zeile_an_dann_den_lauf() -> None:
    tabelle, aufrufe = _Tabelle(), []
    felder = rb.starten({"argumente": ARG, "brief": "BRIEF", "zeichen": ZEICHEN}, tabelle=tabelle, trace_id="tr_1",
                        rufe=_rufe([{"status": "started", "job_id": JOB}], aufrufe))
    (zeile,) = tabelle.zeilen.values()
    assert zeile["status"] == "laeuft" and zeile["frist_s"] == 5400 and zeile["agent"] == "brain-recherche"
    assert zeile["capability"] == "recherche.start"
    assert aufrufe[0][1]["start_token"] == ZEICHEN
    assert zeile["uebergabe"] == {"recherche": {**{k: ARG[k] for k in ("bubble_id", "bubble_titel", "thema")},
                                                "job_id": JOB}}
    assert aufrufe[0][1]["confirm"] is True and aufrufe[0][1]["final_brief"] == "BRIEF"
    assert felder["status"] == "angenommen" and felder["fertig"] is False and felder["ok"] is True
    assert felder["auftraege"][0]["auftrag_id"] == zeile["id"]
    assert "Research: …" in felder["final_text"] and "Nr. 00000000" in felder["final_text"]


def test_ohne_tabelle_kein_start() -> None:
    aufrufe = []
    with pytest.raises(rb.RechercheFehler, match="Auftragstabelle"):
        rb.starten({"argumente": ARG, "brief": "B", "zeichen": ZEICHEN}, tabelle=_Tabelle(anlegen_geht=False),
                   trace_id="t",
                   rufe=_rufe([{"status": "started", "job_id": JOB}], aufrufe))
    assert aufrufe == []


def test_gescheiterter_start_schliesst_die_zeile() -> None:
    tabelle = _Tabelle()
    with pytest.raises(rb.RechercheFehler, match="nicht starten"):
        rb.starten({"argumente": ARG, "brief": "B"}, tabelle=tabelle, trace_id="t",
                   rufe=_rufe([RuntimeError("spawn_failed")], []))
    (zeile,) = tabelle.zeilen.values()
    assert zeile["status"] == "fehler" and zeile["pruefung"]["rueckweg"] == "fehler"
    assert "spawn_failed" not in json.dumps(zeile["pruefung"])


def _laufend(tabelle, job_id=JOB):
    auftrag_id = tabelle.anlegen(capability=rb.EREIGNIS_START, agent=rb.AGENT, auftrag="x", trace_id="t",
                                 plan_id="p", hop_id="s1", uebergabe={"recherche": {**ARG, "job_id": job_id}},
                                 antwortkanal={"art": "api"}, frist_s=5400, status="laeuft")
    return auftrag_id


def test_runde_pending_wartet() -> None:
    tabelle = _Tabelle()
    _laufend(tabelle)
    z = rb.runde(tabelle, status_abfragen=lambda j: {"status": "pending"}, karte_lesen=lambda n: None)
    assert z["recherche_wartet"] == 1 and next(iter(tabelle.zeilen.values()))["status"] == "laeuft"


def test_runde_fertig_prueft_die_karte_am_zustand() -> None:
    tabelle = _Tabelle()
    auftrag_id = _laufend(tabelle)
    karte = {"id": "00000000-0000-4000-8000-0000000000c1", "title": "Research: Stadtfest 2026-11-23 18:00:00",
             "node_type": "research", "linked_idea_id": BUBBLE}
    z = rb.runde(tabelle, status_abfragen=lambda j: {"status": "done", "node_id": karte["id"], "citation_count": 4},
                 karte_lesen=lambda n: karte if n == karte["id"] else None)
    zeile = tabelle.zeilen[auftrag_id]
    assert z["recherche_fertig"] == 1 and zeile["status"] == "fertig"
    assert zeile["pruefung"]["verified"] is True and zeile["pruefung"]["rueckweg"] == "geschrieben"
    assert zeile["pruefung"]["meldung"] == ("Die Recherche ist fertig: „Research: Stadtfest 2026-11-23 18:00:00“ "
                                            "mit 4 Quellen liegt in „Stadtfest“.")


@pytest.mark.parametrize("karte", [None, {"node_type": "note", "linked_idea_id": BUBBLE},
                                   {"node_type": "research", "linked_idea_id": "00000000-0000-4000-8000-0000000000b2"}])
def test_runde_fertig_ohne_passende_karte_ist_fehler(karte) -> None:
    tabelle = _Tabelle()
    auftrag_id = _laufend(tabelle)
    rb.runde(tabelle, status_abfragen=lambda j: {"status": "done", "node_id": "x", "citation_count": 1},
             karte_lesen=lambda n: karte)
    assert tabelle.zeilen[auftrag_id]["status"] == "fehler"
    assert "finde ich in der Bubble nicht" in tabelle.zeilen[auftrag_id]["pruefung"]["meldung"]


@pytest.mark.parametrize("fehler,satz", [
    ("agent call failed before writing a report", "Der Recherche-Agent hat keinen Bericht geschrieben."),
    ("no citation found in report - fail-closed", "Der Bericht nennt keine Quelle, darum lege ich keine Karte an."),
])
def test_runde_failed_sagt_den_grund(fehler, satz) -> None:
    tabelle = _Tabelle()
    auftrag_id = _laufend(tabelle)
    rb.runde(tabelle, status_abfragen=lambda j: {"status": "failed", "error": fehler}, karte_lesen=lambda n: None)
    pruefung = tabelle.zeilen[auftrag_id]["pruefung"]
    assert tabelle.zeilen[auftrag_id]["status"] == "fehler" and pruefung["meldung"].startswith(satz)


def test_runde_transportfehler_laesst_die_zeile_fuer_die_naechste_runde() -> None:
    tabelle = _Tabelle()
    auftrag_id = _laufend(tabelle)

    def kaputt(job_id):
        raise RuntimeError("OpenFang unavailable")

    z = rb.runde(tabelle, status_abfragen=kaputt, karte_lesen=lambda n: None)
    assert z["fehler"] == 1 and tabelle.zeilen[auftrag_id]["status"] == "laeuft"


def test_runde_ohne_job_id_schliesst_ehrlich() -> None:
    tabelle = _Tabelle()
    auftrag_id = _laufend(tabelle, job_id=None)
    rb.runde(tabelle, status_abfragen=lambda j: pytest.fail("kein Aufruf ohne job_id"), karte_lesen=lambda n: None)
    assert tabelle.zeilen[auftrag_id]["status"] == "fehler"


def test_stand_liest_fertig_und_fehler() -> None:
    from core.auftrag_stand import stand_einer_zeile
    tabelle = _Tabelle()
    auftrag_id = _laufend(tabelle)
    assert stand_einer_zeile(tabelle.zeilen[auftrag_id]) == {"stand": "laeuft"}
    rb.runde(tabelle, status_abfragen=lambda j: {"status": "failed", "error": "x"}, karte_lesen=lambda n: None)
    assert stand_einer_zeile(tabelle.zeilen[auftrag_id])["stand"] == "fehler"


def test_nachfasser_fasst_recherchen_nach() -> None:
    from core.nachfasser import Nachfasser

    class _NTabelle(_Tabelle):
        def zu_pruefen(self):
            return []

        def beendete_ohne_meldung(self):
            return []

        def abgelaufene_markieren(self):
            return 0

    tabelle = _NTabelle()
    auftrag_id = _laufend(tabelle)
    karte = {"id": "k", "title": "Research: Stadtfest", "node_type": "research", "linked_idea_id": BUBBLE}
    n = Nachfasser(tabelle, validator=None, router=None, plan_executor=None,
                   recherche_anschluss=lambda: {"status_abfragen": lambda j: {"status": "done", "node_id": "k"},
                                                "karte_lesen": lambda i: karte})
    z = n.runde()
    assert z["recherche_fertig"] == 1 and tabelle.zeilen[auftrag_id]["status"] == "fertig"


def test_tabelle_legt_laufende_zeile_an_und_schliesst_nur_laufende() -> None:
    gesendet = []

    class _Antwort:
        def __init__(self, daten):
            self.daten = daten

        def raise_for_status(self):
            pass

        def json(self):
            return self.daten

    class _Http:
        def request(self, methode, url, *, headers, params, json, timeout):
            gesendet.append((methode, params, json))
            return _Antwort([{"id": "00000000-0000-4000-8000-000000000001"}])

    t = AuftragsTabelle("http://db.test", "schluessel-nur-im-test", http=_Http())
    t.anlegen(capability="c", agent="a", auftrag="x", trace_id="t", plan_id="p", hop_id="s1", status="laeuft",
              frist_s=5400)
    assert gesendet[0][2]["status"] == "laeuft" and "begonnen" in gesendet[0][2]
    with pytest.raises(ValueError):
        t.anlegen(capability="c", agent="a", auftrag="x", trace_id="t", plan_id="p", hop_id="s1", status="fertig")
    assert t.recherche_abschliessen("00000000-0000-4000-8000-000000000001", "fertig", {"x": 1}) is True
    assert gesendet[-1][1] == {"id": "eq.00000000-0000-4000-8000-000000000001", "status": "eq.laeuft"}
    t.recherchen_laufend()
    assert gesendet[-1][1]["uebergabe->recherche"] == "not.is.null" and gesendet[-1][1]["status"] == "eq.laeuft"


@pytest.mark.parametrize("faehigkeit,ziel", [
    ("recherche.start", None),
    ("recherche.status", None),
    ("eigener_alias", "mcp:brain-recherche:spaces-research:research_start"),
    ("eigener_alias", "mcp:brain-researcher:spaces-research:research_start"),
    ("eigener_alias", "mcp:brain-recherche: Spaces_Research :research_start"),
    ("eigener_alias", "mcp:anderer-agent:SPACES-RESEARCH:research_status"),
    ("eigener_alias", "mcp:brain-recherche:spaces:research_research_start"),
    ("eigener_alias", "mcp:brain-recherche:Spaces-Res earch:research_start"),
])
def test_plan_kann_keine_recherche_starten(monkeypatch, faehigkeit, ziel) -> None:
    from core.plan_executor import PlanExecutor
    from core.plan_schema import HopSpec

    gebaut = []

    class _Router:
        def get_capability(self, _capability):
            return {"execution_target": ziel or "mcp:brain-recherche:spaces-research:research_start"}

    monkeypatch.setattr("core.plan_executor.PlanExecutor._capture_kg_hits", lambda *args: [])
    monkeypatch.setattr("core.capability_targets.build_executor", lambda target: gebaut.append(target))
    hop = HopSpec(step_id="s1", description="Recherche per Plan", capability=faehigkeit,
                  execution_target=ziel, arg_template=json.dumps({"bubble_id": BUBBLE, "brief": "x", "confirm": True}))
    ergebnis = PlanExecutor(capability_router=_Router())._exec_hop(hop, {})
    assert ergebnis.ok is False and ergebnis.error == "recherche: nur über die Rückfrage im Gehirn, nie per Plan"
    assert gebaut == []


# ── Teil 2: echter McpExecutor bis zum echten Recherche-Server ────────────────

class _Datenbank:
    def __init__(self) -> None:
        self.tabellen = {
            "ideas": [{"id": BUBBLE, "title": "Stadtfest", "description": ""}],
            "canvas_nodes": [{"id": "00000000-0000-4000-8000-0000000000a1", "title": "Preise", "content": "Stand 900",
                              "node_type": "note", "linked_idea_id": BUBBLE, "metadata": {}}],
            "research_report_artifacts": [],
        }
        self.naechste = 0

    def _passt(self, zeile, params):
        for feld, bedingung in params.items():
            if feld in ("select", "limit", "order"):
                continue
            assert bedingung.startswith("eq."), (feld, bedingung)
            if str(zeile.get(feld)) != bedingung[3:]:
                return False
        return True

    def request(self, method, path, *, params=None, body=None):
        zeilen = self.tabellen[path]
        if method == "GET":
            return [copy.deepcopy(z) for z in zeilen if self._passt(z, dict(params or {}))]
        if method == "POST":
            zeile = copy.deepcopy(body)
            if path == "canvas_nodes":
                self.naechste += 1
                zeile["id"] = f"00000000-0000-4000-8000-0000000000c{self.naechste}"
            zeilen.append(zeile)
            return [copy.deepcopy(zeile)]
        raise AssertionError(method)


@pytest.fixture()
def server_und_openfang(monkeypatch, tmp_path):
    from core import capability_targets as ziele

    spec = importlib.util.spec_from_file_location("recherche_server_unter_test",
                                                  _ROOT / "spaces" / "research" / "mcp_server.py")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    db = _Datenbank()
    monkeypatch.setattr(server, "_request", db.request)
    monkeypatch.setattr(server, "ARTIFACT_DIR", tmp_path)
    gestartet = []
    monkeypatch.setattr(server, "_spawn_agent_call", lambda job_id, brief: gestartet.append(job_id))
    # Der Server startet nur mit Budget-Waechter; der Lauf selbst ist hier ersetzt.
    monkeypatch.setenv("VIBEMIND_BUDGET_MODUL", str(tmp_path / "nur_im_test_budget.py"))

    class _Bereit:
        agent_id = "agent-test"
        approval_ref = "appr-test"
        cost_ref = "cost-test"
        invocation_id = "inv-test"

    class _Autoritaet:
        @classmethod
        def from_environment(cls):
            return cls()

        def prepare_invocation(self, kontext):
            assert kontext.agent_name == "brain-recherche" and kontext.space_id == "recherche"
            return _Bereit()

    angekommen = []

    def openfang(self, method, url, *, headers, payload=None, retry_transient=False, timeout_cap_s=None):
        assert url.endswith("/mcp") and headers["X-OpenFang-Agent-Id"] == "agent-test"
        name = payload["params"]["name"]
        assert name.startswith("mcp_spaces_research_"), name
        werkzeug = name[len("mcp_spaces_research_"):]
        angekommen.append((werkzeug, copy.deepcopy(payload["params"]["arguments"])))
        antwort = server.handle_message({"jsonrpc": "2.0", "id": payload["id"], "method": "tools/call",
                                         "params": {"name": werkzeug, "arguments": payload["params"]["arguments"]}})
        return json.loads(json.dumps(antwort))

    monkeypatch.setattr(ziele, "RuntimeAuthorityClient", _Autoritaet)
    monkeypatch.setattr(ziele.McpExecutor, "_request_json", openfang)
    monkeypatch.setattr(ziele.McpExecutor, "_configuration", lambda self: ("http://openfang.test", "nur-im-test"))
    return {"server": server, "db": db, "gestartet": gestartet, "angekommen": angekommen, "ordner": tmp_path}


def test_ganzer_weg_bis_zur_karte(server_und_openfang) -> None:
    s = server_und_openfang
    # 1. Vorschau: kein Lauf, keine Jobdatei.
    satz = rb.vorschau(ARG)
    assert satz.startswith("Ich würde zu „Stadtfest“ recherchieren"), satz
    # Nur das Ticket der Vorschau liegt da: Hash des Zeichens, nie das Zeichen selbst.
    assert s["gestartet"] == [] and [p.suffixes for p in s["ordner"].iterdir()] == [[".ticket", ".json"]]
    zeichen = s["angekommen"][0][1].pop("start_token")
    assert zeichen not in next(s["ordner"].iterdir()).read_text(encoding="utf-8")
    assert s["angekommen"][0] == ("research_start", {"bubble_id": BUBBLE, "brief": ARG["thema"],
                                                     "depth": "thorough", "language": "german"})
    # 2. Ja: Zeile, dann Start mit dem Brief der Vorschau; die job_id folgt dem Brief.
    tabelle = _Tabelle()
    felder = rb.starten(rb.satz_pruefen("Ja, recherchieren")["offen"], tabelle=tabelle, trace_id="tr_1")
    assert felder["status"] == "angenommen"
    (job_id,) = s["gestartet"]
    (zeile,) = tabelle.zeilen.values()
    assert zeile["uebergabe"]["recherche"]["job_id"] == job_id
    assert "Stand 900" in s["angekommen"][1][1]["final_brief"]  # Bubble-Inhalt als Hintergrund
    assert s["angekommen"][1][1]["start_token"] == zeichen
    assert not list(s["ordner"].glob("*.ticket.json"))           # eingeloest
    # Ein Modell, das denselben Start noch einmal schickt, startet nichts mehr.
    antwort = s["server"].handle_message({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {
        "name": "research_start", "arguments": s["angekommen"][1][1]}})
    assert "start_not_allowed" in json.dumps(antwort) and s["gestartet"] == [job_id]
    # 3. Noch kein Bericht: laeuft weiter.
    z = rb.runde(tabelle, **rb.anschluss() | {"karte_lesen": lambda n: None})
    assert z["recherche_wartet"] == 1
    # 4. Bericht mit Quelle liegt da: Server legt die Karte an, das Gehirn prueft sie am Zustand.
    (s["ordner"] / f"research_{job_id}.md").write_text(
        "# Ergebnis\nEintritt meist frei, siehe https://example.org/stadtfeste .\n", encoding="utf-8")

    def karte_lesen(node_id):
        treffer = [z for z in s["db"].tabellen["canvas_nodes"] if z["id"] == node_id]
        return treffer[0] if treffer else None

    z = rb.runde(tabelle, status_abfragen=rb.anschluss()["status_abfragen"], karte_lesen=karte_lesen)
    assert z["recherche_fertig"] == 1, z
    assert tabelle.zeilen[zeile["id"]]["status"] == "fertig"
    karten = [k for k in s["db"].tabellen["canvas_nodes"] if k.get("node_type") == "research"]
    assert len(karten) == 1 and karten[0]["linked_idea_id"] == BUBBLE
    assert tabelle.zeilen[zeile["id"]]["pruefung"]["meldung"].endswith("mit 1 Quelle liegt in „Stadtfest“.")


def test_bericht_ohne_quelle_ist_fehler_ohne_karte(server_und_openfang) -> None:
    s = server_und_openfang
    rb.vorschau(ARG)
    tabelle = _Tabelle()
    rb.starten(rb.satz_pruefen("Ja, recherchieren")["offen"], tabelle=tabelle, trace_id="tr_2")
    (job_id,) = s["gestartet"]
    (s["ordner"] / f"research_{job_id}.md").write_text("# Ergebnis\nohne Beleg\n", encoding="utf-8")
    rb.runde(tabelle, status_abfragen=rb.anschluss()["status_abfragen"], karte_lesen=lambda n: None)
    (zeile,) = tabelle.zeilen.values()
    assert zeile["status"] == "fehler" and "keine Quelle" in zeile["pruefung"]["meldung"]
    assert not [k for k in s["db"].tabellen["canvas_nodes"] if k.get("node_type") == "research"]


def _router_client(monkeypatch, tabelle):
    import core.difficulty_router as dr
    from core import agent_auftraege as aa
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from web.routers.introspection import router

    class _K:
        def classify(self, intent):
            return {"level": "medium", "score": 0.9, "method": "qwen-cosine", "reason": "x"}

    class _Ausfuehrer:
        plaene = []

        class recorder:  # noqa: N801
            def __getattr__(self, _n):
                return lambda *a, **k: None
        recorder = recorder()

        def execute(self, plan, **kw):
            self.plaene.append(plan)
            raise AssertionError("kein Plan fuer eine Recherche")

    class _Planer:
        async def aplan(self, intent, context=None):
            raise AssertionError("kein Planer")

    monkeypatch.setattr(dr, "get_router", lambda: _K())
    monkeypatch.setattr(aa, "tabelle_aus_umgebung", lambda: tabelle)
    monkeypatch.setenv("SOM_NOPLAN_FALLBACK", "0")
    monkeypatch.setenv("MULTI_VERB_ROUTING", "0")
    app = FastAPI()
    app.include_router(router)
    app.state.plan_executor = _Ausfuehrer()
    app.state.multihop_planner = _Planer()
    app.state.final_synthesizer = None
    client = TestClient(app)
    kontext = {"space": "ideas", "bubble": {"id": BUBBLE, "title": "Stadtfest"}}
    return lambda text: client.post("/api/multihop/execute", json={"intent": text, "context": kontext}).json()


def test_router_vorschau_dann_ja_startet_genau_einmal(server_und_openfang, monkeypatch) -> None:
    s, tabelle = server_und_openfang, _Tabelle()
    satz = _router_client(monkeypatch, tabelle)
    body = satz("Recherchiere zu dieser Bubble: Ticketpreise anderer Stadtfeste")
    assert body["final_text"].startswith("Ich würde zu „Stadtfest“ recherchieren: Ticketpreise"), body
    assert s["gestartet"] == [] and tabelle.zeilen == {}
    body = satz("Ja, recherchieren")
    assert body["status"] == "angenommen" and body["fertig"] is False and body["ok"] is True, body
    assert len(s["gestartet"]) == 1 and len(tabelle.zeilen) == 1
    body = satz("Ja, recherchieren")
    assert body["final_text"] == rb.OHNE_FRAGE and len(s["gestartet"]) == 1


def test_router_anderer_satz_verwirft_die_vorschau(server_und_openfang, monkeypatch) -> None:
    s, tabelle = server_und_openfang, _Tabelle()
    satz = _router_client(monkeypatch, tabelle)
    satz("Recherchiere zu dieser Bubble")
    satz("Ja, erstellen")                      # anderer Satz, endet vor jedem Plan
    body = satz("Ja, recherchieren")
    assert body["final_text"] == rb.OHNE_FRAGE and s["gestartet"] == [] and tabelle.zeilen == {}


def test_registry_bindet_beide_werkzeuge_nur_an_den_rechtetraeger() -> None:
    from core.capability_targets import find_registered_mcp_authority, resolve_registry_execution_target
    assert resolve_registry_execution_target("recherche.start") == (
        "mcp:brain-recherche:spaces-research:research_start")
    assert resolve_registry_execution_target("recherche.status") == (
        "mcp:brain-recherche:spaces-research:research_status")
    assert find_registered_mcp_authority("brain-recherche", "spaces-research", "research_status").space_id == "recherche"
    # Der Modell-Agent hat die Werkzeuge nicht mehr: kein Lauf aus seiner eigenen Schleife.
    for werkzeug in ("research_start", "research_status"):
        assert find_registered_mcp_authority("brain-researcher", "spaces-research", werkzeug) is None


def test_rechtetraeger_ohne_modell_schleife_im_verzeichnis() -> None:
    import yaml
    eintraege = yaml.safe_load((_ROOT / "config" / "space_agent_registry.yml").read_text(encoding="utf-8"))
    spaces = eintraege.get("spaces", eintraege)
    (recherche,) = [s for s in spaces.values() if isinstance(s, dict) and s.get("agent") == "brain-recherche"]
    assert recherche["rights_carrier"] is True
    assert recherche["mcp_tools"] == {"spaces-research": ["research_start", "research_status"]}
    (forscher,) = [s for s in spaces.values() if isinstance(s, dict) and s.get("agent") == "brain-researcher"]
    assert "spaces-research" not in (forscher.get("mcp_servers") or [])
    assert "spaces-research" not in (forscher.get("mcp_tools") or {})

"""Jeder Plan-Schritt kennt seinen Space (Quelle: config/space_agent_registry.yml)."""
from pathlib import Path

import pytest
import yaml

from core.space_contract import load_space_contract
from core.step_space import space_for_step

KATALOG = Path(__file__).resolve().parents[1] / "data" / "capabilities.yaml"

# Faehigkeiten, die bewusst zu keinem Space gehoeren. Wer hier etwas ergaenzt, entscheidet
# damit: Bei dieser Aufgabe wechselt die App nirgendwohin.
ALLGEMEIN = {
    "som_plan", "som_resume", "som_execute",
    "architecture_question",
    "security_scan", "incident_response", "log_analysis",
    "knowledge_query", "site_check", "chitchat",
}


@pytest.fixture(scope="module")
def vertrag():
    return load_space_contract()


@pytest.mark.parametrize("faehigkeit, ziel, space", [
    ("bubble_create", "mcp:brain-bubbles:spaces-ideas:bubble_create", "bubbles"),
    ("bubble_list", "supabase:bubble.list", "bubbles"),
    ("idea_format_table", "supabase:idea.format", "ideas"),
    ("idea_add", "supabase:idea.create", "ideas"),
    ("aenderung_zurueck", "mcp:brain-ideas:spaces-ideas:aenderung_zurueck", "ideas"),
    ("code_generate", "coding-engine:POST:/api/generate", "coding"),
    ("coding_task", "openfang:brain-coder-openai", "coding"),
    ("browser_automation", "openfang:openclaw-visible", "desktop"),
    ("desktop_skill", "openfang:skill-coordinator", "desktop"),
    ("rowboat_chat", "openfang:rowboat-chat", "rowboat"),
    ("rowboat_status", "mcp:rowboat-chat:spaces-rowboat:rowboat_status", "rowboat"),
    ("mirofish_simulate", "mirofish:simulate", "mirofish"),
    ("research_web", "research:web", "research"),
    ("schedule_create", "direct:spaces.schedule.execution:create", "schedule"),
    ("minibook_status", "mcp:brain-knowledge:spaces-minibook:minibook_status", "minibook"),
    ("video_status", "direct:spaces.video.execution_target:execute_video", "video"),
    ("rose.recommend", "supabase:rose.recommend", "flowzen"),
])
def test_bekannte_schritte_finden_ihren_space(vertrag, faehigkeit, ziel, space):
    assert space_for_step(faehigkeit, ziel, vertrag) == space


@pytest.mark.parametrize("faehigkeit, ziel", [
    ("chitchat", ""), ("som_plan", "openfang:som-planner"), ("", ""), (None, None), ("gibt_es_nicht", "x:y"),
])
def test_allgemeine_oder_unbekannte_schritte_haben_keinen_space(vertrag, faehigkeit, ziel):
    assert space_for_step(faehigkeit, ziel, vertrag) is None


def test_eine_kaputte_registry_stoppt_keinen_plan(monkeypatch):
    import core.step_space as modul

    monkeypatch.setattr(modul, "_vertrag", None)
    monkeypatch.setattr(modul, "load_space_contract", lambda: (_ for _ in ()).throw(FileNotFoundError("weg")))

    assert space_for_step("bubble_create", "supabase:bubble.create") is None


def test_jede_faehigkeit_des_katalogs_hat_einen_space_oder_ist_bewusst_allgemein(vertrag):
    """Routing-Vollstaendigkeit: eine neue Faehigkeit ohne Space faellt hier auf."""
    eintraege = yaml.safe_load(KATALOG.read_text(encoding="utf-8"))
    ohne = sorted(
        e["capability"] for e in eintraege
        if not space_for_step(e["capability"], e.get("execution_target") or "", vertrag)
        and e["capability"] not in ALLGEMEIN
    )
    assert ohne == []
    ueberfluessig = sorted(
        name for name in ALLGEMEIN
        if any(e["capability"] == name and space_for_step(name, e.get("execution_target") or "", vertrag)
               for e in eintraege)
    )
    assert ueberfluessig == [], "steht in ALLGEMEIN, hat aber einen Space"


def test_die_schritt_meldungen_tragen_den_space():
    """hop_started und hop_completed nennen den Space, damit kein Abnehmer raten muss."""
    from core.plan_executor import PlanExecutor, _hop_event
    from core.plan_schema import HopResult, HopSpec

    gesendet = []
    ausfuehrer = PlanExecutor.__new__(PlanExecutor)
    ausfuehrer._publish = lambda art, daten: gesendet.append((art, daten))
    schritt = HopSpec(step_id="s1", description="Bubble anlegen", capability="bubble_create",
                      arg_template="Neu", execution_target="mcp:brain-bubbles:spaces-ideas:bubble_create")

    ausfuehrer._publish_started([schritt], {})

    assert gesendet[0][0] == "hop_started"
    assert gesendet[0][1]["space"] == "bubbles"
    ergebnis = HopResult(step_id="s1", capability="bubble_create", ok=True,
                         target="mcp:brain-bubbles:spaces-ideas:bubble_create")
    assert _hop_event(schritt, ergebnis)["space"] == "bubbles"
    allgemein = HopSpec(step_id="s2", description="Plaudern", capability="chitchat", arg_template="")
    assert _hop_event(allgemein, HopResult(step_id="s2", capability="chitchat", ok=True))["space"] is None

@pytest.mark.parametrize("ziel, space", [
    # Ein Schritt nur mit Ziel: der Werkzeug-Server heisst nicht wie der Space.
    ("mcp:brain-sales:auftragsbuch:auftrag_probe", "sales"),
    ("mcp:brain-sales:auftragsbuch:auftrag_lesen", "sales"),
    # Werkzeug ohne eigenes Ereignis: der Agent gehoert genau einem Space.
    ("mcp:brain-sales:auftragsbuch:noch_ohne_ereignis", "sales"),
])
def test_mcp_ziel_wird_ueber_das_verzeichnis_aufgeloest(vertrag, ziel, space):
    assert space_for_step("", ziel, vertrag) == space


def test_mcp_ziel_mit_fremdem_agenten_bleibt_allgemein(vertrag):
    assert space_for_step("", "mcp:niemand:auftragsbuch:auftrag_probe", vertrag) is None
    assert space_for_step("", "mcp:kaputt", vertrag) is None
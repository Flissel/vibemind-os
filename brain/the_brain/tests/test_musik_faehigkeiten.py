"""Musik-Space im App-Chat: Register-Eintrag und vier Faehigkeiten (Weg A).

Die Faehigkeiten zeigen auf ``mcp:brain-musik:musik-studio:<werkzeug>``. Der Eintrag im
Verzeichnis muss jedes dieser Werkzeuge zulassen, sonst verweigert der Ausfuehrer den Aufruf.
"""
import json
from pathlib import Path

import pytest
import yaml

from core import capability_targets
from core.capability_router import CapabilityRouter

DATA = Path(__file__).resolve().parents[1] / "data"
REPO = Path(__file__).resolve().parents[3]

FAEHIGKEITEN = {
    "musik_set_bestellen": "set_bestellen",
    "musik_video_bestellen": "videomusik_bestellen",
    "musik_auftrag_zeigen": "auftrag_zeigen",
    "musik_laura_unterlegen": "in_laura_legen",
}


def _katalog():
    return yaml.safe_load((DATA / "capabilities.yaml").read_text(encoding="utf-8"))


def _eintrag(name):
    (cap,) = [c for c in _katalog() if c["capability"] == name]
    return cap


def _router():
    return CapabilityRouter(DATA / "capabilities.yaml")


def test_musik_faehigkeiten_zeigen_auf_musik_studio():
    router = _router()
    for name, werkzeug in FAEHIGKEITEN.items():
        detail = router.get_capability(name)
        assert detail is not None, name
        assert detail["execution_target"] == f"mcp:brain-musik:musik-studio:{werkzeug}"
        assert detail["patterns"], f"{name}: ohne match_patterns verwirft der Router den Eintrag"


def test_beispielsaetze_treffen_die_richtige_faehigkeit():
    router = _router()
    erwartet = {
        "mach mir 30 Sekunden ruhige Musik": "musik_video_bestellen",
        "make me 45 seconds of calm music": "musik_video_bestellen",
        "mach mir ein Set aus meinen Songs": "musik_set_bestellen",
        "mach mir einen DJ-Set fuer Samstag": "musik_set_bestellen",
        "leg Musik unter das Reel": "musik_laura_unterlegen",
        "wie weit ist mein Musik-Auftrag": "musik_auftrag_zeigen",
        "wie weit ist der Musik-Auftrag": "musik_auftrag_zeigen",
    }
    for satz, faehigkeit in erwartet.items():
        treffer = router.route(satz)
        assert treffer is not None, satz
        assert treffer.capability == faehigkeit, (satz, treffer.capability)


def test_set_braucht_musikbezug():
    """I6: "Set" allein ist kein DJ-Set (Karteikarten, flashcards)."""
    router = _router()
    for satz in ("mach mir ein Set Karteikarten", "make me a set of flashcards"):
        treffer = router.route(satz)
        assert treffer is None or treffer.capability != "musik_set_bestellen", (satz, treffer.capability)
    erwartet = {
        "mach mir ein DJ-Set": "musik_set_bestellen",
        "mach mir ein Set aus meinen Songs": "musik_set_bestellen",
        "make me a DJ set": "musik_set_bestellen",
        "leg die Musik unter das Video": "musik_laura_unterlegen",
    }
    for satz, faehigkeit in erwartet.items():
        treffer = router.route(satz)
        assert treffer is not None and treffer.capability == faehigkeit, (satz, treffer)


def test_musik_vor_som_execute():
    namen = [c["capability"] for c in _katalog()]
    som = namen.index("som_execute")
    for name in FAEHIGKEITEN:
        assert namen.index(name) < som, name


def test_validator_nutzt_url_env_und_health():
    for name in FAEHIGKEITEN:
        v = _eintrag(name)["validator"]
        assert v["kind"] == "truth:http_ok"
        assert v["on_fail"] == "report"
        post = v["postcondition"]
        assert post["check"] == "http_ok"
        assert post["url_env"] == "MUSIK_STUDIO_URL"
        assert post["path"] == "/health"
        assert "url" not in post, "keine feste Adresse im Katalog"


def test_register_laesst_die_werkzeuge_zu():
    for werkzeug in FAEHIGKEITEN.values():
        authority = capability_targets.find_registered_mcp_authority(
            "brain-musik", "musik-studio", werkzeug)
        assert authority is not None, werkzeug
        assert authority.space_id == "musik"


def test_register_eintrag_gestalt():
    doc = yaml.safe_load((REPO / "config" / "space_agent_registry.yml").read_text(encoding="utf-8"))
    musik = doc["spaces"]["musik"]
    assert musik["agent"] == "brain-musik"
    assert musik["prefixes"] == ["musik."]
    assert musik["enabled"] is True
    assert musik["mcp_servers"] == ["musik-studio"]
    assert musik["mcp_tools"]["musik-studio"] == [
        "bestand_zeigen", "set_bestellen", "videomusik_bestellen",
        "auftrag_zeigen", "in_laura_legen", "laura_videos_suchen"]
    for ereignis in ("musik.set.bestellen", "musik.video.bestellen",
                     "musik.auftrag.zeigen", "musik.laura.unterlegen"):
        spec = musik["events"][ereignis]
        assert spec["required_provenance"] == ["approval_ref", "cost_ref"]
        assert spec["execution"] == {"kind": "mcp", "server": "musik-studio"}
    spaces = list(doc["spaces"])
    assert spaces.index("musik") == spaces.index("video") - 1


def test_register_werkzeuge_gibt_es_im_musik_dienst():
    doc = yaml.safe_load((REPO / "config" / "space_agent_registry.yml").read_text(encoding="utf-8"))
    vorhanden = {t.split("__", 2)[2] for t in json.loads(
        (REPO / "spaces" / "musik" / "claw" / "musik-mcp.json").read_text(encoding="utf-8"))["allowedTools"]}
    for werkzeug in doc["spaces"]["musik"]["mcp_tools"]["musik-studio"]:
        assert werkzeug in vorhanden, werkzeug

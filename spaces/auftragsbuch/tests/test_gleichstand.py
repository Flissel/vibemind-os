"""Gleichstand von Space-Vertraegen, Space-Verzeichnis und Werkzeug-Dienst."""
import copy
from pathlib import Path

import yaml

from spaces.auftragsbuch.gleichstand import gleichstand
from spaces.auftragsbuch.vertrag import lade_auftragsarten

WURZEL = Path(__file__).resolve().parents[3]
ORDNER = WURZEL / "spaces" / "_contract"
ARTEN = lade_auftragsarten(ORDNER)


def registry_echt() -> dict:
    roh = yaml.safe_load((WURZEL / "config" / "space_agent_registry.yml").read_text(encoding="utf-8"))
    return roh["spaces"]


def capabilities_echt() -> list[dict]:
    return yaml.safe_load((WURZEL / "brain" / "the_brain" / "data" / "capabilities.yaml").read_text(encoding="utf-8"))


def space(tools: list[str], ereignisse: dict[str, str] | None = None) -> dict:
    """Verzeichnis-Eintrag eines Rechtetraegers; `ereignisse` bildet Ereignisname auf Werkzeug ab."""
    return {
        "agent": "brain-sales",
        "rights_carrier": True,
        "mcp_servers": ["auftragsbuch"],
        "mcp_tools": {"auftragsbuch": tools},
        "events": {
            name: {
                "tool": tool,
                "required_params": [],
                "execution": {"kind": "mcp", "server": "auftragsbuch"},
            }
            for name, tool in (ereignisse or {}).items()
        },
    }


def test_echter_stand_ist_gleich():
    assert gleichstand(lade_auftragsarten(ORDNER), registry_echt(), capabilities_echt()) == []


def test_fremde_auftragsart_faellt_auf():
    reg = {"sales": space(tools=["rowboat_vorschlaege_anlegen"])}
    assert any("rowboat_vorschlaege_anlegen" in s for s in gleichstand(ARTEN, reg, []))


def test_unbekanntes_werkzeug_faellt_auf():
    assert gleichstand(ARTEN, {"sales": space(tools=["sales_alles_loeschen"])}, []) != []


def test_ereignis_ohne_freischaltung_faellt_auf():
    reg = {"sales": space(tools=["auftrag_lesen"], ereignisse={"sales.auftrag_probe": "auftrag_probe"})}
    assert any("sales.auftrag_probe" in s for s in gleichstand(ARTEN, reg, []))


def test_ereignis_mit_fremdem_praefix_faellt_auf():
    reg = {"sales": space(tools=["auftrag_probe"], ereignisse={"rowboat.auftrag_probe": "auftrag_probe"})}
    assert any("rowboat.auftrag_probe" in s for s in gleichstand(ARTEN, reg, []))


def test_freigeschaltetes_ereignis_ist_gleich():
    reg = {"sales": space(tools=["auftrag_probe"], ereignisse={"sales.auftrag_probe": "auftrag_probe"})}
    assert gleichstand(ARTEN, reg, []) == []


def test_rechtetraeger_als_agenten_ziel_faellt_auf():
    caps = [{"capability": "x", "execution_target": "openfang:brain-sales"}]
    assert any("brain-sales" in s for s in gleichstand(ARTEN, {"sales": space(tools=["auftrag_lesen"])}, caps))


def test_anderer_agenten_ziel_ist_erlaubt():
    caps = [{"capability": "x", "execution_target": "openfang:brain-coder-anthropic"}, {"capability": "y"}]
    assert gleichstand(ARTEN, {"sales": space(tools=["auftrag_lesen"])}, caps) == []


def test_dienst_mit_fehlendem_oder_zusaetzlichem_werkzeug():
    soll = ["auftrag_probe", "auftrag_lesen", "sales_post_pruefen", "rowboat_vorschlaege_anlegen"]
    assert gleichstand(ARTEN, {}, [], dienst_werkzeuge=soll) == []
    assert gleichstand(ARTEN, {}, [], dienst_werkzeuge=soll[:-1]) != []
    assert gleichstand(ARTEN, {}, [], dienst_werkzeuge=soll + ["extra"]) != []


def test_echter_stand_ohne_freischaltung_faellt_auf():
    """Gegen den echten Stand: Ohne `auftrag_probe` in mcp_tools meldet der Abgleich das Ereignis."""
    reg = copy.deepcopy(registry_echt())
    reg["sales"]["mcp_tools"]["auftragsbuch"].remove("auftrag_probe")
    assert any("sales.auftrag_probe" in s for s in gleichstand(ARTEN, reg, capabilities_echt()))


def test_space_mit_auftragsbuch_ohne_rechtetraeger_faellt_auf():
    reg = {"sales": space(tools=["auftrag_lesen"])}
    reg["sales"]["rights_carrier"] = False
    assert any("rights_carrier" in s for s in gleichstand(ARTEN, reg, []))
    reg["sales"]["rights_carrier"] = True
    assert gleichstand(ARTEN, reg, []) == []

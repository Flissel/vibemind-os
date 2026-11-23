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
    soll = ["auftrag_probe", "auftrag_lesen", "sales_post_pruefen", "sales_kontakt_finden",
            "rowboat_vorschlaege_anlegen"]
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


# --- Rechtetraeger je Ereignis (Block 1): Space mit eigenem Agenten plus Traeger ---

def space_mit_traeger(tools: list[str], ereignisse: dict[str, str] | None = None, **mehr) -> dict:
    """Space `rowboat`: eigener Agent `rowboat-chat`, Auftragsbuch-Rechte nur beim Traeger `brain-rowboat`."""
    eintrag = {
        "agent": "rowboat-chat",
        "mcp_servers": ["spaces-rowboat"],
        "mcp_tools": {"spaces-rowboat": ["rowboat_status"]},
        "rights_carriers": [
            {
                "agent": "brain-rowboat",
                "mcp_servers": ["auftragsbuch"],
                "mcp_tools": {"auftragsbuch": tools},
            }
        ],
        "events": {
            name: {
                "tool": tool,
                "required_params": [],
                "execution": {"kind": "mcp", "server": "auftragsbuch"},
            }
            for name, tool in (ereignisse or {}).items()
        },
    }
    eintrag.update(mehr)
    return eintrag


def test_traeger_im_space_erlaubt():
    reg = {"rowboat": space_mit_traeger(
        ["rowboat_vorschlaege_anlegen", "auftrag_lesen"],
        {"rowboat.vorschlaege_anlegen": "rowboat_vorschlaege_anlegen", "rowboat.auftrag_lesen": "auftrag_lesen"},
    )}
    caps = [{"capability": "rowboat_search", "execution_target": "openfang:rowboat-chat"}]
    assert gleichstand(ARTEN, reg, caps) == []


def test_traeger_mit_openfang_ziel_faellt_auf():
    reg = {"rowboat": space_mit_traeger(["auftrag_lesen"])}
    caps = [{"capability": "x", "execution_target": "openfang:brain-rowboat"}]
    meldungen = gleichstand(ARTEN, reg, caps)
    assert any("brain-rowboat" in s for s in meldungen)
    assert not any("rowboat-chat" in s for s in meldungen)


def test_auftragsbuch_ohne_traeger_faellt_auf():
    reg = {"rowboat": space_mit_traeger(["rowboat_vorschlaege_anlegen"],
                                        {"rowboat.vorschlaege_anlegen": "rowboat_vorschlaege_anlegen"})}
    del reg["rowboat"]["rights_carriers"]
    assert any("rowboat" in s and "Rechtetr" in s for s in gleichstand(ARTEN, reg, []))
    # Auch der Server allein am Space (ohne Ereignis) braucht einen Traeger.
    reg2 = {"rowboat": {"agent": "rowboat-chat", "mcp_servers": ["auftragsbuch"],
                        "mcp_tools": {"auftragsbuch": ["auftrag_lesen"]}}}
    assert any("rights_carrier" in s for s in gleichstand(ARTEN, reg2, []))


def test_auftragsbuch_am_agenten_neben_dem_traeger_faellt_auf():
    reg = {"rowboat": space_mit_traeger(["auftrag_lesen"], mcp_servers=["spaces-rowboat", "auftragsbuch"])}
    assert any("rowboat-chat" in s for s in gleichstand(ARTEN, reg, []))


def test_werkzeug_im_falschen_space_faellt_auf():
    reg = {"rowboat": space_mit_traeger(["sales_kontakt_finden"])}
    assert any("sales_kontakt_finden" in s for s in gleichstand(ARTEN, reg, []))
    # Das alte Verhalten bleibt: ganzer Space als Traeger, fremde Auftragsart.
    assert any("rowboat_vorschlaege_anlegen" in s
               for s in gleichstand(ARTEN, {"sales": space(tools=["rowboat_vorschlaege_anlegen"])}, []))


def test_ereignis_ohne_werkzeug_beim_traeger_faellt_auf():
    reg = {"rowboat": space_mit_traeger(["auftrag_lesen"],
                                        {"rowboat.vorschlaege_anlegen": "rowboat_vorschlaege_anlegen"})}
    assert any("rowboat.vorschlaege_anlegen" in s for s in gleichstand(ARTEN, reg, []))


def test_traeger_ohne_auftragsbuch_server_faellt_auf():
    reg = {"rowboat": space_mit_traeger(["auftrag_lesen"])}
    reg["rowboat"]["rights_carriers"][0]["mcp_servers"] = ["spaces-rowboat"]
    assert any("brain-rowboat" in s for s in gleichstand(ARTEN, reg, []))


def test_traeger_ohne_agenten_faellt_auf():
    reg = {"rowboat": space_mit_traeger(["auftrag_lesen"])}
    del reg["rowboat"]["rights_carriers"][0]["agent"]
    assert any("rights_carriers" in s for s in gleichstand(ARTEN, reg, []))


def test_ganzer_space_und_traeger_zugleich_faellt_auf():
    reg = {"sales": space(tools=["auftrag_lesen"])}
    reg["sales"]["rights_carriers"] = [
        {"agent": "brain-sales-2", "mcp_servers": ["auftragsbuch"], "mcp_tools": {"auftragsbuch": ["auftrag_lesen"]}}
    ]
    assert any("rights_carriers" in s and "rights_carrier: true" in s for s in gleichstand(ARTEN, reg, []))


# --- Der Verzeichnis-Vorschlag aus lieferung/block-1-rechtetraeger.md haelt den Gleichstand ---

LIEFERUNG_1 = Path(__file__).resolve().parents[1] / "lieferung" / "block-1-rechtetraeger.md"


def _zusammenfuehren(basis, zusatz):
    """Zuordnungen ergaenzen, alles andere (auch Listen) ersetzen."""
    if isinstance(basis, dict) and isinstance(zusatz, dict):
        ergebnis = dict(basis)
        for schluessel, wert in zusatz.items():
            ergebnis[schluessel] = _zusammenfuehren(basis.get(schluessel), wert)
        return ergebnis
    return copy.deepcopy(zusatz)


def _vorschlag_aus_lieferung() -> dict:
    import re as _re
    text = LIEFERUNG_1.read_text(encoding="utf-8")
    bloecke = _re.findall(r"```yaml\n(.*?)```", text, flags=_re.S)
    treffer = [yaml.safe_load(b) for b in bloecke if b.lstrip().startswith("spaces:")]
    assert len(treffer) == 1, "genau ein YAML-Block mit `spaces:` erwartet"
    return treffer[0]["spaces"]


def test_vorschlag_der_lieferung_haelt_den_gleichstand():
    reg = _zusammenfuehren(registry_echt(), _vorschlag_aus_lieferung())
    assert reg["rowboat"]["agent"] == "rowboat-chat"
    assert reg["rowboat"]["rights_carriers"][0]["agent"] == "brain-rowboat"
    assert reg["rowboat"]["events"]["rowboat.status"]["execution"]["server"] == "spaces-rowboat"
    assert "sales.kontakt_finden" in reg["sales"]["events"]
    assert gleichstand(lade_auftragsarten(ORDNER), reg, capabilities_echt()) == []


def test_vorschlag_ohne_traeger_haelt_den_gleichstand_nicht():
    reg = _zusammenfuehren(registry_echt(), _vorschlag_aus_lieferung())
    del reg["rowboat"]["rights_carriers"]
    assert gleichstand(lade_auftragsarten(ORDNER), reg, capabilities_echt()) != []

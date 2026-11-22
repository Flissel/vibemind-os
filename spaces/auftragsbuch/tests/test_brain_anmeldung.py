"""Sales ist im Verzeichnis des Gehirns angemeldet, Rechtetraeger `brain-sales`.

Kein Satz des Betreibers wird in Stufe 1 nach `sales` geroutet; die Anmeldung
macht nur die zwei Werkzeuge des Auftragsbuchs fuer den Rechtetraeger
erreichbar. Alle Beispielwerte sind erfunden.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BRAIN = ROOT / "brain" / "the_brain"
for _p in (ROOT, BRAIN):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from core.capability_targets import find_registered_mcp_authority  # noqa: E402
from core.space_contract import load_space_contract  # noqa: E402

REGISTRY = ROOT / "config" / "space_agent_registry.yml"
KONKRET = ROOT / "openfang" / "openfang.vibemind.toml"
VORLAGE = ROOT / "openfang" / "openfang.vibemind.toml.template"
BRUECKE = "spaces/auftragsbuch/bruecke.py"
ENV_NAMEN = ("AUFTRAGSBUCH_URL", "AUFTRAGSBUCH_SCHLUESSEL")


def test_sales_ist_angemeldet():
    v = load_space_contract(REGISTRY)
    assert v.event_space_map["sales.auftrag_probe"] == "sales"
    assert v.event_space_map["sales.auftrag_lesen"] == "sales"
    assert v.spaces["sales"]["agent"] == "brain-sales"


def test_ziele_sind_registriert():
    for werkzeug in ("auftrag_probe", "auftrag_lesen"):
        a = find_registered_mcp_authority("brain-sales", "auftragsbuch", werkzeug)
        assert a is not None and a.space_id == "sales"


def test_keine_auftragsart_ist_freigeschaltet():
    assert find_registered_mcp_authority("brain-sales", "auftragsbuch", "sales_post_pruefen") is None


def test_kein_satz_routet_nach_sales():
    sales = load_space_contract(REGISTRY).spaces["sales"]
    assert not sales.get("keywords") and not sales.get("aliases")


def _auftragsbuch_server(pfad: Path) -> dict:
    daten = tomllib.loads(pfad.read_text(encoding="utf-8"))
    treffer = [s for s in daten["mcp_servers"] if s.get("name") == "auftragsbuch"]
    assert len(treffer) == 1, f"{pfad.name}: erwartet genau einen Server 'auftragsbuch'"
    return treffer[0]


def _gemeinsam_pruefen(server: dict) -> list[str]:
    assert server["transport"]["type"] == "stdio"
    assert "headers" not in server and "auth_headers" not in server
    assert "headers" not in server["transport"] and "auth_headers" not in server["transport"]
    args = server["transport"]["args"]
    assert args[-1].endswith(BRUECKE)
    return args


def test_werkzeug_server_in_konkreter_datei():
    args = _gemeinsam_pruefen(_auftragsbuch_server(KONKRET))
    for name in ENV_NAMEN:
        assert args[args.index(name) - 1] == "--env"


def test_werkzeug_server_in_vorlage():
    server = _auftragsbuch_server(VORLAGE)
    args = _gemeinsam_pruefen(server)
    assert args[-1].startswith("${VIBEMIND_ROOT}/")
    assert set(ENV_NAMEN) <= set(server["env"])


def test_navigator_kennt_sales_ohne_aliase():
    from core.space_contract import registry_health
    from spaces._navigator.registry import SPACES
    import yaml

    nav = SPACES["sales"]
    assert nav["event_prefix"] == "sales."
    assert nav["aliases"] == []
    assert nav["renderer_id"] is None
    caps = list(yaml.safe_load((BRAIN / "data" / "capabilities.yaml").read_text(encoding="utf-8")))
    probleme = registry_health(load_space_contract(REGISTRY), capabilities=caps)["issues"]
    assert not any("sales" in p.get("spaces", []) for p in probleme if p["kind"] == "navigator_missing_spaces")


def test_manifest_von_brain_sales_ist_nur_ausweis():
    import pytest

    pfad = ROOT / "openfang" / "agents" / "brain-sales" / "agent.toml"
    if not pfad.exists():
        pytest.skip("Submodul openfang nicht ausgecheckt")
    m = tomllib.loads(pfad.read_text(encoding="utf-8"))
    assert m["mcp_servers"] == ["auftragsbuch"]
    assert set(m["capabilities"]["tools"]) == {
        "mcp_auftragsbuch_auftrag_probe", "mcp_auftragsbuch_auftrag_lesen"}
    for schluessel in ("network", "memory_read", "memory_write"):
        assert m["capabilities"][schluessel] == []
    prompt = m["model"]["system_prompt"]
    assert "accepts no messages" in prompt and "user_text" not in prompt
    assert "rights-carrier" in m["tags"]

"""Der Kopf X-Aenderung-Id: ein Plan zaehlt in der Datenbank als eine Aenderung.

Die Ausloeser in der Supabase (Migration 20261120_ideen_aenderungen) lesen diesen
Kopf und fassen alle Schreibvorgaenge mit derselben Kennung zu einer Gruppe zusammen,
die "zurueck" in einem Zug zuruecknimmt. Ohne Kopf gilt eine Aenderung als von Hand.
"""
import pytest

from core import capability_targets
from core.plan_executor import _aenderung_id_fuer
from core.supabase_ideas_client import SupabaseIdeasClient


def _client(aenderung_id=None):
    return SupabaseIdeasClient(url="http://supabase.test", anon_key="a.b.c", aenderung_id=aenderung_id)


def test_kopf_traegt_die_plan_kennung():
    kopf = _client("plan-7f3a")._headers(prefer="return=representation")
    assert kopf["X-Aenderung-Id"] == "plan-7f3a"
    assert kopf["Prefer"] == "return=representation"


def test_ohne_kennung_kein_kopf():
    assert "X-Aenderung-Id" not in _client()._headers()


@pytest.mark.parametrize("wert", ["", "   ", "a\nb", "a\rb", "x" * 201, "plän", 7, None, ["plan"]])
def test_unbrauchbare_kennung_wird_weggelassen_statt_die_anfrage_zu_brechen(wert):
    assert "X-Aenderung-Id" not in _client(wert)._headers()


def test_kennung_wird_von_leerraum_befreit():
    assert _client("  plan-1 ")._headers()["X-Aenderung-Id"] == "plan-1"


def test_ausfuehrer_gibt_die_kennung_dem_client_und_nicht_der_operation(monkeypatch):
    gesehen = {}

    class FalscherClient:
        def __init__(self, **kwargs):
            gesehen["client"] = kwargs

    async def operation(client, payload):
        gesehen["payload"] = dict(payload)
        return "ok"

    monkeypatch.setattr("core.supabase_ideas_client.SupabaseIdeasClient", FalscherClient)
    monkeypatch.setattr("core.supabase_ideas_ops.bubble_list_op", operation)

    ausfuehrer = capability_targets.SupabaseExecutor("supabase:bubble.list")
    assert ausfuehrer._call({"_aenderung_id": "plan-7", "limit": 3}) == "ok"

    assert gesehen["client"] == {"aenderung_id": "plan-7"}
    assert gesehen["payload"] == {"limit": 3}


def test_ausfuehrer_ohne_kennung_baut_den_client_ohne_kopf(monkeypatch):
    gesehen = {}

    class FalscherClient:
        def __init__(self, **kwargs):
            gesehen["client"] = kwargs

    async def operation(client, payload):
        return "ok"

    monkeypatch.setattr("core.supabase_ideas_client.SupabaseIdeasClient", FalscherClient)
    monkeypatch.setattr("core.supabase_ideas_ops.bubble_list_op", operation)

    capability_targets.SupabaseExecutor("supabase:bubble.list")._call({"limit": 3})

    assert gesehen["client"] == {"aenderung_id": None}


def test_plan_ausfuehrer_setzt_die_kennung_nur_fuer_supabase_ziele():
    assert _aenderung_id_fuer("supabase:idea.create", {"plan_id": "p-1"}) == "p-1"
    assert _aenderung_id_fuer("SUPABASE:bubble.delete", {"plan_id": "p-1"}) == "p-1"
    assert _aenderung_id_fuer("openfang:brain-ideas", {"plan_id": "p-1"}) == ""
    assert _aenderung_id_fuer("mcp:brain-ideas:spaces-ideas:idea_connect", {"plan_id": "p-1"}) == ""


@pytest.mark.parametrize("ctx", [None, {}, {"plan_id": ""}, {"plan_id": None}, {"plan_id": 12}])
def test_plan_ausfuehrer_ohne_plan_kennung_setzt_nichts(ctx):
    assert _aenderung_id_fuer("supabase:idea.create", ctx) == ""


def test_plan_ausfuehrer_vertraegt_ein_ziel_das_kein_text_ist():
    assert _aenderung_id_fuer(None, {"plan_id": "p-1"}) == ""


# ── mcp-Weg ueber den Ideen-Server ───────────────────────────────────────


@pytest.mark.parametrize("ziel", [
    "mcp:brain-bubbles:spaces-ideas:bubble_create",
    "mcp:brain-researcher:spaces-ideas:idea_create",
    "mcp:brain-researcher:spaces-ideas:idea_update",
    "mcp:brain-bubbles:spaces-ideas:bubble_promote",
])
def test_mcp_weg_gibt_die_plan_kennung_als_argument_mit(ziel):
    ausfuehrer = capability_targets.McpExecutor(ziel)
    assert ausfuehrer._mit_aenderung_id({"title": "Neu"}, "plan-7f3a") == {"title": "Neu", "aenderung_id": "plan-7f3a"}


@pytest.mark.parametrize("ziel", [
    "mcp:brain-ideas:spaces-ideas:idea_connect",        # fester Vertrag: nur drei Argumente
    "mcp:brain-ideas:spaces-ideas:aenderung_zurueck",   # nimmt selbst zurueck
    "mcp:brain-ideas:spaces-ideas:bubble_list",         # liest nur
    "mcp:brain-knowledge:spaces-minibook:minibook_status",
])
def test_mcp_weg_laesst_andere_werkzeuge_unberuehrt(ziel):
    ausfuehrer = capability_targets.McpExecutor(ziel)
    nutzlast = {"from_id": "a", "to_id": "b"}
    assert ausfuehrer._mit_aenderung_id(nutzlast, "plan-7f3a") == nutzlast


@pytest.mark.parametrize("kennung", ["", None, "a\nb", "x" * 201, 7])
def test_mcp_weg_ohne_brauchbare_kennung_haengt_nichts_an(kennung):
    ausfuehrer = capability_targets.McpExecutor("mcp:brain-bubbles:spaces-ideas:bubble_create")
    assert ausfuehrer._mit_aenderung_id({"title": "Neu"}, kennung) == {"title": "Neu"}


def test_mcp_weg_ueberschreibt_die_nutzlast_nicht():
    ausfuehrer = capability_targets.McpExecutor("mcp:brain-bubbles:spaces-ideas:bubble_create")
    nutzlast = {"title": "Neu"}
    ausfuehrer._mit_aenderung_id(nutzlast, "plan-1")
    assert nutzlast == {"title": "Neu"}

"""Die Faehigkeit "zurueck": letzte Aenderung an Ideen und Bubbles zuruecknehmen.

Sie laeuft ueber den Ideen-Server (``mcp:brain-ideas:spaces-ideas:aenderung_zurueck``),
weil nur der Dienstschluessel ``ideen_zurueck`` aufrufen darf. Die Nachpruefung liest
auf zweitem Weg das Aenderungsprotokoll: Die Gruppe muss dort als zurueckgenommen stehen.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from core import capability_targets
from core import world_observer as wo
from core.aenderung_zurueck_contract import (
    AENDERUNG_ZURUECK_EVENT,
    AENDERUNG_ZURUECK_TARGET,
    aenderung_fehler_aus_mcp_ergebnis,
    extract_aenderung_zurueck_receipt,
    gruppe_aus_plan_argument,
)
from core.capability_validator import CapabilityValidator

DATA = Path(__file__).resolve().parents[1] / "data"


def _capability():
    caps = yaml.safe_load((DATA / "capabilities.yaml").read_text(encoding="utf-8"))
    (cap,) = [c for c in caps if c["capability"] == "aenderung_zurueck"]
    return cap


def _mcp_ergebnis(payload, *, fehler=False):
    return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": fehler}


# ── Verdrahtung ──────────────────────────────────────────────────────────


def test_ziel_ist_im_verzeichnis_zugelassen():
    authority = capability_targets.find_registered_mcp_authority(
        "brain-ideas", "spaces-ideas", "aenderung_zurueck")
    assert authority is not None
    assert (authority.space_id, authority.event_id) == ("ideas", AENDERUNG_ZURUECK_EVENT)
    assert authority.target == AENDERUNG_ZURUECK_TARGET == "mcp:brain-ideas:spaces-ideas:aenderung_zurueck"


def test_faehigkeit_fuehrt_zum_ereignis_und_zum_ziel():
    assert capability_targets.canonical_space_event_id("aenderung_zurueck") == "idea.aenderung_zurueck"
    assert capability_targets.resolve_registry_execution_target("idea.aenderung_zurueck") == AENDERUNG_ZURUECK_TARGET


def test_katalogeintrag():
    cap = _capability()
    assert cap["execution_target"] == AENDERUNG_ZURUECK_TARGET
    assert cap["arg_kwarg"] == "gruppe"
    # Ohne Muster verwirft der Katalog den Eintrag ganz (im Betrieb gemessen am 21.11.).
    assert cap["match_patterns"]
    assert cap["validator"]["kind"] == "truth:supabase_aenderung_zurueck"
    effects = yaml.safe_load((DATA / "capability_effects.yaml").read_text(encoding="utf-8"))
    assert effects["effects"]["aenderung_zurueck"] == "write"
    assert "aenderung_zurueck" not in (effects.get("restliste") or {})


def test_plan_ausfuehrer_versucht_zurueck_nur_einmal():
    # Ein zweiter Versuch nach verlorener Antwort naehme die naechstaeltere Aenderung zurueck.
    from core import plan_executor
    assert AENDERUNG_ZURUECK_EVENT in plan_executor._EINMAL_EREIGNISSE
    assert "bubble.create" in plan_executor._EINMAL_EREIGNISSE


# ── Gruppe aus dem Plan-Argument ─────────────────────────────────────────


@pytest.mark.parametrize("argument, absicht, erwartet", [
    ("", "mach das rueckgaengig", ""),
    ("letzte", "nimm die letzte Aenderung zurueck", ""),
    ("rueckgaengig", "mach das rueckgaengig", ""),
    ("plan_7f3a9c1e", "nimm plan_7f3a9c1e zurueck", "plan_7f3a9c1e"),
    ("hand-4711", "nimm hand-4711 zurueck", "hand-4711"),
    ("zurueck:plan_7f3a9c1e", "nimm zurueck:plan_7f3a9c1e zurueck", "zurueck:plan_7f3a9c1e"),
    ("plan_7f3a9c1e", "mach das rueckgaengig", ""),      # vom Planer erfunden
    ("  hand-4711 ", "nimm hand-4711 zurueck", "hand-4711"),
    (None, "zurueck", ""),
    ({"gruppe": "hand-1"}, "nimm hand-1 zurueck", "hand-1"),
    # die Muster-Abkuerzung setzt das letzte Wort ein: die Kennung kommt dann aus dem Satz
    ("zurueck", "nimm hand-4711 zurueck", "hand-4711"),
    ("zur\u00fcck", "nimm plan_7f3a9c1e bitte wieder zur\u00fcck.", "plan_7f3a9c1e"),
    ("", "nimm die Aenderung vom 21.11. zurueck", ""),
    # mehrere Kennungen und keine davon als Argument: nichts zuruecknehmen
    ("zurueck", "nimm hand-4711 und plan_7f3a zurueck", None),
    ("hand-4711", "nimm hand-4711 zurueck, nicht plan_7f3a", "hand-4711"),
    ("x" * 201 + "-1", "x" * 201 + "-1", ""),
])
def test_gruppe_nur_wenn_der_mensch_sie_genannt_hat(argument, absicht, erwartet):
    assert gruppe_aus_plan_argument(argument, absicht) == erwartet


def test_der_katalog_kennt_die_faehigkeit_und_waehlt_sie():
    """Im Betrieb fehlte sie zuerst: Eintraege ohne Muster laedt der Katalog nicht."""
    from core.capability_router import CapabilityRouter
    router = CapabilityRouter(DATA / "capabilities.yaml")
    detail = router.get_capability("aenderung_zurueck")
    assert detail and detail["execution_target"] == AENDERUNG_ZURUECK_TARGET
    assert detail["validator"]["kind"] == "truth:supabase_aenderung_zurueck"
    for satz in ("zur\u00fcck", "Zurueck!", "mach das r\u00fcckg\u00e4ngig", "mach die letzte \u00c4nderung bitte wieder r\u00fcckg\u00e4ngig",
                 "nimm das zur\u00fcck", "nimm die letzte Aenderung zurueck", "nimm hand-4711 zurueck",
                 "stell die gel\u00f6schte Bubble wieder her", "undo the last change", "take that back"):
        treffer = router.route(satz)
        assert treffer is not None and treffer.capability == "aenderung_zurueck", satz
    for satz in ("mach das Format r\u00fcckg\u00e4ngig", "undo the format", "geh zur\u00fcck zur Bubble Marketing",
                 "l\u00f6sche die Idee Testlauf", "nimm die Idee zur\u00fcck in die Bubble"):
        treffer = router.route(satz)
        assert treffer is None or treffer.capability != "aenderung_zurueck", satz


def test_plan_schritt_mit_mehreren_kennungen_nimmt_nichts_zurueck(monkeypatch):
    hr, ausfuehrer, _ziele = _zurueck_schritt(
        monkeypatch, argument="zurueck", absicht="nimm hand-4711 und plan_7f3a zurueck",
        ergebnis={"ok": True, "result": _mcp_ergebnis({"gruppe": "x-1", "anzahl": 1, "eintraege": []})})
    assert hr.ok is False and "mehrere Kennungen" in hr.error
    assert ausfuehrer.aufrufe == []


def test_plan_schritt_liest_die_kennung_aus_dem_satz(monkeypatch):
    _hr, ausfuehrer, _ziele = _zurueck_schritt(
        monkeypatch, argument="zurueck", absicht="nimm hand-4711 zurueck",
        ergebnis={"ok": True, "result": _mcp_ergebnis({"gruppe": "hand-4711", "anzahl": 1, "eintraege": []})})
    assert ausfuehrer.aufrufe == [("hand-4711", "gruppe", {"_runtime_authority"})]


# ── Quittung des Werkzeugs ───────────────────────────────────────────────


def test_quittung_nennt_gruppe_und_anzahl():
    receipt, grund = extract_aenderung_zurueck_receipt(_mcp_ergebnis(
        {"gruppe": "plan_7f3a", "anzahl": 2, "erstellt_am": "2026-11-20T10:00:00Z", "eintraege": []}))
    assert (receipt, grund) == ({"gruppe": "plan_7f3a", "anzahl": 2}, "")


def test_quittung_ohne_offene_aenderung():
    receipt, _ = extract_aenderung_zurueck_receipt(_mcp_ergebnis({"gruppe": None, "anzahl": 0, "eintraege": []}))
    assert receipt == {"gruppe": None, "anzahl": 0}


@pytest.mark.parametrize("wert", [
    None, "ok", {}, {"content": [], "isError": False},
    {"content": [{"type": "text", "text": "kein json"}], "isError": False},
    {"content": [{"type": "text", "text": "[]"}], "isError": False},
])
def test_unlesbare_quittung_wird_abgelehnt(wert):
    receipt, grund = extract_aenderung_zurueck_receipt(wert)
    assert receipt is None and grund


@pytest.mark.parametrize("payload", [
    {"gruppe": "plan_1", "anzahl": 0},        # Gruppe genannt, aber nichts getan
    {"gruppe": None, "anzahl": 3},            # etwas getan, aber keine Gruppe
    {"gruppe": "plan_1", "anzahl": "2"},
    {"gruppe": "plan_1", "anzahl": True},
    {"gruppe": "a\nb", "anzahl": 1},
    {"gruppe": "x" * 201, "anzahl": 1},
    {"anzahl": 1},
])
def test_widerspruechliche_quittung_wird_abgelehnt(payload):
    assert extract_aenderung_zurueck_receipt(_mcp_ergebnis(payload))[0] is None


def test_fehlgeschlagenes_werkzeug_ist_keine_quittung():
    ergebnis = _mcp_ergebnis({"error": "aenderung_konflikt: public.ideas b1"}, fehler=True)
    assert extract_aenderung_zurueck_receipt(ergebnis)[0] is None


def test_die_drei_meldungen_kommen_durch_anderes_nicht():
    assert aenderung_fehler_aus_mcp_ergebnis(
        _mcp_ergebnis({"error": "aenderung_konflikt: public.canvas_nodes n1"}, fehler=True)
    ) == "aenderung_konflikt: public.canvas_nodes n1"
    assert aenderung_fehler_aus_mcp_ergebnis(
        _mcp_ergebnis({"error": "aenderung_unbekannt: plan-9"}, fehler=True)) == "aenderung_unbekannt: plan-9"
    for anderes in (
        _mcp_ergebnis({"error": "supabase_http_error: status=500"}, fehler=True),
        _mcp_ergebnis({"error": "aenderung_konflikt: x\nKEY=geheim"}, fehler=True),
        _mcp_ergebnis({"error": 7}, fehler=True),
        _mcp_ergebnis({"gruppe": "p", "anzahl": 1}),
        {"isError": True}, None,
    ):
        assert aenderung_fehler_aus_mcp_ergebnis(anderes) == ""


# ── Nachpruefung auf zweitem Weg ─────────────────────────────────────────


@pytest.fixture
def _dienst(monkeypatch, tmp_path):
    from core import config as core_config
    # get_secret liest sonst die .env und /run/secrets dieser Maschine mit.
    monkeypatch.setattr(core_config, "_load_env_file", lambda *a, **k: None)
    monkeypatch.setattr(core_config, "_SECRETS_DIR", str(tmp_path))
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY_FILE", raising=False)
    monkeypatch.setattr(wo, "GROUND_TRUTH_ENABLED", True)
    monkeypatch.setenv("SUPABASE_URL", "http://supabase.test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "dienst-schluessel")


def _antwort(monkeypatch, zeilen, status=200):
    antwort = MagicMock(status_code=status)
    antwort.json.return_value = zeilen
    get = MagicMock(return_value=antwort)
    import requests
    monkeypatch.setattr(requests, "get", get)
    return get


def test_gruppe_ganz_zurueckgenommen_ist_bestaetigt(monkeypatch, _dienst):
    get = _antwort(monkeypatch, [{"id": 8, "zurueckgenommen_am": "2026-11-20T10:05:00Z"},
                                 {"id": 9, "zurueckgenommen_am": "2026-11-20T10:05:00Z"}])
    v = wo.observe({"check": "supabase_aenderung_zurueck", "gruppe": "plan_7f3a", "anzahl": 2})
    assert v.verified_ok is True
    (url,), kwargs = get.call_args
    assert url == "http://supabase.test/rest/v1/ideen_aenderungen"
    assert kwargs["params"]["gruppe"] == "eq.plan_7f3a"
    assert kwargs["headers"]["apikey"] == "dienst-schluessel"
    assert kwargs["allow_redirects"] is False


def test_noch_offene_zeile_widerlegt(monkeypatch, _dienst):
    _antwort(monkeypatch, [{"id": 8, "zurueckgenommen_am": "2026-11-20T10:05:00Z"},
                           {"id": 9, "zurueckgenommen_am": None}])
    assert wo.observe({"check": "supabase_aenderung_zurueck", "gruppe": "plan_7f3a", "anzahl": 2}).verified_ok is False


def test_unbekannte_gruppe_widerlegt(monkeypatch, _dienst):
    _antwort(monkeypatch, [])
    assert wo.observe({"check": "supabase_aenderung_zurueck", "gruppe": "plan_7f3a", "anzahl": 2}).verified_ok is False


def test_nichts_offen_wird_am_protokoll_bestaetigt(monkeypatch, _dienst):
    get = _antwort(monkeypatch, [])
    v = wo.observe({"check": "supabase_aenderung_zurueck", "gruppe": None, "anzahl": 0})
    assert v.verified_ok is True
    params = get.call_args.kwargs["params"]
    assert params["quelle"] == "eq.agent" and params["zurueckgenommen_am"] == "is.null"


def test_nichts_offen_gemeldet_aber_es_ist_etwas_offen(monkeypatch, _dienst):
    _antwort(monkeypatch, [{"id": 4, "zurueckgenommen_am": None}])
    assert wo.observe({"check": "supabase_aenderung_zurueck", "gruppe": None, "anzahl": 0}).verified_ok is False


@pytest.mark.parametrize("spec", [
    {"gruppe": "a\nb", "anzahl": 1},
    {"gruppe": "plan_1", "anzahl": 0},
    {"gruppe": None, "anzahl": 2},
    {"gruppe": 7, "anzahl": 1},
    {},
])
def test_unbrauchbare_angabe_bleibt_ungeprueft_und_fragt_nicht(monkeypatch, _dienst, spec):
    get = _antwort(monkeypatch, [])
    assert wo.observe({"check": "supabase_aenderung_zurueck", **spec}).verified_ok is None
    get.assert_not_called()


def test_ohne_dienstschluessel_ungeprueft(monkeypatch, _dienst):
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY")
    get = _antwort(monkeypatch, [])
    assert wo.observe({"check": "supabase_aenderung_zurueck", "gruppe": "plan_1", "anzahl": 1}).verified_ok is None
    get.assert_not_called()


def test_dienstschluessel_aus_der_datei_wie_im_stack(monkeypatch, _dienst, tmp_path):
    datei = tmp_path / "dienst"
    datei.write_text("schluessel-aus-datei\n", encoding="utf-8")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY_FILE", str(datei))
    get = _antwort(monkeypatch, [{"id": 8, "zurueckgenommen_am": "2026-11-20T10:05:00Z"}])
    assert wo.observe({"check": "supabase_aenderung_zurueck", "gruppe": "plan_1", "anzahl": 1}).verified_ok is True
    assert get.call_args.kwargs["headers"]["apikey"] == "schluessel-aus-datei"


@pytest.mark.parametrize("status", [301, 401, 500])
def test_abgewiesene_nachfrage_bleibt_ungeprueft(monkeypatch, _dienst, status):
    _antwort(monkeypatch, {"message": "nein"}, status=status)
    assert wo.observe({"check": "supabase_aenderung_zurueck", "gruppe": "plan_1", "anzahl": 1}).verified_ok is None


# ── Pruefer nimmt die Gruppe nur aus der Quittung ────────────────────────


def test_pruefer_fragt_mit_der_gruppe_aus_der_quittung(monkeypatch, _dienst):
    get = _antwort(monkeypatch, [{"id": 8, "zurueckgenommen_am": "2026-11-20T10:05:00Z"}])
    urteil = CapabilityValidator().validate(
        _capability()["validator"], intent="mach das rueckgaengig", arg="",
        raw_result=_mcp_ergebnis({"gruppe": "plan_7f3a", "anzahl": 1, "eintraege": []}),
    )
    assert urteil["valid"] is True and urteil["verified"] is True
    assert get.call_args.kwargs["params"]["gruppe"] == "eq.plan_7f3a"


def test_pruefer_ohne_lesbare_quittung_ist_ungeprueft_und_fragt_nicht(monkeypatch, _dienst):
    get = _antwort(monkeypatch, [])
    urteil = CapabilityValidator().validate(
        _capability()["validator"], intent="zurueck", arg="", raw_result="irgendein Text mit id: plan_7f3a")
    assert urteil["valid"] is False and urteil["verified"] is None
    get.assert_not_called()


# ── Plan-Ausfuehrer ──────────────────────────────────────────────────────


class _Aufzeichner:
    def __init__(self, ergebnis):
        self.ergebnis = ergebnis
        self.aufrufe = []

    def call_with_arg(self, arg, arg_kwarg=None, extra_params=None):
        self.aufrufe.append((arg, arg_kwarg, set(extra_params or {})))
        return self.ergebnis


class _Agenten:
    def get_event_agent(self, event_id):
        return {"idea.aenderung_zurueck": "brain-ideas"}.get(event_id)


def _zurueck_schritt(monkeypatch, *, argument, absicht, ergebnis):
    from core.plan_executor import PlanExecutor
    from core.plan_schema import HopSpec

    monkeypatch.setattr("core.plan_executor.PlanExecutor._capture_kg_hits", lambda *args: [])
    monkeypatch.setattr("core.agent_yaml_registry.get_registry", lambda: _Agenten())
    ausfuehrer = _Aufzeichner(ergebnis)
    ziele = []
    monkeypatch.setattr("core.capability_targets.build_executor",
                        lambda ziel: ziele.append(ziel) or ausfuehrer)
    schritt = HopSpec(step_id="s1", description="zurueck", capability="aenderung_zurueck",
                      execution_target=AENDERUNG_ZURUECK_TARGET, arg_kwarg="gruppe",
                      arg_template=argument, retries=3)
    hr = PlanExecutor()._exec_hop(schritt, {}, plan_ctx={
        "plan_id": "plan-zurueck", "plan_revision": 1, "trace_id": "t1", "plan_intent": absicht})
    return hr, ausfuehrer, ziele


def test_plan_schritt_ohne_genannte_gruppe_ruft_das_werkzeug_ohne_argument(monkeypatch):
    hr, ausfuehrer, ziele = _zurueck_schritt(
        monkeypatch, argument="letzte", absicht="nimm die letzte Aenderung zurueck",
        ergebnis={"ok": True, "result": _mcp_ergebnis({"gruppe": "plan_a", "anzahl": 1, "eintraege": []})})
    assert ziele == [AENDERUNG_ZURUECK_TARGET]
    assert ausfuehrer.aufrufe == [("", "gruppe", {"_runtime_authority"})]


def test_plan_schritt_reicht_eine_genannte_gruppe_durch(monkeypatch):
    _hr, ausfuehrer, _ziele = _zurueck_schritt(
        monkeypatch, argument="hand-4711", absicht="nimm hand-4711 zurueck",
        ergebnis={"ok": True, "result": _mcp_ergebnis({"gruppe": "hand-4711", "anzahl": 1, "eintraege": []})})
    assert ausfuehrer.aufrufe == [("hand-4711", "gruppe", {"_runtime_authority"})]


def test_plan_schritt_wird_nach_einem_fehler_nicht_wiederholt(monkeypatch):
    hr, ausfuehrer, _ziele = _zurueck_schritt(
        monkeypatch, argument="", absicht="zurueck",
        ergebnis={"ok": False, "error": "OpenFangUnavailable: gateway down"})
    assert hr.ok is False
    assert len(ausfuehrer.aufrufe) == 1


# ── Werkzeugfehler ohne Fehlerkennzeichen (OpenFang reicht isError nicht durch) ──


def test_quittung_pruefen_laesst_gueltige_quittung_durch():
    from core.aenderung_zurueck_contract import quittung_oder_fehler
    assert quittung_oder_fehler(_mcp_ergebnis({"gruppe": "plan_a", "anzahl": 1, "eintraege": []})) == ""
    assert quittung_oder_fehler(_mcp_ergebnis({"gruppe": None, "anzahl": 0, "eintraege": []})) == ""


@pytest.mark.parametrize("ergebnis, erwartet", [
    # so kam es am 21.11. im Betrieb an: Fehlertext, aber isError false
    (_mcp_ergebnis({"error": "configuration_error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set"}),
     "aenderung_zurueck: Werkzeug meldet einen Fehler (configuration_error)"),
    (_mcp_ergebnis({"error": "aenderung_konflikt: public.canvas_nodes n1"}),
     "aenderung_zurueck: aenderung_konflikt: public.canvas_nodes n1"),
    (_mcp_ergebnis({"error": "aenderung_unbekannt: plan-9"}, fehler=True),
     "aenderung_zurueck: aenderung_unbekannt: plan-9"),
    (_mcp_ergebnis({"error": "supabase_http_error: status=500"}),
     "aenderung_zurueck: Werkzeug meldet einen Fehler (supabase_http_error)"),
    (_mcp_ergebnis({"error": "boese\nKEY=geheim"}), "aenderung_zurueck: Werkzeug meldet einen Fehler"),
    (_mcp_ergebnis({"irgendwas": 1}), "aenderung_zurueck: keine gueltige Quittung"),
    ("Text", "aenderung_zurueck: keine gueltige Quittung"),
])
def test_quittung_pruefen_macht_aus_fehlertext_einen_fehler(ergebnis, erwartet):
    from core.aenderung_zurueck_contract import quittung_oder_fehler
    assert quittung_oder_fehler(ergebnis) == erwartet


def test_plan_schritt_scheitert_wenn_das_werkzeug_nur_einen_fehlertext_liefert(monkeypatch):
    """Am 21.11. meldete der Schritt ok, obwohl nichts zurueckgenommen war."""
    from core import capability_targets as ct

    ausfuehrer = ct.McpExecutor(AENDERUNG_ZURUECK_TARGET)
    fehlertext = _mcp_ergebnis({"error": "configuration_error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set"})
    with pytest.raises(RuntimeError, match="aenderung_zurueck: Werkzeug meldet einen Fehler"):
        ausfuehrer._pruefe_werkzeug_ergebnis(fehlertext)
    gut = _mcp_ergebnis({"gruppe": "plan_a", "anzahl": 1, "eintraege": []})
    assert ausfuehrer._pruefe_werkzeug_ergebnis(gut) is gut
    # andere Werkzeuge bleiben unberuehrt
    assert ct.McpExecutor("mcp:brain-ideas:spaces-ideas:idea_connect")._pruefe_werkzeug_ergebnis(fehlertext) is fehlertext

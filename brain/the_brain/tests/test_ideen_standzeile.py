"""Standzeile: Ideen-Schritte senden deutsche Saetze statt Rohtext (Arbeitslauf 2, Punkt 6)."""
from __future__ import annotations

import json
import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.ideen_standzeile import fuer_app  # noqa: E402
from core.plan_executor import _hop_event  # noqa: E402
from core.plan_schema import HopResult, HopSpec  # noqa: E402

ZIEL = "mcp:brain-ideas:spaces-ideas:idea_rate"


def _hop(**kw):
    spec = HopSpec(step_id="s1", description="idea.rate via Ideen-Abkuerzung", capability="idea.rate",
                   execution_target=ZIEL)
    return spec, HopResult(step_id="s1", capability="idea.rate", target=ZIEL, **kw)


def _roh(ereignis) -> str:
    return json.dumps(ereignis, ensure_ascii=False, default=str)


def test_fehler_und_name_sind_deutsch() -> None:
    spec, hr = _hop(ok=False, error="RuntimeError: Ideen-Werkzeug meldet einen Fehler (idea_not_found)")
    ereignis = _hop_event(spec, hr)
    assert ereignis["description"] == "Bewerten"
    assert "RuntimeError" not in _roh(ereignis) and "via Ideen-Abkuerzung" not in _roh(ereignis)
    assert ereignis["error"] and ereignis["result_preview"] == ""


def test_zeitueberschreitung_kommt_nicht_roh() -> None:
    spec, hr = _hop(ok=False, error="ReadTimeout: HTTPConnectionPool: Read timed out. (read timeout=30)")
    ereignis = _hop_event(spec, hr)
    assert ereignis["error"].startswith("Der Dienst hat nicht rechtzeitig geantwortet")
    assert "HTTPConnectionPool" not in _roh(ereignis)


def test_ergebnis_ist_ein_satz_statt_repr() -> None:
    ergebnis = {"content": [{"type": "text", "text": json.dumps({"anzahl": 3})}], "isError": False}
    spec = HopSpec(step_id="s1", description="x", capability="idea.auto_link",
                   execution_target="mcp:brain-ideas:spaces-ideas:idea_auto_link")
    hr = HopResult(step_id="s1", capability="idea.auto_link", target=spec.execution_target, ok=True, result=ergebnis)
    ereignis = _hop_event(spec, hr)
    assert ereignis["result_preview"] == "3 Verbindungen angelegt."


def test_angenommener_auftrag_ist_kein_fehler_und_freigabe_wartet() -> None:
    spec, hr = _hop(ok=False, pending=True, error="runtime authority approval pending",
                    result={"auftrag_id": "x1", "agent": "ideen", "status": "offen"})
    ereignis = _hop_event(spec, hr)
    assert ereignis["ok"] is True and ereignis["status"] == "angenommen" and ereignis["error"] is None
    spec, hr = _hop(ok=False, pending=True, error="runtime authority approval pending")
    ereignis = _hop_event(spec, hr)
    assert ereignis["ok"] is False and ereignis["error"].startswith("Wartet auf Freigabe")
    assert "runtime authority" not in _roh(ereignis)


def test_andere_spaces_ohne_fehler_bleiben_unveraendert() -> None:
    ereignis = {"capability": "desktop_open", "space": "desktop", "description": "roh", "error": None}
    assert fuer_app(ereignis) is ereignis


@pytest.mark.parametrize("fehler,satz", [
    ("ConnectionError: HTTPConnectionPool(host='coding-api', port=8000): Max retries exceeded",
     "Der Dienst ist gerade nicht erreichbar; geändert habe ich nichts."),
    ("ReadTimeout: timed out after 30s", "Der Dienst hat nicht rechtzeitig geantwortet."),
    ("KeyError: 'x'", "Der Schritt hat nicht geklappt."),
])
def test_andere_spaces_kein_rohfehler_im_strom(fehler, satz) -> None:
    # Nachmessung 23.11.2026: in der Standzeile stand "ConnectionError: HTTPConnectionPool(...".
    ereignis = {"capability": "code_status", "space": "projects", "description": "Status", "ok": False,
                "error": fehler, "result_preview": "None"}
    neu = fuer_app(ereignis)
    assert neu["error"] == satz and neu["result_preview"] == ""
    assert ereignis["error"] == fehler  # Kopie; der Rohtext bleibt im Schnappschuss

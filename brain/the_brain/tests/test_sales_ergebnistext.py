"""Ergebnistext des Auftragsbuchs: keine Inhalte von Auftraegen ausserhalb des MCP-Ausfuehrers.

Jede Antwort des Servers ``auftragsbuch`` verlaesst ``McpExecutor._call`` nur gekuerzt
(core/ergebnis_text.fuers_protokoll); ``auftrag_lesen`` eines Sales-Auftrags traegt dazu den festen
Satz ("2 Kontakte gefunden.") und das Sprungziel in den Sales-Reiter. Namen, Titel und Links stehen
damit weder im Schrittergebnis noch in Spur, Protokoll, Synthese oder final_text.
Die Kontaktdaten unten sind erfunden.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "brain" / "the_brain"))

from core import capability_targets as ct  # noqa: E402
from core.openfang_runtime_authority import RuntimeInvocationContext  # noqa: E402
from core.sales_antwort import antwort_fuer_aufruf, quittung  # noqa: E402

NR = "4aef2c02-0000-4000-8000-000000000001"
K1 = "0b1c2d3e-0000-4000-8000-00000000000a"
K2 = "0b1c2d3e-0000-4000-8000-00000000000b"
NAMEN = ("Erika Muster", "Musterfirma", "/kontakte/")


def _gelesen(stand="fertig", ergebnis=None, art="kontakt_finden"):
    if ergebnis is None:
        ergebnis = {"treffer": [
            {"kontakt_id": K1, "titel": "Erika Muster (Musterfirma)", "link": f"/kontakte/{K1}"},
            {"kontakt_id": K2, "titel": "Max Muster", "link": f"/kontakte/{K2}"},
        ], "anzahl": 2}
    return {"auftrag_id": NR, "an": "sales", "art": art, "version": 1, "stand": stand,
            "ergebnis": ergebnis, "fehler": None, "plan_id": "sales_1", "frist_bis": "2026-11-24T12:00:00Z",
            "beleg": "entfaellt"}


@pytest.fixture
def openfang(monkeypatch):
    """OpenFang antwortet mit dem JSON-Text, den das Auftragsbuch liefern wuerde."""
    antwort = {}

    class _Bereit:
        agent_id = "agent-uuid"
        approval_ref = "appr-1"
        cost_ref = "cost-1"
        invocation_id = "inv-1"

    class _Freigabe:
        @classmethod
        def from_environment(cls):
            return cls()

        def prepare_invocation(self, context):
            return _Bereit()

    def _anfrage(self, method, url, *, headers, payload=None, retry_transient=False, timeout_cap_s=None):
        return {"jsonrpc": "2.0", "id": payload["id"],
                "result": {"content": [{"type": "text", "text": json.dumps(antwort["inhalt"])}],
                           "isError": antwort.get("fehler", False)}}

    monkeypatch.setattr(ct, "RuntimeAuthorityClient", _Freigabe)
    monkeypatch.setattr(ct.McpExecutor, "_request_json", _anfrage)
    monkeypatch.setattr(ct.McpExecutor, "_configuration", lambda self: ("http://openfang.test", "nur-im-test"))
    return antwort


def _rufe(werkzeug: str, argumente: dict, eingabe_herkunft="getippt"):
    kontext = RuntimeInvocationContext(correlation_id="t", plan_id="sales_1", plan_revision=1, step_id="s1",
                                       space_id="sales", agent_name="brain-sales", invocation_id="i",
                                       eingabe_herkunft=eingabe_herkunft)
    exe = ct.build_executor(f"mcp:brain-sales:auftragsbuch:{werkzeug}")
    return exe.call_with_arg(argumente, extra_params={"_runtime_authority": kontext})


def _ohne_namen(wert) -> None:
    text = json.dumps(wert, ensure_ascii=False)
    for n in NAMEN:
        assert n not in text, (n, text)


def test_lesen_kontakt_finden_nur_anzahl_und_fester_satz(openfang):
    openfang["inhalt"] = _gelesen()
    aus = _rufe("auftrag_lesen", {"id": NR}, eingabe_herkunft=None)
    assert aus["ok"] is True
    r = aus["result"]
    _ohne_namen(aus)
    assert r["satz"] == "2 Kontakte gefunden."
    assert r["ziel"] == {"space": "sales", "pfad": "/kontakte"}
    assert r["listen"] == {"treffer": 2} and r["zahlen"] == {"anzahl": 2}
    assert r["stand"] == "fertig" and r["art"] == "kontakt_finden"


def test_lesen_ein_treffer_springt_nur_auf_die_seite(openfang):
    openfang["inhalt"] = _gelesen(ergebnis={"treffer": [
        {"kontakt_id": K1, "titel": "Erika Muster (Musterfirma)", "link": f"/kontakte/{K1}"}], "anzahl": 1})
    r = _rufe("auftrag_lesen", {"id": NR})["result"]
    _ohne_namen(r)
    assert K1 not in json.dumps(r)
    assert r["satz"] == "1 Kontakt gefunden." and r["ziel"] == {"space": "sales", "pfad": "/kontakte"}


@pytest.mark.parametrize("stand, satz", [
    ("offen", "Ich suche (Auftrag 4aef2c02)."),
    ("in_arbeit", "Ich suche (Auftrag 4aef2c02)."),
    ("fehler", "Die Suche ist fehlgeschlagen (Auftrag 4aef2c02)."),
])
def test_lesen_andere_staende(openfang, stand, satz):
    openfang["inhalt"] = _gelesen(stand=stand, ergebnis=None if stand == "fertig" else {})
    openfang["inhalt"]["ergebnis"] = None
    assert _rufe("auftrag_lesen", {"id": NR})["result"]["satz"] == satz


def test_lesen_zaehlart(openfang):
    openfang["inhalt"] = _gelesen(art="post_offen", ergebnis={"chats_unbeantwortet": 3, "mails_offen": 0})
    r = _rufe("auftrag_lesen", {"id": NR})["result"]
    assert r["satz"] == "3 Chats sind unbeantwortet, 0 Mails sind offen."
    assert r["ziel"] == {"space": "sales", "pfad": "/posteingang"}


def test_lesen_falsche_form_nur_fester_satz(openfang):
    openfang["inhalt"] = _gelesen(ergebnis={"treffer": [{"titel": "Erika Muster"}], "anzahl": 1})
    r = _rufe("auftrag_lesen", {"id": NR})["result"]
    _ohne_namen(r)
    assert r["satz"] == "Die Antwort von Sales passt nicht zur Form (Auftrag 4aef2c02)."


def test_lesen_fremder_empfaenger_ohne_satz(openfang):
    inhalt = _gelesen()
    inhalt["an"] = "rowboat"
    openfang["inhalt"] = inhalt
    r = _rufe("auftrag_lesen", {"id": NR})["result"]
    _ohne_namen(r)
    assert "satz" not in r


def test_anlegen_quittung_bleibt_lesbar(openfang):
    openfang["inhalt"] = {"auftrag_id": NR, "an": "sales", "art": "kontakt_finden", "version": 1,
                          "stand": "offen"}
    r = _rufe("sales_kontakt_finden", {"eingabe": {"suche": "Muster"}})["result"]
    q = quittung(r)
    assert q is not None and q["auftrag_id"] == NR and q["art"] == "kontakt_finden"
    felder = antwort_fuer_aufruf("sales.kontakt_finden", True, r, None)
    assert felder["final_text"] == "Ich suche (Auftrag 4aef2c02)."


def test_fehler_des_auftragsbuchs_unveraendert_fest(openfang):
    openfang["inhalt"] = "Unbekannte Auftragsart"
    openfang["fehler"] = True
    aus = _rufe("sales_kontakt_finden", {"eingabe": {"suche": "Muster"}})
    assert aus["ok"] is False and "Unbekannte Auftragsart" in aus["error"]


def test_chat_antwort_auf_auftrag_lesen(openfang):
    openfang["inhalt"] = _gelesen()
    r = _rufe("auftrag_lesen", {"id": NR})["result"]
    felder = antwort_fuer_aufruf("sales.auftrag_lesen", True, r, None)
    assert felder == {"ok": True, "space": "sales", "final_text": "2 Kontakte gefunden.",
                      "ziel": {"space": "sales", "pfad": "/kontakte"}}
    _ohne_namen(felder)


def test_chat_antwort_ohne_satz_fest_und_ohne_inhalt():
    felder = antwort_fuer_aufruf("sales.auftrag_lesen", True, {"stand": "unlesbar"}, None)
    assert felder["ok"] is False and felder["final_text"].startswith("Das Auftragsbuch antwortet gerade nicht")


def test_router_nimmt_festen_satz_fuer_auftrag_lesen():
    quelle = (ROOT / "brain" / "the_brain" / "web" / "routers" / "introspection.py").read_text(encoding="utf-8")
    assert '_sh[0].capability == "sales.auftrag_lesen"' in quelle
    assert quelle.index('_sh[0].capability == "sales.auftrag_lesen"') < quelle.index(
        "if _sales_fest:\n        _fest = _sales_antwort(plan, exec_result)")

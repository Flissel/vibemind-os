"""Ereignis "Chat" statt starrer Absage (core/chat_ereignis.py, _ehrlich in introspection).

Das Modell ist immer eine Attrappe: kein echter Aufruf, kein Netz. Saetze und Namen erfunden.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import chat_ereignis as ce  # noqa: E402
from core import entscheidung_graph as eg  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_verstehen import (  # noqa: E402
    IDEEN, STARK, BETRIEB, _Ausfuehrer, _MehrPlaner, _OhnePlan, _Planer, _ZaehlSynth, _client,
)

UNSINN = "Wie viele Zähne hat ein Regenbogen?"
IDEEN_OHNE_BUBBLE = {"space": "ideas", "bubbles": [{"id": "b0b1e001", "title": "Stadtfest"}]}
ANTWORT = "Dazu habe ich keinen festen Weg. Frag mich nach den Karten im Stadtfest."


class _Modell:
    """Attrappe fuer das Chat-Modell: merkt sich die Nachrichten, liefert einen festen Text."""

    def __init__(self, text=ANTWORT, fehler=None, warten=0.0):
        self.text, self.fehler, self.warten = text, fehler, warten
        self.aufrufe: list = []

    async def __call__(self, nachrichten):
        self.aufrufe.append(nachrichten)
        if self.warten:
            await asyncio.sleep(self.warten)
        if self.fehler:
            raise self.fehler
        return self.text


@pytest.fixture
def modell(monkeypatch):
    m = _Modell()
    monkeypatch.setattr(ce, "_openfang", m)
    monkeypatch.setenv(ce.SCHALTER, "1")
    return m


@pytest.fixture(scope="module")
def muster():
    from core.capability_router import CapabilityRouter

    return CapabilityRouter(Path(__file__).resolve().parents[1] / "data" / "capabilities.yaml")


def _ist_chat(body, space):
    assert body["ok"] is True and body["weg"] == "chat" and body["ereignis"] == "chat"
    assert body["final_text"] == ANTWORT and body.get("space") == space
    assert body["executed"] == {} and "ziel" not in body and "rueckfrage" not in body


# ── Je Ausgang ein Satz ins Chat-Ereignis ──────────────────────────────────

def test_planer_nicht_erlaubt_wird_chat(monkeypatch, muster, modell) -> None:
    client, state = _client(monkeypatch, STARK, muster=muster)
    body = client.post("/api/multihop/execute", json={"intent": "Wie viele Notizen hat Stadtfest eigentlich",
                                                      "context": {"space": "roarboot"}}).json()
    _ist_chat(body, "roarboot")
    assert state.plan_executor.plaene == [] and len(modell.aufrufe) == 1


def test_planer_ohne_plan_wird_chat(monkeypatch, muster, modell) -> None:
    client, state = _client(monkeypatch, STARK, planer=_OhnePlan(), muster=muster)
    body = client.post("/api/multihop/execute", json={"intent": UNSINN, "context": IDEEN_OHNE_BUBBLE}).json()
    _ist_chat(body, "ideas")
    assert state.plan_executor.plaene == []


def test_plan_ohne_faehigkeit_wird_chat(monkeypatch, muster, modell) -> None:
    ausfuehrer = _Ausfuehrer(None, ok=False)
    client, _ = _client(monkeypatch, STARK, ausfuehrer=ausfuehrer, planer=_Planer("unknown"), muster=muster)
    body = client.post("/api/multihop/execute", json={"intent": UNSINN, "context": IDEEN_OHNE_BUBBLE}).json()
    _ist_chat(body, "ideas")
    assert ausfuehrer.plaene == []


def test_mehrdeutiger_plan_wird_chat_mit_bubble_titel(monkeypatch, modell) -> None:
    ausfuehrer = _Ausfuehrer({"x": 1})
    client, _ = _client(monkeypatch, STARK, ausfuehrer=ausfuehrer,
                        planer=_MehrPlaner(["video_team_status", "bubble_stats"]))
    body = client.post("/api/multihop/execute", json={"intent": "Wie läuft es bei Stadtfest?",
                                                      "context": IDEEN}).json()
    _ist_chat(body, "ideas")
    assert ausfuehrer.plaene == []
    benutzer = modell.aufrufe[0][-1]["content"]
    assert "Offene Bubble: Stadtfest" in benutzer and "Reiter: ideas" in benutzer


def test_bereichsfrage_wird_chat(monkeypatch, muster, modell) -> None:
    client, _ = _client(monkeypatch, STARK, muster=muster)
    body = client.post("/api/multihop/execute", json={"intent": "Welche Kurse gibt es?",
                                                      "context": {"space": "learning"}}).json()
    assert body["weg"] == "chat" and body["ereignis"] == "chat"


# ── Was fest bleibt ────────────────────────────────────────────────────────

def test_schalter_aus_ist_die_heutige_absage(monkeypatch, muster, modell) -> None:
    monkeypatch.delenv(ce.SCHALTER)
    client, _ = _client(monkeypatch, STARK, planer=_OhnePlan(), muster=muster)
    body = client.post("/api/multihop/execute", json={"intent": UNSINN, "context": IDEEN_OHNE_BUBBLE}).json()
    assert body["weg"] == "nicht-verstanden" and body["rueckfrage"] is True and modell.aufrufe == []


def test_vorgabe_ist_aus(monkeypatch) -> None:
    monkeypatch.delenv(ce.SCHALTER, raising=False)
    assert ce.an() is False


@pytest.mark.parametrize("fehler,warten", [(RuntimeError("weg"), 0.0), (None, 0.5)], ids=["fehler", "zeit"])
def test_fehler_und_zeitgrenze_fallen_auf_die_absage(monkeypatch, muster, fehler, warten) -> None:
    monkeypatch.setenv(ce.SCHALTER, "1")
    monkeypatch.setattr(ce, "_openfang", _Modell(fehler=fehler, warten=warten))
    monkeypatch.setattr(ce, "ZEITGRENZE_S", 0.05)
    client, _ = _client(monkeypatch, STARK, planer=_OhnePlan(), muster=muster)
    body = client.post("/api/multihop/execute", json={"intent": UNSINN, "context": IDEEN_OHNE_BUBBLE}).json()
    assert body["weg"] == "nicht-verstanden" and body["final_text"].startswith("Das habe ich nicht verstanden.")


def test_zeitgrenze_ist_20_sekunden() -> None:
    assert ce.ZEITGRENZE_S == 20.0


def test_plan_lief_schon_bleibt_fest(monkeypatch, modell) -> None:
    ausfuehrer = _Ausfuehrer(None, ok=False)
    client, _ = _client(monkeypatch, BETRIEB, ausfuehrer=ausfuehrer, planer=_Planer("browser_open"),
                        synth=_ZaehlSynth("x"))
    body = client.post("/api/multihop/execute", json={"intent": UNSINN, "context": IDEEN_OHNE_BUBBLE}).json()
    assert len(ausfuehrer.plaene) == 1 and body["weg"] == "nicht-verstanden" and modell.aufrufe == []


def test_offene_rueckfrage_bleibt_fest(monkeypatch, muster, modell) -> None:
    import core.hoeflichkeit as h

    monkeypatch.setattr(h, "etwas_offen", lambda: True)
    client, _ = _client(monkeypatch, STARK, planer=_OhnePlan(), muster=muster)
    body = client.post("/api/multihop/execute", json={"intent": UNSINN, "context": IDEEN_OHNE_BUBBLE}).json()
    assert body["weg"] != "chat" and modell.aufrufe == []


@pytest.mark.parametrize("satz", [
    "Lösche die Karte Bühnenplan", "Bitte lege eine Karte Sponsoren an", "Verschiebe Druckerei nach Stadtfest",
    "Kannst du Stadtfest befördern", "Halt fest: Bühne steht", "Benenne Stadtfest um", "Hallo", "Danke!", "", "  ",
])
def test_schreibwunsch_hoeflichkeit_und_leeres_bleiben_fest(monkeypatch, satz) -> None:
    import core.hoeflichkeit as h

    monkeypatch.setattr(h, "etwas_offen", lambda: False)
    assert ce.fest_bleiben(satz) is True


@pytest.mark.parametrize("satz", ["Wie viele Zähne hat ein Regenbogen?", "Was kann ich hier machen?",
                                  "Erzähl mir was über das Stadtfest"])
def test_freie_frage_darf_in_den_chat(monkeypatch, satz) -> None:
    import core.hoeflichkeit as h

    monkeypatch.setattr(h, "etwas_offen", lambda: False)
    assert ce.fest_bleiben(satz) is False


@pytest.mark.parametrize("text", [
    "Ich habe die Karte Bühnenplan gelöscht.", "Die Bubble Stadtfest ist jetzt befördert.",
    "Sure, here is what I found for you today.", "", None,
])
def test_unbrauchbare_antwort_faellt_zurueck(text) -> None:
    assert ce.brauchbar(text) is None


def test_andere_wege_bleiben_fest(monkeypatch) -> None:
    monkeypatch.setenv(ce.SCHALTER, "1")
    m = _Modell()
    for weg in ("kein-weg", "noch-nicht"):
        assert asyncio.run(ce.antwort(UNSINN, weg, "ideas", rufer=m)) is None
    assert m.aufrufe == []


# ── Modell ohne Werkzeuge ueber die bestehende Rolle ───────────────────────

def test_openfang_aufruf_ohne_werkzeuge_ueber_brain_fallback(monkeypatch) -> None:
    gesehen: dict = {}

    class _Completions:
        async def create(self, **kw):
            gesehen.update(kw)
            nachricht = types.SimpleNamespace(content=ANTWORT)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=nachricht)])

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=_Completions()))
    attrappe = types.ModuleType("vibemind_shared")
    attrappe.get_model = lambda rolle: {"brain_fast_reasoning": "openfang:brain-fallback"}[rolle]
    attrappe.get_client = lambda rolle: client
    monkeypatch.setitem(sys.modules, "vibemind_shared", attrappe)
    text = asyncio.run(ce._openfang(ce.nachrichten(UNSINN, "ideas", "Stadtfest")))
    assert text == ANTWORT and gesehen["model"] == "openfang:brain-fallback"
    assert "tools" not in gesehen and "tool_choice" not in gesehen
    assert [n["role"] for n in gesehen["messages"]] == ["system", "user"]


# ── Decision-Graph ─────────────────────────────────────────────────────────

def test_entscheidung_traegt_weg_chat_und_quelle_reiter() -> None:
    daten = {"ok": True, "weg": "chat", "ereignis": "chat", "space": "ideas", "executed": {},
             "final_text": ANTWORT, "trace_id": "tr_a0000001"}
    eintrag = eg.eintrag_bauen(daten, satz=UNSINN, reiter="ideas", laenge=len(UNSINN), dauer_ms=5)
    assert eintrag["weg"] == "chat" and eintrag["bereich_quelle"] == "reiter" and eintrag["bereich"] == "ideas"
    assert eg.bereich_bestimmen({"weg": "chat", "space": "ideas"}, None) == ("ideas", "reiter")

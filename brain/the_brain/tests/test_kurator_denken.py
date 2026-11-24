"""Kurator-Deutung über brain_denken mit Messzeile (24.11.2026). Nur Attrappen, kein Modell."""
from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timezone
from typing import Any

import pytest

from core import denken_messung
from core.denken_messung import DENKEN_ROLLE, DenkenNichtEingerichtet
from core.knowledge import kurator
from core.knowledge.schema import Beleg, Dokument, Fakt

T = datetime(2026, 9, 23, tzinfo=timezone.utc)
GEHEIM = "Bühnenplan geheim"


def _dok() -> Dokument:
    return Dokument(typ="bubble", id="b0b1e001", titel="Stadtfest", stand=T,
                    fakten=[Fakt(schluessel="status", wert=GEHEIM, beleg=1)],
                    belege=[Beleg(nr=1, quelle="supabase", ziel="x", feld="status", wert=GEHEIM, gemessen=T)])


def _zeilen() -> list[dict[str, Any]]:
    pfad = denken_messung.protokoll_pfad()
    return [json.loads(z) for z in pfad.read_text(encoding="utf-8").splitlines()] if pfad.exists() else []


@pytest.fixture
def router(monkeypatch: pytest.MonkeyPatch):
    """Ersetzt core.multi_llm_router durch eine Attrappe; merkt sich die Aufrufe."""
    stand: dict[str, Any] = {"aufrufe": [], "antwort": "Läuft [B1].", "fehler": None,
                             "nutzung": {"tokens_rein": 120, "tokens_raus": 9, "kosten_usd": None}}

    class _Router:
        def _call_openrouter(self, **kw: Any) -> Any:
            stand["aufrufe"].append(kw)
            if kw.get("nutzung") is not None:
                kw["nutzung"].update(stand["nutzung"])
            if stand["fehler"] is not None:
                raise stand["fehler"]
            return stand["antwort"]

        def _complete_sync(self, *a: Any, **k: Any) -> Any:
            raise AssertionError("Kurator darf den Planer nicht mehr rufen")

    modul = types.ModuleType("core.multi_llm_router")
    modul.MultiLLMRouter = _Router
    monkeypatch.setitem(sys.modules, "core.multi_llm_router", modul)
    return stand


def test_deutung_ruft_die_denk_rolle_und_misst(router) -> None:
    assert kurator.llm_deuten(_dok()) == "Läuft [B1]."
    (aufruf,) = router["aufrufe"]
    assert aufruf["role"] == DENKEN_ROLLE and aufruf["max_tokens"] == 300
    (zeile,) = _zeilen()
    assert zeile["agent"] == "kurator" and zeile["ergebnis"] == "ok"
    assert zeile["tokens_rein"] == 120 and zeile["tokens_raus"] == 9 and zeile["zeichen_raus"] == len("Läuft [B1].")
    assert zeile["zeichen_rein"] > 0


def test_messzeile_ohne_inhalte(router) -> None:
    router["fehler"] = RuntimeError(GEHEIM)
    with pytest.raises(RuntimeError):
        kurator.llm_deuten(_dok())
    text = denken_messung.protokoll_pfad().read_text(encoding="utf-8")
    assert GEHEIM not in text and "Stadtfest" not in text
    (zeile,) = _zeilen()
    assert zeile["ergebnis"] == "fehler" and zeile["fehler"] == "RuntimeError"


def test_leere_antwort_zaehlt_als_versuch(router) -> None:
    router["antwort"] = "  "
    assert kurator.llm_deuten(_dok()) == ""
    assert [z["ergebnis"] for z in _zeilen()] == ["leer"]


def test_ohne_eigenen_agenten_wird_gemessen_und_weitergeworfen(router) -> None:
    router["fehler"] = DenkenNichtEingerichtet("x")
    with pytest.raises(DenkenNichtEingerichtet):
        kurator.llm_deuten(_dok())
    assert [z["ergebnis"] for z in _zeilen()] == ["nicht_eingerichtet"]


@pytest.mark.parametrize("schalter", ["umgebung", "stoppdatei", "bis"])
def test_abgeschaltet_kein_aufruf_keine_zeile(router, monkeypatch: pytest.MonkeyPatch, schalter: str) -> None:
    if schalter == "umgebung":
        monkeypatch.setenv("BRAIN_DENKEN", "0")
    elif schalter == "stoppdatei":
        denken_messung.stopp_datei().parent.mkdir(parents=True, exist_ok=True)
        denken_messung.stopp_datei().write_text("", encoding="utf-8")
    else:
        monkeypatch.setenv("BRAIN_DENKEN_BIS", "2000-01-01T00:00:00+00:00")
    with pytest.raises(kurator.DenkenAus):
        kurator.llm_deuten(_dok())
    assert router["aufrufe"] == [] and _zeilen() == []


class _Tresor:
    def __init__(self) -> None:
        self.letzter_status = "geschrieben"

    def laden(self, dok):
        return (False, None)

    def deutung_noch_gueltig(self, dok, geladen=None):
        return False

    def schreiben(self, dok, bekannte=None, geladen=None):
        return True, []

    def bekannte_namen(self):
        return set()

    def alle(self):
        return []

    def veraltete_verschieben(self, typ, geliefert):
        return []


def test_kurator_ohne_eigenen_agenten_fragt_im_lauf_nur_einmal(router) -> None:
    """Fehlt der Agent, schaltet der Kurator die Deutung für den Lauf ab: ein Versuch, eine Zeile."""
    router["fehler"] = DenkenNichtEingerichtet("x")
    leser = types.SimpleNamespace(bubbles=lambda j: [_dok(), _dok().model_copy(update={"id": "b0b1e002"})],
                                  GEKAPPT={})
    k = kurator.Kurator(_Tresor(), deuten=kurator.llm_deuten, leser=leser)
    k._lauf(("bubbles",))
    assert len(router["aufrufe"]) == 1 and len(_zeilen()) == 1
    assert k.stats["geschrieben"] == 2 and k.stats["deutung_verworfen"] == 1

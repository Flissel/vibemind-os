"""Hintergrund-Denken über eine eigene Rolle, mit Abkühlzeit, Messprotokoll und Schalter (24.11.2026).

Frage des Betreibers: Was kostet eine Stunde Hintergrund-Denken auf einem kleinen Modell?
"""
from __future__ import annotations

import importlib
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

BRAIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRAIN))

from core import denken_messung  # noqa: E402
from core.brain_chat import MicroAgentPool  # noqa: E402

GEHEIM = "Stadtfest Bühnenplan geheim"


def _zeilen() -> list[dict[str, Any]]:
    pfad = denken_messung.protokoll_pfad()
    if not pfad.exists():
        return []
    return [json.loads(z) for z in pfad.read_text(encoding="utf-8").splitlines()]


def _pool(antwort: Any = "Ein kurzer Gedanke.", fehler: Exception | None = None,
          nutzung: dict[str, Any] | None = None) -> tuple[MicroAgentPool, MagicMock]:
    router = MagicMock()

    def rufen(**kwargs: Any) -> Any:
        if nutzung is not None and kwargs.get("nutzung") is not None:
            kwargs["nutzung"].update(nutzung)
        if fehler is not None:
            raise fehler
        return antwort

    router._call_openrouter = MagicMock(side_effect=rufen)
    return MicroAgentPool(llm_router=router), router


# ── Rolle ───────────────────────────────────────────────────────────────


def test_hintergrund_ruft_die_eigene_rolle() -> None:
    pool, router = _pool()
    assert pool.summarize(GEHEIM) is not None
    assert router._call_openrouter.call_args.kwargs["role"] == denken_messung.DENKEN_ROLLE == "brain_denken"


def test_wartender_mensch_bleibt_beim_planer() -> None:
    pool, router = _pool("Antwort für den Menschen.")
    assert pool.enhance_response("Was ist das Stadtfest?", ["Eintrag"]) is not None
    assert "role" not in router._call_openrouter.call_args.kwargs
    assert _zeilen() == []


def test_forscher_mit_werkzeugen_ruft_nicht() -> None:
    pool, router = _pool()
    assert pool.research(GEHEIM, "Stadtfest") is None
    router._call_openrouter.assert_not_called()
    router._call_openrouter_with_tools.assert_not_called()


# ── Abkühlzeit ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("antwort,fehler,erwartet", [
    ("", None, "leer"),
    ("   ", None, "leer"),
    (None, RuntimeError("abgelehnt " + GEHEIM), "fehler"),
    (None, denken_messung.DenkenNichtEingerichtet("x"), "nicht_eingerichtet"),
])
def test_leer_oder_abgelehnt_zaehlt_als_lauf(antwort: Any, fehler: Exception | None, erwartet: str) -> None:
    pool, router = _pool(antwort, fehler)
    assert pool.summarize(GEHEIM) is None
    assert pool._can_run("summarizer") is False  # Abkühlzeit gesetzt
    assert pool.summarize(GEHEIM) is None
    assert router._call_openrouter.call_count == 1  # der nächste Takt ruft nicht sofort neu
    (zeile,) = _zeilen()
    assert zeile["ergebnis"] == erwartet


def test_erfolg_setzt_die_abkuehlzeit_wie_bisher() -> None:
    pool, _router = _pool()
    pool.summarize(GEHEIM)
    assert pool._can_run("summarizer") is False


# ── Messprotokoll ───────────────────────────────────────────────────────


def test_messzeile_ohne_inhalte_mit_tokens() -> None:
    pool, _router = _pool("Gedanke über den Bühnenplan.", nutzung={"tokens_rein": 120, "tokens_raus": 18})
    pool.summarize(GEHEIM)
    (zeile,) = _zeilen()
    assert set(zeile) == {"ts", "agent", "dauer_ms", "ergebnis", "zeichen_rein", "zeichen_raus",
                          "tokens_rein", "tokens_raus", "kosten_usd"}
    assert zeile["agent"] == "summarizer" and zeile["ergebnis"] == "ok"
    assert zeile["tokens_rein"] == 120 and zeile["tokens_raus"] == 18 and zeile["kosten_usd"] is None
    assert zeile["zeichen_raus"] == len("Gedanke über den Bühnenplan.")
    assert zeile["zeichen_rein"] > len(GEHEIM)
    roh = denken_messung.protokoll_pfad().read_text(encoding="utf-8")
    assert "Stadtfest" not in roh and "Bühnenplan" not in roh


def test_ohne_tokens_vom_weg_bleiben_sie_leer() -> None:
    pool, _router = _pool()
    pool.summarize(GEHEIM)
    (zeile,) = _zeilen()
    assert zeile["tokens_rein"] is None and zeile["tokens_raus"] is None


def test_fehler_nennt_nur_die_art() -> None:
    pool, _router = _pool(fehler=RuntimeError("abgelehnt " + GEHEIM))
    pool.summarize(GEHEIM)
    (zeile,) = _zeilen()
    assert zeile["fehler"] == "RuntimeError"
    assert GEHEIM not in denken_messung.protokoll_pfad().read_text(encoding="utf-8")


def test_schreibfehler_bricht_das_denken_nicht(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "datei").write_text("x", encoding="utf-8")
    monkeypatch.setenv("BRAIN_DENKEN_MESSUNG", str(tmp_path / "datei" / "unmoeglich.jsonl"))
    pool, _router = _pool()
    assert pool.summarize(GEHEIM) is not None


# ── Schalter ────────────────────────────────────────────────────────────


def test_schalter_umgebung_aus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAIN_DENKEN", "0")
    pool, router = _pool()
    assert pool.summarize(GEHEIM) is None
    router._call_openrouter.assert_not_called()
    assert pool._can_run("summarizer") is True and _zeilen() == []


def test_schalter_stoppdatei_ohne_neustart() -> None:
    pool, router = _pool()
    denken_messung.stopp_datei().parent.mkdir(parents=True, exist_ok=True)
    denken_messung.stopp_datei().write_text("", encoding="utf-8")
    assert pool.summarize(GEHEIM) is None
    router._call_openrouter.assert_not_called()
    denken_messung.stopp_datei().unlink()
    assert pool.summarize(GEHEIM) is not None


def test_schalter_bis_zeit(monkeypatch: pytest.MonkeyPatch) -> None:
    jetzt = datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("BRAIN_DENKEN_BIS", (jetzt + timedelta(hours=1)).isoformat())
    assert denken_messung.denken_an(jetzt) is True
    assert denken_messung.denken_an(jetzt + timedelta(hours=1)) is False
    monkeypatch.setenv("BRAIN_DENKEN_BIS", "2026-11-24T16:00:00")  # ohne Zone gilt UTC
    assert denken_messung.denken_an(jetzt) is True
    monkeypatch.setenv("BRAIN_DENKEN_BIS", "morgen irgendwann")
    assert denken_messung.denken_an(jetzt) is False  # unlesbar heißt aus


def test_schalter_gilt_nicht_fuer_den_wartenden_menschen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAIN_DENKEN", "0")
    pool, router = _pool("Antwort.")
    assert pool.enhance_response("Frage?", []) is not None
    router._call_openrouter.assert_called_once()


# ── Router ──────────────────────────────────────────────────────────────


class _Antwort:
    def __init__(self, text: str, usage: Any) -> None:
        self.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=text))]
        self.usage = usage


@pytest.fixture
def router_modul(monkeypatch: pytest.MonkeyPatch) -> Any:
    modelle = {"default": "openfang:brain-fallback", "brain_planning": "openfang:brain-planner",
               "brain_denken": "openfang:brain-denken"}
    shared = types.ModuleType("vibemind_shared")
    shared.OpenFangUnavailable = type("OpenFangUnavailable", (RuntimeError,), {})
    shared.get_client = lambda role: None
    shared.get_client_sync = lambda role: None
    shared.get_model = lambda role="default": modelle.get(role, modelle["default"])
    monkeypatch.setitem(sys.modules, "vibemind_shared", shared)
    sys.modules.pop("core.multi_llm_router", None)
    modul = importlib.import_module("core.multi_llm_router")
    modul._modelle = modelle
    return modul


def _client(rufe: list, antwort: Any) -> Any:
    def create(**kwargs: Any) -> Any:
        rufe.append(kwargs)
        return antwort
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))


def test_router_ruft_die_denk_rolle_und_liest_tokens(router_modul: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    rufe: list = []
    rollen: list = []

    def get_client_sync(role: str) -> Any:
        rollen.append(role)
        return _client(rufe, _Antwort("Gedanke", types.SimpleNamespace(prompt_tokens=90, completion_tokens=12)))

    monkeypatch.setattr(router_modul, "get_client_sync", get_client_sync)
    router = router_modul.MultiLLMRouter(enable_infinite_chat=False)
    nutzung: dict = {}
    text = router._call_openrouter(model="egal", prompt="p", max_tokens=50, temperature=0.5,
                                   role="brain_denken", nutzung=nutzung)
    assert text == "Gedanke" and rollen == ["brain_denken"]
    assert rufe[0]["model"] == "openfang:brain-denken"
    assert nutzung == {"tokens_rein": 90, "tokens_raus": 12, "kosten_usd": None}


def test_router_ohne_usage_liefert_none(router_modul: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(router_modul, "get_client_sync", lambda role: _client([], _Antwort("x", None)))
    router = router_modul.MultiLLMRouter(enable_infinite_chat=False)
    nutzung: dict = {}
    router._call_openrouter(model="m", prompt="p", max_tokens=5, temperature=0, role="brain_denken", nutzung=nutzung)
    assert nutzung == {"tokens_rein": None, "tokens_raus": None, "kosten_usd": None}


def test_router_usage_als_dict_mit_input_output(router_modul: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    usage = {"input_tokens": 40, "output_tokens": 7, "cost_usd": 0.0002}
    monkeypatch.setattr(router_modul, "get_client_sync", lambda role: _client([], _Antwort("x", usage)))
    router = router_modul.MultiLLMRouter(enable_infinite_chat=False)
    nutzung: dict = {}
    router._call_openrouter(model="m", prompt="p", max_tokens=5, temperature=0, role="brain_denken", nutzung=nutzung)
    assert nutzung == {"tokens_rein": 40, "tokens_raus": 7, "kosten_usd": 0.0002}


@pytest.mark.parametrize("ziel", ["openfang:brain-fallback", "openfang:brain-planner", "brain-denken"])
def test_router_ruft_nicht_ohne_eigenen_agenten(router_modul: Any, monkeypatch: pytest.MonkeyPatch, ziel: str) -> None:
    router_modul._modelle["brain_denken"] = ziel
    rufe: list = []
    monkeypatch.setattr(router_modul, "get_client_sync", lambda role: _client(rufe, _Antwort("x", None)))
    router = router_modul.MultiLLMRouter(enable_infinite_chat=False)
    with pytest.raises(denken_messung.DenkenNichtEingerichtet):
        router._call_openrouter(model="m", prompt="p", max_tokens=5, temperature=0, role="brain_denken")
    assert rufe == []


def test_router_ohne_rolle_bleibt_beim_planer(router_modul: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    rollen: list = []

    def get_client_sync(role: str) -> Any:
        rollen.append(role)
        return _client([], _Antwort("x", None))

    monkeypatch.setattr(router_modul, "get_client_sync", get_client_sync)
    router = router_modul.MultiLLMRouter(enable_infinite_chat=False)
    assert router._call_openrouter(model="m", prompt="p", max_tokens=5, temperature=0) == "x"
    assert rollen == ["brain_planning"]


# ── Konfiguration ───────────────────────────────────────────────────────


def test_rolle_steht_gleich_in_beiden_konfigurationen() -> None:
    gehirn = yaml.safe_load((BRAIN / "llm_config.yml").read_text(encoding="utf-8"))["roles"]
    zentral = yaml.safe_load((BRAIN.parents[1] / "llm_config.yml.example").read_text(encoding="utf-8"))["roles"]
    assert gehirn["brain_denken"] == zentral["brain_denken"]
    assert gehirn["brain_denken"]["model"] == "openfang:brain-denken"
    assert gehirn["brain_planning"]["model"] == "openfang:brain-planner"

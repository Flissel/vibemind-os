"""Budget-Huelle des Rowboat-Shims (deploy/headless/shim/rowboat_shim_huelle.py) mit Attrappen."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy" / "headless" / "shim"))

import rowboat_shim_huelle as huelle  # noqa: E402


class ShimError(RuntimeError):
    pass


class FalscherShim:
    ShimError = ShimError

    def __init__(self, fehler: Exception | None = None) -> None:
        self.aufrufe: list[dict] = []
        self.fehler = fehler
        self.run_claude = self._run

    def _run(self, **kwargs):
        self.aufrufe.append(kwargs)
        if self.fehler is not None:
            raise self.fehler
        return "antwort"


class FalschesBudget:
    def __init__(self, erlaubt: bool = True, wirft: bool = False, grund: str = "tagesgrenze") -> None:
        self.erlaubt, self.wirft, self.grund = erlaubt, wirft, grund
        self.log: list[tuple] = []

    def pruefen(self, agent):
        self.log.append(("pruefen", agent))
        if self.wirft:
            raise OSError("zaehldatei weg")
        return SimpleNamespace(erlaubt=self.erlaubt, grund="ok" if self.erlaubt else self.grund)

    def buchen(self, agent, ergebnis, dauer_s):
        self.log.append(("buchen", agent, ergebnis))


def test_abgelehnt_ruft_claude_nicht_auf():
    shim, budget = FalscherShim(), FalschesBudget(erlaubt=False)
    huelle.umhuellen(shim, budget, "rowboat-server")
    with pytest.raises(ShimError, match="tagesgrenze"):
        shim.run_claude(prompt="x")
    assert shim.aufrufe == []
    assert budget.log == [("pruefen", "rowboat-server")]


def test_waechterfehler_lehnt_ab():
    shim, budget = FalscherShim(), FalschesBudget(wirft=True)
    huelle.umhuellen(shim, budget, "rowboat-server")
    with pytest.raises(ShimError, match="waechter_fehler"):
        shim.run_claude(prompt="x")
    assert shim.aufrufe == []


def test_erfolg_bucht_danach_ok():
    shim, budget = FalscherShim(), FalschesBudget()
    huelle.umhuellen(shim, budget, "rowboat-server")
    assert shim.run_claude(prompt="x") == "antwort"
    assert shim.aufrufe == [{"prompt": "x"}]
    assert budget.log == [("pruefen", "rowboat-server"), ("buchen", "rowboat-server", "ok")]


def test_cli_fehler_bucht_fehler():
    shim, budget = FalscherShim(fehler=ShimError("cli tot")), FalschesBudget()
    huelle.umhuellen(shim, budget, "rowboat-server")
    with pytest.raises(ShimError, match="cli tot"):
        shim.run_claude(prompt="x")
    assert budget.log[-1] == ("buchen", "rowboat-server", "fehler")


def test_agent_und_budgetordner_pflicht(tmp_path):
    with pytest.raises(SystemExit):
        huelle.agent_aus_umgebung({})
    assert huelle.agent_aus_umgebung({"VIBEMIND_AGENT": " rowboat-server "}) == "rowboat-server"
    with pytest.raises(SystemExit):
        huelle.budget_ordner_pruefen({})
    with pytest.raises(SystemExit):
        huelle.budget_ordner_pruefen({"VIBEMIND_BUDGET_DIR": str(tmp_path / "fehlt")})
    assert huelle.budget_ordner_pruefen({"VIBEMIND_BUDGET_DIR": str(tmp_path)}) == tmp_path


def test_textaufruf_wird_fehler():
    def parse_reply(text, tools):
        return SimpleNamespace(calls=[], errors=[], text=text)

    werkzeuge = SimpleNamespace(parse_reply=parse_reply,
                                ParsedReply=lambda **kw: SimpleNamespace(**kw))
    huelle.textaufrufe_abfangen(werkzeuge)
    tools = [{"function": {"name": "workspace-readFile"}}]
    ergebnis = werkzeuge.parse_reply('Ich rufe workspace-readFile({"path": "a.md"}) auf', tools)
    assert ergebnis.errors and ergebnis.had_block
    normal = werkzeuge.parse_reply("Hier ist die Antwort.", tools)
    assert normal.errors == [] and normal.text == "Hier ist die Antwort."


# --- Feste Antwort bei Tagesgrenze (VIBEMIND_BUDGET_ANTWORT, opt-in; Event-Management 15.11.) ---

def test_tagesgrenze_mit_antwort_gibt_text_zurueck():
    shim, budget = FalscherShim(), FalschesBudget(erlaubt=False)
    huelle.umhuellen(shim, budget, "event-management-claw", antwort="Fuer heute ist Schluss.")
    payload = shim.run_claude(prompt="x")
    assert payload["result"] == "Fuer heute ist Schluss."
    assert payload["is_error"] is False
    assert payload["stop_reason"] == "end_turn" and payload["usage"] == {}
    assert shim.aufrufe == []
    assert budget.log == [("pruefen", "event-management-claw")]  # nichts gebucht


@pytest.mark.parametrize("grund", ["waechter_fehler", "not_aus", "unbekannter_agent"])
def test_andere_gruende_bleiben_fehler_trotz_antwort(grund):
    shim, budget = FalscherShim(), FalschesBudget(erlaubt=False, grund=grund)
    huelle.umhuellen(shim, budget, "event-management-claw", antwort="Fuer heute ist Schluss.")
    with pytest.raises(ShimError, match=grund):
        shim.run_claude(prompt="x")
    assert shim.aufrufe == []


def test_waechter_ausnahme_bleibt_fehler_trotz_antwort():
    shim, budget = FalscherShim(), FalschesBudget(wirft=True)
    huelle.umhuellen(shim, budget, "event-management-claw", antwort="Fuer heute ist Schluss.")
    with pytest.raises(ShimError, match="waechter_fehler"):
        shim.run_claude(prompt="x")


@pytest.mark.parametrize("umgebung, erwartet", [
    ({}, None),
    ({"VIBEMIND_BUDGET_ANTWORT": "  "}, None),
    ({"VIBEMIND_BUDGET_ANTWORT": " Hallo "}, "Hallo"),
])
def test_antwort_aus_umgebung(umgebung, erwartet):
    assert huelle.antwort_aus_umgebung(umgebung) == erwartet


def test_antwort_leer_gilt_als_ungesetzt():
    shim, budget = FalscherShim(), FalschesBudget(erlaubt=False)
    huelle.umhuellen(shim, budget, "rowboat-server",
                     antwort=huelle.antwort_aus_umgebung({"VIBEMIND_BUDGET_ANTWORT": ""}))
    with pytest.raises(ShimError, match="tagesgrenze"):
        shim.run_claude(prompt="x")

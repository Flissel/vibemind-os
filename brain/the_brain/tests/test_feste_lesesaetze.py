"""Feste Lesesaetze fuer Desktop, The Brain und Rowboat (core/feste_lesesaetze.py).

Ende-zu-Ende am echten Router (/api/multihop/execute) mit Attrappen fuer Planer und Ausfuehrer;
der Weg ueber den echten McpExecutor fuer desktop_fenster steht in test_desktop_schleife.py.
Alle Fenstertitel und Gedanken sind erfunden.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import thebrain_gedanken, desktop_fenster, rowboat_spaces  # noqa: E402
from core import verstehen as v  # noqa: E402
from web.routers.introspection import router  # noqa: E402

SCHWACH = {"level": "medium", "score": 0.44, "method": "qwen-cosine", "reason": "x"}
ALLE = [s for s in v.SPACES if s != "ideas"]


# ── Welcher Satz geht wohin ───────────────────────────────────────────────

@pytest.mark.parametrize("satz,space,faehigkeit", [
    ("Welche Fenster sind offen?", "desktop", "desktop_fenster"),
    ("Welche Fenster sind offen", None, "desktop_fenster"),
    ("Welche Fenster sind gerade auf dem Desktop offen?", "desktop", "desktop_fenster"),
    ("Welche Fenster sind gerade auf dem Desktop offen?", "video", "desktop_fenster"),
    ("Was für Fenster habe ich geöffnet?", "desktop", "desktop_fenster"),
    ("Woran denkt das Gehirn gerade?", "thebrain", "thebrain_gedanken"),
    ("Woran denkt das Gehirn gerade?", "sales", "thebrain_gedanken"),
    ("woran denkt the brain", None, "thebrain_gedanken"),
    ("Welche Agenten gibt es in Rowboat?", "roarboot", "rowboat_spaces"),
    ("Welche Agenten gibt es in Rowboat?", "thebrain", "rowboat_spaces"),
    ("Welche Spaces stehen in roarboot?", None, "rowboat_spaces"),
    ("Welche Agenten gibt es?", "roarboot", "rowboat_spaces"),
])
def test_satz_bindet_den_festen_weg(satz, space, faehigkeit) -> None:
    assert v.feste_lesefrage(satz, space) == {"capability": faehigkeit}


@pytest.mark.parametrize("satz,space", [
    ("Welche Fenster sind offen?", "video"),          # ohne "Desktop" nur im eigenen Reiter
    ("Welche Agenten gibt es?", "desktop"),           # ohne "Rowboat" nur im eigenen Reiter
    ("Welche Agenten gibt es?", None),
    ("Woran denkt das Gehirn gerade?", "ideas"),      # im Ideen-Bereich entscheidet die Ideen-Abkuerzung
    ("Schließ alle Fenster", "desktop"),              # kein Lesesatz
    ("Welche Fenster sind offen und schließ sie", "desktop"),
    ("Welche Teams laufen gerade in der Agent Farm?", "agentfarm"),
])
def test_kein_fester_weg(satz, space) -> None:
    assert v.feste_lesefrage(satz, space) is None


def test_the_brain_hat_einen_weg_agent_farm_nicht() -> None:
    assert "thebrain" not in v.OHNE_WEG and "agentfarm" in v.OHNE_WEG
    for space, satz in (("desktop", "Welche Fenster sind offen?"), ("roarboot", "Welche Agenten gibt es in Rowboat?"),
                        ("thebrain", "Woran denkt das Gehirn gerade?")):
        assert v.SPACES[space].beispiele == (satz,)
        assert v.feste_lesefrage(satz, space)  # das Beispiel laeuft selbst
    assert v.faehigkeit_space("thebrain_gedanken") == "thebrain"
    assert v.faehigkeit_space("rowboat_spaces") == "roarboot"
    assert v.faehigkeit_space("desktop_fenster") == "desktop"


def test_rowboat_heisst_auch_roarboot() -> None:
    assert v.space_aus_kontext({"space": "rowboat"}) == "roarboot"
    assert v.genannter_space("Was steht in roarboot?") == "roarboot"
    from core.space_contract import CANONICAL_ALIASES
    assert CANONICAL_ALIASES["roarboot"] == "rowboat"


def test_faehigkeiten_stehen_im_katalog_und_im_verzeichnis() -> None:
    import yaml
    wurzel = Path(__file__).resolve().parents[3]
    katalog = {e["capability"]: e for e in yaml.safe_load(
        (wurzel / "brain/the_brain/data/capabilities.yaml").read_text(encoding="utf-8"))}
    assert katalog["desktop_fenster"]["execution_target"] == "direct:core.desktop_fenster:fenster_lesen"
    assert katalog["thebrain_gedanken"]["execution_target"] == "brain:GET:/api/cortex/thoughts"
    assert katalog["rowboat_spaces"]["execution_target"] == "direct:core.rowboat_spaces:spaces_lesen"
    register = yaml.safe_load((wurzel / "config/space_agent_registry.yml").read_text(encoding="utf-8"))["spaces"]
    assert register["desktop"]["events"]["desktop.fenster"]["tool"] == "desktop_fenster"
    assert "desktop_fenster" in register["desktop"]["mcp_tools"]["spaces-desktop"]
    assert register["thebrain"]["events"]["thebrain.gedanken"]["tool"] == "brain:GET:/api/cortex/thoughts"
    assert register["rowboat"]["events"]["rowboat.spaces"]["tool"] == "direct:core.rowboat_spaces:spaces_lesen"


# ── Saetze ────────────────────────────────────────────────────────────────

def test_fenster_satz() -> None:
    liste = {"fenster": [{"gesperrt": False, "programm": "notepad", "titel": "Einkauf - Editor"},
                         {"gesperrt": True},
                         {"gesperrt": False, "programm": "explorer", "titel": "x" * 80}],
             "anzahl": 3}
    satz = desktop_fenster.satz(liste)
    assert satz == ("3 Fenster offen: notepad „Einkauf - Editor“, ein gesperrtes Fenster, "
                    f"explorer „{'x' * 49}…“.")
    viele = {"fenster": [{"gesperrt": False, "programm": "notepad", "titel": f"F{i}"} for i in range(40)],
             "anzahl": 52}
    assert desktop_fenster.satz(viele).startswith("52 Fenster offen: notepad „F0“")
    assert desktop_fenster.satz(viele).endswith("notepad „F7“ (und 44 weitere).")
    assert desktop_fenster.satz({"fenster": [], "anzahl": 0}) == "Es ist kein Fenster offen."
    assert desktop_fenster.satz({"fenster": [{"gesperrt": False, "programm": "notepad", "titel": "A"}],
                                 "anzahl": 1}) == "Ein Fenster offen: notepad „A“."
    assert desktop_fenster.satz({"anzahl": 3}) is None and desktop_fenster.satz("roh") is None


def test_fenster_satz_liegt_nur_fluechtig_und_einmal() -> None:
    kennung = desktop_fenster._merken("Ein Fenster offen: notepad „Geheim“.")
    ergebnis = {"success": True, "anzahl": 1, "gesperrt": 0, "satz_id": kennung}
    assert desktop_fenster.protokoll(ergebnis) == (
        "1 Fenster offen, davon 0 gesperrt (Titel nur in der Antwort an die App).")
    assert desktop_fenster.antwort(ergebnis) == "Ein Fenster offen: notepad „Geheim“."
    assert desktop_fenster.antwort(ergebnis) is None  # einmal abgeholt, dann weg
    assert desktop_fenster.antwort({"success": False, "error": "x"}) is None


def test_fenster_satz_verfaellt(monkeypatch) -> None:
    kennung = desktop_fenster._merken("alt")
    monkeypatch.setattr(desktop_fenster, "SATZ_HALTBAR_S", -1)
    assert desktop_fenster.antwort({"success": True, "satz_id": kennung}) is None


def test_gedanken_satz() -> None:
    gedanken = [{"timestamp": t, "content": f"Gedanke {t}"} for t in (1.0, 5.0, 3.0, 4.0, 2.0)]
    assert thebrain_gedanken.antwort({"thoughts": gedanken}) == (
        "Die letzten Gedanken des Gehirns: „Gedanke 5.0“; „Gedanke 4.0“; „Gedanke 3.0“.")
    lang = {"thoughts": [{"timestamp": 1, "content": "a\nb " + "z" * 300}]}
    satz = thebrain_gedanken.antwort(lang)
    assert satz.startswith("Zuletzt dachte das Gehirn: „a b z") and satz.endswith("…“.")
    assert len(satz) < 170
    assert thebrain_gedanken.antwort({"thoughts": [], "stats": {}, "thinking": False}) == (
        "Das Gehirn denkt gerade nichts im Hintergrund; die Denkschleife ist aus.")
    assert thebrain_gedanken.antwort({"thoughts": [], "stats": {"running": True}}).startswith(
        "Das Gehirn denkt im Hintergrund, hat aber")
    assert thebrain_gedanken.antwort({"raw": "<html>"}) is None and thebrain_gedanken.antwort(None) is None


def _karte(ordner: Path, space: str, titel: str, stand: str) -> None:
    (ordner / f"{space}.md").write_text(
        f"---\nspace: {space}\nagent: brain-{space}\ntitel: {titel}\nrolle: x\nstand: {stand}\n---\n<!-- x -->\n",
        encoding="utf-8")


@pytest.fixture
def karten(tmp_path, monkeypatch):
    _karte(tmp_path, "ideas", "Ideen", "läuft")
    _karte(tmp_path, "desktop", "Desktop", "läuft")
    _karte(tmp_path, "sales", "Sales", "noch nicht angeschlossen")
    (tmp_path / "README.md").write_text("---\ntitel: Liesmich\n---\n", encoding="utf-8")
    (tmp_path / "kaputt.md").write_text("kein Kopf", encoding="utf-8")
    monkeypatch.setenv("VIBEMIND_SPACES_KARTEN", str(tmp_path))
    return tmp_path


def test_rowboat_satz_aus_den_karten(karten) -> None:
    ergebnis = rowboat_spaces.spaces_lesen({"value": ""})
    assert ergebnis["anzahl"] == 3
    assert rowboat_spaces.antwort(ergebnis) == (
        "In Rowboat stehen 3 VibeMind-Spaces: Desktop, Ideen, Sales. Läuft heute: Desktop, Ideen.")


def test_rowboat_ohne_karten(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VIBEMIND_SPACES_KARTEN", str(tmp_path / "fehlt"))
    assert rowboat_spaces.spaces_lesen() == {"success": False, "error": "karten_fehlen"}
    assert rowboat_spaces.antwort({"success": False}) is None


# ── Ende-zu-Ende am Router ────────────────────────────────────────────────

class _Recorder:
    def __init__(self):
        self.finals = []

    def attach_final(self, plan_id, text, *a, **k):
        self.finals.append(text)

    def record_lite(self, *a, **k):
        pass

    def append_stage(self, *a, **k):
        pass


class _Ausfuehrer:
    def __init__(self, ergebnis):
        self.recorder = _Recorder()
        self.plaene = []
        self.ergebnis = ergebnis

    def execute(self, plan, **kw):
        self.plaene.append(plan)
        ergebnis = self.ergebnis() if callable(self.ergebnis) else self.ergebnis
        return {"ok": True, "plan": plan.to_dict(), "executed": {"s1": {"ok": True, "result": ergebnis}},
                "state": {}, "elapsed_s": 0.01, "replans": 0}


class _Planer:
    aufrufe = 0

    async def aplan(self, intent, context=None):
        self.aufrufe += 1
        raise AssertionError("kein Planer fuer feste Lesesaetze")


class _Klassierer:
    def classify(self, intent):
        return dict(SCHWACH)


def _client(monkeypatch, ausfuehrer):
    import core.difficulty_router as dr

    monkeypatch.setattr(dr, "get_router", lambda: _Klassierer())
    monkeypatch.setenv("SOM_NOPLAN_FALLBACK", "0")
    monkeypatch.setenv("MULTI_VERB_ROUTING", "0")
    app = FastAPI()
    app.include_router(router)
    app.state.plan_executor = ausfuehrer
    app.state.multihop_planner = _Planer()
    app.state.final_synthesizer = None
    return TestClient(app)


def _frage(monkeypatch, ausfuehrer, satz, space):
    return _client(monkeypatch, ausfuehrer).post(
        "/api/multihop/execute", json={"intent": satz, "context": {"space": space}}).json()


def test_fenster_titel_nur_in_der_antwort_nie_im_verlauf(monkeypatch) -> None:
    satz = "2 Fenster offen: notepad „Brief an Erika - Editor“, ein gesperrtes Fenster."
    ausfuehrer = _Ausfuehrer(lambda: {"success": True, "anzahl": 2, "gesperrt": 1,
                                      "satz_id": desktop_fenster._merken(satz)})
    body = _frage(monkeypatch, ausfuehrer, "Welche Fenster sind offen?", "desktop")
    assert [h.capability for h in ausfuehrer.plaene[0].hops] == ["desktop_fenster"]
    assert (body["ok"], body["final_text"]) == (True, satz)
    assert "_protokoll" not in body
    assert ausfuehrer.recorder.finals == ["2 Fenster offen, davon 1 gesperrt (Titel nur in der Antwort an die App)."]
    assert "Erika" not in repr(body["executed"])


def test_fenster_ohne_dienst_ist_kein_haken(monkeypatch) -> None:
    ausfuehrer = _Ausfuehrer({"success": False, "error": "dienst_fehlt"})
    body = _frage(monkeypatch, ausfuehrer, "Welche Fenster sind offen?", "desktop")
    assert body["ok"] is False and body["final_text"] == v.FEHLERSATZ


def test_gedanken_ende_zu_ende(monkeypatch) -> None:
    ausfuehrer = _Ausfuehrer({"thoughts": [{"timestamp": 2, "content": "Wie hängen Bubbles zusammen?"}],
                              "stats": {"running": True}, "thinking": True})
    body = _frage(monkeypatch, ausfuehrer, "Woran denkt das Gehirn gerade?", "thebrain")
    assert body["final_text"] == "Zuletzt dachte das Gehirn: „Wie hängen Bubbles zusammen?“."
    assert ausfuehrer.recorder.finals == [body["final_text"]]  # kein Bildschirminhalt, kein eigener Satz


def test_rowboat_ende_zu_ende(monkeypatch, karten) -> None:
    ausfuehrer = _Ausfuehrer(lambda: rowboat_spaces.spaces_lesen({"value": ""}))
    body = _frage(monkeypatch, ausfuehrer, "Welche Agenten gibt es in Rowboat?", "roarboot")
    assert body["final_text"].startswith("In Rowboat stehen 3 VibeMind-Spaces: ")
    assert body["ok"] is True


def test_agent_farm_bleibt_ehrlich(monkeypatch) -> None:
    ausfuehrer = _Ausfuehrer(None)
    body = _frage(monkeypatch, ausfuehrer, "Welche Teams laufen gerade in der Agent Farm?", "agentfarm")
    assert ausfuehrer.plaene == [] and body["weg"] == "kein-weg"

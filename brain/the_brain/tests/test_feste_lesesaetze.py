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
    # Lauf 16 (48): ein Zusatz in Klammern am Satzende stoert den festen Satz nicht.
    ("Woran denkt das Gehirn gerade? (Skript 1)", "thebrain", "thebrain_gedanken"),
    ("Woran denkt das Gehirn gerade (Test) [2]", "thebrain", "thebrain_gedanken"),
    ("Welche Fenster sind offen? [Probe]", "desktop", "desktop_fenster"),
])
def test_satz_bindet_den_festen_weg(satz, space, faehigkeit) -> None:
    assert v.feste_lesefrage(satz, space) == {"capability": faehigkeit}


@pytest.mark.parametrize("satz,space", [
    ("Welche Fenster sind offen?", "video"),          # ohne "Desktop" nur im eigenen Reiter
    ("Welche Agenten gibt es?", "desktop"),           # ohne "Rowboat" nur im eigenen Reiter
    ("Welche Agenten gibt es?", None),
    ("Welche Fenster sind offen?", "ideas"),          # Kurzform: im Ideen-Bereich entscheidet die Ideen-Abkuerzung
    ("Schließ alle Fenster", "desktop"),              # kein Lesesatz
    ("Welche Fenster sind offen und schließ sie", "desktop"),
    ("Welche Teams laufen gerade in der Agent Farm?", "agentfarm"),
    ("Woran denkt das Gehirn gerade? (Skript 1) und warum", "thebrain"),   # nur ein Zusatz am Ende zaehlt nicht
    ("Woran denkt das Gehirn gerade? (Skript 1)", "ideas"),
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
    from core import fluechtige_saetze
    monkeypatch.setattr(fluechtige_saetze, "HALTBAR_S", -1)
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
    ausfuehrer = _Ausfuehrer({"success": False, "error": "dienst_aus"})
    body = _frage(monkeypatch, ausfuehrer, "Welche Fenster sind offen?", "desktop")
    assert body["ok"] is False
    assert body["final_text"] == ("Die Fensterliste kann ich gerade nicht lesen: der Werkzeug-Dienst des Desktop "
                                  "antwortet nicht.")


@pytest.mark.parametrize("meldung,art", [
    ("OpenFang MCP JSON-RPC error: Tool not permitted for caller agent: mcp_spaces_desktop_desktop_fenster",
     "nicht_freigegeben"),
    ("OpenFang runtime approval was not approved", "freigabe_ausstehend"),
    ("Connection refused", "dienst_aus"),
    ("fenster_unlesbar: die Fensterliste ist nicht lesbar", "fenster_unlesbar"),
    ("nicht_gebunden: desktop_fenster ist nicht gebunden", "nicht_gebunden"),
    ("irgendwas anderes", "werkzeug_fehler"),
])
def test_fenster_fehler_wird_eingeordnet(meldung, art) -> None:
    assert desktop_fenster.fehlerart(meldung) == art


def test_fenster_ohne_freigabe_sagt_genau_das(monkeypatch) -> None:
    ausfuehrer = _Ausfuehrer({"success": False, "error": "nicht_freigegeben"})
    body = _frage(monkeypatch, ausfuehrer, "Welche Fenster sind offen?", "desktop")
    assert body["final_text"].startswith("Die Fensterliste kann ich noch nicht lesen: OpenFang hat dem Desktop-Agenten")
    assert desktop_fenster.fehlersatz({"success": False, "error": "werkzeug_fehler"}) == desktop_fenster.ALLGEMEIN


def test_rowboat_ohne_karten_sagt_genau_das(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VIBEMIND_SPACES_KARTEN", str(tmp_path / "fehlt"))
    ausfuehrer = _Ausfuehrer(lambda: rowboat_spaces.spaces_lesen({"value": ""}))
    body = _frage(monkeypatch, ausfuehrer, "Welche Agenten gibt es in Rowboat?", "roarboot")
    assert body["ok"] is False
    assert body["final_text"] == ("Welche VibeMind-Spaces in Rowboat stehen, kann ich noch nicht sagen: die "
                                  "Lese-Karten (spaces/rowboat/vibemind-spaces) fehlen auf diesem Stand.")


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


# ── Augen: "Bestätige: Fenster X ist vorn und zeigt Y" ─────────────────────

@pytest.mark.parametrize("satz,erwartet", [
    ("Bestätige: Fenster Editor ist vorn und zeigt Speichern", ("Editor", "Speichern")),
    ("bestaetige fenster „Unbenannt - Editor“ ist vorne und zeigt „Datei“.", ("Unbenannt - Editor", "Datei")),
    ("Bitte bestätige, dass das Fenster Rechner ist vorn", ("Rechner", None)),
    ("Fenster Editor ist vorn und zeigt Speichern", ("Editor", "Speichern")),
    ("Prüfe: Fenster Mail ist vorn", ("Mail", None)),
])
def test_bestaetigungssatz(satz, erwartet) -> None:
    from core.desktop_augen import auftrag_aus_satz
    assert auftrag_aus_satz(satz) == erwartet


@pytest.mark.parametrize("satz", ["Bestätige den Termin", "Fenster schließen", "Welche Fenster sind offen?",
                                  "Bestätige: Fenster ist vorn", None])
def test_kein_bestaetigungssatz(satz) -> None:
    from core.desktop_augen import auftrag_aus_satz
    assert auftrag_aus_satz(satz) is None


@pytest.mark.parametrize("space", ["desktop", "video", "sales", None])
def test_bestaetigung_aus_jedem_reiter(space) -> None:
    satz = "Bestätige: Fenster Editor ist vorn und zeigt Speichern"
    assert v.feste_lesefrage(satz, space) == {"capability": "desktop_bestaetigen"}


def test_bestaetigung_auftrag_aus_feldern_oder_absicht() -> None:
    from core.desktop_augen import auftrag
    assert auftrag({"fenster": "Editor", "zeigt": "Speichern", "programm": "notepad"}) == {
        "fenstertitel_enthaelt": "Editor", "element_enthaelt": "Speichern", "programm": "^notepad$"}
    assert auftrag({"value": "", "_intent": "Bestätige: Fenster Editor ist vorn"}) == {
        "fenstertitel_enthaelt": "Editor"}
    assert auftrag("Fenster Editor ist vorn und zeigt Datei") == {
        "fenstertitel_enthaelt": "Editor", "element_enthaelt": "Datei"}
    assert auftrag({"value": "irgendwas"}) is None


def test_bestaetigung_ende_zu_ende_titel_nie_im_verlauf(monkeypatch) -> None:
    from core import fluechtige_saetze
    satz = "Bestätigt. Zu sehen: Fenster „Brief an Erika - Editor“ in Programm notepad."
    ausfuehrer = _Ausfuehrer(lambda: {"success": True, "bestaetigt": True, "signal": {"gesperrt": False},
                                      "grund": "x", "satz_id": fluechtige_saetze.merken(satz)})
    body = _frage(monkeypatch, ausfuehrer, "Bestätige: Fenster Brief ist vorn und zeigt Speichern", "sales")
    assert [h.capability for h in ausfuehrer.plaene[0].hops] == ["desktop_bestaetigen"]
    assert (body["ok"], body["final_text"]) == (True, satz)
    assert ausfuehrer.recorder.finals == ["Bestätigung am Bildschirm: ja (Titel nur in der Antwort an die App)."]


def test_bestaetigung_programm_ist_woertlich_kein_muster() -> None:
    import re
    from core.desktop_augen import auftrag
    muster = auftrag({"fenster": "A", "programm": "(a+)+$"})["programm"]
    assert muster == "^" + re.escape("(a+)+$") + "$"
    assert re.search(muster, "a" * 40 + "!") is None  # kein Rueckverfolgen
    assert re.search(auftrag({"fenster": "A", "programm": "MSEDGE.EXE"})["programm"], "msedge")


# ── Lauf 17: Namensform aus jedem Reiter, Kontextform der App ─────────────

# Kontextform der App im Ideen-Reiter: ohne offene Bubble kommt die Bubble-Liste mit
# (sonst gilt der Reiter als unbekannt, core/verstehen.space_aus_kontext).
IDEEN_OHNE_BUBBLE = {"space": "ideas", "bubbles": [{"id": "b0b1e001", "title": "Stadtfest"}]}
IDEEN_MIT_BUBBLE = {"space": "ideas", "bubble": {"id": "b0b1e001", "title": "Stadtfest"},
                    "cards": [{"id": "a0000001", "title": "Sponsoren"}]}


def _frage_kontext(monkeypatch, ausfuehrer, satz, kontext):
    return _client(monkeypatch, ausfuehrer).post(
        "/api/multihop/execute", json={"intent": satz, "context": dict(kontext)}).json()


@pytest.mark.parametrize("kontext", [IDEEN_OHNE_BUBBLE, IDEEN_MIT_BUBBLE], ids=["ohne-bubble", "mit-bubble"])
def test_fenster_mit_namen_aus_ideen_ist_fester_lesesatz(monkeypatch, kontext) -> None:
    satz = "2 Fenster offen: notepad „Beispiel - Editor“, ein gesperrtes Fenster."
    ausfuehrer = _Ausfuehrer(lambda: {"success": True, "anzahl": 2, "gesperrt": 1,
                                      "satz_id": desktop_fenster._merken(satz)})
    body = _frage_kontext(monkeypatch, ausfuehrer, "Welche Fenster sind gerade auf dem Desktop offen?", kontext)
    assert body["ok"] is True and body["final_text"] == satz
    assert [h.capability for h in ausfuehrer.plaene[0].hops] == ["desktop_fenster"]
    assert "festen Lesesatz" in ausfuehrer.plaene[0].hops[0].description


@pytest.mark.parametrize("satz,faehigkeit", [
    ("Welche Fenster sind gerade auf dem Desktop offen?", "desktop_fenster"),
    ("Woran denkt das Gehirn gerade?", "thebrain_gedanken"),
    ("Welche Agenten gibt es in Rowboat?", "rowboat_spaces"),
    ("Bestätige: Fenster Editor ist vorn", "desktop_bestaetigen"),
])
def test_namensformen_gelten_auch_in_ideen(satz, faehigkeit) -> None:
    assert v.feste_lesefrage(satz, "ideas") == {"capability": faehigkeit}


@pytest.mark.parametrize("satz", ["Welche Fenster sind offen?", "Welche Agenten gibt es?"])
def test_kurzformen_bleiben_im_eigenen_reiter(satz) -> None:
    assert v.feste_lesefrage(satz, "ideas") is None


@pytest.mark.parametrize("kontext", [IDEEN_OHNE_BUBBLE, IDEEN_MIT_BUBBLE, {"space": "roarboot"}],
                         ids=["ideen", "ideen-bubble", "rowboat"])
def test_rowboat_ohne_karten_kein_plan_kein_sprung(monkeypatch, tmp_path, kontext) -> None:
    monkeypatch.setenv("VIBEMIND_SPACES_KARTEN", str(tmp_path / "fehlt"))
    ausfuehrer = _Ausfuehrer(lambda: pytest.fail("ohne Karten kein Plan"))
    body = _frage_kontext(monkeypatch, ausfuehrer, "Welche Agenten gibt es in Rowboat?", kontext)
    assert ausfuehrer.plaene == []  # kein Plan -> kein plan_started -> kein Sprung
    assert body["ok"] is False and body["executed"] == {}
    assert body["final_text"] == ("Welche VibeMind-Spaces in Rowboat stehen, kann ich noch nicht sagen: die "
                                  "Lese-Karten (spaces/rowboat/vibemind-spaces) fehlen auf diesem Stand.")
    # bleibt im Reiter der App (fuer "ideas" nennt das Gehirn keinen Space, wie space_aus_kontext)
    assert body.get("space") in (None, kontext["space"])
    assert body.get("space") != "roarboot" or kontext["space"] == "roarboot"


def test_rowboat_mit_karten_aus_ideen(monkeypatch, karten) -> None:
    ausfuehrer = _Ausfuehrer(lambda: rowboat_spaces.spaces_lesen({"value": ""}))
    body = _frage_kontext(monkeypatch, ausfuehrer, "Welche Agenten gibt es in Rowboat?", IDEEN_MIT_BUBBLE)
    assert body["ok"] is True
    assert body["final_text"].startswith("In Rowboat stehen 3 VibeMind-Spaces")


def test_abkuerzung_auf_lesefaehigkeit_meldet_bei_erfolg_nie_fehlgeschlagen(monkeypatch) -> None:
    """Ein Ein-Schritt-Plan auf desktop_fenster ueber die alte Abkuerzung bekommt dieselbe Antwort."""
    import web.routers.introspection as intro
    from core.plan_schema import HopSpec, Plan

    def _abkuerzung(state, intent):
        return Plan(plan_id="shortcut_1", intent=intent, rationale="test",
                    hops=[HopSpec(step_id="s1", description="desktop_fenster via shortcut",
                                  capability="desktop_fenster", arg_template="")],
                    final_synthesis_prompt="", estimated_cost_usd=0.0)

    monkeypatch.setattr(intro, "_try_capability_shortcut", _abkuerzung)
    satz = "Ein Fenster offen: notepad „Beispiel - Editor“."
    ausfuehrer = _Ausfuehrer(lambda: {"success": True, "anzahl": 1, "gesperrt": 0,
                                      "satz_id": desktop_fenster._merken(satz)})
    body = _frage_kontext(monkeypatch, ausfuehrer, "Zeig mir bitte die Fensterliste", {"space": "video"})
    assert [h.description for h in ausfuehrer.plaene[0].hops] == ["desktop_fenster via shortcut"]
    assert body["ok"] is True and body["final_text"] == satz
    assert not body.get("nicht_lesbar")
    assert ausfuehrer.recorder.finals == ["1 Fenster offen, davon 0 gesperrt (Titel nur in der Antwort an die App)."]
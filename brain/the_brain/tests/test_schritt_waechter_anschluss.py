"""Anschluss des Schritt-Waechters an PlanExecutor._exec_hop (Schatten).

Jeder Lauf bekommt einen eigenen Recorder und eine eigene Tagebuch-Queue unter
tmp_path; der Waechter schreibt nur in eine Datei unter tmp_path. Nichts
landet in data/ der echten Installation, kein Netz.
"""

import json

from core import schritt_waechter
from core.decision_outcome import is_test_run
from core.plan_executor import PlanExecutor, PlanRecorder
from core.plan_schema import HopSpec, Plan
from core.schritt_waechter import Schatten

ABSICHT = "Lege die Bubble X an"            # is_test_run: falsch
TEST_ABSICHT = "EXECTEST Bubble X anlegen"  # is_test_run: wahr
VERGLEICHSFELDER = ("ok", "result", "error", "validator_verdict")


class _Roh(dict):
    """Vollstaendige Ausfuehrer-Antwort statt eines Ergebnistexts."""


class _StubExecutor:
    def __init__(self, ergebnis):
        self._ergebnis = ergebnis

    def call_with_arg(self, *args, **kwargs):
        if isinstance(self._ergebnis, _Roh):
            return dict(self._ergebnis)
        return {"ok": True, "result": self._ergebnis, "elapsed_s": 0.0, "target": "stub"}


class _KaputterRouter:
    """Faehigkeits-Nachschlag scheitert: fruehe Rueckgabe in _exec_hop_kern."""

    def get_capability(self, name):
        raise RuntimeError(f"unbekannt: {name}")


class _StubValidator:
    def validate(self, spec, *, intent, arg, raw_result):
        return {"valid": True, "reason": "stub", "on_fail": "report"}


class _KaputterSchatten(Schatten):
    def __init__(self, pfad):
        super().__init__(pfad)
        self.aufrufe = 0

    def schreibe(self, **kwargs):
        self.aufrufe += 1
        raise OSError("Platte voll")


def _hop(step_id, ergebnis_ziel, depends_on=()):
    return HopSpec(step_id=step_id, description=f"Schritt {step_id}",
                   execution_target=f"direct:stub:{ergebnis_ziel}",
                   arg_template="X", depends_on=list(depends_on),
                   validator={"kind": "stub"})


def _plan(plan_id, hops, intent=ABSICHT):
    return Plan(plan_id=plan_id, intent=intent, rationale="", hops=hops)


def _umgebung(tmp_path, monkeypatch, ergebnisse):
    """Stub-Ausfuehrer je Ziel, Tagebuch unter tmp_path, Schalter aus."""
    monkeypatch.delenv("SCHRITT_WAECHTER", raising=False)
    monkeypatch.setenv("MULTIHOP_DIARY_QUEUE", str(tmp_path / "diary.jsonl"))
    monkeypatch.setattr(
        "core.capability_targets.build_executor",
        lambda target: _StubExecutor(ergebnisse[target.rsplit(":", 1)[-1]]),
    )


def _executor(tmp_path, name, schritt_waechter=None, router=None):
    return PlanExecutor(
        capability_router=router,
        validator=_StubValidator(),
        recorder=PlanRecorder(tmp_path / f"history_{name}.jsonl"),
        schritt_waechter=schritt_waechter,
    )


def _felder(ergebnis):
    return {sid: {f: hr[f] for f in VERGLEICHSFELDER}
            for sid, hr in ergebnis["executed"].items()}


def _vergleiche_mit_lauf_ohne_waechter(tmp_path, plan_bauer, ergebnis, router=None,
                                       validator_lief=True):
    """HopResult je Schritt gleich einem Lauf ohne Waechter."""
    ohne = _executor(tmp_path, "ohne", router=router)
    assert ohne._schritt_waechter is None
    referenz = ohne.execute(plan_bauer())
    assert ergebnis["ok"] == referenz["ok"]
    assert _felder(ergebnis) == _felder(referenz)
    if validator_lief:
        assert all(v["validator_verdict"] == {"valid": True, "reason": "stub",
                                              "on_fail": "report"}
                   for v in _felder(referenz).values())


def _zeilen(pfad):
    return {z["step_id"]: z for z in
            (json.loads(t) for t in pfad.read_text(encoding="utf-8").splitlines())}


def test_schalter_aus_schreibt_nichts(tmp_path, monkeypatch):
    _umgebung(tmp_path, monkeypatch, {"a": "Please enter a space first."})
    vorgabe = tmp_path / "vorgabe" / "schritt_waechter.jsonl"
    monkeypatch.setattr(schritt_waechter, "vorgabe_pfad", lambda: vorgabe)

    def plan():
        return _plan("plan_aus", [_hop("s1", "a")])

    pe = PlanExecutor(validator=_StubValidator(),
                      recorder=PlanRecorder(tmp_path / "history.jsonl"))
    assert pe._schritt_waechter is None
    ergebnis = pe.execute(plan())

    assert ergebnis["executed"]["s1"]["ok"] is True
    assert not vorgabe.exists()
    _vergleiche_mit_lauf_ohne_waechter(tmp_path, plan, ergebnis)


def test_schalter_schatten_ohne_angabe_nimmt_vorgabepfad(tmp_path, monkeypatch):
    _umgebung(tmp_path, monkeypatch, {"a": "Please enter a space first."})
    vorgabe = tmp_path / "vorgabe" / "schritt_waechter.jsonl"
    monkeypatch.setattr(schritt_waechter, "vorgabe_pfad", lambda: vorgabe)
    monkeypatch.setenv("SCHRITT_WAECHTER", "schatten")

    def plan():
        return _plan("plan_vorgabe", [_hop("s1", "a")])

    pe = PlanExecutor(validator=_StubValidator(),
                      recorder=PlanRecorder(tmp_path / "history.jsonl"))
    assert isinstance(pe._schritt_waechter, Schatten)
    ergebnis = pe.execute(plan())

    assert list(_zeilen(vorgabe)) == ["s1"]
    monkeypatch.delenv("SCHRITT_WAECHTER")
    _vergleiche_mit_lauf_ohne_waechter(tmp_path, plan, ergebnis)


def test_schatten_schreibt_je_schritt_eine_zeile(tmp_path, monkeypatch):
    _umgebung(tmp_path, monkeypatch, {
        "erstellt": "Bubble 'X' (id=1) created.",
        "rueckfrage": "Please enter a space first.",
    })
    datei = tmp_path / "schatten.jsonl"
    assert not is_test_run(ABSICHT)

    def plan():
        return _plan("plan_zwei", [_hop("s1", "erstellt"), _hop("s2", "rueckfrage")])

    ergebnis = _executor(tmp_path, "mit", Schatten(datei)).execute(plan())

    zeilen = _zeilen(datei)
    assert len(datei.read_text(encoding="utf-8").splitlines()) == 2
    assert set(zeilen) == {"s1", "s2"}
    assert zeilen["s1"]["auffaellig"] is None
    assert zeilen["s1"]["quelle"] == "keine"
    assert zeilen["s2"]["auffaellig"] is True
    assert zeilen["s2"]["quelle"] == "regel"
    for z in zeilen.values():
        assert z["plan_id"] == "plan_zwei"
        assert z["capability"] is None
        assert isinstance(z["dauer_s"], float) and z["dauer_s"] >= 0.0
    _vergleiche_mit_lauf_ohne_waechter(tmp_path, plan, ergebnis)


def test_vorgaenger_auffaellig_faerbt_abhaengigen_schritt(tmp_path, monkeypatch):
    _umgebung(tmp_path, monkeypatch, {
        "fehlschlag": "Failed to create bubble 'X'.",
        "erstellt": "Bubble 'Y' (id=2) created.",
    })
    datei = tmp_path / "schatten.jsonl"

    def plan():
        return _plan("plan_kette", [_hop("s1", "fehlschlag"),
                                    _hop("s2", "erstellt", depends_on=["s1"])])

    ergebnis = _executor(tmp_path, "mit", Schatten(datei)).execute(plan())

    zeilen = _zeilen(datei)
    assert len(datei.read_text(encoding="utf-8").splitlines()) == 2
    assert zeilen["s1"]["auffaellig"] is True
    assert zeilen["s1"]["quelle"] == "regel"
    assert zeilen["s2"]["auffaellig"] is True
    assert zeilen["s2"]["quelle"] == "regel"
    assert zeilen["s2"]["grund"] == "Vorgaenger-Schritt war auffaellig"
    _vergleiche_mit_lauf_ohne_waechter(tmp_path, plan, ergebnis)


def test_waechter_fehler_aendert_hopresult_nicht(tmp_path, monkeypatch):
    _umgebung(tmp_path, monkeypatch, {
        "erstellt": "Bubble 'X' (id=1) created.",
        "rueckfrage": "Please enter a space first.",
    })
    kaputt = _KaputterSchatten(tmp_path / "schatten.jsonl")

    def plan():
        return _plan("plan_kaputt", [_hop("s1", "erstellt"),
                                     _hop("s2", "rueckfrage", depends_on=["s1"])])

    ergebnis = _executor(tmp_path, "mit", kaputt).execute(plan())

    assert kaputt.aufrufe == 2
    assert ergebnis["ok"] is True
    assert not (tmp_path / "schatten.jsonl").exists()
    _vergleiche_mit_lauf_ohne_waechter(tmp_path, plan, ergebnis)


def test_kaputter_waechter_warnt_genau_einmal(tmp_path, monkeypatch, caplog):
    import logging
    _umgebung(tmp_path, monkeypatch, {
        "erstellt": "Bubble 'X' (id=1) created.",
        "zweiter": "Bubble 'Y' (id=2) created.",
    })
    kaputt = _KaputterSchatten(tmp_path / "schatten.jsonl")

    def plan():
        return _plan("plan_warnung", [_hop("s1", "erstellt"),
                                      _hop("s2", "zweiter", depends_on=["s1"])])

    with caplog.at_level(logging.WARNING, logger="core.plan_executor"):
        ergebnis = _executor(tmp_path, "mit", kaputt).execute(plan())

    warnungen = [r for r in caplog.records if "Schritt-Waechter" in r.getMessage()]
    assert kaputt.aufrufe == 2
    assert len(warnungen) == 1
    assert "OSError" in warnungen[0].getMessage()
    _vergleiche_mit_lauf_ohne_waechter(tmp_path, plan, ergebnis)


def test_testlaeufe_werden_nicht_beurteilt(tmp_path, monkeypatch):
    _umgebung(tmp_path, monkeypatch, {"rueckfrage": "Please enter a space first."})
    datei = tmp_path / "schatten.jsonl"
    assert is_test_run(TEST_ABSICHT)

    def plan():
        return _plan("plan_test", [_hop("s1", "rueckfrage")], intent=TEST_ABSICHT)

    ergebnis = _executor(tmp_path, "mit", Schatten(datei)).execute(plan())

    assert ergebnis["executed"]["s1"]["ok"] is True
    assert not datei.exists()
    _vergleiche_mit_lauf_ohne_waechter(tmp_path, plan, ergebnis)


def test_fruehe_rueckgabe_bekommt_eine_zeile(tmp_path, monkeypatch):
    # s1 scheitert schon am Faehigkeits-Nachschlag (fruehes return im Kern).
    # s2 haengt von s1 ab: execute() fuehrt ihn nach dem Fehlschlag nicht aus,
    # sondern setzt ein synthetisches "dependency failed" - ohne _exec_hop,
    # also auch ohne Zeile. Der Fall "abhaengiger Schritt laeuft trotzdem"
    # kommt bei einem Fehlschlag damit nicht vor.
    _umgebung(tmp_path, monkeypatch, {"erstellt": "Bubble 'Y' (id=2) created."})
    datei = tmp_path / "schatten.jsonl"

    def plan():
        return _plan("plan_frueh", [
            HopSpec(step_id="s1", description="Schritt s1", capability="gibt.es.nicht",
                    arg_template="X", validator={"kind": "stub"}),
            _hop("s2", "erstellt", depends_on=["s1"]),
        ])

    ergebnis = _executor(tmp_path, "mit", Schatten(datei),
                         router=_KaputterRouter()).execute(plan())

    assert ergebnis["executed"]["s1"]["ok"] is False
    assert ergebnis["executed"]["s1"]["error"].startswith("capability lookup:")
    assert ergebnis["executed"]["s2"]["error"] == "dependency failed: ['s1']"
    assert len(datei.read_text(encoding="utf-8").splitlines()) == 1
    zeilen = _zeilen(datei)
    assert zeilen["s1"]["auffaellig"] is True
    assert zeilen["s1"]["quelle"] == "regel"
    assert zeilen["s1"]["grund"].startswith("Schritt meldet Fehler: capability lookup:")
    assert zeilen["s1"]["capability"] == "gibt.es.nicht"
    assert zeilen["s1"]["plan_id"] == "plan_frueh"
    _vergleiche_mit_lauf_ohne_waechter(tmp_path, plan, ergebnis,
                                       router=_KaputterRouter(), validator_lief=False)


def test_pending_ergebnis_schreibt_keine_zeile(tmp_path, monkeypatch):
    _umgebung(tmp_path, monkeypatch, {"wartet": _Roh(
        ok=False, pending=True, result={"warte": "Freigabe"},
        invocation_id="inv-1", elapsed_s=0.0, target="stub")})
    datei = tmp_path / "schatten.jsonl"

    def plan():
        return _plan("plan_pending", [_hop("s1", "wartet")])

    ergebnis = _executor(tmp_path, "mit", Schatten(datei)).execute(plan())

    assert ergebnis["pending"] is True
    assert ergebnis["executed"]["s1"]["pending"] is True
    assert ergebnis["executed"]["s1"]["error"] == "runtime authority approval pending"
    assert not datei.exists()
    _vergleiche_mit_lauf_ohne_waechter(tmp_path, plan, ergebnis, validator_lief=False)

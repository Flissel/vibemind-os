from core.nachfasser import Nachfasser, takt_schleife


class Tab:
    def __init__(self, fertige=(), beendete=(), verloren=()):
        self.fertige, self.beendete, self.gesetzt, self.abgelaufen_calls = list(fertige), list(beendete), {}, 0
        self.verloren = set(verloren)  # ids, bei denen ein anderer Lauf schneller war

    def zu_pruefen(self, limit=20): return [f for f in self.fertige if f["id"] not in self.gesetzt]
    def beendete_ohne_meldung(self, limit=20): return [b for b in self.beendete if b["id"] not in self.gesetzt]

    def pruefung_setzen(self, i, p):
        if i in self.verloren:
            return False
        self.gesetzt[i] = p
        return True

    def abgelaufene_markieren(self, jetzt_iso=None): self.abgelaufen_calls += 1; return 0

    def pruefung_ergaenzen(self, i, p):
        self.ergaenzt = getattr(self, "ergaenzt", []) + [(i, p)]


class Router:
    def __init__(self, cfg): self.cfg = cfg
    def get_capability(self, cap): return {"validator": self.cfg}


class Val:
    def __init__(self, verdict): self.verdict, self.calls = verdict, []
    def validate(self, cfg, *, intent, arg, raw_result):
        self.calls.append(raw_result); return self.verdict


class PE:
    def __init__(self): self.calls = []
    def execute(self, plan, *, start_state=None, antwortkanal=None, **kw):
        self.calls.append((plan, start_state, antwortkanal)); return {"ok": True}


def _auftrag(**kw):
    a = {"id": "1", "capability": "rowboat_search", "auftrag": "suche x", "ergebnis": "treffer A",
         "plan_id": "p", "plan_rest": None}
    a.update(kw); return a


def _rest(**kw):
    r = {"plan": {"plan_id": "p", "intent": "i", "hops": [{"step_id": "s2", "description": "d",
         "capability": "x", "execution_target": "supabase:x", "depends_on": []}]},
         "state": {}, "output_var": "treffer", "antwortkanal": {"art": "telegram"}}
    r.update(kw); return r


def test_bestaetigtes_ergebnis_setzt_plan_fort():
    t, pe = Tab(fertige=[_auftrag(plan_rest=_rest())]), PE()
    z = Nachfasser(t, validator=Val({"valid": True, "verified": True, "reason": "ok"}),
                   router=Router({"kind": "rule:non_empty_result", "on_fail": "block"}), plan_executor=pe).runde()
    assert t.gesetzt["1"]["verified"] is True and z["fortgesetzt"] == 1
    assert t.gesetzt["1"]["fortsetzung"] == "laeuft"
    assert t.ergaenzt == [("1", {**t.gesetzt["1"], "fortsetzung": "fertig"})]
    plan, state, kanal = pe.calls[0]
    # M2: Paritaet zum synchronen Pfad (OpenFangExecutor liefert ein dict mit response)
    assert state == {"treffer": {"response": "treffer A"}} and kanal == {"art": "telegram"} and plan.hops[0].step_id == "s2"


def test_fehlender_state_im_rest_gilt_als_leer():
    rest = _rest()
    del rest["state"]
    t, pe = Tab(fertige=[_auftrag(plan_rest=rest)]), PE()
    Nachfasser(t, validator=Val({"valid": True, "verified": True}),
               router=Router({"kind": "rule:x"}), plan_executor=pe).runde()
    assert pe.calls[0][1] == {"treffer": {"response": "treffer A"}}


def test_leeres_ergebnis_bei_block_ist_nicht_bestaetigt_und_setzt_nicht_fort():
    rest = {"plan": {"plan_id": "p", "intent": "i", "hops": []}, "state": {}, "output_var": None}
    t, pe = Tab(fertige=[_auftrag(ergebnis="", plan_rest=rest)]), PE()
    Nachfasser(t, validator=Val({"valid": False, "verified": None, "reason": "leer", "on_fail": "block"}),
               router=Router({"kind": "rule:non_empty_result", "on_fail": "block"}), plan_executor=pe).runde()
    assert t.gesetzt["1"]["verified"] is False and pe.calls == []


def test_ueberlappender_lauf_setzt_nicht_doppelt_fort():
    t, pe = Tab(fertige=[_auftrag(plan_rest=_rest())], verloren=["1"]), PE()
    z = Nachfasser(t, validator=Val({"valid": True, "verified": True}),
                   router=Router({"kind": "rule:x"}), plan_executor=pe).runde()
    assert pe.calls == [] and z["fortgesetzt"] == 0


def test_ohne_pruefer_verified_none():
    t = Tab(fertige=[_auftrag()])
    Nachfasser(t, validator=Val({}), router=Router(None), plan_executor=PE()).runde()
    assert t.gesetzt["1"] == {"verified": None, "reason": "kein Pruefer", "kind": None,
                              "fortsetzung": "keine"}


def test_beendete_werden_zur_meldung_freigegeben_und_ablaufen_laeuft():
    t = Tab(beendete=[{"id": "9", "status": "abgelehnt"}])
    z = Nachfasser(t, validator=Val({}), router=Router(None), plan_executor=PE()).runde()
    assert t.gesetzt["9"] == {"verified": None, "reason": "abgelehnt"} and z["gemeldet"] == 1
    assert t.abgelaufen_calls == 1


def test_fehler_eines_auftrags_stoppt_die_runde_nicht():
    class KaputtVal(Val):
        def validate(self, *a, **k): raise RuntimeError("x")
    t = Tab(fertige=[_auftrag(id="1"), _auftrag(id="2")])
    z = Nachfasser(t, validator=KaputtVal({}), router=Router({"kind": "rule:x"}), plan_executor=PE()).runde()
    assert z["fehler"] == 2


def test_takt_schleife_laeuft_runden_mal():
    t, n = Tab(), []
    takt_schleife(Nachfasser(t, validator=Val({}), router=Router(None), plan_executor=PE()),
                  takt_s=0.0, schlafen=lambda s: n.append(s), runden=3)
    assert t.abgelaufen_calls == 3


def _lauf(pe, verdict=None, cfg=None):
    t = Tab(fertige=[_auftrag(plan_rest=_rest())])
    z = Nachfasser(t, validator=Val(verdict or {"valid": True, "verified": True, "reason": "ok"}),
                   router=Router(cfg or {"kind": "rule:x"}), plan_executor=pe).runde()
    return t, z


def test_fortsetzung_wirft_wird_sichtbar():
    class Boese(PE):
        def execute(self, *a, **k): raise ValueError("kaputt")
    t, z = _lauf(Boese())
    (i, p), = t.ergaenzt
    assert i == "1" and p["verified"] is True and p["fortsetzung_fehler"].startswith("ValueError: kaputt")
    assert p["fortsetzung"] == "fehler"
    assert z["fehler"] == 1 and z["fortgesetzt"] == 0


def test_fortsetzung_ok_false_wird_sichtbar():
    class NichtOk(PE):
        def execute(self, *a, **k): return {"ok": False}
    t, z = _lauf(NichtOk())
    assert "fortsetzung_fehler" in t.ergaenzt[0][1] and z["fehler"] == 1


def test_erfolgreiche_fortsetzung_meldet_nur_fertig():
    t, z = _lauf(PE())
    (i, p), = t.ergaenzt
    assert p["fortsetzung"] == "fertig" and "fortsetzung_fehler" not in p and z["fortgesetzt"] == 1


def test_agent_pruefer_wird_nicht_aufgerufen():
    v = Val({"valid": True, "verified": True})
    t = Tab(fertige=[_auftrag()])
    Nachfasser(t, validator=v, router=Router({"kind": "agent:pruefer"}), plan_executor=PE()).runde()
    assert v.calls == []
    assert t.gesetzt["1"] == {"verified": None, "reason": "agent-Pruefer nicht im Takt", "kind": "agent:pruefer",
                              "fortsetzung": "keine"}


# ---- Schlussreview (I3/I7/M1/M2) -----------------------------------------

def test_roh_ergebnis_ist_der_string_leer_wird_none():
    for ergebnis, erwartet in (("treffer A", "treffer A"), ("", None), ("  \n", None), (None, None)):
        v = Val({"valid": True, "verified": None})
        Nachfasser(Tab(fertige=[_auftrag(ergebnis=ergebnis)]), validator=v,
                   router=Router({"kind": "rule:x"}), plan_executor=PE()).runde()
        assert v.calls == [erwartet]


def test_echter_validator_leeres_ergebnis_nicht_bestaetigt_kein_fortsetzen():
    # I3: mit dem ECHTEN CapabilityValidator und rule:non_empty_result.
    from core.capability_validator import CapabilityValidator
    for leer in ("", "   "):
        t, pe = Tab(fertige=[_auftrag(ergebnis=leer, plan_rest=_rest())]), PE()
        z = Nachfasser(t, validator=CapabilityValidator(),
                       router=Router({"kind": "rule:non_empty_result", "on_fail": "block"}),
                       plan_executor=pe).runde()
        assert t.gesetzt["1"]["verified"] is False and pe.calls == []
        assert t.gesetzt["1"]["fortsetzung"] == "keine" and z["fortgesetzt"] == 0


def test_echter_validator_nicht_leeres_ergebnis_setzt_fort():
    from core.capability_validator import CapabilityValidator
    t, pe = Tab(fertige=[_auftrag(plan_rest=_rest())]), PE()
    Nachfasser(t, validator=CapabilityValidator(),
               router=Router({"kind": "rule:non_empty_result", "on_fail": "block"}), plan_executor=pe).runde()
    assert t.gesetzt["1"]["verified"] is not False and len(pe.calls) == 1


def test_fortsetzung_laeuft_ist_gesetzt_waehrend_der_plan_rest_laeuft():
    # I7: Zustellung erst nach der Fortsetzung - waehrend execute steht "laeuft".
    t = Tab(fertige=[_auftrag(plan_rest=_rest())])
    gesehen = []

    class Beobachter(PE):
        def execute(self, plan, **kw):
            gesehen.append(dict(t.gesetzt["1"]))
            return {"ok": True}

    Nachfasser(t, validator=Val({"valid": True, "verified": True}), router=Router({"kind": "rule:x"}),
               plan_executor=Beobachter()).runde()
    assert gesehen[0]["fortsetzung"] == "laeuft"
    assert t.ergaenzt[-1][1]["fortsetzung"] == "fertig"


def test_busy_wird_sichtbarer_fortsetzungsfehler():
    class Busy(PE):
        def execute(self, *a, **k): return {"ok": False, "busy": True, "error": "cap"}
    t, z = _lauf(Busy())
    (i, p), = t.ergaenzt
    assert p["fortsetzung"] == "fehler"
    assert p["fortsetzung_fehler"] == "Brain ausgelastet – bitte erneut anfragen"
    assert z["fehler"] == 1 and z["fortgesetzt"] == 0


def test_leerer_plan_rest_heisst_keine_fortsetzung():
    for rest in ({}, {"plan": {"plan_id": "p", "intent": "i", "hops": []}}):
        t, pe = Tab(fertige=[_auftrag(plan_rest=rest)]), PE()
        Nachfasser(t, validator=Val({"valid": True, "verified": True}), router=Router({"kind": "rule:x"}),
                   plan_executor=pe).runde()
        assert pe.calls == [] and t.gesetzt["1"]["fortsetzung"] == "keine"
        assert not hasattr(t, "ergaenzt")


def test_plan_rest_null_wartet_bis_60s_nach_beendet():
    # M1: NULL = noch nicht geschrieben -> bis 60 s nach beendet warten.
    from datetime import datetime, timedelta, timezone
    jetzt = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)
    frisch = _auftrag(id="f", beendet=(jetzt - timedelta(seconds=10)).isoformat())
    alt = _auftrag(id="a", beendet=(jetzt - timedelta(seconds=61)).isoformat().replace("+00:00", "Z"))
    t = Tab(fertige=[frisch, alt])
    z = Nachfasser(t, validator=Val({"valid": True, "verified": True}), router=Router({"kind": "rule:x"}),
                   plan_executor=PE(), jetzt=lambda: jetzt.timestamp()).runde()
    assert "f" not in t.gesetzt and z["wartet"] == 1
    assert t.gesetzt["a"]["fortsetzung"] == "keine"

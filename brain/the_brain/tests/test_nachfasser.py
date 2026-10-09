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
    plan, state, kanal = pe.calls[0]
    assert state == {"treffer": "treffer A"} and kanal == {"art": "telegram"} and plan.hops[0].step_id == "s2"


def test_fehlender_state_im_rest_gilt_als_leer():
    rest = _rest()
    del rest["state"]
    t, pe = Tab(fertige=[_auftrag(plan_rest=rest)]), PE()
    Nachfasser(t, validator=Val({"valid": True, "verified": True}),
               router=Router({"kind": "rule:x"}), plan_executor=pe).runde()
    assert pe.calls[0][1] == {"treffer": "treffer A"}


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
    assert t.gesetzt["1"] == {"verified": None, "reason": "kein Pruefer", "kind": None}


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

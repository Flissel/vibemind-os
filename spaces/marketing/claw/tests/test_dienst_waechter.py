"""Waechter der Marketing-Dienste: Entscheidung, Beende-Sperre und ein Lauf - ohne echte Prozesse."""
from spaces.marketing.claw import dienst_waechter as w

R_BEENDEN = w.Regel(pfad="/health", beenden=True, starten=True)
R_NUR_MELDEN = w.Regel(pfad=None, beenden=False, starten=False)


def test_ok_setzt_zaehler_zurueck():
    aktion, e = w.entscheiden("ok", R_BEENDEN, {"stumm": 2, "gemeldet": 5.0}, 1000.0)
    assert aktion == "nichts"
    assert e["stumm"] == 0 and e["gemeldet"] == 0


def test_tot_wird_gestartet_und_gezaehlt():
    aktion, e = w.entscheiden("tot", R_BEENDEN, {}, 1000.0)
    assert aktion == "starten"
    assert e["neustarts"] == [1000.0]


def test_stumm_erst_beim_dritten_mal_neustart():
    e = {}
    for _ in range(w.STUMM_GRENZE - 1):
        aktion, e = w.entscheiden("stumm", R_BEENDEN, e, 1000.0)
        assert aktion == "nichts"
    aktion, e = w.entscheiden("stumm", R_BEENDEN, e, 1000.0)
    assert aktion == "neustart"
    assert e["stumm"] == 0


def test_stumm_ohne_beende_recht_wird_nur_gemeldet_und_gedrosselt():
    e = {"stumm": w.STUMM_GRENZE - 1}
    aktion, e = w.entscheiden("stumm", R_NUR_MELDEN, e, 1000.0)
    assert aktion == "melden"
    aktion, e = w.entscheiden("stumm", R_NUR_MELDEN, e, 1000.0 + 60)
    assert aktion == "nichts"
    aktion, e = w.entscheiden("stumm", R_NUR_MELDEN, e, 1000.0 + w.MELDE_ABSTAND_S)
    assert aktion == "melden"


def test_tot_ohne_start_recht_wird_nur_gemeldet():
    aktion, _ = w.entscheiden("tot", R_NUR_MELDEN, {}, 1000.0)
    assert aktion == "melden"


def test_kappe_gibt_auf_und_meldet_das_nur_einmal():
    t = 10_000.0
    e = {"neustarts": [t - 30, t - 20, t - 10]}
    aktion, e = w.entscheiden("tot", R_BEENDEN, e, t)
    assert aktion == "aufgeben"
    aktion, e = w.entscheiden("tot", R_BEENDEN, e, t + 300)
    assert aktion == "nichts"


def test_alte_neustarts_zaehlen_nicht_zur_kappe():
    t = 100_000.0
    e = {"neustarts": [t - w.FENSTER_S - 1] * 5}
    aktion, e = w.entscheiden("tot", R_BEENDEN, e, t)
    assert aktion == "starten"
    assert e["neustarts"] == [t]


def test_beende_sperre_verlangt_alle_argumente():
    args = ["-u", "-m", "spaces.marketing.workers.ton_worker"]
    assert w.prozess_passt(["C:\\Python311\\python.exe", "-u", "-m", "spaces.marketing.workers.ton_worker"], args)
    assert not w.prozess_passt(["python.exe", "-m", "spaces.marketing.workers.bild_worker"], args)
    shim = ["C:\\x\\marketing_shim.py", "--host", "127.0.0.1", "--port", "8117"]
    assert w.prozess_passt(["python.exe", "c:\\X\\marketing_shim.py", "--host", "127.0.0.1", "--port", "8117"], shim)
    assert not w.prozess_passt(["python.exe", "C:\\x\\marketing_shim.py", "--host", "127.0.0.1", "--port", "8118"], shim)
    assert not w.prozess_passt([], args)


def _dienst(name, port, args=("-u", "-m", "x")):
    return {"name": name, "port": port, "args": list(args), "was": name}


def test_lauf_startet_tote_und_meldet_ergebnis():
    gestartet, gemeldet = [], []
    befunde = {5510: "ok", 8130: "tot"}
    lauscht_danach = {8130: True}
    z = w.lauf(
        [_dienst("marketing_api", 5510), _dienst("marketing_claw_mcp", 8130)],
        {}, 1000.0,
        pruefe=lambda port, pfad: befunde[port],
        lauscht=lambda port: lauscht_danach.get(port, False),
        beende=lambda d: True,
        starte=gestartet.extend,
        melde=gemeldet.append,
    )
    assert gestartet == ["marketing_claw_mcp"]
    assert len(gemeldet) == 1 and ":8130" in gemeldet[0] and "laeuft wieder" in gemeldet[0]
    assert z["marketing_claw_mcp"]["neustarts"] == [1000.0]


def test_lauf_startet_nicht_wenn_beende_sperre_greift():
    gestartet, gemeldet = [], []
    zustand = {"marketing_ton_arbeiter": {"stumm": w.STUMM_GRENZE - 1}}
    w.lauf(
        [_dienst("marketing_ton_arbeiter", 8135)],
        zustand, 1000.0,
        pruefe=lambda port, pfad: "stumm",
        lauscht=lambda port: True,
        beende=lambda d: False,
        starte=gestartet.extend,
        melde=gemeldet.append,
    )
    assert gestartet == []
    assert "nicht beendet" in gemeldet[0]


def test_lauf_ohne_befund_meldet_nichts():
    gemeldet = []
    w.lauf([_dienst("marketing_api", 5510)], {}, 1000.0,
           pruefe=lambda port, pfad: "ok", lauscht=lambda port: True,
           beende=lambda d: True, starte=lambda n: None, melde=gemeldet.append)
    assert gemeldet == []


def test_comfyui_wird_nie_beendet_oder_gestartet():
    r = w.REGELN["comfyui"]
    assert not r.beenden and not r.starten


def test_mcp_sidecars_werden_nicht_wegen_stille_beendet():
    for name in ("marketing_claw_mcp", "marketing_video_werkzeuge"):
        assert not w.REGELN[name].beenden
        assert w.REGELN[name].starten


def test_ein_scheiternder_start_wird_gemeldet_und_der_neustart_bleibt_gezaehlt():
    """Sonst geht der Zustand verloren, und die Kappe greift nie."""
    gemeldet = []

    def starte(namen):
        raise TimeoutError("Startskript haengt")

    z = w.lauf([_dienst("marketing_claw_mcp", 8130)], {}, 1000.0,
               pruefe=lambda port, pfad: "tot", lauscht=lambda port: False,
               beende=lambda d: True, starte=starte, melde=gemeldet.append)
    assert z["marketing_claw_mcp"]["neustarts"] == [1000.0]
    assert any("Startskript haengt" in m for m in gemeldet)


def test_bot_token_kommt_nicht_ins_log():
    roh = "HTTPError: https://api.telegram.org/bot123456:AAH-x_yZ09/sendMessage 401"
    assert "AAH-x_yZ09" not in w.geschwaerzt(roh)
    assert "bot<geschwaerzt>/sendMessage" in w.geschwaerzt(roh)


def test_eine_kaputte_pruefung_bricht_den_lauf_nicht_ab():
    gemeldet = []

    def pruefe(port, pfad):
        if port == 1:
            raise OSError("kaputt")
        return "ok"

    w.lauf([_dienst("a", 1), _dienst("b", 2)], {}, 1000.0, pruefe=pruefe, lauscht=lambda p: True,
           beende=lambda d: True, starte=lambda n: None, melde=gemeldet.append)
    assert len(gemeldet) == 1 and "a" in gemeldet[0] and "kaputt" in gemeldet[0]

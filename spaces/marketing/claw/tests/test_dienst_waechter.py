"""Waechter der Marketing-Dienste: Entscheidung, Beende-Sperre und ein Lauf - ohne echte Prozesse."""
import socket
import sys

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
        kopien=lambda d: [],
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
        kopien=lambda d: [1],
        starte=gestartet.extend,
        melde=gemeldet.append,
    )
    assert gestartet == []
    assert "nicht beendet" in gemeldet[0]


def test_lauf_ohne_befund_meldet_nichts():
    gemeldet = []
    w.lauf([_dienst("marketing_api", 5510)], {}, 1000.0,
           pruefe=lambda port, pfad: "ok", lauscht=lambda port: True,
           beende=lambda d: True, kopien=lambda d: [], starte=lambda n: None, melde=gemeldet.append)
    assert gemeldet == []


def test_comfyui_wird_nie_beendet_oder_gestartet():
    r = w.REGELN["comfyui"]
    assert not r.beenden and not r.starten


def test_agenten_sidecar_hat_lebenszeichen_und_wird_bei_stille_neu_gestartet():
    """22.10.2026: auch :8130 fuehrt seine Werkzeuge in Threads aus und hat /health."""
    r = w.REGELN["marketing_claw_mcp"]
    assert r.pfad == "/health" and r.beenden and r.starten


def test_video_werkzeuge_haben_lebenszeichen_und_werden_bei_stille_neu_gestartet():
    """21.10.2026: :8137 fror eine Stunde ein, niemand merkte es. Die Werkzeuge laufen seitdem in
    Threads, /health antwortet also auch waehrend reel_bauen - Stille heisst eingefroren."""
    r = w.REGELN["marketing_video_werkzeuge"]
    assert r.pfad == "/health" and r.beenden and r.starten


def test_ein_scheiternder_start_wird_gemeldet_und_der_neustart_bleibt_gezaehlt():
    """Sonst geht der Zustand verloren, und die Kappe greift nie."""
    gemeldet = []

    def starte(namen):
        raise TimeoutError("Startskript haengt")

    z = w.lauf([_dienst("marketing_claw_mcp", 8130)], {}, 1000.0,
               pruefe=lambda port, pfad: "tot", lauscht=lambda port: False,
               beende=lambda d: True, kopien=lambda d: [], starte=starte, melde=gemeldet.append)
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
           beende=lambda d: True, kopien=lambda d: [], starte=lambda n: None, melde=gemeldet.append)
    assert len(gemeldet) == 1 and "a" in gemeldet[0] and "kaputt" in gemeldet[0]



# --- 03.11.2026: ton_dienst lief dreimal (je ~6 GB, Commit-Grenze des PCs erreicht) -------------------

def test_port_lauscht_prueft_mit_echter_verbindung_nicht_ueber_psutil(monkeypatch):
    """psutil meldete die Listener hier mit Status NONE statt LISTEN: 8135 in 30 von 30 Abfragen "tot",
    obwohl der Dienst antwortete. Die Pruefung darf psutil gar nicht brauchen."""
    class KaputtesPsutil:
        def __getattr__(self, name):
            raise AssertionError("port_lauscht darf psutil nicht fragen")
    monkeypatch.setitem(sys.modules, "psutil", KaputtesPsutil())
    lauscher = socket.socket()
    lauscher.bind(("127.0.0.1", 0))
    lauscher.listen()
    port = lauscher.getsockname()[1]
    try:
        assert w.port_lauscht(port)
    finally:
        lauscher.close()
    assert not w.port_lauscht(port)


# Prozessbild vom 03.11.2026 12:04 (gemessen): je Kopie startet das venv-python.exe den echten
# Interpreter als Kind mit derselben Kommandozeile.
TON_ARGS = ["-u", "-m", "spaces.marketing.claw.ton_dienst"]
VENV = r"C:\Users\User\ClaudeWork\chatterbox-venv\Scripts\python.exe"
ECHT = r"C:\Users\User\AppData\Local\Programs\Python\Python311\python.exe"
PROZESSE_0311 = [
    (22144, 24844, [VENV, *TON_ARGS]), (13748, 22144, [ECHT, *TON_ARGS]),   # Waechter 08:08
    (27520, 26012, [VENV, *TON_ARGS]), (26864, 27520, [ECHT, *TON_ARGS]),   # Anmeldeaufgabe 08:09
    (3364, 2884, [VENV, *TON_ARGS]), (22248, 3364, [ECHT, *TON_ARGS]),      # Waechter 08:17
    (8660, 1, ["python.exe", "-u", "-m", "spaces.marketing.workers.ton_worker"]),
    (35668, 34404, ["python.exe", "-I", "-c", "import psutil  # spaces.marketing.claw.ton_dienst"]),
]


def test_eltern_und_kind_sind_eine_kopie():
    assert w.wurzeln(PROZESSE_0311, TON_ARGS) == [3364, 22144, 27520]
    assert w.wurzeln(PROZESSE_0311, ["-u", "-m", "spaces.marketing.claw.server"]) == []


def test_verwaist_startet_nie_und_meldet_erst_nach_der_stumm_grenze():
    """Prozess lebt, Port zu (laedt noch oder haengt): eine zweite Kopie waere das Problem vom 03.11."""
    e = {}
    for _ in range(w.STUMM_GRENZE - 1):
        aktion, e = w.entscheiden("verwaist", R_BEENDEN, e, 1000.0)
        assert aktion == "nichts"
    aktion, e = w.entscheiden("verwaist", R_BEENDEN, e, 1000.0)
    assert aktion == "melden"
    assert e.get("neustarts", []) == []
    aktion, e = w.entscheiden("verwaist", R_BEENDEN, e, 1060.0)
    assert aktion == "nichts"


def test_tot_aber_kopie_lebt_wird_nicht_gestartet():
    gestartet, gemeldet = [], []
    z = w.lauf([_dienst("marketing_ton_dienst", 8138, TON_ARGS)], {}, 1000.0,
               pruefe=lambda port, pfad: "tot", lauscht=lambda port: False, beende=lambda d: True,
               kopien=lambda d: [22144], starte=gestartet.extend, melde=gemeldet.append)
    assert gestartet == []
    assert gemeldet == []
    assert z["marketing_ton_dienst"].get("neustarts", []) == []


def test_mehrere_kopien_werden_gemeldet_aber_nicht_beendet():
    gemeldet, beendet = [], []
    d = _dienst("marketing_ton_dienst", 8138, TON_ARGS)

    def lauf(zustand, jetzt):
        return w.lauf([d], zustand, jetzt, pruefe=lambda port, pfad: "ok", lauscht=lambda port: True,
                      beende=beendet.append, kopien=lambda d: [3364, 22144, 27520],
                      starte=lambda n: None, melde=gemeldet.append)
    z = lauf({}, 1000.0)
    assert len(gemeldet) == 1 and "3-mal" in gemeldet[0] and "22144" in gemeldet[0]
    z = lauf(z, 1300.0)
    assert len(gemeldet) == 1, "gedrosselt wie die anderen Meldungen"
    lauf(z, 1000.0 + w.MELDE_ABSTAND_S)
    assert len(gemeldet) == 2
    assert beendet == []


def test_simulierter_morgen_0311_startet_hoechstens_eine_kopie():
    """Nachbau 03.11.2026 08:08-08:42: zwei Waechter-Instanzen je 5 min (22 s versetzt), dazu sagt die
    Portpruefung des alten Wegs immer "tot" (psutil-NONE). Vorher: drei Kopien. Jetzt: genau eine."""
    kopien: list[int] = []
    gemeldet: list[str] = []
    d = _dienst("marketing_ton_dienst", 8138, TON_ARGS)

    def starte(namen):
        kopien.append(1000 + len(kopien))

    zustand: dict = {}
    t = 0.0
    for _ in range(8):
        for versatz in (0.0, 22.0):
            zustand = w.lauf([d], zustand, t + versatz, pruefe=lambda port, pfad: "tot",
                             lauscht=lambda port: bool(kopien), beende=lambda d: True,
                             kopien=lambda d: list(kopien), starte=starte, melde=gemeldet.append)
        t += 300.0
    assert len(kopien) == 1
    assert zustand["marketing_ton_dienst"]["neustarts"] == [0.0]


def test_beende_dienst_beendet_nur_eine_eindeutige_kopie(monkeypatch):
    aufrufe = []
    monkeypatch.setattr(w.subprocess, "run", lambda argv, **kw: aufrufe.append(argv))
    d = _dienst("marketing_ton_dienst", 8138, TON_ARGS)

    monkeypatch.setattr(w, "laufende_kopien", lambda d: [3364, 22144])
    assert not w.beende_dienst(d)
    assert aufrufe == []

    stand = [[22144], []]
    monkeypatch.setattr(w, "laufende_kopien", lambda d: stand.pop(0) if len(stand) > 1 else stand[0])
    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    assert w.beende_dienst(d)
    assert aufrufe == [["taskkill", "/PID", "22144", "/T", "/F"]]

    monkeypatch.setattr(w, "laufende_kopien", lambda d: [])
    aufrufe.clear()
    assert w.beende_dienst(d)
    assert aufrufe == []


def test_werkbank_dienste_stehen_im_startskript_und_in_den_regeln():
    """12.11.2026: Shim :8154 und Arbeiter :8155 (8147 und 8149 sind belegt; 8150/8151 gehoeren der Lern-Huelle, am 12.11. beim Ausrollen als Kollision gefunden) - eine Liste, ein Wächter."""
    import re
    from pathlib import Path
    skript = (Path(w.__file__).parent / "scripts" / "marketing-dienste-starten.ps1").read_text(encoding="utf-8")
    shim = re.search(r"Name = 'marketing_werkbank_shim'.*?Was\s+=", skript, re.S).group(0)
    assert "Port = 8154" in shim and "'8154'" in shim and "'--timeout', '900'" in shim
    assert "VIBEMIND_AGENT        = 'marketing-werkbank'" in shim and "claude_budget.py" in shim
    arbeiter = re.search(r"Name = 'marketing_werkbank_arbeiter'.*?Was\s+=", skript, re.S).group(0)
    assert "Port = 8155" in arbeiter and "workers.werkbank_arbeiter" in arbeiter
    assert r"TON_STIMMEN_ORDNER = 'E:\marketing-stimmen'" in arbeiter
    assert len(re.findall(r"Port = (\d+)", skript)) == len(set(re.findall(r"Port = (\d+)", skript)))
    assert w.REGELN["marketing_werkbank_shim"] == w.Regel("/health", True, True)
    assert w.REGELN["marketing_werkbank_arbeiter"] == w.Regel("/", True, True)

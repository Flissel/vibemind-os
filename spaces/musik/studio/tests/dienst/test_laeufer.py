"""Auftrags-Laeufer mit Ersatzskript statt dj-set."""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from djset.dienst.auftraege import Ablage
from djset.dienst.laeufer import Laeufer

FAKE = str(Path(__file__).with_name("fake_djset.py"))


@pytest.fixture(autouse=True)
def _umgebung(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSIK_DATEN", str(tmp_path))
    monkeypatch.delenv("FAKE_EXIT", raising=False)
    monkeypatch.delenv("FAKE_SCHLAF", raising=False)


class Spur:
    def __init__(self):
        self.gebucht = []
        self.budget_aufrufe = []


def _bauen(tmp_path, speicher=32.0, budget=(True, "ok"), zeitlimits=None, laura=None):
    spur = Spur()

    def budget_fn(agent):
        spur.budget_aufrufe.append(agent)
        return budget

    def buchen(agent, ergebnis, dauer_s):
        spur.gebucht.append((agent, ergebnis))

    ablage = Ablage(tmp_path)
    laeufer = Laeufer(
        ablage,
        [sys.executable, FAKE],
        lambda: speicher,
        budget_fn,
        buchen,
        laura or (lambda a: {"ok": True}),
        zeitlimits=zeitlimits,
    )
    return ablage, laeufer, spur


def _lebt(pid):
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, timeout=20
    ).stdout
    return str(pid) in out.decode("ascii", errors="replace")


def test_exit_2_ist_fertig_mit_befund(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT", "2")
    ablage, laeufer, spur = _bauen(tmp_path)
    a = ablage.anlegen("set", {"wunsch": "house", "length": 20}, "mcp")
    assert laeufer.ein_schritt() is True
    assert ablage.hole(a.id).zustand == "fertig_mit_befund"
    assert spur.gebucht == [("musik-kette", "fertig_mit_befund")]


def test_exit_0_ist_fertig(tmp_path):
    ablage, laeufer, _ = _bauen(tmp_path)
    a = ablage.anlegen("aufnehmen", {"datei": "x.wav", "titel": "T", "herkunft": "eigen"}, "mcp")
    laeufer.ein_schritt()
    assert ablage.hole(a.id).zustand == "fertig"


def test_exit_1_ist_gescheitert_mit_letzter_stderr_zeile(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT", "1")
    ablage, laeufer, _ = _bauen(tmp_path)
    a = ablage.anlegen("video", {"wunsch": "w", "sekunden": 10}, "mcp")
    laeufer.ein_schritt()
    fertig = ablage.hole(a.id)
    assert fertig.zustand == "gescheitert"
    assert "Song fehlt im Bestand" in fertig.grund
    assert "zuerst etwas Warnung" not in fertig.grund


def test_lauf_id_aus_ausgabe(tmp_path):
    ablage, laeufer, _ = _bauen(tmp_path)
    a = ablage.anlegen("video", {"wunsch": "w", "sekunden": 10}, "mcp")
    laeufer.ein_schritt()
    fertig = ablage.hole(a.id)
    assert fertig.lauf == "fake-lauf-01"
    assert fertig.pid is not None
    assert (tmp_path / "auftraege" / a.id / "ausgabe.log").is_file()


def test_zeitlimit_reisst(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_SCHLAF", "5")
    ablage, laeufer, _ = _bauen(tmp_path, zeitlimits={"video": 1})
    a = ablage.anlegen("video", {"wunsch": "w", "sekunden": 10}, "mcp")
    laeufer.ein_schritt()
    fertig = ablage.hole(a.id)
    assert fertig.zustand == "gescheitert" and "Zeitlimit" in fertig.grund
    assert not _lebt(fertig.pid)


def test_wenig_speicher_bleibt_wartet(tmp_path):
    ablage, laeufer, _ = _bauen(tmp_path, speicher=7.5)
    a = ablage.anlegen("set", {"wunsch": "w", "length": 10}, "mcp")
    assert laeufer.ein_schritt() is False
    teil = ablage.hole(a.id)
    assert teil.zustand == "wartet" and "Speicher" in teil.grund


def test_laura_render_braucht_keinen_speicher(tmp_path):
    ablage, laeufer, spur = _bauen(tmp_path, speicher=1.0)
    a = ablage.anlegen("laura_render", {"x": 1}, "mcp")
    laeufer.laura_render = lambda auftrag: {"ok": True, "clip": "c1"}
    assert laeufer.ein_schritt() is True
    fertig = ablage.hole(a.id)
    assert fertig.zustand == "fertig" and fertig.ergebnis == {"ok": True, "clip": "c1"}
    assert spur.gebucht == []


def test_laura_render_fehler_und_ausnahme(tmp_path):
    ablage, laeufer, _ = _bauen(tmp_path)
    a = ablage.anlegen("laura_render", {}, "mcp")
    laeufer.laura_render = lambda auftrag: {"ok": False, "fehler": "laura sagt nein"}
    laeufer.ein_schritt()
    assert ablage.hole(a.id).zustand == "gescheitert"
    assert ablage.hole(a.id).grund == "laura sagt nein"

    b = ablage.anlegen("laura_render", {}, "mcp")

    def kaputt(auftrag):
        raise RuntimeError("x" * 500)

    laeufer.laura_render = kaputt
    laeufer.ein_schritt()
    fertig = ablage.hole(b.id)
    assert fertig.zustand == "gescheitert" and len(fertig.grund) <= 300


def test_budget_aus_bleibt_wartet_nicht(tmp_path):
    ablage, laeufer, spur = _bauen(tmp_path, budget=(False, "tagesgrenze"))
    a = ablage.anlegen("set", {"wunsch": "w", "length": 10}, "mcp")
    laeufer.ein_schritt()
    fertig = ablage.hole(a.id)
    assert fertig.zustand == "gescheitert" and fertig.grund == "Budget: tagesgrenze"
    assert spur.gebucht == []
    assert spur.budget_aufrufe == ["musik-kette"]


def test_neustart_beendet_verwaisten_prozess(tmp_path):
    ablage, laeufer, _ = _bauen(tmp_path)
    fremd = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        a = ablage.anlegen("video", {}, "mcp")
        a.zustand, a.pid = "laeuft", fremd.pid
        ablage.speichern(a)
        laeufer.beim_start()
        fertig = ablage.hole(a.id)
        assert fertig.zustand == "gescheitert" and "Dienst neu gestartet" in fertig.grund
        fremd.wait(timeout=10)
        assert fremd.poll() is not None
    finally:
        if fremd.poll() is None:
            fremd.kill()


def test_abbrechen_beendet_unterprozess(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_SCHLAF", "30")
    ablage, laeufer, _ = _bauen(tmp_path)
    a = ablage.anlegen("video", {"wunsch": "w", "sekunden": 10}, "mcp")
    t = threading.Thread(target=laeufer.ein_schritt)
    t.start()
    ende = time.monotonic() + 15
    while ablage.hole(a.id).pid is None and time.monotonic() < ende:
        time.sleep(0.05)
    pid = ablage.hole(a.id).pid
    assert pid is not None
    assert laeufer.abbrechen(a.id) is True
    t.join(timeout=15)
    assert not t.is_alive()
    assert ablage.hole(a.id).zustand == "abgebrochen"
    assert not _lebt(pid)


def test_abbrechen_wartenden_und_fertigen(tmp_path):
    ablage, laeufer, _ = _bauen(tmp_path)
    a = ablage.anlegen("video", {}, "mcp")
    assert laeufer.abbrechen(a.id) is True
    assert ablage.hole(a.id).zustand == "abgebrochen"
    assert laeufer.abbrechen(a.id) is False


def test_argumente_ohne_pfade(tmp_path):
    ablage, laeufer, _ = _bauen(tmp_path)
    a = ablage.anlegen(
        "video", {"wunsch": "ruhig", "frames": 900, "fps": "30000/1001", "songs": ["a", "b"]}, "mcp"
    )
    laeufer.ein_schritt()
    argv = json.loads((tmp_path / "runs" / "fake-lauf-01" / "argv.json").read_text("utf-8"))
    text = " ".join(argv)
    assert "--frames 900 --fps 30000/1001" in text
    assert "--video" not in argv
    assert argv[0] == "video" and "--songs" in argv and argv[argv.index("--songs") + 1] == "a,b"
    assert ablage.hole(a.id).zustand == "fertig"


def test_set_und_aufnehmen_argumente(tmp_path):
    ablage, laeufer, _ = _bauen(tmp_path)
    ablage.anlegen("set", {"wunsch": "house", "length": 30, "ohne_gesang": True}, "mcp")
    laeufer.ein_schritt()
    argv = json.loads((tmp_path / "runs" / "fake-lauf-01" / "argv.json").read_text("utf-8"))
    assert argv == ["set", "--wunsch", "house", "--length", "30", "--ohne-gesang"]
    ablage.anlegen(
        "aufnehmen",
        {"datei": "a.wav", "titel": "T", "herkunft": "eigen", "suno_id": "s1"},
        "mcp",
    )
    laeufer.ein_schritt()
    argv = json.loads((tmp_path / "runs" / "fake-lauf-01" / "argv.json").read_text("utf-8"))
    assert argv == ["aufnehmen", "--datei", "a.wav", "--titel", "T", "--herkunft", "eigen",
                    "--suno-id", "s1"]


def test_budget_aus_umgebung_ohne_modul_erlaubt_alles(monkeypatch):
    from djset.dienst.laeufer import budget_aus_umgebung

    monkeypatch.delenv("VIBEMIND_BUDGET_MODUL", raising=False)
    budget, buchen = budget_aus_umgebung()
    assert budget("musik-kette") == (True, "aus")
    buchen("musik-kette", "fertig", 1.0)  # wirft nicht
    monkeypatch.setenv("VIBEMIND_BUDGET_MODUL", "C:/gibt/es/nicht.py")
    assert budget("musik-kette") == (True, "aus")


def test_nichts_zu_tun(tmp_path):
    _, laeufer, _ = _bauen(tmp_path)
    assert laeufer.ein_schritt() is False


def test_schleife_stoppt(tmp_path):
    ablage, laeufer, _ = _bauen(tmp_path)
    a = ablage.anlegen("aufnehmen", {"datei": "a", "titel": "t", "herkunft": "h"}, "mcp")
    stopp = threading.Event()
    t = threading.Thread(target=laeufer.schleife, args=(stopp,))
    t.start()
    ende = time.monotonic() + 20
    while ablage.hole(a.id).zustand in ("wartet", "laeuft") and time.monotonic() < ende:
        time.sleep(0.05)
    stopp.set()
    t.join(timeout=10)
    assert not t.is_alive()
    assert ablage.hole(a.id).zustand == "fertig"

"""Abnahme: "fertig" nur, wenn ein unabhaengiger Pruefschritt gruen ist.

Der Erzeuger der Engine laeuft hier nicht. An seiner Stelle startet die Aufsicht ein kleines
Python-Skript, das wie die Engine alle Aufgaben als erledigt meldet und ein Projekt mit gruenen
eigenen Tests hinlegt - genau die Lage der unmoeglichen Aufgabe 14 in Lauf 1. Den Pruefer der
Aufgabe stellt ein Skript, dessen Urteil der Test festlegt. Gebraucht wird Node.js.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path
from typing import Callable

import pytest

from spaces.coding import abnahme, dienst, pipeline

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js fehlt")

GRUENER_TEST = ("const test = require('node:test');\nconst assert = require('node:assert');\n"
                "test('eins', () => { assert.strictEqual(1 + 1, 2); });\n")


@pytest.fixture(autouse=True)
def _ablage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CODING_PIPELINE_DIR", str(tmp_path / "auftraege"))
    monkeypatch.setenv("CODING_WORKSPACE_ROOT", str(tmp_path / "arbeit"))
    monkeypatch.delenv("CODING_DIENST_PRUEFER", raising=False)
    return tmp_path


def _projekt(ziel: Path, tests: dict) -> Path:
    (ziel / "test").mkdir(parents=True, exist_ok=True)
    namen = []
    for name, inhalt in tests.items():
        (ziel / "test" / name).write_text(inhalt, encoding="utf-8")
        namen.append(f"test/{name}")
    (ziel / "package.json").write_text(json.dumps({"name": "p", "scripts": {"test": "node --test " + " ".join(namen)}}),
                                       encoding="utf-8")
    return ziel


# ── Allgemeine Regeln ─────────────────────────────────────────────────────


def test_gruene_tests_sind_allgemein_gruen(tmp_path: Path) -> None:
    ziel = _projekt(tmp_path / "p", {"a.test.js": GRUENER_TEST})
    assert abnahme.allgemein(ziel, ["test/a.test.js"], mit_app=False) == []


@pytest.mark.parametrize("inhalt, erwartet", [
    ("", "enthält keinen Test"),
    ("// test('eins', () => {});\n/* it('zwei', () => {}) */\n", "enthält keinen Test"),
    ("const test = require('node:test');\ntest.skip('eins', () => {});\n" + GRUENER_TEST.split("\n", 1)[1],
     "übersprungen"),
    ("const test = require('node:test');\nconst assert = require('node:assert');\n"
     "test('rot', () => { assert.strictEqual(1, 2); });\n", "ist rot"),
])
def test_leere_auskommentierte_uebersprungene_und_rote_tests_sind_rot(tmp_path: Path, inhalt: str,
                                                                       erwartet: str) -> None:
    ziel = _projekt(tmp_path / "p", {"a.test.js": GRUENER_TEST, "b.test.js": inhalt})
    gruende = abnahme.allgemein(ziel, [], mit_app=False)
    assert any("b.test.js" in g and erwartet in g for g in gruende), gruende


def test_fehlende_datei_und_kein_test_sind_rot(tmp_path: Path) -> None:
    ziel = tmp_path / "p"
    ziel.mkdir()
    (ziel / "package.json").write_text("{}", encoding="utf-8")
    gruende = abnahme.allgemein(ziel, ["src/x.js"], mit_app=False)
    assert "Gemeldete Datei fehlt: src/x.js" in gruende and "Es gibt keine Testdatei" in gruende


def test_app_muss_auf_health_antworten(tmp_path: Path) -> None:
    ziel = _projekt(tmp_path / "p", {"a.test.js": GRUENER_TEST})
    (ziel / "src").mkdir()
    (ziel / "src" / "server.js").write_text("process.exit(4);\n", encoding="utf-8")
    assert any("beim Start beendet" in g for g in abnahme.allgemein(ziel, [], mit_app=True))
    (ziel / "src" / "server.js").write_text(SERVER, encoding="utf-8")
    assert abnahme.allgemein(ziel, [], mit_app=True) == []


# ── Pruefer der Aufgabe ───────────────────────────────────────────────────


def _pruefer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, urteil: str) -> None:
    skript = tmp_path / "pruefer.py"
    skript.write_text(f"import sys\nassert sys.argv[1:3] == ['pruefen', '14'] and sys.argv[3] == '--ziel'\n"
                      f"print({urteil!r})\n", encoding="utf-8")
    monkeypatch.setenv("CODING_DIENST_PRUEFER", str(skript))


def test_pruefer_der_aufgabe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert abnahme.aufgabe(None, tmp_path)[0] == "fehlt"
    assert abnahme.aufgabe("14", tmp_path)[0] == "fehlt"  # kein Skript eingerichtet
    _pruefer(tmp_path, monkeypatch, '{"richtig": true, "gruende": []}')
    assert abnahme.aufgabe("14", tmp_path) == ("gruen", [])
    _pruefer(tmp_path, monkeypatch, '{"richtig": false, "gruende": ["zu langsam"]}')
    assert abnahme.aufgabe("14", tmp_path) == ("rot", ["zu langsam"])
    _pruefer(tmp_path, monkeypatch, "kein json")
    assert abnahme.aufgabe("14", tmp_path)[0] == "kaputt"


def test_urteil_drei_staende() -> None:
    assert abnahme.urteil([], "gruen", [], "14")["zustand"] == pipeline.FERTIG
    ungeprueft = abnahme.urteil([], "fehlt", ["Für diese Aufgabe ist kein Prüfer genannt"], None)
    assert ungeprueft["zustand"] == pipeline.UNGEPRUEFT and "kein Prüfer" in ungeprueft["satz"]
    rot = abnahme.urteil([], "rot", ["zu langsam"], "14")
    assert rot["zustand"] == pipeline.FEHLGESCHLAGEN and "zu langsam" in rot["satz"]
    assert abnahme.urteil(["Es gibt keine Testdatei"], "gruen", [], "14")["zustand"] == pipeline.FEHLGESCHLAGEN


# ── Ganzer Weg durch die Aufsicht ─────────────────────────────────────────

SERVER = ("const http = require('http');\n"
          "http.createServer((q, a) => { a.writeHead(q.url === '/health' ? 200 : 404); a.end('{\"status\":\"ok\"}'); })"
          ".listen(Number(process.env.PORT || 3000), '127.0.0.1');\n")

# Wie die Engine in Lauf 1 bei Aufgabe 14: jede Aufgabe erledigt, Abschluss "completed",
# eigene Tests gruen (mit kleinen Zahlen). Die Anforderung selbst prueft niemand.
ENGINE_SAGT_ERFOLG = (
    "import json, pathlib, sys\n"
    "ziel = pathlib.Path(sys.argv[1]); projekt = ziel.parent\n"
    f"TEST = {GRUENER_TEST!r}\nSERVER = {SERVER!r}\n"
    "def schreibe(rel):\n"
    "    d = ziel / rel; d.parent.mkdir(parents=True, exist_ok=True)\n"
    "    if d.exists(): return\n"
    "    if rel.endswith('.test.js'): d.write_text(TEST)\n"
    "    elif rel.endswith('server.js'): d.write_text(SERVER)\n"
    "    elif rel.endswith('.js'): d.write_text('module.exports = { primfaktoren: (n) => [n] };\\n')\n"
    "    else: d.write_text('x')\n"
    "for f in sorted((projekt / 'tasks').glob('*-tasks.json')):\n"
    "    inhalt = json.loads(f.read_text(encoding='utf-8'))\n"
    "    for a in inhalt['tasks']:\n"
    "        a['status'] = 'completed'\n"
    "        for rel in a.get('output_files') or []: schreibe(rel)\n"
    "    f.write_text(json.dumps(inhalt), encoding='utf-8')\n"
    "paket = json.loads((ziel / 'package.json').read_text(encoding='utf-8'))\n"
    "for rel in paket['scripts']['test'].split()[2:]: schreibe(rel)\n"
    "(ziel / '.generation_status.json').write_text(json.dumps({'status': 'completed', 'elapsed_seconds': 1}))\n"
)


def _aufsicht() -> dienst.Aufsicht:
    return dienst.Aufsicht(
        befehl=lambda auftrag: [sys.executable, "-c", ENGINE_SAGT_ERFOLG, auftrag["zielordner"]],
        arbeitsordner=lambda: Path(sys.executable).parent, zeitgrenze_s=60)


def _warte(aufsicht: dienst.Aufsicht, bis: Callable[[], bool], sekunden: float = 120.0) -> None:
    ende = time.time() + sekunden
    while time.time() < ende:
        aufsicht.pruefen()
        if bis():
            return
        time.sleep(0.1)
    raise AssertionError("Zeit abgelaufen")


def _bis_ende(aufsicht: dienst.Aufsicht, kennung: str) -> dict:
    _warte(aufsicht, lambda: pipeline.stand(kennung)["zustand"] in pipeline.ENDZUSTAENDE)
    return pipeline.stand(kennung)


def test_unmoegliche_aufgabe_14_engine_sagt_erfolg_stand_ist_gescheitert(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pruefer(tmp_path, monkeypatch,
             '{"richtig": false, "gruende": ["primfaktoren einer 300-stelligen Zahl dauert ueber 20 s"]}')
    aufsicht = _aufsicht()
    sicht = pipeline.auftrag_starten(aufgabe="Schreibe primfaktoren(n) fuer 300-stellige Zahlen in unter 1 ms",
                                     pruefer="14", anstoss=aufsicht.anstoss)

    stand = _bis_ende(aufsicht, sicht["auftrag"])

    assert stand["zustand"] == pipeline.FEHLGESCHLAGEN
    assert "300-stelligen Zahl" in stand["fehler"]
    assert stand["abnahme"]["allgemein"] == "gruen" and stand["abnahme"]["pruefer_ausgang"] == "rot"
    # Die Engine hat Erfolg gemeldet: das steht nur als Notiz da.
    assert stand["notiz_engine"].startswith("Selbstauskunft der Engine: alle Pakete gebaut")
    with pytest.raises(pipeline.PipelineFehler):
        pipeline.ergebnis(sicht["auftrag"])


def test_ohne_pruefer_der_aufgabe_ist_der_stand_ungeprueft() -> None:
    aufsicht = _aufsicht()
    sicht = pipeline.auftrag_starten(aufgabe="Schreibe primfaktoren(n)", anstoss=aufsicht.anstoss)

    stand = _bis_ende(aufsicht, sicht["auftrag"])

    assert stand["zustand"] == pipeline.UNGEPRUEFT
    assert "kein Prüfer genannt" in stand["abnahme"]["satz"]
    ergebnis = pipeline.ergebnis(sicht["auftrag"])
    assert ergebnis["pruefung"]["bestanden"] is False and ergebnis["pruefung"]["zustand"] == pipeline.UNGEPRUEFT


def test_mit_gruenem_pruefer_ist_der_stand_fertig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pruefer(tmp_path, monkeypatch, '{"richtig": true, "gruende": []}')
    aufsicht = _aufsicht()
    sicht = pipeline.auftrag_starten(aufgabe="Schreibe primfaktoren(n)", pruefer="14", anstoss=aufsicht.anstoss)

    stand = _bis_ende(aufsicht, sicht["auftrag"])

    assert stand["zustand"] == pipeline.FERTIG
    assert pipeline.ergebnis(sicht["auftrag"])["pruefung"]["bestanden"] is True


def test_bis_zur_abnahme_heisst_es_laeuft_und_der_naechste_bau_wartet() -> None:
    gestartet = []
    halt = __import__("threading").Event()

    def langsame_abnahme(auftrag: dict) -> dict:
        gestartet.append(auftrag["id"])
        halt.wait(30)
        return abnahme.urteil([], "fehlt", ["kein Prüfer"], None)

    aufsicht = dienst.Aufsicht(
        befehl=lambda auftrag: [sys.executable, "-c", ENGINE_SAGT_ERFOLG, auftrag["zielordner"]],
        arbeitsordner=lambda: Path(sys.executable).parent, abnahme=langsame_abnahme)
    erster = pipeline.auftrag_starten(aufgabe="eins", anstoss=aufsicht.anstoss)
    _warte(aufsicht, lambda: bool(gestartet))
    zweiter = pipeline.auftrag_starten(aufgabe="zwei", anstoss=aufsicht.anstoss)

    stand = pipeline.stand(erster["auftrag"])
    assert stand["zustand"] == pipeline.LAEUFT and stand["abnahme"]["zustand"] == pipeline.LAEUFT
    assert aufsicht.lage()["laeuft"] is None and aufsicht.lage()["reihe"] == [zweiter["auftrag"]]

    halt.set()
    _warte(aufsicht, lambda: pipeline.stand(erster["auftrag"])["zustand"] == pipeline.UNGEPRUEFT)


def test_alter_auftrag_ohne_abnahme_ist_nie_fertig() -> None:
    aufsicht = _aufsicht()
    sicht = pipeline.auftrag_starten(aufgabe="x", anstoss=None)
    roh = json.loads((pipeline.auftragsordner() / f"{sicht['auftrag']}.json").read_text(encoding="utf-8"))
    roh.pop("abnahme_noetig")
    (pipeline.auftragsordner() / f"{sicht['auftrag']}.json").write_text(json.dumps(roh), encoding="utf-8")
    aufsicht.anstoss(roh)
    _warte(aufsicht, lambda: aufsicht.lage()["laeuft"] is None)
    assert pipeline.stand(sicht["auftrag"])["zustand"] == pipeline.UNGEPRUEFT


def test_nach_neustart_wird_die_offene_abnahme_nachgeholt() -> None:
    erste = _aufsicht()
    erste_abnahme = []
    erste._abnahme = lambda auftrag: erste_abnahme.append(1) or {}  # wird nie gerufen
    sicht = pipeline.auftrag_starten(aufgabe="x", anstoss=None)
    auftrag = pipeline._laden(sicht["auftrag"])
    # Die Engine ist fertig, der Dienst endet vor der Abnahme.
    import subprocess
    subprocess.run([sys.executable, "-c", ENGINE_SAGT_ERFOLG, auftrag["zielordner"]], check=True)
    assert pipeline.stand(sicht["auftrag"])["zustand"] == pipeline.LAEUFT

    neu = _aufsicht()
    assert neu.verwaiste_schliessen() == []
    assert _bis_ende(neu, sicht["auftrag"])["zustand"] == pipeline.UNGEPRUEFT
    assert not erste_abnahme

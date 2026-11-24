"""Tests des Dienstes: Lesen, Schreiben nur mit Schluessel, Anstoss und Aufsicht.

Der Erzeuger der Engine laeuft hier nicht. An seiner Stelle startet die Aufsicht
einen winzigen Python-Prozess, der tut, was der Test ueber den Erzeuger wissen
will (enden mit oder ohne Abschluss, haengen bleiben, seine Umgebung aufschreiben).
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import pytest

from spaces.coding import dienst, pipeline

BEISPIEL = Path(__file__).resolve().parents[1] / "beispiele" / "aufgabenliste.entwurf.json"
SCHLUESSEL = "nur-fuer-den-test"


@pytest.fixture(autouse=True)
def _ablage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CODING_PIPELINE_DIR", str(tmp_path / "auftraege"))
    monkeypatch.setenv("CODING_WORKSPACE_ROOT", str(tmp_path / "arbeit"))
    monkeypatch.delenv("CODING_DIENST_SCHLUESSEL", raising=False)
    return tmp_path


def _aufsicht(skript: str, zeitgrenze_s: float = 60.0) -> dienst.Aufsicht:
    """Aufsicht, deren "Erzeuger" das gegebene Skript ist; es bekommt den Zielordner als Argument."""
    return dienst.Aufsicht(
        befehl=lambda auftrag: [sys.executable, "-c", skript, auftrag["zielordner"]],
        arbeitsordner=lambda: Path(sys.executable).parent, zeitgrenze_s=zeitgrenze_s)


def _warte(aufsicht: dienst.Aufsicht, bis: Callable[[], bool], sekunden: float = 20.0) -> None:
    ende = time.time() + sekunden
    while time.time() < ende:
        aufsicht.pruefen()
        if bis():
            return
        time.sleep(0.05)
    raise AssertionError("Zeit abgelaufen")


MIT_ABSCHLUSS = (
    "import json, pathlib, sys\n"
    "z = pathlib.Path(sys.argv[1]); z.mkdir(parents=True, exist_ok=True)\n"
    "(z / '.generation_running').write_text('x')\n"
    "(z / '.generation_status.json').write_text(json.dumps({'status': 'completed_with_errors'}))\n"
)
OHNE_ABSCHLUSS = (
    "import pathlib, sys\n"
    "z = pathlib.Path(sys.argv[1]); z.mkdir(parents=True, exist_ok=True)\n"
    "(z / '.generation_running').write_text('x')\nsys.exit(3)\n"
)
HAENGT = "import time\ntime.sleep(120)\n"


def test_erzeuger_endet_ohne_abschluss_heisst_unterbrochen_und_sperrdatei_ist_weg() -> None:
    aufsicht = _aufsicht(OHNE_ABSCHLUSS)
    sicht = pipeline.auftrag_starten(aufgabe="Schreib hallo.js", anstoss=aufsicht.anstoss)
    assert sicht["angestossen"] is True

    _warte(aufsicht, lambda: aufsicht.lage()["laeuft"] is None)

    ende = pipeline.stand(sicht["auftrag"])
    assert ende["zustand"] == pipeline.FEHLGESCHLAGEN
    assert ende["fehler"] == "unterbrochen: der Erzeuger endete ohne Abschluss (Code 3)"
    assert not (Path(pipeline._laden(sicht["auftrag"])["zielordner"]) / ".generation_running").exists()


def test_erzeuger_mit_abschluss_wird_nicht_als_unterbrochen_markiert() -> None:
    aufsicht = _aufsicht(MIT_ABSCHLUSS)
    sicht = pipeline.auftrag_starten(aufgabe="x", anstoss=aufsicht.anstoss)
    _warte(aufsicht, lambda: aufsicht.lage()["laeuft"] is None)

    assert "unterbrochen" not in pipeline._laden(sicht["auftrag"])
    # Der Abschluss der Engine meldet Fehler, also gescheitert - aber aus ihrem Grund, nicht "unterbrochen".
    assert pipeline.stand(sicht["auftrag"])["zustand"] == pipeline.FEHLGESCHLAGEN
    assert not (Path(pipeline._laden(sicht["auftrag"])["zielordner"]) / ".generation_running").exists()


def test_hoechstens_ein_erzeuger_zugleich_der_zweite_wartet_in_der_reihe() -> None:
    aufsicht = _aufsicht(HAENGT)
    erster = pipeline.auftrag_starten(aufgabe="eins", anstoss=aufsicht.anstoss)
    zweiter = pipeline.auftrag_starten(aufgabe="zwei", anstoss=aufsicht.anstoss)

    assert aufsicht.lage() == {"laeuft": erster["auftrag"], "reihe": [zweiter["auftrag"]]}

    aufsicht.abbrechen(erster["auftrag"])
    assert aufsicht.lage() == {"laeuft": zweiter["auftrag"], "reihe": []}
    aufsicht.abbrechen(zweiter["auftrag"])
    assert aufsicht.lage() == {"laeuft": None, "reihe": []}


def test_ohne_modelldienst_wartet_der_auftrag_und_startet_wenn_er_antwortet() -> None:
    zeit, bereit = [1000.0], [False]
    aufsicht = dienst.Aufsicht(
        befehl=lambda auftrag: [sys.executable, "-c", HAENGT], arbeitsordner=lambda: Path(sys.executable).parent,
        bereit=lambda: bereit[0], warten_s=300, uhr=lambda: zeit[0])
    sicht = pipeline.auftrag_starten(aufgabe="x", anstoss=aufsicht.anstoss)
    assert aufsicht.lage() == {"laeuft": None, "reihe": [sicht["auftrag"]]}
    zeit[0] += 5
    bereit[0] = True
    aufsicht.pruefen()                                   # vor Ablauf der 10 s wird nicht erneut gefragt
    assert aufsicht.lage()["laeuft"] is None
    zeit[0] += 10
    aufsicht.pruefen()
    assert aufsicht.lage() == {"laeuft": sicht["auftrag"], "reihe": []}
    aufsicht.abbrechen(sicht["auftrag"])


def test_modelldienst_bleibt_weg_dann_scheitert_der_auftrag_ehrlich() -> None:
    zeit = [1000.0]
    aufsicht = dienst.Aufsicht(
        befehl=lambda auftrag: pytest.fail("ohne Modelldienst startet nichts"),
        arbeitsordner=lambda: Path(sys.executable).parent, bereit=lambda: False, warten_s=300, uhr=lambda: zeit[0])
    sicht = pipeline.auftrag_starten(aufgabe="x", anstoss=aufsicht.anstoss)
    for _ in range(31):
        zeit[0] += 10
        aufsicht.pruefen()
    assert aufsicht.lage() == {"laeuft": None, "reihe": []}
    ende = pipeline.stand(sicht["auftrag"])
    assert ende["zustand"] == pipeline.FEHLGESCHLAGEN
    assert "Modelldienst (OpenFang) war 5 min nicht erreichbar" in ende["fehler"]


def test_modelldienst_probe_ohne_dienst_ist_nein(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENFANG_URL", "http://127.0.0.1:9")
    assert dienst.modelldienst_bereit() is False


def test_abbrechen_haelt_den_erzeuger_wirklich_an() -> None:
    aufsicht = _aufsicht(HAENGT)
    sicht = pipeline.auftrag_starten(aufgabe="x", anstoss=aufsicht.anstoss)
    prozess = aufsicht._laufend[1]
    assert prozess.poll() is None

    ende = aufsicht.abbrechen(sicht["auftrag"])

    assert prozess.poll() is not None
    assert ende["zustand"] == pipeline.ABGEBROCHEN
    assert "unterbrochen" not in pipeline._laden(sicht["auftrag"])


def test_abbrechen_in_der_reihe_startet_den_auftrag_nie() -> None:
    aufsicht = _aufsicht(HAENGT)
    erster = pipeline.auftrag_starten(aufgabe="eins", anstoss=aufsicht.anstoss)
    zweiter = pipeline.auftrag_starten(aufgabe="zwei", anstoss=aufsicht.anstoss)

    aufsicht.abbrechen(zweiter["auftrag"])
    aufsicht.abbrechen(erster["auftrag"])

    assert aufsicht.lage() == {"laeuft": None, "reihe": []}
    # Angelegt ist nur das package.json des Auftrags; der Erzeuger hat dort nie etwas geschrieben.
    ziel = Path(pipeline._laden(zweiter["auftrag"])["zielordner"])
    assert [d.name for d in ziel.iterdir()] == ["package.json"]


def test_zeitgrenze_haelt_den_erzeuger_an() -> None:
    aufsicht = _aufsicht(HAENGT, zeitgrenze_s=0.3)
    sicht = pipeline.auftrag_starten(aufgabe="x", anstoss=aufsicht.anstoss)
    prozess = aufsicht._laufend[1]

    _warte(aufsicht, lambda: aufsicht.lage()["laeuft"] is None)

    assert prozess.poll() is not None
    assert pipeline.stand(sicht["auftrag"])["fehler"] == "unterbrochen: Zeitgrenze von 0 s ueberschritten"


def test_umgebung_des_erzeugers_kennt_nur_system_und_openfang(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "SUPERMEMORY_CC_API_KEY", "CODING_DIENST_SCHLUESSEL"):
        monkeypatch.setenv(name, "darf-nicht-mitgehen")
    monkeypatch.setenv("OPENFANG_API_KEY", "platzhalter")
    skript = ("import json, os, pathlib, sys\n"
              "z = pathlib.Path(sys.argv[1]); z.mkdir(parents=True, exist_ok=True)\n"
              "(z / 'umgebung.json').write_text(json.dumps(dict(os.environ)))\n")
    aufsicht = _aufsicht(skript)
    sicht = pipeline.auftrag_starten(aufgabe="x", anstoss=aufsicht.anstoss)
    _warte(aufsicht, lambda: aufsicht.lage()["laeuft"] is None)

    auftrag = pipeline._laden(sicht["auftrag"])
    umgebung = json.loads((Path(auftrag["zielordner"]) / "umgebung.json").read_text(encoding="utf-8"))
    assert "darf-nicht-mitgehen" not in json.dumps(umgebung)
    assert umgebung["LLM_BACKEND"] == "openfang" and umgebung["OPENFANG_MAX_CALLS"] == "40"
    assert umgebung["VIBEMIND_CODING_AGENT"] == "brain-coder" and umgebung["OPENFANG_API_KEY"] == "platzhalter"
    assert umgebung["ENGINE_AUDIT_LOG"] == str(Path(auftrag["projektordner"]) / "pruefspur.log")
    erlaubt = dienst.SYSTEM | {"openfang_url", "openfang_api_key", "llm_backend", "openfang_max_calls",
                               "vibemind_coding_agent", "engine_audit_log", "pythonioencoding",
                               "engine_npm_install", "engine_min_free_commit_gb"}
    assert {name.lower() for name in umgebung} <= erlaubt
    # Ein getippter Satz laeuft ohne Pakete: npm install bleibt aus.
    assert umgebung["ENGINE_NPM_INSTALL"] == "0" and umgebung["ENGINE_MIN_FREE_COMMIT_GB"] == "8"


def test_npm_install_nur_fuer_auftraege_mit_paketen() -> None:
    basis = {"projektordner": "P"}
    assert dienst.saubere_umgebung({**basis, "ohne_npm": True})["ENGINE_NPM_INSTALL"] == "0"
    assert dienst.saubere_umgebung({**basis, "ohne_npm": False})["ENGINE_NPM_INSTALL"] == "1"
    assert dienst.saubere_umgebung(basis)["ENGINE_NPM_INSTALL"] == "0"


def test_erzeuger_befehl_ist_der_des_gemessenen_laufs() -> None:
    befehl = dienst.erzeuger_befehl({"projektordner": "P", "zielordner": "Z"})
    assert befehl[1:] == ["run_generation.py", "--project-path", "P", "--output-dir", "Z",
                          "--parallelism", "1", "--no-loop", "--skip-task-gen", "--stop-on-failed-epic"]


def test_beim_start_werden_verwaiste_laufende_auftraege_unterbrochen(tmp_path: Path) -> None:
    sicht = pipeline.auftrag_starten(aufgabe="x")
    ziel = Path(pipeline._laden(sicht["auftrag"])["zielordner"])
    ziel.mkdir(parents=True, exist_ok=True)
    (ziel / ".generation_running").write_text("x", encoding="utf-8")
    wartend = pipeline.auftrag_starten(aufgabe="y")
    assert pipeline.stand(sicht["auftrag"])["zustand"] == pipeline.LAEUFT

    assert dienst.Aufsicht().verwaiste_schliessen() == [sicht["auftrag"]]

    ende = pipeline.stand(sicht["auftrag"])
    assert ende["zustand"] == pipeline.FEHLGESCHLAGEN and ende["fehler"].startswith("unterbrochen: der Dienst")
    assert not (ziel / ".generation_running").exists()
    assert pipeline.stand(wartend["auftrag"])["zustand"] == pipeline.WARTET


# ── HTTP ───────────────────────────────────────────────────────────────────


@pytest.fixture
def laufend() -> Iterator[Tuple[str, dienst.Aufsicht]]:
    aufsicht = _aufsicht(HAENGT)
    server = dienst.server(0, aufsicht)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", aufsicht
    for kennung in [aufsicht.lage()["laeuft"], *aufsicht.lage()["reihe"]]:
        if kennung:
            aufsicht.abbrechen(kennung)
    server.shutdown()
    server.server_close()


def _frage(url: str, daten: Optional[Dict[str, Any]] = None, kopf: Optional[Dict[str, str]] = None,
           ) -> Tuple[int, Dict[str, str], bytes]:
    koepfe = dict(kopf or {})
    koerper = None
    if daten is not None:
        koerper = json.dumps(daten).encode("utf-8")
        koepfe.setdefault("Content-Type", "application/json")
    anfrage = urllib.request.Request(url, data=koerper, headers=koepfe, method="POST" if daten is not None else "GET")
    try:
        with urllib.request.urlopen(anfrage, timeout=10) as antwort:  # noqa: S310 - lokale Adresse
            return antwort.status, dict(antwort.headers), antwort.read()
    except urllib.error.HTTPError as fehler:
        return fehler.code, dict(fehler.headers), fehler.read()


def test_lesen_seite_health_und_strenge_koepfe(laufend: Tuple[str, dienst.Aufsicht]) -> None:
    url, _ = laufend

    status, koepfe, koerper = _frage(url + "/")
    assert status == 200 and b"<title>Project Space</title>" in koerper
    assert "script-src 'self'" in koepfe["Content-Security-Policy"]
    assert "unsafe-inline" not in koepfe["Content-Security-Policy"]
    assert koepfe["X-Content-Type-Options"] == "nosniff" and koepfe["Cache-Control"] == "no-store"
    for pfad, art in (("/seite.js", "text/javascript"), ("/seite.css", "text/css")):
        status, koepfe, _ = _frage(url + pfad)
        assert status == 200 and koepfe["Content-Type"].startswith(art)

    status, _, koerper = _frage(url + "/health")
    assert status == 200
    assert json.loads(koerper) == {"status": "ok", "service": "coding-dienst",
                                   "stufen": ["annahme", "plan", "bau", "pruefung", "abschluss"], "schreiben": False}


def test_seite_setzt_inhalte_nur_als_text() -> None:
    skript = (Path(dienst.__file__).parent / "seite" / "seite.js").read_text(encoding="utf-8")
    seite = (Path(dienst.__file__).parent / "seite" / "seite.html").read_text(encoding="utf-8")
    for verboten in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert verboten not in skript
    assert "<script src=\"/seite.js\"></script>" in seite and seite.count("<script") == 1
    assert "style=" not in seite and "onclick" not in seite


def test_fremder_hostname_wird_abgelehnt(laufend: Tuple[str, dienst.Aufsicht]) -> None:
    url, _ = laufend
    port = url.rsplit(":", 1)[1]
    assert _frage(url + "/api/liste", kopf={"Host": f"boese.example:{port}"})[0] == 403
    assert _frage(url + "/api/liste", kopf={"Host": f"host.docker.internal:{port}"})[0] == 200
    assert _frage(url + "/api/liste", kopf={"Host": f"localhost:{port}"})[0] == 200


def test_lesen_liste_stand_ergebnis(laufend: Tuple[str, dienst.Aufsicht]) -> None:
    url, _ = laufend
    assert _frage(url + "/api/stand")[0] == 404
    sicht = pipeline.auftrag_starten(entwurf=json.loads(BEISPIEL.read_text(encoding="utf-8")))

    status, _, koerper = _frage(url + "/api/liste")
    assert status == 200 and json.loads(koerper)["anzahl"] == 1
    stand = json.loads(_frage(url + f"/api/stand?auftrag={sicht['auftrag']}")[2])
    assert stand["kurz"] == {"stufe": "bau", "zustand": "wartet", "paket_nr": 1, "pakete_gesamt": 4}
    status, _, koerper = _frage(url + "/api/ergebnis")
    assert status == 404 and "keinen passenden" in json.loads(koerper)["error"]
    assert _frage(url + "/api/stand?auftrag=../x")[0] == 400
    assert _frage(url + "/api/gibtsnicht")[0] == 404


def test_schreiben_ist_ohne_gesetzten_schluessel_aus(laufend: Tuple[str, dienst.Aufsicht]) -> None:
    url, aufsicht = laufend
    status, _, koerper = _frage(url + "/api/auftrag", {"aufgabe": "x"}, {dienst.KOPF_SCHLUESSEL: "egal"})
    assert status == 403 and json.loads(koerper) == {"error": "schreiben_aus"}
    assert pipeline.liste()["anzahl"] == 0 and aufsicht.lage()["laeuft"] is None


def test_schreiben_nur_mit_richtigem_schluessel(laufend: Tuple[str, dienst.Aufsicht],
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    url, aufsicht = laufend
    monkeypatch.setenv("CODING_DIENST_SCHLUESSEL", SCHLUESSEL)
    recht = {dienst.KOPF_SCHLUESSEL: SCHLUESSEL}

    assert _frage(url + "/api/auftrag", {"aufgabe": "x"})[0] == 401
    assert _frage(url + "/api/auftrag", {"aufgabe": "x"}, {dienst.KOPF_SCHLUESSEL: "falsch"})[0] == 401
    assert _frage(url + "/api/auftrag", {"aufgabe": "x"}, {**recht, "Origin": "http://boese.example"})[0] == 403
    assert _frage(url + "/api/auftrag", {"aufgabe": "x"}, {**recht, "Content-Type": "text/plain"})[0] == 403
    assert pipeline.liste()["anzahl"] == 0

    status, _, koerper = _frage(url + "/api/auftrag", {"aufgabe": "Schreib hallo.js", "titel": "Hallo"}, recht)
    sicht = json.loads(koerper)
    assert status == 200 and sicht["titel"] == "Hallo" and sicht["angestossen"] is True
    assert aufsicht.lage()["laeuft"] == sicht["auftrag"]
    assert SCHLUESSEL not in koerper.decode("utf-8")

    status, _, koerper = _frage(url + "/api/abbrechen", {"auftrag": sicht["auftrag"]}, recht)
    assert status == 200 and json.loads(koerper)["zustand"] == "abgebrochen"
    assert aufsicht.lage()["laeuft"] is None
    assert _frage(url + "/api/auftrag", {}, recht)[0] == 400
    assert json.loads(_frage(url + "/health")[2])["schreiben"] is True


def test_zweiter_dienst_bekommt_den_belegten_port_nicht() -> None:
    # Zwei Dienste auf einem Port hiessen zwei Aufsichten, die je einen Erzeuger starten koennen.
    erster = dienst.server(0, dienst.Aufsicht())
    try:
        with pytest.raises(OSError):
            zweiter = dienst.server(erster.server_address[1], dienst.Aufsicht())
            zweiter.server_close()
    finally:
        erster.server_close()

"""Live: der Wert geht durch das Formular in den Vault -- und sonst nirgends.

Opt-in wie die uebrigen Live-Beweise: ohne PLUGIN_SETUP_FENSTER_LIVE=1
wird uebersprungen, nicht bestanden. Ein Beweis, der sich ohne Umgebung
selbst gruen meldet, ist wertlos.

SIEBTE STELLE (Ruling zu Task 7, nicht im urspruenglichen Brief): der Brief
verlangte sechs Stellen, an denen der Wert nicht auftauchen darf. Diese
Datei prueft eine siebte dazu -- das POSTGRES-CONTAINERLOG selbst. Genau
diese Klasse ist auf diesem Zweig schon zweimal aufgetreten: einmal ein
Klartext-Credential im Postgres-Log (`ablage.py`, C1-Fix, 37 Treffer nach
einer einzigen ausgeloesten Kollision -- `log_min_error_statement = error`
loggt jede fehlschlagende Anweisung wortwoertlich), und einmal ein Test, der
bei jedem Lauf einen geheimnisfoermigen Wert protokollierte. Ein Beweis, der
diese Stelle ausliesse, liesse die einzige Stelle aus, an der es hier
wirklich schon geleckt hat.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_LIVE = os.environ.get("PLUGIN_SETUP_FENSTER_LIVE", "").strip() == "1"
_HIER = Path(__file__).resolve().parents[1]
_FAKE = f"offensichtlich-erfunden-fenster-{uuid.uuid4().hex}"

pytestmark = pytest.mark.skipif(not _LIVE, reason="PLUGIN_SETUP_FENSTER_LIVE != 1")


def _freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _supabase_container() -> str:
    behaelter = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                               capture_output=True, text=True, check=True)
    return next(z.strip() for z in behaelter.stdout.splitlines() if "supabase-db" in z)


def _psql_als_postgres(sql: str) -> str:
    name = _supabase_container()
    res = subprocess.run(["docker", "exec", "-i", name, "psql", "-U", "postgres",
                          "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-tA"],
                         input=sql, capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout


def _psql_als_postgres_fehler_erlaubt(sql: str) -> None:
    """Wie `_psql_als_postgres`, aber fuer die Nullkontrolle unten: die
    loest ABSICHTLICH einen Postgres-Fehler aus, um zu belegen, dass ein
    solcher Fehler im Containerlog auftaucht -- ein Fehlschlag hier ist
    der Punkt, kein Grund zum Abbrechen."""
    subprocess.run(["docker", "exec", "-i", _supabase_container(), "psql",
                    "-U", "postgres", "-d", "postgres", "-tA"],
                   input=sql, capture_output=True, text=True, check=False)


def _docker_logs_seit(dauer: str) -> str:
    """`docker logs --since <Go-Dauer>` -- ABSICHTLICH eine DAUER ('5m'),
    NICHT ein Zeitstempel. Ein Zeitstempel ohne Zeitzone ist auf diesem
    Zweig schon zweimal teuer gewesen (unauffaellig falsches statt
    fehlerndes Verhalten) -- eine Dauer hat diese Fallklasse strukturell
    nicht: `--since 5m` kann nicht "ohne Zeitzone" sein."""
    res = subprocess.run(["docker", "logs", "--since", dauer, _supabase_container()],
                         capture_output=True, text=True, check=False)
    return res.stdout + res.stderr


def _mcp(basis: str, methode: str, params: dict, sid: str = "") -> tuple[dict, str]:
    kopf = {"Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"}
    if sid:
        kopf["Mcp-Session-Id"] = sid
    rumpf = json.dumps({"jsonrpc": "2.0", "id": 1, "method": methode,
                        "params": params}).encode()
    anfrage = urllib.request.Request(f"{basis}/mcp", data=rumpf, headers=kopf,
                                     method="POST")
    with urllib.request.urlopen(anfrage, timeout=30) as antwort:
        neue_sid = antwort.headers.get("Mcp-Session-Id", sid)
        roh = antwort.read().decode("utf-8", "replace")
    for zeile in roh.splitlines():
        if zeile.startswith("data: "):
            roh = zeile[6:]
            break
    return json.loads(roh), neue_sid


def test_der_wert_erreicht_den_vault_und_steht_in_keiner_antwort():
    port = _freier_port()
    referenz = f"PYTEST_FENSTER_{uuid.uuid4().hex[:8].upper()}"
    umgebung = dict(os.environ,
                    PLUGIN_SETUP_MCP_HOST="127.0.0.1",
                    PLUGIN_SETUP_MCP_PORT=str(port),
                    PLUGIN_SETUP_FENSTER_BASIS=f"http://127.0.0.1:{port}",
                    PYTHONDONTWRITEBYTECODE="1")
    protokoll = Path(os.environ.get("TMP", "/tmp")) / f"fenster-live-{port}.log"
    with protokoll.open("w", encoding="utf-8") as aus:
        kind = subprocess.Popen([sys.executable, "server.py"], cwd=str(_HIER),
                                env=umgebung, stdout=aus, stderr=subprocess.STDOUT)
    try:
        basis = f"http://127.0.0.1:{port}"
        frist = time.time() + 60
        while time.time() < frist:
            try:
                urllib.request.urlopen(f"{basis}/mcp", timeout=2)
            except Exception as fehler:  # noqa: BLE001 -- 406 heisst: er lebt
                if "406" in str(fehler):
                    break
            time.sleep(1)
        else:
            pytest.fail("Sidecar ist nicht hochgekommen")

        _, sid = _mcp(basis, "initialize",
                      {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "live", "version": "1"}})
        antwort, _ = _mcp(basis, "tools/call",
                          {"name": "eingabe_anfordern",
                           "arguments": {"projekt": "pytest-live", "plugin": "demo-plugin",
                                         "referenz": referenz, "art": "bearer", "ziel": ""}},
                          sid)
        text = antwort["result"]["content"][0]["text"]
        assert _FAKE not in text, "die Anforderung darf keinen Wert kennen"
        url = json.loads(text)["url"]

        with urllib.request.urlopen(url, timeout=15) as seite:
            seite_html = seite.read().decode("utf-8", "replace")
        assert referenz in seite_html
        assert _FAKE not in seite_html

        daten = urllib.parse.urlencode({"wert": _FAKE}).encode()
        with urllib.request.urlopen(urllib.request.Request(url, data=daten),
                                    timeout=90) as ergebnis:
            ergebnis_html = ergebnis.read().decode("utf-8", "replace")
        assert _FAKE not in ergebnis_html

        status = _psql_als_postgres(
            "SELECT status FROM plugin_setup.einrichtungen "
            f"WHERE referenz_name = '{referenz}';").strip()
        assert status == "fehlgeschlagen", (
            "ein erfundener Wert MUSS beim Anbieter durchfallen -- alles andere "
            "hiesse, die Verifikation prueft nicht wirklich")

        leck = _psql_als_postgres(
            "SELECT count(*) FROM plugin_setup.einrichtungen "
            f"WHERE hinweis LIKE '%{_FAKE}%';").strip()
        assert leck == "0"
        assert _FAKE not in protokoll.read_text(encoding="utf-8", errors="replace")

        # --- Siebte Stelle: das Postgres-CONTAINERLOG selbst -------------
        # Nullkontrolle ZUERST: ohne einen Beleg, dass die Suche im
        # gewaehlten Zeitfenster ueberhaupt etwas faende, ist "0 Treffer
        # fuer _FAKE" bedeutungslos -- es koennte ebenso gut heissen, dass
        # Containername, Zeitfenster oder Suche falsch sind, nicht dass der
        # Code sauber ist. Also erst absichtlich einen Postgres-Fehler mit
        # einem harmlosen, garantiert einzigartigen Marker (KEIN Credential
        # -- ein erfundener, nicht existierender Tabellenname) ausloesen und
        # sein Auftauchen im Log belegen, bevor die eigentliche
        # Abwesenheits-Zusicherung fuer _FAKE etwas beweisen kann.
        marker = f"nullkontrolle_marker_{uuid.uuid4().hex}"
        _psql_als_postgres_fehler_erlaubt(f"SELECT 1 FROM {marker};\n")
        zeitfenster = _docker_logs_seit("5m")
        assert marker in zeitfenster, (
            "Nullkontrolle gescheitert: die absichtlich ausgeloeste Fehlermeldung "
            "taucht selbst nicht im Postgres-Containerlog auf -- ohne das ist eine "
            "Abwesenheits-Zusicherung auf demselben Log wertlos (Containername, "
            "Zeitfenster oder Suche koennten falsch sein statt der Code)")
        assert _FAKE not in zeitfenster, (
            "der erfundene Wert steht im Postgres-CONTAINERLOG -- genau die "
            "Leckklasse, die auf diesem Zweig schon zweimal aufgetreten ist "
            "(s. ablage.py, C1-Fix: 37 Treffer nach einer Kollision)")
    finally:
        kind.terminate()
        kind.wait(timeout=30)
        _psql_als_postgres(
            "DELETE FROM vault.secrets WHERE id IN (SELECT vault_secret_id FROM "
            f"plugin_setup.einrichtungen WHERE referenz_name = '{referenz}' "
            "AND vault_secret_id IS NOT NULL); "
            f"DELETE FROM plugin_setup.einrichtungen WHERE referenz_name = '{referenz}';")
        protokoll.unlink(missing_ok=True)

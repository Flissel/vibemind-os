"""Aufgabe 6 -- Supabase-Anbindung: der Zustandsautomat aus Aufgabe 4
(`db/0001_plugin_setup.sql`, `db/0002_state_machine.sql`) als Python-
Schnittstelle fuer den plugin-setup-Agenten.

ROLLE (Review-Vorgabe #3, gemessen 11.09.2026 gegen die laufende
vibemind_supabase-db): postgres/service_role/supabase_admin umgehen RLS
strukturell (rolbypassrls=t bzw. Tabellenbesitz) und koennten den in
0002_state_machine.sql erzwungenen Automaten per rohem
`UPDATE plugin_setup.einrichtungen SET status = ...` umgehen -- genau der
Review-Befund, den dieser Task loest. Diese Datei verbindet sich darum NIE
als eine der drei; sie nutzt ausschliesslich die eigens angelegte Rolle
`plugin_setup_agent` (db/0003_least_privilege_role.sql): NOBYPASSRLS, nur
INSERT auf `einrichtungen` (keine SELECT/UPDATE/DELETE) plus EXECUTE auf
`verifizieren`/`fehlschlagen`/`uebernommen` und `vault.create_secret`. Ein
roher UPDATE scheitert als diese Rolle doppelt: kein Tabellenrecht, und
selbst MIT Recht griffe die erzwungene RLS ohne eine einzige UPDATE-Policy.
Bewiesen in tests/test_ablage.py (`test_rohes_update_scheitert_als_agent_rolle`).

ZUGRIFFSPFAD: `docker exec <supabase-db-container> psql -U plugin_setup_agent
-h 127.0.0.1 ...` -- TCP-Loopback, `trust`-Auth per pg_hba.conf (gemessen).
Der lokale Unix-Socket mappt JEDE Verbindung per `peer`-Auth auf die Rolle
`postgres` (pg_ident.conf: `root -> postgres`), darum ist `-h 127.0.0.1`
fuer jede andere Rolle zwingend, nicht optional. SQL reist immer auf stdin,
nie ueber argv (Muster spaces/marketing/sync/_db.py) -- kein
Shell-Quoting-Risiko, egal was `wert` enthaelt.

FAIL-SOFT (Muster spaces/marketing/claw/werkzeuge.py): keine Funktion hier
wirft je. Rueckgabe ist immer `{"ok": True, ...}` oder
`{"ok": False, "fehler": "..."}` -- und `wert` steht in KEINEM Fehlertext
(siehe `_ohne_wert`); Aufrufer duerfen `wert` nur zum Aufbau der Anfrage
verwenden, nie in einer Log- oder Fehlerzeile.

Atomaritaet: `entgegennehmen()` legt den Vault-Secret UND die Zustandszeile
in EINEM SQL-Statement an (CTE `WITH v AS (SELECT vault.create_secret(...))
INSERT ... SELECT ... FROM v`). Scheitert der INSERT (z.B. `referenz_name`
schon vergeben), rollt Postgres das ganze Statement zurueck -- inklusive des
Vault-Secrets aus der CTE. Kein verwaister Vault-Eintrag bei einer
Kollision.
"""
from __future__ import annotations

import os
import re
import subprocess
import uuid
from typing import Optional

_ART_ERLAUBT = {"bearer", "oauth", "connector"}
_REFERENZ_MUSTER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_HINWEIS_MAXLAENGE = 200

_DB_NAME = "postgres"
_LOCAL_CONTAINER_HINWEIS = "supabase-db"


def _rolle() -> str:
    return os.environ.get("PLUGIN_SETUP_DB_ROLE", "plugin_setup_agent").strip() or "plugin_setup_agent"


def _host() -> str:
    # Zwingend TCP-Loopback (trust), NIE der lokale Socket -- der mappt per
    # peer-Auth auf `postgres`, egal welche Rolle man mit -U angibt.
    return os.environ.get("PLUGIN_SETUP_DB_HOST", "127.0.0.1").strip() or "127.0.0.1"


_CONTAINER: Optional[str] = None


def _container() -> str:
    """Der laufende supabase-db-Container. Override per
    PLUGIN_SETUP_DB_CONTAINER; sonst Auto-Erkennung wie tests/conftest.py
    und spaces/marketing/sync/_db.py (Namenssubstring `supabase-db`)."""
    global _CONTAINER
    override = os.environ.get("PLUGIN_SETUP_DB_CONTAINER", "").strip()
    if override:
        return override
    if _CONTAINER is not None:
        return _CONTAINER
    res = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=True, timeout=15,
    )
    for name in res.stdout.splitlines():
        name = name.strip()
        if _LOCAL_CONTAINER_HINWEIS in name:
            _CONTAINER = name
            return name
    raise RuntimeError(f"kein laufender {_LOCAL_CONTAINER_HINWEIS}-Container gefunden (docker ps)")


def _sql_literal(value) -> str:
    """Sichere Literal-Quotierung, angelehnt an libpq PQescapeLiteral
    (identische Logik zu spaces/marketing/sync/_db.py::_sql_literal) --
    unabhaengig von standard_conforming_strings, NUL-Byte wird abgelehnt
    (Postgres-Text kann es nicht speichern, und es wuerde das
    psql-Kommando am NUL abschneiden)."""
    if value is None:
        return "NULL"
    s = str(value)
    if "\x00" in s:
        raise ValueError("NUL-Byte in SQL-Literal nicht erlaubt")
    if "\\" in s:
        s = s.replace("\\", "\\\\").replace("'", "''")
        return f"E'{s}'"
    return "'" + s.replace("'", "''") + "'"


def _ohne_wert(text: str, *werte: str) -> str:
    """Scrubt jeden uebergebenen Wert aus einem Text, bevor er in eine
    Fehlermeldung wandert -- Verteidigung in der Tiefe zusaetzlich zur
    Tatsache, dass psql-Fehlertexte normalerweise nur Constraint-/
    Spaltennamen nennen, nie die eingefuegten Werte selbst."""
    for wert in werte:
        if wert:
            text = text.replace(wert, "<wert>")
    return text


def _psql(sql: str, *, rolle: Optional[str] = None) -> tuple:
    """Fuehrt `sql` auf stdin gegen die gewaehlte Rolle aus (TCP-Loopback).
    Wirft nie -- Netzwerk-/Docker-Fehler werden vom Aufrufer als
    {"ok": False, ...} behandelt."""
    argv = ["docker", "exec", "-i", _container(),
            "psql", "-U", rolle or _rolle(), "-h", _host(), "-d", _DB_NAME,
            "-v", "ON_ERROR_STOP=1", "-tA"]
    res = subprocess.run(
        argv, input=sql, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False, timeout=30,
    )
    return res.returncode, (res.stdout + res.stderr)


def _copy_csv_feld(wert: str) -> str:
    """Ein einzelnes CSV-Feld (COPY ... WITH (FORMAT csv)), Anfuehrungszeichen
    verdoppelt -- Postgres' CSV-COPY-Format erlaubt eingebettete Zeilenumbrueche
    in einem gequoteten Feld, also braucht es (anders als das text-Format)
    keine \\n/\\t/\\\\-Sonderbehandlung."""
    return '"' + wert.replace('"', '""') + '"'


def entgegennehmen(projekt: str, plugin: str, referenz: str, art: str, wert: str) -> dict:
    """Legt den Wert verschluesselt im Vault ab und daneben die
    Zustandszeile (Status `entgegengenommen`) -- ein einziges Statement,
    darum atomar (siehe Moduldoku). `wert` erscheint in keinem
    Rueckgabefeld und keiner Fehlermeldung.

    C1-FIX (Review Runde 1, 2026-09-11): `wert` reist NICHT mehr als
    SQL-Literal im Statement-Text -- diese Instanz hat
    `log_min_error_statement = error`, und jede fehlschlagende Anweisung
    (z.B. eine `referenz_name`-Kollision) landete darum wortwoertlich in
    `docker logs <supabase-db>` (gemessen: 37 Treffer nach einer einzigen
    absichtlich ausgeloesten Kollision). `log_parameter_max_length_on_error
    = 0` auf dieser Instanz wuerde echte gebundene Parameter schuetzen,
    aber wir haben keinen nativen Treiber -- nur `docker exec ... psql`
    (Muster spaces/marketing/sync/_db.py). Der Wert reist darum stattdessen
    ueber `COPY ... FROM STDIN`: COPY-Nutzdaten sind KEIN Statement-Text,
    sie laufen als eigener Protokoll-Stream und tauchen in der
    `STATEMENT:`-Logzeile eines Fehlers nicht auf (bewiesen in
    tests/test_ablage.py::test_wert_landet_nie_im_postgres_server_log).
    Eine temporaere Tabelle nimmt die Kopierten Daten auf; `vault.create_secret`
    liest `wert` aus einer Spalte dieser Tabelle (`t.wert`), nie aus einem
    Literal. Alles in EINER expliziten Transaktion, damit ein scheiternder
    INSERT (referenz_name-Kollision) das ganze Statement inkl. des bereits
    erzeugten Vault-Secrets zurueckrollt -- exakt dieselbe Atomaritaets-
    Zusicherung wie vorher, nur ohne den Wert im Statement-Text.
    """
    if art not in _ART_ERLAUBT:
        return {"ok": False, "fehler": f"unbekannte art: {art!r} (erlaubt: {sorted(_ART_ERLAUBT)})"}
    if not _REFERENZ_MUSTER.match(referenz or ""):
        return {"ok": False, "fehler": "referenz ungueltig (erwartet ^[A-Za-z_][A-Za-z0-9_]{0,127}$)"}
    if not projekt or not plugin:
        return {"ok": False, "fehler": "projekt und plugin sind Pflicht"}
    if not wert:
        return {"ok": False, "fehler": "wert fehlt"}
    if "\x00" in wert:
        return {"ok": False, "fehler": "wert enthaelt ein NUL-Byte, nicht speicherbar"}

    secret_name = f"plugin-setup-{referenz}-{uuid.uuid4().hex[:8]}"
    sql = (
        "BEGIN;\n"
        "CREATE TEMP TABLE _psu_wert (wert text) ON COMMIT DROP;\n"
        "COPY _psu_wert (wert) FROM STDIN WITH (FORMAT csv, QUOTE '\"');\n"
        f"{_copy_csv_feld(wert)}\n"
        "\\.\n"
        "WITH v AS (\n"
        "  SELECT vault.create_secret(t.wert, "
        f"{_sql_literal(secret_name)}, {_sql_literal('plugin-setup: ' + referenz)}) AS id\n"
        "  FROM _psu_wert t\n"
        ")\n"
        "INSERT INTO plugin_setup.einrichtungen "
        "(projekt_id, plugin, referenz_name, art, vault_secret_id)\n"
        f"SELECT {_sql_literal(projekt)}, {_sql_literal(plugin)}, "
        f"{_sql_literal(referenz)}, {_sql_literal(art)}, v.id FROM v;\n"
        "COMMIT;\n"
    )
    try:
        rc, out = _psql(sql)
    except Exception as e:  # noqa: BLE001 -- fail-soft ist der Vertrag
        return {"ok": False, "fehler": _ohne_wert(
            f"Supabase nicht erreichbar ({type(e).__name__}: {e})", wert)}
    if rc != 0:
        return {"ok": False, "fehler": _ohne_wert(
            f"Ablage fehlgeschlagen: {out.strip()[:300]}", wert)}
    return {"ok": True, "referenz": referenz}


def _uebergang(fn_sql: str, wert_zum_scrubben: str = "") -> dict:
    try:
        rc, out = _psql(fn_sql)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "fehler": _ohne_wert(
            f"Supabase nicht erreichbar ({type(e).__name__}: {e})", wert_zum_scrubben)}
    if rc != 0:
        return {"ok": False, "fehler": _ohne_wert(out.strip()[:300], wert_zum_scrubben)}
    return {"ok": True}


def verifizieren(referenz: str) -> dict:
    """Uebergang `entgegengenommen` -> `verifiziert` (einziger sanktionierter
    Weg, plugin_setup.verifizieren() selbst waecht ueber den Ausgangszustand)."""
    if not _REFERENZ_MUSTER.match(referenz or ""):
        return {"ok": False, "fehler": "referenz ungueltig"}
    return _uebergang(f"SELECT plugin_setup.verifizieren({_sql_literal(referenz)});")


def fehlschlagen(referenz: str, hinweis: str) -> dict:
    """Uebergang `entgegengenommen` -> `fehlgeschlagen`. `hinweis` ist fuer
    einen Statuscode/eine kurze Ursache gedacht -- NIE einen Wert oder
    Antwortkoerper; diese Datei erzwingt das nicht inhaltlich (das ist die
    Disziplin des Aufrufers, wie bei jedem anderen Schreibpfad), kappt aber
    defensiv die Laenge."""
    if not _REFERENZ_MUSTER.match(referenz or ""):
        return {"ok": False, "fehler": "referenz ungueltig"}
    hinweis = (hinweis or "")[:_HINWEIS_MAXLAENGE]
    return _uebergang(
        f"SELECT plugin_setup.fehlschlagen({_sql_literal(referenz)}, {_sql_literal(hinweis)});")


def uebernommen(referenz: str) -> dict:
    """Uebergang `verifiziert` -> `uebernommen`: loescht das Vault-Secret,
    setzt vault_secret_id NULL. Der einzige Weg, wie ein Wert wieder
    verschwindet -- danach gibt es genau eine Verwahrstelle (OpenFang)."""
    if not _REFERENZ_MUSTER.match(referenz or ""):
        return {"ok": False, "fehler": "referenz ungueltig"}
    return _uebergang(f"SELECT plugin_setup.uebernommen({_sql_literal(referenz)});")

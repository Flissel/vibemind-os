"""Aufgabe 6 -- Supabase-Anbindung: der Zustandsautomat aus Aufgabe 4
(`db/0001_plugin_setup.sql`, `db/0002_state_machine.sql`) als Python-
Schnittstelle fuer den plugin-setup-Agenten.

ROLLE (Review-Vorgabe #3). KORREKTUR (Schluss-Review, nachgemessen
11.09.2026 gegen die laufende vibemind_supabase-db): eine fruehere Fassung
dieses Absatzes behauptete als "gemessen", `service_role` koenne den in
0002_state_machine.sql erzwungenen Automaten per rohem
`UPDATE plugin_setup.einrichtungen SET status = ...` umgehen. Das ist
FALSCH. Tatsaechlich gemessen (has_table_privilege auf
plugin_setup.einrichtungen):

    service_role   : update=f  canlogin=f  super=f  bypassrls=t
    supabase_admin : update=t  canlogin=t  super=t
    postgres       : update=t  (Eigentuemer der Tabelle)  bypassrls=t

`rolbypassrls=t` hebt nur die RLS auf, nicht das fehlende Tabellenrecht --
`service_role` hat auf dieser Tabelle ueberhaupt kein UPDATE (und ist mit
rolcanlogin=f nicht einmal direkt verbindbar). Einen rohen UPDATE koennen
also NUR der Tabelleneigentuemer (`postgres`) und der Superuser
(`supabase_admin`) -- DBA-Zugriff, eine andere und deutlich engere
Risikoklasse als behauptet. Der Grund, sich hier trotzdem nicht als eine
dieser Rollen zu verbinden, bleibt bestehen (geringste Rechte, und der
Automat soll nicht davon abhaengen, dass niemand DBA-Zugriff missbraucht);
nur die Behauptung ueber `service_role` war eine falsche Messaussage.

Diese Datei verbindet sich darum NIE als eine der drei; sie nutzt
ausschliesslich die eigens angelegte Rolle
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
in EINER expliziten Transaktion an (COPY der base64-kodierten `wert` in eine
Temp-Tabelle, ein DO-Block-Guard, dann CTE `WITH v AS (SELECT
vault.create_secret(...)) INSERT ... SELECT ... FROM v`). Scheitert der
INSERT (z.B. `referenz_name` schon vergeben), rollt Postgres das ganze
Statement zurueck -- inklusive des Vault-Secrets aus der CTE. Kein
verwaister Vault-Eintrag bei einer Kollision. Details zum Transportweg
(base64 statt CSV, und warum) stehen bei `entgegennehmen()` selbst.
"""
from __future__ import annotations

import base64
import os
import re
import subprocess
import uuid
from typing import Optional

_ART_ERLAUBT = {"bearer", "oauth", "connector"}
_REFERENZ_MUSTER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_HINWEIS_MAXLAENGE = 200
_FEHLER_MAXLAENGE = 200

# Steuerzeichen, die in `wert` weiterhin erlaubt sind (C1-Runde-2, Punkt c):
# Tab/LF/CR, weil sie in mehrzeiligen Werten wie PEM-Schluesseln legitim
# vorkommen (gemessen: mehrzeilige PEM-artige Werte, Anfuehrungszeichen,
# Kommas, Tabs und ein eingebettetes "\." speichern nach dem Base64-Fix
# alle korrekt). Alles andere im C0-Bereich plus DEL ist verdaechtig genug,
# um fail-closed abzulehnen, statt es unbesehen zu transportieren.
_ERLAUBTE_STEUERZEICHEN = {"\t", "\n", "\r"}

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


def _hat_unzulaessige_steuerzeichen(s: str) -> bool:
    return any((ord(c) < 0x20 or ord(c) == 0x7F) and c not in _ERLAUBTE_STEUERZEICHEN for c in s)


def _erste_fehlerzeile(out: str) -> str:
    """Die Meldungszeile einer psql-Ausgabe -- und NICHTS sonst.

    Warum nicht einfach die erste nichtleere Zeile: `_psql` gibt
    `stdout + stderr` zurueck, und psql schreibt bei `-tA` die
    Kommando-Tags jeder Anweisung (`BEGIN`, `CREATE TABLE`, `COPY 1`,
    `DO`, ...) auf STDOUT, waehrend `ERROR:` auf STDERR geht. Die erste
    Zeile ist darum immer `BEGIN` und nie die Diagnose. Eine fruehere
    Fassung behauptete hier, Zeile 1 sei aus strukturellen Gruenden
    sicher; das war doppelt falsch -- sie war nicht die Fehlerzeile, und
    die Begruendung haette einen spaeteren Leser dazu verleitet, darauf
    aufzubauen.

    Also: die erste Zeile nehmen, die mit `ERROR:` oder `FATAL:`
    beginnt. Das IST eine Heuristik ueber das Ausgabeformat, und sie
    wird hier nicht als Sicherheitsgrenze benutzt. Die Sicherheit kommt
    aus zwei anderen Quellen: der Wert reist base64-kodiert und kann
    darum gar keinen Parse-Fehler und also keine `CONTEXT:`-Zeile mit
    Nutzdaten erzeugen (s. `entgegennehmen`), und `_ohne_wert` scrubbt
    als Rueckhalt. Entscheidend fuer diese Funktion ist nur, dass sie
    NUR die Meldungszeile durchlaesst und die ihr folgenden
    `CONTEXT:`/`DETAIL:`/`STATEMENT:`-Zeilen nie mitnimmt.
    """
    for zeile in out.splitlines():
        zeile = zeile.strip()
        if zeile.startswith("ERROR:") or zeile.startswith("FATAL:"):
            return zeile[:_FEHLER_MAXLAENGE]
    for zeile in out.splitlines():
        zeile = zeile.strip()
        if zeile:
            return zeile[:_FEHLER_MAXLAENGE]
    return "(keine Ausgabe)"


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
    absichtlich ausgeloesten Kollision).

    C1-FIX RUNDE 2 (2026-09-11): die erste COPY-Fassung (CSV-Format,
    QUOTE '"') schloss diesen Sink nicht wirklich -- sie ersetzte "wert
    steht im Statement-Text" durch "wert kann eine COPY-Parse-Fehlermeldung
    ausloesen, und DIE landet ebenso im Log". Gemessen: ein `wert`, der
    eine Zeile enthaelt, die exakt `\\.` lautet, laesst den CSV-Parser mit
    "unterminated CSV quoted field" abbrechen, und Postgres' `CONTEXT:`-Zeile
    zu diesem Fehler zitiert das WERT-FRAGMENT woertlich -- in `docker logs`
    UND (weil `entgegennehmen()` frueher die ganze psql-Ausgabe in `fehler`
    spiegelte) im Rueckgabewert dieser Funktion. Zwei Sinks, nicht einer,
    und beide haengen an einer psql-Client-Eigenheit (End-of-Data-Marker
    `\\.`), nicht an echtem CSV-Verhalten (das serverseitig eingebettete
    Zeilenumbrueche in einem gequoteten Feld erlaubt -- nur der *psql-Client*
    scannt zeilenweise nach dem Marker, unabhaengig vom Quoting-Zustand).

    Fix, in drei Teilen:
      (a) TRANSPORT: `wert` wird client-seitig base64-kodiert, per COPY
          (TEXT-Format, keine CSV-Quotierung noetig) uebertragen und
          serverseitig mit `convert_from(decode(t.wert, 'base64'), 'UTF8')`
          zurueckdekodiert, BEVOR er an `vault.create_secret` geht. Der
          Punkt ist NICHT, dass Base64 den Wert verbirgt (Base64 EINES
          Geheimnisses IST das Geheimnis) -- der Punkt ist, dass Base64s
          Alphabet (A-Z a-z 0-9 + /) weder Backslash noch Zeilenumbruch
          noch Anfuehrungszeichen kennt: der COPY-End-of-Data-Marker `\\.`
          kann in einer base64-kodierten Zeile STRUKTURELL nicht auftreten,
          also kann auch keine `CONTEXT:`-Zeile mit einem Wert-Fragment
          mehr entstehen -- unabhaengig davon, was `wert` selbst enthaelt.
      (b) RUECKGABE: `entgegennehmen()` spiegelt bei einem Fehler nicht mehr
          bis zu 300 Zeichen der rohen psql-Ausgabe -- nur noch die ERSTE
          Zeile (psql schreibt `ERROR: ...` immer zuerst, `CONTEXT:`/
          `DETAIL:`/`STATEMENT:` erst danach, s. `_erste_fehlerzeile`), auf
          200 Zeichen gekappt, UND weiterhin durch `_ohne_wert` geschickt --
          als Verteidigung in der Tiefe, nicht als die Garantie selbst.
      (c) EINGABE: Steuerzeichen ausser Tab/LF/CR werden jetzt generell
          abgelehnt (vorher nur NUL) -- fail-soft, bevor irgendetwas an die
          Datenbank geht.

    Eine temporaere Tabelle nimmt die kopierten (weiterhin base64-kodierten)
    Daten auf; ein DO-Block prueft direkt danach, dass GENAU EINE Zeile
    ankam (macht "COPY liefert 0 Zeilen -> INSERT betrifft 0 Zeilen -> Funktion
    meldet trotzdem ok:true" durch Konstruktion unerreichbar, statt sich auf
    einen nie beobachteten Pfad zu verlassen). `vault.create_secret` liest
    den dekodierten Wert aus einer Spalte dieser Tabelle (`t.wert`), nie aus
    einem Literal. Alles in EINER expliziten Transaktion, damit ein
    scheiternder INSERT (referenz_name-Kollision) das ganze Statement inkl.
    des bereits erzeugten Vault-Secrets zurueckrollt.
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
    if _hat_unzulaessige_steuerzeichen(wert):
        return {"ok": False, "fehler": "wert enthaelt ein nicht erlaubtes Steuerzeichen "
                                        "(erlaubt: Tab, LF, CR)"}

    # (a) Base64, EIN Encode-Schritt, EINE Zeile -- kein Zeilenumbruch, kein
    # Backslash, kein Anfuehrungszeichen moeglich, darum keine CSV-Quotierung
    # noetig (COPY im Standard-TEXT-Format reicht).
    wert_b64 = base64.b64encode(wert.encode("utf-8")).decode("ascii")

    secret_name = f"plugin-setup-{referenz}-{uuid.uuid4().hex[:8]}"
    sql = (
        "BEGIN;\n"
        "CREATE TEMP TABLE _psu_wert (wert_b64 text) ON COMMIT DROP;\n"
        "COPY _psu_wert (wert_b64) FROM STDIN;\n"
        f"{wert_b64}\n"
        "\\.\n"
        "DO $psu_check$\n"
        "BEGIN\n"
        "  IF (SELECT count(*) FROM _psu_wert) <> 1 THEN\n"
        "    RAISE EXCEPTION 'plugin-setup: COPY lieferte nicht genau eine Zeile';\n"
        "  END IF;\n"
        "END;\n"
        "$psu_check$;\n"
        "WITH v AS (\n"
        "  SELECT vault.create_secret(convert_from(decode(t.wert_b64, 'base64'), 'UTF8'), "
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
            f"Supabase nicht erreichbar ({type(e).__name__}: {e})", wert, wert_b64)}
    if rc != 0:
        # (b) NUR die erste Zeile, nie die volle Ausgabe -- s. Docstring.
        return {"ok": False, "fehler": _ohne_wert(
            f"Ablage fehlgeschlagen: {_erste_fehlerzeile(out)}", wert, wert_b64)}
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


def zustand(referenz: str) -> dict:
    """Liest Zustand und Hinweis ueber die bewachte Funktion -- die Rolle
    hat kein SELECT auf der Tabelle. Gibt NIE vault_secret_id und nie
    einen Wert zurueck; die Funktion liefert nur zwei Spalten."""
    rc, out = _psql(
        f"SELECT status || '|' || hinweis FROM plugin_setup.zustand({_sql_literal(referenz)});"
    )
    if rc != 0:
        return {"ok": False, "fehler": f"Zustand nicht lesbar: {_erste_fehlerzeile(out)}"}
    zeile = out.strip()
    if "|" not in zeile:
        return {"ok": False, "fehler": "Zustand nicht lesbar: unerwartete Antwortform"}
    status, hinweis = zeile.split("|", 1)
    return {"ok": True, "zustand": status, "hinweis": hinweis}


def neu_aufnehmen(referenz: str) -> dict:
    """Gibt eine fehlgeschlagene Referenz fuer eine neue Aufnahme frei.
    Die Wache sitzt in der Datenbank (nur aus 'fehlgeschlagen')."""
    return _uebergang(f"SELECT plugin_setup.neu_aufnehmen({_sql_literal(referenz)});")

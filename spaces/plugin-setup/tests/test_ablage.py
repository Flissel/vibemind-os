"""Aufgabe 6 -- ablage.py gegen die ECHTE, laufende Supabase (kein Mock,
keine In-Memory-DB; Muster test_eingang.py). Zwei Dinge werden hier bewiesen,
die kein Unit-Test mit gemocktem Callable zeigen kann:

  1. Review-Vorgabe #3: die eigens angelegte Rolle `plugin_setup_agent`
     (db/0003_least_privilege_role.sql) kann den vollen Weg gehen (Aufnahme
     -> verifizieren -> uebernommen; Aufnahme -> fehlschlagen), aber ein
     ROHER `UPDATE plugin_setup.einrichtungen SET status = ...` scheitert
     als diese Rolle -- die Zustandsmaschine wird von der Datenbank
     erzwungen, nicht von der Disziplin dieses Moduls.
  2. ablage.py selbst spricht tatsaechlich diese Rolle (nicht postgres/
     service_role/supabase_admin) und funktioniert Ende-zu-Ende gegen sie.

Regeln, die diese Datei selbst befolgt (Global Constraints im Brief):
  - Keine echten Geheimniswerte, nur ein offensichtlich erfundener String.
  - Kein Wert wird je geprintet oder in eine Assertion-Nachricht gehaengt.
  - Aufraeumen: jeder Test entfernt, was er anlegt (eigene uuid-Praefixe),
    ueber die postgres-Rolle (plugin_setup_agent selbst darf nicht loeschen
    -- genau die Einschraenkung, die dieser Test beweist).
"""
from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ablage  # noqa: E402

_FAKE_WERT = "offensichtlich-erfunden-kein-echtes-secret-ablage"


def _container() -> str:
    return ablage._container()


def _psql_als_postgres(sql: str) -> tuple[int, str]:
    res = subprocess.run(
        ["docker", "exec", "-i", _container(),
         "psql", "-U", "postgres", "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-tA"],
        input=sql, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    return res.returncode, (res.stdout + res.stderr)


def _psql_als_postgres_ok(sql: str) -> str:
    rc, out = _psql_als_postgres(sql)
    assert rc == 0, f"psql (postgres) failed rc={rc} out={out[:500]}"
    return out


def _cleanup(referenz: str) -> None:
    """Best-effort: als postgres, nie als plugin_setup_agent (der darf das
    nicht -- genau der Punkt dieser Datei)."""
    _psql_als_postgres(f"""
        DELETE FROM vault.secrets
         WHERE id IN (
             SELECT vault_secret_id FROM plugin_setup.einrichtungen
              WHERE referenz_name = '{referenz}' AND vault_secret_id IS NOT NULL
         );
        DELETE FROM plugin_setup.einrichtungen WHERE referenz_name = '{referenz}';
    """)


def _docker_logs(container: str) -> list[str]:
    """Der komplette stdout+stderr-Log des Containers, als Zeilenliste --
    Postgres' `log_min_error_statement = error` schreibt eine fehlschlagende
    Anweisung als `STATEMENT:`-Zeile genau dorthin (`docker logs`)."""
    res = subprocess.run(
        ["docker", "logs", container],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    return (res.stdout + res.stderr).splitlines()


def _status_of(referenz: str) -> str:
    return _psql_als_postgres_ok(
        f"SELECT status FROM plugin_setup.einrichtungen WHERE referenz_name = '{referenz}';"
    ).strip()


@pytest.fixture(scope="module", autouse=True)
def _rolle_vorhanden():
    """Voraussetzung fuer die ganze Datei: die Rolle existiert. Legt sie
    NICHT an (das ist db/0003_least_privilege_role.sql's Job, angewandt
    von deploy/bootstrap.sh) -- fehlt sie, ist das ein echter Befund, kein
    Grund, die Probe stillschweigend zu ueberspringen."""
    out = _psql_als_postgres_ok(
        "SELECT count(*) FROM pg_roles WHERE rolname = 'plugin_setup_agent';"
    ).strip()
    if out != "1":
        pytest.fail(
            "Rolle plugin_setup_agent fehlt -- zuerst "
            "db/0003_least_privilege_role.sql anwenden "
            "(docker exec -i <supabase-db-container> psql -U postgres -d postgres "
            "-v ON_ERROR_STOP=1 -f - < db/0003_least_privilege_role.sql)")
    yield


# ─── Review-Vorgabe #3: die Rolle kann ihre Arbeit tun ────────────────────


def test_der_volle_weg_als_plugin_setup_agent_rolle():
    referenz = f"PYTEST_ABLAGE_VOLLWEG_{uuid.uuid4().hex[:8].upper()}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    try:
        aufnahme = ablage.entgegennehmen(projekt, "demo-plugin", referenz, "bearer", _FAKE_WERT)
        assert aufnahme == {"ok": True, "referenz": referenz}
        assert _status_of(referenz) == "entgegengenommen"

        verifiziert = ablage.verifizieren(referenz)
        assert verifiziert == {"ok": True}
        assert _status_of(referenz) == "verifiziert"

        uebernommen = ablage.uebernommen(referenz)
        assert uebernommen == {"ok": True}
        assert _status_of(referenz) == "uebernommen"

        # Die eigentliche Zusicherung: nach der Uebernahme gibt es genau
        # eine Verwahrstelle, und Supabase ist es nicht mehr.
        restlicher_vault_eintrag = _psql_als_postgres_ok(
            "SELECT count(*) FROM plugin_setup.einrichtungen "
            f"WHERE referenz_name = '{referenz}' AND vault_secret_id IS NOT NULL;"
        ).strip()
        assert restlicher_vault_eintrag == "0"
    finally:
        _cleanup(referenz)


def test_fehlschlag_pfad_als_plugin_setup_agent_rolle_laesst_kopie_stehen():
    referenz = f"PYTEST_ABLAGE_FEHLSCHLAG_{uuid.uuid4().hex[:8].upper()}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    try:
        assert ablage.entgegennehmen(projekt, "demo-plugin", referenz, "bearer", _FAKE_WERT)["ok"] is True
        ergebnis = ablage.fehlschlagen(referenz, "401")
        assert ergebnis == {"ok": True}
        assert _status_of(referenz) == "fehlgeschlagen"

        # Kopie bleibt zur Fehlersuche stehen -- vault_secret_id nicht NULL.
        vault_id_vorhanden = _psql_als_postgres_ok(
            "SELECT (vault_secret_id IS NOT NULL)::text FROM plugin_setup.einrichtungen "
            f"WHERE referenz_name = '{referenz}';"
        ).strip()
        assert vault_id_vorhanden == "true"
    finally:
        _cleanup(referenz)


# ─── I2 (Review Runde 1): OpenFang-Ausfall ist retryable, kein Sackgassenzustand ─


def test_verifiziert_ist_kein_sackgassenzustand_wenn_openfang_ausfaellt():
    """(a) Ein Fehlschlag der OpenFang-Uebergabe NACH bestandener
    Verifikation darf die Zeile nicht in `fehlgeschlagen` schieben (0002
    erlaubt das ohnehin nur aus `entgegengenommen`) -- sie bleibt auf
    `verifiziert` stehen, Vault-Kopie intakt. (b) Ein spaeterer Retry
    (`uebernommen()` erneut aufrufen, sobald OpenFang wieder erreichbar
    ist) muss aus genau diesem Zustand heraus gelingen -- das ist die
    eigentliche Zusicherung von I2: der Zustand ist retryable, keine
    Sackgasse."""
    referenz = f"PYTEST_ABLAGE_RETRYABLE_{uuid.uuid4().hex[:8].upper()}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    try:
        assert ablage.entgegennehmen(projekt, "demo-plugin", referenz, "bearer", _FAKE_WERT)["ok"] is True
        assert ablage.verifizieren(referenz) == {"ok": True}

        # (a) Simuliert "OpenFang war nicht erreichbar" -- werkzeuge.py ruft
        # in diesem Fall bewusst weder fehlschlagen() noch uebernommen()
        # auf (s. schluessel_entgegennehmen). Zustand nach der Verifikation,
        # vor der (gescheiterten) Uebergabe:
        assert _status_of(referenz) == "verifiziert"
        vault_id_vorhanden = _psql_als_postgres_ok(
            "SELECT (vault_secret_id IS NOT NULL)::text FROM plugin_setup.einrichtungen "
            f"WHERE referenz_name = '{referenz}';"
        ).strip()
        assert vault_id_vorhanden == "true", "Vault-Kopie muss nach einem OpenFang-Ausfall erhalten bleiben"

        # (b) Der Retry: OpenFang ist jetzt (simuliert) wieder erreichbar,
        # uebernommen() wird aus 'verifiziert' heraus erneut versucht.
        retry = ablage.uebernommen(referenz)
        assert retry == {"ok": True}
        assert _status_of(referenz) == "uebernommen"
    finally:
        _cleanup(referenz)


def test_referenz_kollision_hinterlaesst_keinen_verwaisten_vault_eintrag():
    """Atomaritaet: scheitert der INSERT (referenz_name schon vergeben),
    darf der Vault-Secret aus derselben CTE nicht uebrig bleiben."""
    referenz = f"PYTEST_ABLAGE_KOLLISION_{uuid.uuid4().hex[:8].upper()}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    try:
        erster = ablage.entgegennehmen(projekt, "demo-plugin", referenz, "bearer", _FAKE_WERT)
        assert erster["ok"] is True

        vor_vault_anzahl = _psql_als_postgres_ok(
            "SELECT count(*) FROM vault.secrets;"
        ).strip()

        zweiter = ablage.entgegennehmen(projekt, "demo-plugin", referenz, "bearer", _FAKE_WERT)
        assert zweiter["ok"] is False
        assert _FAKE_WERT not in zweiter["fehler"]

        nach_vault_anzahl = _psql_als_postgres_ok(
            "SELECT count(*) FROM vault.secrets;"
        ).strip()
        assert nach_vault_anzahl == vor_vault_anzahl, \
            "ein gescheiterter zweiter Versuch darf keinen zusaetzlichen Vault-Eintrag hinterlassen"

        # Es existiert weiterhin genau eine Zeile (die erste).
        anzahl_zeilen = _psql_als_postgres_ok(
            f"SELECT count(*) FROM plugin_setup.einrichtungen WHERE referenz_name = '{referenz}';"
        ).strip()
        assert anzahl_zeilen == "1"
    finally:
        _cleanup(referenz)


# ─── C1 (Review Runde 1): wert landet nie im Postgres-Server-Log ─────────


def test_wert_landet_nie_im_postgres_server_log():
    """Die eigentliche Zusicherung von C1: `log_min_error_statement = error`
    ist auf dieser Instanz aktiv (gemessen), und eine referenz_name-
    Kollision loest garantiert einen Fehler aus -- genau der Pfad, ueber
    den der Wert vorher (als SQL-Literal im Statement-Text) 37-fach in
    `docker logs` auftauchte. Nach dem Fix (COPY statt Literal) duerfen es
    ZERO Treffer sein, in genau den Log-Zeilen, die dieser Testlauf selbst
    erzeugt hat (nicht die gesamte Container-Historie -- die kann von
    fremden Sessions stammen)."""
    referenz = f"PYTEST_ABLAGE_LOGLECK_{uuid.uuid4().hex[:8].upper()}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    container = _container()
    try:
        vor = len(_docker_logs(container))

        erster = ablage.entgegennehmen(projekt, "demo-plugin", referenz, "bearer", _FAKE_WERT)
        assert erster["ok"] is True
        # Die Kollision -- derselbe Pfad, der vor dem Fix 37 Treffer erzeugte.
        zweiter = ablage.entgegennehmen(projekt, "demo-plugin", referenz, "bearer", _FAKE_WERT)
        assert zweiter["ok"] is False

        neue_zeilen = _docker_logs(container)[vor:]
        treffer = [z for z in neue_zeilen if _FAKE_WERT in z]
        assert treffer == [], (
            f"{len(treffer)} Log-Zeile(n) dieses Testlaufs enthalten den Testwert -- "
            "C1 waere nicht behoben")
    finally:
        _cleanup(referenz)


# ─── Review-Vorgabe #3: ein roher UPDATE scheitert als diese Rolle ────────


def test_rohes_update_scheitert_als_agent_rolle():
    """Die eigentliche Zusicherung von Review-Vorgabe #3: anders als
    postgres/service_role/supabase_admin (die RLS strukturell umgehen)
    kann plugin_setup_agent den Zustandsautomaten NICHT per rohem UPDATE
    umgehen -- weder mit noch ohne WHERE-Klausel."""
    referenz = f"PYTEST_ABLAGE_ROHUPDATE_{uuid.uuid4().hex[:8].upper()}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    try:
        assert ablage.entgegennehmen(projekt, "demo-plugin", referenz, "bearer", _FAKE_WERT)["ok"] is True

        rc, out = ablage._psql(
            f"UPDATE plugin_setup.einrichtungen SET status = 'uebernommen' "
            f"WHERE referenz_name = '{referenz}';")
        assert rc != 0, "roher UPDATE mit WHERE-Klausel muss als plugin_setup_agent scheitern"
        assert "permission denied" in out.lower()

        rc2, out2 = ablage._psql("UPDATE plugin_setup.einrichtungen SET status = 'uebernommen';")
        assert rc2 != 0, "roher UPDATE ohne WHERE-Klausel muss als plugin_setup_agent ebenso scheitern"
        assert "permission denied" in out2.lower()

        # Kein Teilzustand: die Zeile steht unveraendert auf entgegengenommen.
        assert _status_of(referenz) == "entgegengenommen"
    finally:
        _cleanup(referenz)


def test_rolle_kann_keine_zeile_an_terminalem_status_faelschen():
    """I1 (Review Runde 1): vor 0004_least_privilege_role_hardening.sql
    erlaubten das tabellenweite INSERT-Recht plus `WITH CHECK (true)`
    einen INSERT, der `status` direkt auf `'uebernommen'` setzt -- eine
    gefaelschte Audit-Zeile, die eine nie stattgefundene Uebergabe
    behauptet. Nach dem Fix (spaltengenaues INSERT-Recht + verschaerfte
    Policy) muss das scheitern, egal ob `status` oder einer der vier
    `*_am`-Zeitstempel bzw. `hinweis` explizit gesetzt wird."""
    referenz = f"PYTEST_ABLAGE_FAELSCHUNG_{uuid.uuid4().hex[:8].upper()}"
    try:
        rc, out = ablage._psql(
            "INSERT INTO plugin_setup.einrichtungen "
            "(projekt_id, plugin, referenz_name, art, vault_secret_id, status) "
            f"VALUES ('pytest-projekt', 'demo-plugin', '{referenz}', 'bearer', NULL, 'uebernommen');")
        assert rc != 0, "eine gefaelschte Terminal-Zeile (status='uebernommen') muss scheitern"
        assert "permission denied" in out.lower()

        rc2, out2 = ablage._psql(
            "INSERT INTO plugin_setup.einrichtungen "
            "(projekt_id, plugin, referenz_name, art, vault_secret_id, verifiziert_am) "
            f"VALUES ('pytest-projekt', 'demo-plugin', '{referenz}', 'bearer', NULL, now());")
        assert rc2 != 0, "ein vorgetaeuschter verifiziert_am-Zeitstempel muss ebenso scheitern"
        assert "permission denied" in out2.lower()

        # Kein Teilzustand: keine Zeile wurde angelegt.
        anzahl = _psql_als_postgres_ok(
            f"SELECT count(*) FROM plugin_setup.einrichtungen WHERE referenz_name = '{referenz}';"
        ).strip()
        assert anzahl == "0"
    finally:
        _cleanup(referenz)


def test_rolle_kann_die_tabelle_nicht_lesen():
    """plugin_setup_agent bekommt bewusst kein SELECT (s. ablage.py-Moduldoku)
    -- der Agent liest den Zustand nie zurueck, nur die drei Funktionen
    aendern ihn. Ein roher SELECT muss also ebenso scheitern wie der UPDATE."""
    rc, out = ablage._psql("SELECT count(*) FROM plugin_setup.einrichtungen;")
    assert rc != 0
    assert "permission denied" in out.lower()


# ─── Eingabevalidierung bleibt lokal fail-closed (keine DB-Rundreise) ─────


def test_unbekannte_art_wird_vor_der_db_abgelehnt():
    ergebnis = ablage.entgegennehmen("proj", "demo-plugin", "PYTEST_X", "unbekannt", _FAKE_WERT)
    assert ergebnis == {"ok": False, "fehler": (
        "unbekannte art: 'unbekannt' (erlaubt: ['bearer', 'connector', 'oauth'])")}


def test_referenz_mit_verbotenen_zeichen_wird_abgelehnt():
    ergebnis = ablage.entgegennehmen("proj", "demo-plugin", "nicht gueltig!", "bearer", _FAKE_WERT)
    assert ergebnis["ok"] is False
    assert _FAKE_WERT not in ergebnis["fehler"]

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

import hashlib
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


def _docker_since_timestamp(container: str) -> str:
    """RFC3339 (Sekundengenauigkeit) 'jetzt', gemessen IM Container (nicht
    auf dem Host) -- vermeidet Uhrenversatz zwischen beiden. Review Runde 2:
    die vorherige Fassung zaehlte Log-Zeilen vor/nach und schnitt per Index
    -- unter Log-Rotation/-Truncation waere das eine STILLE FALSCH-GRUEN-
    Quelle (der Index zeigt dann auf die falschen Zeilen oder ins Leere).
    `docker logs --since` filtert stattdessen nach dem Zeitstempel jedes
    einzelnen Log-Eintrags -- Rotation/Truncation aendern daran nichts."""
    res = subprocess.run(
        ["docker", "exec", "-i", container, "date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=True, timeout=15,
    )
    return res.stdout.strip()


def _docker_logs_since(container: str, since: str) -> list[str]:
    """Der stdout+stderr-Log des Containers AB `since` (RFC3339), als
    Zeilenliste -- Postgres' `log_min_error_statement = error` schreibt
    eine fehlschlagende Anweisung als `ERROR:`/`STATEMENT:`/`CONTEXT:`-
    Zeilen genau dorthin (`docker logs`)."""
    res = subprocess.run(
        ["docker", "logs", "--since", since, container],
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
    `docker logs` auftauchte. Nach dem Fix duerfen es ZERO Treffer sein, in
    genau dem Zeitfenster, das dieser Testlauf selbst erzeugt hat (per
    `docker logs --since`, nicht per Zeilen-Index -- s. `_docker_since_timestamp`).

    Positiv-Kontrolle (Review Runde 2, "zero-control window"): ein leeres
    Treffer-Ergebnis ist nur dann aussagekraeftig, wenn das Fenster
    ueberhaupt etwas enthielt -- sonst waere ein STILLER Filterdefekt (z.B.
    `--since` liefert versehentlich nichts) nicht von einem echten "kein
    Leck" zu unterscheiden. Die Kollision loest GARANTIERT eine echte
    ERROR-Zeile aus; die muss im `--since`-Fenster auftauchen, sonst ist
    der Test selbst kaputt, nicht (notwendigerweise) der Fix."""
    referenz = f"PYTEST_ABLAGE_LOGLECK_{uuid.uuid4().hex[:8].upper()}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    container = _container()
    try:
        seit = _docker_since_timestamp(container)

        erster = ablage.entgegennehmen(projekt, "demo-plugin", referenz, "bearer", _FAKE_WERT)
        assert erster["ok"] is True
        # Die Kollision -- derselbe Pfad, der vor dem Fix 37 Treffer erzeugte.
        zweiter = ablage.entgegennehmen(projekt, "demo-plugin", referenz, "bearer", _FAKE_WERT)
        assert zweiter["ok"] is False

        neue_zeilen = _docker_logs_since(container, seit)

        # Positiv-Kontrolle: das Fenster muss die echte Kollisions-Fehlerzeile
        # enthalten -- sonst ist unklar, ob "kein Treffer" unten "kein Leck"
        # oder "--since hat nichts gefunden" bedeutet.
        assert any("ERROR" in z for z in neue_zeilen), (
            "kein 'ERROR' im --since-Fenster gefunden -- entweder loest die "
            "Kollision keinen Log-Eintrag mehr aus, oder das --since-Fenster "
            "selbst ist kaputt; ohne diese Kontrolle waere ein leeres "
            "Treffer-Ergebnis unten nicht von einem defekten Filter zu "
            "unterscheiden")

        treffer = [z for z in neue_zeilen if _FAKE_WERT in z]
        assert treffer == [], (
            f"{len(treffer)} Log-Zeile(n) dieses Testlaufs enthalten den Testwert -- "
            "C1 waere nicht behoben")
    finally:
        _cleanup(referenz)


def test_wert_mit_alleinstehender_copy_endezeile_speichert_und_leckt_nicht():
    """C1-Runde-2: `\\.` als eigene Zeile im Wert liess den psql-Client den
    CSV-COPY-Datenstrom vorzeitig fuer beendet halten (`unterminated CSV
    quoted field`) -- die `CONTEXT:`-Fehlerzeile zitierte dabei ein
    Wert-Fragment woertlich, sowohl in `docker logs` als auch im
    Rueckgabewert von `entgegennehmen()` (der die rohe psql-Ausgabe
    spiegelte). Nach dem Base64-Fix (COPY traegt nur das base64-Alphabet,
    das `\\.` und CSV-Quotierung strukturell nicht kennt) muss genau dieser
    Wert (a) erfolgreich gespeichert werden -- Rundreise ueber den Vault
    beweist das -- und (b) in keiner Log-Zeile des eigenen Testfensters
    auftauchen, auch nicht in Fragmenten."""
    wert = "zeile-eins-mit-geheimnis-XYZQWERTY\n\\.\nzeile-zwei"
    referenz = f"PYTEST_ABLAGE_BACKSLASHPUNKT_{uuid.uuid4().hex[:8].upper()}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    container = _container()
    try:
        seit = _docker_since_timestamp(container)

        ergebnis = ablage.entgegennehmen(projekt, "demo-plugin", referenz, "bearer", wert)
        assert ergebnis == {"ok": True, "referenz": referenz}

        # (a) Rundreise: der Wert liegt tatsaechlich unveraendert im Vault
        # (als postgres gelesen -- plugin_setup_agent selbst darf das nicht,
        # s. test_rolle_kann_die_tabelle_nicht_lesen).
        # psql -tA haengt an die Ausgabe genau EINEN abschliessenden
        # Zeilenumbruch an (nicht Teil des gespeicherten Werts) -- hier
        # bewusst per [:-1] statt .strip() entfernt, damit ein absichtlich
        # im Wert enthaltener fuehrender/nachgestellter Leerraum (Teil
        # dieses Tests: der Wert endet auf "zeile-zwei", kein Newline) nicht
        # mit-weggeschnitten wird.
        gespeichert = _psql_als_postgres_ok(
            "SELECT decrypted_secret FROM vault.decrypted_secrets ds "
            "JOIN plugin_setup.einrichtungen e ON e.vault_secret_id = ds.id "
            f"WHERE e.referenz_name = '{referenz}';"
        )
        if gespeichert.endswith("\n"):
            gespeichert = gespeichert[:-1]
        assert gespeichert == wert

        # (b) keine Log-Zeile des eigenen Fensters enthaelt ein Fragment des
        # Werts (das laengste unveraenderte Teilstueck reicht als Nachweis).
        # Die Positiv-Kontrolle fuer den --since-Mechanismus selbst steht
        # bereits in test_wert_landet_nie_im_postgres_server_log -- dieser
        # Test loest bewusst KEINEN Fehler aus (der Wert soll erfolgreich
        # gespeichert werden), ein leeres Fenster ist hier der Normalfall.
        neue_zeilen = _docker_logs_since(container, seit)
        fragment = "zeile-eins-mit-geheimnis-XYZQWERTY"
        treffer = [z for z in neue_zeilen if fragment in z]
        assert treffer == [], (
            f"{len(treffer)} Log-Zeile(n) enthalten ein Fragment des Werts -- "
            "C1-Runde-2 waere nicht behoben")
    finally:
        _cleanup(referenz)


# ─── Review-Vorgabe #3: ein roher UPDATE scheitert als diese Rolle ────────


def test_rohes_update_scheitert_als_agent_rolle():
    """Die eigentliche Zusicherung von Review-Vorgabe #3: plugin_setup_agent
    kann den Zustandsautomaten NICHT per rohem UPDATE umgehen -- weder mit
    noch ohne WHERE-Klausel.

    KORREKTUR (Schluss-Review, nachgemessen 11.09.2026): hier stand vorher
    "anders als postgres/service_role/supabase_admin (die RLS strukturell
    umgehen)". Fuer `service_role` stimmt das nicht -- rolbypassrls=t hebt
    nur die RLS auf, und auf dieser Tabelle hat die Rolle gar kein UPDATE
    (has_table_privilege=f, dazu rolcanlogin=f). Rohes UPDATE koennen nur
    der Tabelleneigentuemer `postgres` und der Superuser `supabase_admin`."""
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


# ─── Die Waechter, die Runde 2 einbaute, aber nicht festhielt ────────────
#
# Beide Luecken sind derselbe Fehlertyp wie I3 aus Runde 1: der Code tut das
# Richtige, aber kein Test faellt, wenn man ihn entfernt. Ein ungepinnter
# Waechter ist ein Waechter auf Abruf.


@pytest.mark.parametrize("steuerzeichen", ["\x0b", "\x1b", "\x7f", "\x01"])
def test_steuerzeichen_im_wert_werden_abgelehnt(steuerzeichen):
    """Steuerzeichen ausser Tab/LF/CR werden vor jedem Schreibvorgang
    abgelehnt. Entfernt man die Pruefung in ablage.entgegennehmen, laeuft
    der Wert in die Datenbank und dieser Test wird rot."""
    referenz = f"PYTEST_CTRL_{uuid.uuid4().hex[:8].upper()}"
    try:
        ergebnis = ablage.entgegennehmen(
            f"pytest-projekt-{uuid.uuid4().hex[:8]}", "demo-plugin",
            referenz, "bearer", f"erfunden{steuerzeichen}wert",
        )
        assert ergebnis["ok"] is False
        # Nichts darf geschrieben worden sein -- fail closed heisst: VOR der DB.
        assert _psql_als_postgres_ok(
            f"SELECT count(*) FROM plugin_setup.einrichtungen WHERE referenz_name = '{referenz}';"
        ).strip() == "0"
    finally:
        _cleanup(referenz)


@pytest.mark.parametrize("zeichen,name", [("\t", "Tab"), ("\r\n", "CRLF"), ("\n", "LF")])
def test_mehrzeilige_werte_bleiben_erlaubt(zeichen, name):
    """Die Gegenprobe zum Test darueber: Tab, CR und LF sind ausdruecklich
    erlaubt, sonst waere ein PEM-Schluessel nicht ablegbar. Zieht jemand
    die Steuerzeichen-Pruefung zu weit, wird dieser Test rot."""
    referenz = f"PYTEST_MEHRZEIL_{uuid.uuid4().hex[:8].upper()}"
    wert = f"erfunden-oben{zeichen}erfunden-unten"
    try:
        ergebnis = ablage.entgegennehmen(
            f"pytest-projekt-{uuid.uuid4().hex[:8]}", "demo-plugin",
            referenz, "bearer", wert,
        )
        assert ergebnis["ok"] is True, f"{name} muss erlaubt bleiben"
        # Serverseitig per md5 vergleichen, NICHT ueber die Textausgabe:
        # ein Textrundlauf ueber `_psql`/`_psql_als_postgres` ist nicht
        # CR-exakt, ein CRLF-Wert saehe darum beschaedigt aus. Ist er nicht --
        # er liegt byteidentisch im Vault, nur der Rueckweg luegt.
        # Schuld ist NICHT psql: dessen Rohausgabe behaelt das CR-Byte
        # (nachgestellt). Es ist Pythons `subprocess.run(..., text=True)`,
        # das beim Dekodieren universal newlines anwendet und CRLF zu LF
        # macht. Eine fruehere Fassung dieses Kommentars schob es auf
        # `psql -tA`; richtige Folgerung, falscher Mechanismus -- und genau
        # so ein Satz wird spaeter als Grundlage zitiert.
        # md5 im Server umgeht die Textschicht ganz; die Servercodierung ist
        # UTF8, also rechnen beide Seiten ueber dieselben Bytes.
        in_der_db = _psql_als_postgres_ok(
            "SELECT md5(decrypted_secret) FROM vault.decrypted_secrets WHERE id = "
            f"(SELECT vault_secret_id FROM plugin_setup.einrichtungen WHERE referenz_name = '{referenz}');"
        ).strip()
        assert in_der_db == hashlib.md5(wert.encode("utf-8")).hexdigest(), \
            "der Wert muss byteidentisch im Vault liegen"
    finally:
        _cleanup(referenz)


def test_fehlermeldung_nennt_den_fehler_und_nur_die_meldungszeile():
    """Zwei Zusicherungen an einem erzwungenen Fehler:

    1. Die Meldung nennt den tatsaechlichen Fehler. psql schreibt bei -tA die
       Kommando-Tags (BEGIN, CREATE TABLE, COPY 1, DO) auf stdout und ERROR:
       auf stderr, und `_psql` gibt beides zusammen zurueck -- wer schlicht
       die erste nichtleere Zeile nimmt, meldet bei JEDEM Fehler "BEGIN".
    2. Die Meldung traegt NUR die Meldungszeile. CONTEXT:/DETAIL:/STATEMENT:
       folgen ihr und koennten Nutzdaten-Fragmente tragen -- genau der
       zweite Abfluss aus der C1-Runde-2.
    """
    referenz = f"PYTEST_FEHLTEXT_{uuid.uuid4().hex[:8].upper()}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    try:
        assert ablage.entgegennehmen(projekt, "demo-plugin", referenz, "bearer", _FAKE_WERT)["ok"] is True
        zweiter = ablage.entgegennehmen(projekt, "demo-plugin", referenz, "bearer", _FAKE_WERT)
        assert zweiter["ok"] is False

        fehler = zweiter["fehler"]
        assert "ERROR:" in fehler, f"die Diagnose muss den Fehler nennen, nicht ein Kommando-Tag: {fehler!r}"
        assert "BEGIN" not in fehler, f"Kommando-Tag statt Fehlermeldung: {fehler!r}"
        for verboten in ("CONTEXT:", "DETAIL:", "STATEMENT:"):
            assert verboten not in fehler, f"{verboten} darf den Aufrufer nie erreichen: {fehler!r}"
        assert _FAKE_WERT not in fehler
    finally:
        _cleanup(referenz)

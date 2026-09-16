"""Aufgabe 4 — der Eingang in Supabase: Wert verschlüsselt im Vault,
Zustand daneben, niemals ein Wert in einer Zustandsspalte. Plus die
Review-Fix-Runde: der Zustandsautomat (entgegengenommen -> verifiziert ->
uebernommen, oder entgegengenommen -> fehlgeschlagen) wird in der
Datenbank erzwungen, nicht nur von der Disziplin des Aufrufers erwartet.

Alles hier spricht gegen die ECHTE, laufende Supabase (`docker exec ...
psql`) — kein Mock, keine In-Memory-DB. Siehe
docs/superpowers/specs/2026-09-08-plugin-setup-agent.md (D2, "Der Weg
eines Schluessels", und D3/D4 fuer den Automaten) fuer das Warum.

Regeln, die diese Datei selbst befolgt (siehe Global Constraints im
Brief):
  - Keine echten Geheimniswerte, nur offensichtlich erfundene Test-Strings.
  - Kein Wert wird je geprintet oder in eine Assertion-Nachricht gehaengt.
  - Aufraeumen: jeder Test entfernt, was er anlegt (eigene UUID-Praefixe).
"""
from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_DB_DIR = _HERE.parents[1] / "db"
_MIGRATION_PATHS = [
    _DB_DIR / "0001_plugin_setup.sql",
    _DB_DIR / "0002_state_machine.sql",
    _DB_DIR / "0005_fenster_oberflaeche.sql",
    # 0006 muss hier mitlaufen: dieses Modul reapplied 0001/0002/0005 vor
    # JEDEM Testlauf (autouse, s. _migrations_applied unten). Ohne 0006 in
    # dieser Liste wuerde die Reapplikation von 0005 (CREATE OR REPLACE
    # FUNCTION neu_aufnehmen) 0006s Fix stillschweigend wieder auf den
    # alten Guard (nur 'fehlgeschlagen') zuruecksetzen, sobald dieses Modul
    # NACH 0006s manueller Anwendung importiert wird -- genau die Falle aus
    # .superpowers/sdd/sackgasse-brief.md ("ein autouse-Fixture, das
    # Migrationen neu anwendet, ueberschreibt eine DB-gepatchte Mutante
    # still"), hier aber gegen den ECHTEN Fix statt gegen eine Testmutante.
    _DB_DIR / "0006_neu_aufnehmen_ab_verifiziert.sql",
]
_DB_USER = "postgres"
_DB_NAME = "postgres"

# Offensichtlich erfundene Test-Werte -- nie ein echtes Secret.
_FAKE_WERT = "offensichtlich-erfunden-kein-echtes-secret"
_FAKE_HINWEIS = "offensichtlich-erfunden-http-401"


def _find_container() -> str:
    res = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=True,
    )
    for name in res.stdout.splitlines():
        name = name.strip()
        if "supabase-db" in name:
            return name
    raise RuntimeError("no supabase-db container running (docker ps | grep supabase-db)")


_CONTAINER: str | None = None


def _container() -> str:
    global _CONTAINER
    if _CONTAINER is None:
        _CONTAINER = _find_container()
    return _CONTAINER


def _psql(sql: str) -> tuple[int, str]:
    """Run `sql` on stdin via psql -tA (unaligned, no header). Never via
    argv -- SQL always travels on stdin, no shell-quoting hazard."""
    res = subprocess.run(
        ["docker", "exec", "-i", _container(),
         "psql", "-U", _DB_USER, "-d", _DB_NAME, "-v", "ON_ERROR_STOP=1", "-tA"],
        input=sql, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    return res.returncode, (res.stdout + res.stderr)


def _psql_ok(sql: str) -> str:
    rc, out = _psql(sql)
    assert rc == 0, f"psql failed rc={rc} out={out[:500]}"
    return out


def _apply_migrations() -> None:
    for path in _MIGRATION_PATHS:
        _psql_ok(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module", autouse=True)
def _migrations_applied():
    """Apply both migrations, in order, before any test runs. This is also
    half of the idempotency proof: this fixture's own apply is
    "application #1" for test_migrations_are_idempotent's "application #2"."""
    _apply_migrations()
    yield


def _cleanup_referenz(referenz: str) -> None:
    """Best-effort cleanup: remove any vault secret + intake row a test
    created, identified only by the test's own uuid-prefixed referenz name
    (never by value)."""
    _psql(f"""
        DELETE FROM vault.secrets
         WHERE id IN (
             SELECT vault_secret_id FROM plugin_setup.einrichtungen
              WHERE referenz_name = '{referenz}' AND vault_secret_id IS NOT NULL
         );
        DELETE FROM plugin_setup.einrichtungen WHERE referenz_name = '{referenz}';
    """)


def _neu_entgegengenommen(referenz: str, projekt: str, secret_name: str) -> str:
    """Insert a fresh 'entgegengenommen' row with a real vault secret
    attached, mirroring how the intake actually happens. Returns the vault
    secret id."""
    vault_id = _psql_ok(
        f"SELECT vault.create_secret('{_FAKE_WERT}', '{secret_name}', 'pytest fixture, harmless');"
    ).strip()
    assert vault_id, "vault.create_secret must return an id"
    _psql_ok(
        "INSERT INTO plugin_setup.einrichtungen "
        "(projekt_id, plugin, referenz_name, art, vault_secret_id) VALUES "
        f"('{projekt}', 'demo-plugin', '{referenz}', 'bearer', '{vault_id}');"
    )
    return vault_id


def _status_of(referenz: str) -> str:
    return _psql_ok(
        f"SELECT status FROM plugin_setup.einrichtungen WHERE referenz_name = '{referenz}';"
    ).strip()


# ─── Schritt 4: Migrationen sind zweimal anwendbar (idempotent) ──────────


def test_migrations_are_idempotent():
    """Applying both migration files a second time, in order, must succeed
    cleanly -- no 'already exists' errors, no duplicated objects, and the
    zeitpunkte-backfill/drop block must be a safe no-op the second time."""
    for path in _MIGRATION_PATHS:
        rc, out = _psql(path.read_text(encoding="utf-8"))
        assert rc == 0, f"second apply of {path.name} failed: {out[:800]}"

    schema_count = _psql_ok(
        "SELECT count(*) FROM pg_namespace WHERE nspname = 'plugin_setup';"
    ).strip()
    assert schema_count == "1", "schema must exist exactly once after two applies"

    fn_names = _psql_ok(
        "SELECT proname FROM pg_proc "
        "WHERE pronamespace = 'plugin_setup'::regnamespace ORDER BY proname;"
    ).strip().splitlines()
    # 0005 (Aufgabe 1, Eingabefenster) fuegt neu_aufnehmen/zustand hinzu --
    # _MIGRATION_PATHS wendet es oben mit an, darum gehoert es auch hier
    # zur erwarteten Menge.
    assert fn_names == ["fehlschlagen", "neu_aufnehmen", "uebernommen", "verifizieren", "zustand"]

    zeitpunkte_gone = _psql_ok(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = 'plugin_setup' AND table_name = 'einrichtungen' "
        "AND column_name = 'zeitpunkte';"
    ).strip()
    assert zeitpunkte_gone == "0", "zeitpunkte must be gone, not merely emptied"


# ─── Schritt 1: die Verbotsliste ───────────────────────────────────────────


def test_einrichtungen_hat_keine_geheimniswert_spalte():
    """einrichtungen darf keine Spalte haben, deren Name auf einen
    Geheimniswert deutet. Die einzige zulaessige Ausnahme ist
    vault_secret_id: ein reiner UUID-Verweis in vault.secrets, kein
    Wertspeicher (D2) -- deshalb wird sie separat geprueft statt
    freigesprochen."""
    out = _psql_ok(
        "SELECT column_name || '|' || data_type FROM information_schema.columns "
        "WHERE table_schema = 'plugin_setup' AND table_name = 'einrichtungen' "
        "ORDER BY ordinal_position;"
    )
    rows = [line.split("|", 1) for line in out.strip().splitlines() if line.strip()]
    assert rows, "einrichtungen must exist and have columns"

    forbidden_substrings = ("value", "secret", "token", "key")
    allowed_pointer_columns = {"vault_secret_id"}

    verdaechtig: list[str] = []
    for column_name, data_type in rows:
        if column_name in allowed_pointer_columns:
            assert data_type == "uuid", (
                f"{column_name} must be a pure uuid pointer, not a value holder"
            )
            continue
        lowered = column_name.lower()
        if any(bad in lowered for bad in forbidden_substrings):
            verdaechtig.append(column_name)

    assert verdaechtig == [], f"Verbotsliste verletzt durch Spalten: {verdaechtig}"


def test_insert_mit_geheimwert_spalte_scheitert():
    """Ein Insert, der versucht, einen Wert direkt in eine Zusatzspalte zu
    schreiben, muss an der Datenbank scheitern (die Spalte existiert
    schlicht nicht) -- nicht nur an Anwendungslogik."""
    referenz = f"pytest-verbot-{uuid.uuid4().hex[:8]}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    try:
        # Der Wert ist fuer diese Zusicherung bedeutungslos -- behauptet wird,
        # dass die SPALTE fehlt. Und dieses Statement MUSS scheitern, also
        # protokolliert Postgres es bei jedem Lauf woertlich
        # (log_min_error_statement = error). Ein geheimnisfoermiger Platzhalter
        # liefe damit garantiert in jedes Containerlog. Der Platzhalter enthaelt
        # ausserdem bewusst nicht "wert", damit die Assertion unten wirklich den
        # Spaltennamen aus der Fehlermeldung prueft und nicht sich selbst.
        rc, out = _psql(
            "INSERT INTO plugin_setup.einrichtungen "
            "(projekt_id, plugin, referenz_name, art, wert) VALUES "
            f"('{projekt}', 'demo-plugin', '{referenz}', 'bearer', 'platzhalter-ohne-bedeutung');"
        )
        assert rc != 0, "insert with a value-shaped extra column must fail"
        assert "wert" in out.lower(), "failure must name the offending column"

        # Fail closed: nothing partial was written.
        leftover = _psql_ok(
            f"SELECT count(*) FROM plugin_setup.einrichtungen WHERE referenz_name = '{referenz}';"
        ).strip()
        assert leftover == "0"
    finally:
        _cleanup_referenz(referenz)


# ─── Der Automat: entgegengenommen -> verifiziert -> uebernommen ─────────


def test_der_volle_weg_entgegengenommen_verifiziert_uebernommen():
    referenz = f"pytest-vollweg-{uuid.uuid4().hex[:8]}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    secret_name = f"pytest-secret-{uuid.uuid4().hex[:8]}"
    try:
        vault_id = _neu_entgegengenommen(referenz, projekt, secret_name)
        assert _status_of(referenz) == "entgegengenommen"

        _psql_ok(f"SELECT plugin_setup.verifizieren('{referenz}');")
        assert _status_of(referenz) == "verifiziert"
        verifiziert_am = _psql_ok(
            f"SELECT (verifiziert_am IS NOT NULL)::text FROM plugin_setup.einrichtungen WHERE referenz_name = '{referenz}';"
        ).strip()
        assert verifiziert_am == "true"

        _psql_ok(f"SELECT plugin_setup.uebernommen('{referenz}');")

        row = _psql_ok(
            "SELECT status || '|' || COALESCE(vault_secret_id::text, 'NULL') || '|' || (uebernommen_am IS NOT NULL)::text "
            f"FROM plugin_setup.einrichtungen WHERE referenz_name = '{referenz}';"
        ).strip()
        status, vault_secret_id_after, hat_uebernommen_am = row.split("|")
        assert status == "uebernommen"
        assert vault_secret_id_after == "NULL"
        assert hat_uebernommen_am == "true"

        # Die eigentliche Zusicherung dieser Aufgabe: nach der Uebernahme
        # gibt es genau eine Verwahrstelle, und das ist nicht mehr Supabase.
        post_by_name = _psql_ok(
            f"SELECT count(*) FROM vault.decrypted_secrets WHERE name = '{secret_name}';"
        ).strip()
        assert post_by_name == "0"
        post_by_id = _psql_ok(
            f"SELECT count(*) FROM vault.secrets WHERE id = '{vault_id}';"
        ).strip()
        assert post_by_id == "0"
    finally:
        _cleanup_referenz(referenz)


def test_uebernommen_lehnt_sprung_aus_entgegengenommen_ab():
    """Review-Fix: uebernommen() darf NICHT direkt aus 'entgegengenommen'
    erreichbar sein -- nur aus 'verifiziert'. Nach der Ablehnung muss das
    Vault-Secret unveraendert vorhanden sein (kein Teilzustand)."""
    referenz = f"pytest-sprung-{uuid.uuid4().hex[:8]}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    secret_name = f"pytest-secret-{uuid.uuid4().hex[:8]}"
    try:
        vault_id = _neu_entgegengenommen(referenz, projekt, secret_name)
        assert _status_of(referenz) == "entgegengenommen"

        rc, out = _psql(f"SELECT plugin_setup.uebernommen('{referenz}');")
        assert rc != 0, "uebernommen() must refuse from 'entgegengenommen'"
        assert "verifiziert" in out.lower()

        # Kein Teilzustand: Status unveraendert, Secret noch da (per id
        # UND per name -- nicht nur der Zeiger, sondern der Vault-Eintrag
        # selbst).
        assert _status_of(referenz) == "entgegengenommen"
        still_by_id = _psql_ok(
            f"SELECT count(*) FROM vault.secrets WHERE id = '{vault_id}';"
        ).strip()
        assert still_by_id == "1"
        still_by_name = _psql_ok(
            f"SELECT count(*) FROM vault.decrypted_secrets WHERE name = '{secret_name}';"
        ).strip()
        assert still_by_name == "1"
    finally:
        _cleanup_referenz(referenz)


def test_uebernommen_lehnt_sprung_aus_fehlgeschlagen_ab():
    """Auch aus 'fehlgeschlagen' darf uebernommen() nicht durchgehen --
    genau der zweite in der Review genannte Fall."""
    referenz = f"pytest-sprungfehl-{uuid.uuid4().hex[:8]}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    secret_name = f"pytest-secret-{uuid.uuid4().hex[:8]}"
    try:
        _neu_entgegengenommen(referenz, projekt, secret_name)
        _psql_ok(f"SELECT plugin_setup.fehlschlagen('{referenz}', '{_FAKE_HINWEIS}');")
        assert _status_of(referenz) == "fehlgeschlagen"

        rc, out = _psql(f"SELECT plugin_setup.uebernommen('{referenz}');")
        assert rc != 0, "uebernommen() must refuse from 'fehlgeschlagen'"
        assert _status_of(referenz) == "fehlgeschlagen"
    finally:
        _cleanup_referenz(referenz)


def test_uebernommen_ist_fail_closed_bei_unbekannter_referenz():
    """Eine nicht ableitbare Referenz endet in einer Ablehnung, nie in
    einem Teilzustand."""
    unbekannt = f"pytest-nie-angelegt-{uuid.uuid4().hex[:8]}"
    rc, out = _psql(f"SELECT plugin_setup.uebernommen('{unbekannt}');")
    assert rc != 0
    assert unbekannt in out


# ─── verifizieren() / fehlschlagen(): die einzigen anderen Ausgaenge ──────


def test_verifizieren_lehnt_falschen_ausgangszustand_ab():
    referenz = f"pytest-verifz-{uuid.uuid4().hex[:8]}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    secret_name = f"pytest-secret-{uuid.uuid4().hex[:8]}"
    try:
        _neu_entgegengenommen(referenz, projekt, secret_name)
        _psql_ok(f"SELECT plugin_setup.verifizieren('{referenz}');")
        assert _status_of(referenz) == "verifiziert"

        # Ein zweiter Aufruf aus 'verifiziert' heraus muss scheitern --
        # nicht aus 'entgegengenommen'.
        rc, out = _psql(f"SELECT plugin_setup.verifizieren('{referenz}');")
        assert rc != 0
        assert "entgegengenommen" in out.lower()
        assert _status_of(referenz) == "verifiziert"
    finally:
        _cleanup_referenz(referenz)


def test_verifizieren_ist_fail_closed_bei_unbekannter_referenz():
    unbekannt = f"pytest-nie-angelegt-{uuid.uuid4().hex[:8]}"
    rc, out = _psql(f"SELECT plugin_setup.verifizieren('{unbekannt}');")
    assert rc != 0
    assert unbekannt in out


def test_fehlschlagen_setzt_status_und_hinweis_ohne_secret_zu_loeschen():
    referenz = f"pytest-fehlschl-{uuid.uuid4().hex[:8]}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    secret_name = f"pytest-secret-{uuid.uuid4().hex[:8]}"
    try:
        vault_id = _neu_entgegengenommen(referenz, projekt, secret_name)
        _psql_ok(f"SELECT plugin_setup.fehlschlagen('{referenz}', '{_FAKE_HINWEIS}');")

        row = _psql_ok(
            "SELECT status || '|' || hinweis || '|' || (fehlgeschlagen_am IS NOT NULL)::text "
            f"FROM plugin_setup.einrichtungen WHERE referenz_name = '{referenz}';"
        ).strip()
        status, hinweis, hat_fehlgeschlagen_am = row.split("|")
        assert status == "fehlgeschlagen"
        assert hinweis == _FAKE_HINWEIS
        assert hat_fehlgeschlagen_am == "true"

        # Der Wert bleibt erhalten -- ein Mensch kann nach Korrektur erneut
        # verifizieren, ohne den Wert neu einzutragen. Nur uebernommen()
        # loescht ihn.
        still_there = _psql_ok(
            f"SELECT count(*) FROM vault.secrets WHERE id = '{vault_id}';"
        ).strip()
        assert still_there == "1"
    finally:
        _cleanup_referenz(referenz)


def test_fehlschlagen_lehnt_falschen_ausgangszustand_ab():
    referenz = f"pytest-fehlschlx-{uuid.uuid4().hex[:8]}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    secret_name = f"pytest-secret-{uuid.uuid4().hex[:8]}"
    try:
        _neu_entgegengenommen(referenz, projekt, secret_name)
        _psql_ok(f"SELECT plugin_setup.verifizieren('{referenz}');")
        assert _status_of(referenz) == "verifiziert"

        rc, out = _psql(f"SELECT plugin_setup.fehlschlagen('{referenz}', '{_FAKE_HINWEIS}');")
        assert rc != 0, "fehlschlagen() must refuse from 'verifiziert'"
        assert _status_of(referenz) == "verifiziert"
    finally:
        _cleanup_referenz(referenz)


def test_fehlschlagen_ist_fail_closed_bei_unbekannter_referenz():
    unbekannt = f"pytest-nie-angelegt-{uuid.uuid4().hex[:8]}"
    rc, out = _psql(f"SELECT plugin_setup.fehlschlagen('{unbekannt}', '{_FAKE_HINWEIS}');")
    assert rc != 0
    assert unbekannt in out


# ─── Zeitstempel: echte Spalten statt jsonb ────────────────────────────────


def test_entgegengenommen_am_wird_bei_insert_gesetzt():
    referenz = f"pytest-zeit-{uuid.uuid4().hex[:8]}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    secret_name = f"pytest-secret-{uuid.uuid4().hex[:8]}"
    try:
        _neu_entgegengenommen(referenz, projekt, secret_name)
        row = _psql_ok(
            "SELECT (entgegengenommen_am IS NOT NULL)::text || '|' "
            "|| (verifiziert_am IS NULL)::text || '|' "
            "|| (uebernommen_am IS NULL)::text || '|' "
            "|| (fehlgeschlagen_am IS NULL)::text "
            f"FROM plugin_setup.einrichtungen WHERE referenz_name = '{referenz}';"
        ).strip()
        hat_entgegengenommen, kein_verifiziert, kein_uebernommen, kein_fehlgeschlagen = row.split("|")
        assert hat_entgegengenommen == "true"
        assert kein_verifiziert == "true"
        assert kein_uebernommen == "true"
        assert kein_fehlgeschlagen == "true"
    finally:
        _cleanup_referenz(referenz)


# ─── RLS: gleiches Muster wie marketing.* ──────────────────────────────────


def test_anon_und_authenticated_sind_ausgesperrt():
    for role in ("anon", "authenticated"):
        rc, out = _psql(f"SET ROLE {role}; SELECT count(*) FROM plugin_setup.einrichtungen;")
        assert rc != 0, f"{role} must not be able to read plugin_setup.einrichtungen"
        assert "permission denied" in out.lower()

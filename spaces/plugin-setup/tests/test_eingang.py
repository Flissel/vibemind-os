"""Aufgabe 4 — der Eingang in Supabase: Wert verschlüsselt im Vault,
Zustand daneben, niemals ein Wert in einer Zustandsspalte.

Alles hier spricht gegen die ECHTE, laufende Supabase (`docker exec ...
psql`) — kein Mock, keine In-Memory-DB. Siehe
docs/superpowers/specs/2026-09-08-plugin-setup-agent.md (D2, "Der Weg
eines Schluessels") fuer das Warum.

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
_MIGRATION_PATH = _HERE.parents[1] / "db" / "0001_plugin_setup.sql"
_DB_USER = "postgres"
_DB_NAME = "postgres"

# Offensichtlich erfundener Test-Wert -- nie ein echtes Secret.
_FAKE_WERT = "offensichtlich-erfunden-kein-echtes-secret"


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


@pytest.fixture(scope="module", autouse=True)
def _migration_applied():
    """Apply the migration before any test runs. Applying it here (rather
    than relying on a pre-applied schema) is also half of the idempotency
    proof: this fixture's own apply is "application #1" for
    test_migration_is_idempotent's "application #2"."""
    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    _psql_ok(sql)
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


# ─── Schritt 4: Migration ist zweimal anwendbar (idempotent) ──────────────


def test_migration_is_idempotent():
    """Applying 0001_plugin_setup.sql a second time must succeed cleanly --
    no 'already exists' errors, no duplicated objects."""
    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    rc, out = _psql(sql)
    assert rc == 0, f"second apply of migration failed: {out[:800]}"

    count = _psql_ok(
        "SELECT count(*) FROM pg_namespace WHERE nspname = 'plugin_setup';"
    ).strip()
    assert count == "1", "schema must exist exactly once after two applies"


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
        rc, out = _psql(
            "INSERT INTO plugin_setup.einrichtungen "
            "(projekt_id, plugin, referenz_name, art, wert) VALUES "
            f"('{projekt}', 'demo-plugin', '{referenz}', 'bearer', '{_FAKE_WERT}');"
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


# ─── Schritt 1 (Fortsetzung): uebernommen() ist der einzige Weg hinaus ────


def test_uebernommen_loescht_vault_secret_und_setzt_status():
    referenz = f"pytest-uebernahme-{uuid.uuid4().hex[:8]}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    secret_name = f"pytest-secret-{uuid.uuid4().hex[:8]}"
    try:
        vault_id = _psql_ok(
            f"SELECT vault.create_secret('{_FAKE_WERT}', '{secret_name}', 'pytest fixture, harmless');"
        ).strip()
        assert vault_id, "vault.create_secret must return an id"

        _psql_ok(
            "INSERT INTO plugin_setup.einrichtungen "
            "(projekt_id, plugin, referenz_name, art, vault_secret_id) VALUES "
            f"('{projekt}', 'demo-plugin', '{referenz}', 'bearer', '{vault_id}');"
        )

        # Precondition: the vault copy is actually there before uebernommen().
        pre = _psql_ok(
            f"SELECT count(*) FROM vault.decrypted_secrets WHERE name = '{secret_name}';"
        ).strip()
        assert pre == "1"

        _psql_ok(f"SELECT plugin_setup.uebernommen('{referenz}');")

        row = _psql_ok(
            "SELECT status || '|' || COALESCE(vault_secret_id::text, 'NULL') "
            f"FROM plugin_setup.einrichtungen WHERE referenz_name = '{referenz}';"
        ).strip()
        status, vault_secret_id_after = row.split("|", 1)
        assert status == "uebernommen"
        assert vault_secret_id_after == "NULL"

        # The actual assertion this task exists for: nothing findable in
        # the vault anymore, by id or by name.
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


def test_uebernommen_ist_fail_closed_bei_unbekannter_referenz():
    """Eine nicht ableitbare Referenz endet in einer Ablehnung, nie in
    einem Teilzustand."""
    unbekannt = f"pytest-nie-angelegt-{uuid.uuid4().hex[:8]}"
    rc, out = _psql(f"SELECT plugin_setup.uebernommen('{unbekannt}');")
    assert rc != 0
    assert unbekannt in out


# ─── RLS: gleiches Muster wie marketing.* ──────────────────────────────────


def test_anon_und_authenticated_sind_ausgesperrt():
    for role in ("anon", "authenticated"):
        rc, out = _psql(f"SET ROLE {role}; SELECT count(*) FROM plugin_setup.einrichtungen;")
        assert rc != 0, f"{role} must not be able to read plugin_setup.einrichtungen"
        assert "permission denied" in out.lower()

-- ============================================================================
-- plugin_setup: der Eingang in Supabase (Aufgabe 4)
-- ============================================================================
-- Wert verschlüsselt im Vault, Zustand daneben, NIEMALS ein Wert in einer
-- Zustandsspalte. Siehe docs/superpowers/specs/2026-09-08-plugin-setup-agent.md
-- (D2, "Der Weg eines Schlüssels") für das Warum.
--
-- Der Schlüssel landet ZUERST hier: Wert in vault.secrets (verschlüsselt),
-- daneben eine Zustandszeile in plugin_setup.einrichtungen. Nach bestandener
-- Verifikation übernimmt OpenFang den Wert in seinen eigenen Vault; die
-- Kopie hier wird über plugin_setup.uebernommen(referenz) gelöscht — der
-- einzige Weg, wie ein Wert wieder verschwindet, und er ist unumkehrbar.
--
-- Muster: spaces/marketing/db/001_marketing_schema.sql (Schema + Tabellen)
-- und spaces/marketing/db/002_rls_baseline.sql (RLS-Lockdown: anon/
-- authenticated bekommen KEINE Grants, RLS wird trotzdem enabled+forced
-- als Verteidigung in der Tiefe — service_role/postgres/supabase_admin
-- umgehen RLS ohnehin via rolbypassrls).
--
-- Apply (lokal):
--   docker exec -i <supabase-db-container> psql -U postgres -d postgres -f -  < 0001_plugin_setup.sql
--
-- Idempotent: zweimal anwendbar (CREATE ... IF NOT EXISTS / CREATE OR
-- REPLACE FUNCTION / REVOKE+ENABLE RLS sind von Natur aus wiederholbar).
--
-- Rollback:
--   DROP FUNCTION IF EXISTS plugin_setup.uebernommen(text);
--   DROP SCHEMA IF EXISTS plugin_setup CASCADE;

BEGIN;

CREATE SCHEMA IF NOT EXISTS plugin_setup;

-- ============================================================================
-- einrichtungen — der Zustand. Der WERT lebt ausschließlich in
-- vault.secrets; vault_secret_id ist ein reiner UUID-Verweis dorthin,
-- kein Wertspeicher. Keine Spalte hier darf je einen Geheimniswert
-- aufnehmen (siehe tests/test_eingang.py::test_einrichtungen_hat_keine_
-- geheimniswert_spalte für die durchgesetzte Verbotsliste).
-- ============================================================================

CREATE TABLE IF NOT EXISTS plugin_setup.einrichtungen (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    projekt_id       text NOT NULL,
    plugin           text NOT NULL,
    referenz_name    text NOT NULL,
    art              text NOT NULL,
    status           text NOT NULL DEFAULT 'entgegengenommen',
    vault_secret_id  uuid,
    eingerichtet_von text NOT NULL DEFAULT 'system',
    zeitpunkte       jsonb NOT NULL DEFAULT jsonb_build_object('entgegengenommen', now()),
    hinweis          text,
    CONSTRAINT einrichtungen_referenz_name_key UNIQUE (referenz_name),
    CONSTRAINT einrichtungen_art_check CHECK (art IN ('bearer', 'oauth', 'connector')),
    CONSTRAINT einrichtungen_status_check CHECK (
        status IN ('entgegengenommen', 'verifiziert', 'uebernommen', 'fehlgeschlagen')
    )
);

CREATE INDEX IF NOT EXISTS idx_einrichtungen_plugin ON plugin_setup.einrichtungen(plugin);
CREATE INDEX IF NOT EXISTS idx_einrichtungen_status ON plugin_setup.einrichtungen(status);

-- ============================================================================
-- RLS-Lockdown (identisch zum marketing.*-Muster): anon/authenticated
-- bekommen KEINE Grants auf Schema, Tabellen, Sequenzen oder Funktionen.
-- RLS wird trotzdem enabled+forced als Verteidigung in der Tiefe — ohne
-- eine einzige permissive Policy sieht selbst eine Rolle MIT Grant null
-- Zeilen.
--
-- KORREKTUR (Schluss-Review, nachgemessen 11.09.2026): eine fruehere Fassung
-- dieser Zeile sagte, "nur bypassrls-Rollen (postgres, service_role,
-- supabase_admin) erreichen diese Tabelle ueberhaupt". `rolbypassrls` allein
-- reicht dafuer nicht -- es hebt die RLS auf, ersetzt aber kein Tabellenrecht.
-- Gemessen erreichen diese Tabelle: `postgres` (Eigentuemer) und
-- `supabase_admin` (Superuser); `service_role` hat trotz rolbypassrls=t weder
-- SELECT noch UPDATE darauf (und rolcanlogin=f). Dazu kommt seit 0003 die
-- Rolle `plugin_setup_agent` -- ausschliesslich INSERT, ueber genau eine
-- INSERT-Policy.
-- ============================================================================

REVOKE ALL ON SCHEMA plugin_setup FROM anon, authenticated, PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA plugin_setup FROM anon, authenticated, PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA plugin_setup FROM anon, authenticated, PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA plugin_setup FROM anon, authenticated, PUBLIC;

ALTER DEFAULT PRIVILEGES IN SCHEMA plugin_setup REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA plugin_setup REVOKE ALL ON SEQUENCES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA plugin_setup REVOKE ALL ON FUNCTIONS FROM anon, authenticated;

ALTER TABLE plugin_setup.einrichtungen ENABLE ROW LEVEL SECURITY;
ALTER TABLE plugin_setup.einrichtungen FORCE ROW LEVEL SECURITY;

-- ============================================================================
-- plugin_setup.uebernommen(referenz) — der EINZIGE Weg, wie ein Wert
-- wieder verschwindet. Löscht das Vault-Secret, setzt vault_secret_id
-- auf NULL, Status auf 'uebernommen'. Unumkehrbar.
--
-- Fail closed: eine unbekannte referenz_name endet in einer Exception,
-- nie in einem Teilzustand — SELECT ... FOR UPDATE findet die Zeile
-- nicht, RAISE EXCEPTION bricht ab, bevor irgendetwas geschrieben wird.
-- SECURITY DEFINER, weil vault.secrets normalerweise nicht offen für
-- Aufrufer dieser Funktion ist; SET search_path verhindert
-- Search-Path-Hijacking der Definer-Rechte.
-- ============================================================================

CREATE OR REPLACE FUNCTION plugin_setup.uebernommen(referenz text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = plugin_setup, vault, pg_temp
AS $fn$
DECLARE
    v_vault_id uuid;
BEGIN
    SELECT vault_secret_id INTO v_vault_id
    FROM plugin_setup.einrichtungen
    WHERE referenz_name = referenz
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'plugin_setup.uebernommen: unbekannte referenz_name %', referenz
            USING ERRCODE = 'no_data_found';
    END IF;

    IF v_vault_id IS NOT NULL THEN
        DELETE FROM vault.secrets WHERE id = v_vault_id;
    END IF;

    UPDATE plugin_setup.einrichtungen
    SET vault_secret_id = NULL,
        status = 'uebernommen',
        zeitpunkte = zeitpunkte || jsonb_build_object('uebernommen', to_jsonb(now()))
    WHERE referenz_name = referenz;
END;
$fn$;

REVOKE ALL ON FUNCTION plugin_setup.uebernommen(text) FROM PUBLIC, anon, authenticated;

COMMIT;

-- ============================================================================
-- Verifikation (manuell):
--   SELECT tablename FROM pg_tables WHERE schemaname = 'plugin_setup';
--   -- erwartet: einrichtungen
--   SELECT relrowsecurity, relforcerowsecurity FROM pg_class
--    WHERE relnamespace = 'plugin_setup'::regnamespace AND relname = 'einrichtungen';
--   -- erwartet: t, t
-- ============================================================================

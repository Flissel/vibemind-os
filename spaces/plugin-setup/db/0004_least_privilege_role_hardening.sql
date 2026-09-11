-- ============================================================================
-- plugin_setup: haertet plugin_setup_agent gegen eine gefaelschte
-- Terminalzeile (Aufgabe 6, Review Runde 1, I1)
-- ============================================================================
-- 0003_least_privilege_role.sql ist bereits auf der laufenden Instanz
-- angewandt und wird nicht nachtraeglich umgeschrieben (gleiches Muster wie
-- 0002 gegenueber 0001). Diese Migration ist eine zweite, idempotente
-- Nachbesserung:
--
-- BEFUND (Review Runde 1): `GRANT INSERT ON plugin_setup.einrichtungen TO
-- plugin_setup_agent` (0003:64) ist tabellenweit, und die Policy
-- `plugin_setup_agent_insert` (0003:72-74) hat `WITH CHECK (true)` --
-- beides zusammen erlaubt `plugin_setup_agent` einen INSERT, der `status`
-- direkt auf `'uebernommen'` (oder jeden anderen Wert) setzt: eine
-- gefaelschte Audit-Zeile, die eine Uebergabe behauptet, die nie
-- stattgefunden hat. `ablage.py::entgegennehmen()` nennt in seiner eigenen
-- INSERT-Spaltenliste nur `(projekt_id, plugin, referenz_name, art,
-- vault_secret_id)` -- die Rolle sollte nie mehr koennen als das.
--
-- FIX, zwei Schichten (Verteidigung in der Tiefe, nicht nur eine):
--   1. Spaltengenaues GRANT: INSERT nur noch auf genau die fuenf Spalten,
--      die ablage.py tatsaechlich schreibt. `status`/`hinweis`/die vier
--      `*_am`-Spalten bleiben aussen vor -- ein INSERT, der sie explizit
--      nennt, scheitert am fehlenden Spaltenrecht, bevor die Policy
--      ueberhaupt zum Zug kommt. (Ein INSERT, der eine Spalte NICHT
--      nennt, bekommt ihren DEFAULT -- das braucht KEIN Spaltenrecht auf
--      dieser Spalte, s. Postgres-Doku zu Column Privileges -- darum
--      bleibt ablage.py's bestehendes INSERT unveraendert lauffaehig.)
--   2. WITH CHECK verschaerft: selbst ein INSERT, der nur die fuenf
--      erlaubten Spalten nennt, wird abgelehnt, wenn NACH Anwendung der
--      DEFAULTs nicht exakt der frische Aufnahme-Zustand herauskommt
--      (status='entgegengenommen', alle vier Zeitstempel/hinweis NULL bis
--      auf entgegengenommen_am, deren Spalte hier keinen Bezug hat, s.u.).
--
-- Apply:
--   docker exec -i <supabase-db-container> psql -U postgres -d postgres \
--     -v ON_ERROR_STOP=1 -f - < 0004_least_privilege_role_hardening.sql
--
-- Idempotent: REVOKE+GRANT und DROP POLICY IF EXISTS+CREATE POLICY sind von
-- Natur aus wiederholbar.
--
-- Rollback:
--   DROP POLICY IF EXISTS plugin_setup_agent_insert ON plugin_setup.einrichtungen;
--   CREATE POLICY plugin_setup_agent_insert ON plugin_setup.einrichtungen
--       FOR INSERT TO plugin_setup_agent WITH CHECK (true);
--   GRANT INSERT ON plugin_setup.einrichtungen TO plugin_setup_agent;

BEGIN;

-- Schicht 1: spaltengenaues INSERT-Recht statt tabellenweit.
REVOKE INSERT ON plugin_setup.einrichtungen FROM plugin_setup_agent;
GRANT INSERT (projekt_id, plugin, referenz_name, art, vault_secret_id)
    ON plugin_setup.einrichtungen TO plugin_setup_agent;

-- Schicht 2: WITH CHECK erzwingt zusaetzlich den frischen Aufnahme-Zustand
-- -- auch fuer den (durch Schicht 1 ohnehin schon blockierten) Fall, dass
-- jemand die Spaltenbeschraenkung kuenftig lockert, ohne die Policy
-- gegenzupruefen.
DROP POLICY IF EXISTS plugin_setup_agent_insert ON plugin_setup.einrichtungen;
CREATE POLICY plugin_setup_agent_insert ON plugin_setup.einrichtungen
    FOR INSERT TO plugin_setup_agent WITH CHECK (
        status = 'entgegengenommen'
        AND hinweis IS NULL
        AND verifiziert_am IS NULL
        AND uebernommen_am IS NULL
        AND fehlgeschlagen_am IS NULL
    );

COMMIT;

-- ============================================================================
-- Verifikation (manuell):
--   SELECT privilege_type, column_name FROM information_schema.column_privileges
--    WHERE table_schema='plugin_setup' AND table_name='einrichtungen'
--      AND grantee='plugin_setup_agent' ORDER BY column_name;
--   -- erwartet: INSERT auf genau art, plugin, projekt_id, referenz_name,
--   --           vault_secret_id -- NICHT status/hinweis/*_am
--
--   SET ROLE plugin_setup_agent;
--   INSERT INTO plugin_setup.einrichtungen
--     (projekt_id, plugin, referenz_name, art, vault_secret_id, status)
--     VALUES ('p', 'demo-plugin', 'x', 'bearer', NULL, 'uebernommen');
--   -- erwartet: FEHLER permission denied for table einrichtungen (Schicht 1
--   --           -- Postgres nennt bei einer Spaltenrechte-Verletzung im
--   --           INSERT-Kontext hier den Tabellennamen, nicht den
--   --           Spaltennamen; gemessen 11.09.2026)
-- ============================================================================

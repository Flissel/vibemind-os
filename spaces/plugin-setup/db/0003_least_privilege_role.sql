-- ============================================================================
-- plugin_setup: die eigene, geringstberechtigte Rolle (Aufgabe 6, Review-
-- Vorgabe #3) — der Agent verbindet sich NICHT als postgres/service_role/
-- supabase_admin.
-- ============================================================================
-- Messung an der laufenden Instanz (11.09.2026, vibemind_supabase-db):
--   - postgres  : rolsuper=f, rolbypassrls=t, rolcreaterole=t, rolcreatedb=t
--   - service_role / supabase_admin: rolbypassrls=t (service_role zusaetzlich
--     rolcanlogin=f -- ueberhaupt nicht direkt verbindbar)
--   -> alle drei umgehen RLS strukturell (bypassrls-Attribut bzw. Owner) und
--      koennten die in 0002_state_machine.sql erzwungene Zustandsmaschine
--      per rohem UPDATE umgehen (der Review-Befund, den dieser Task loest).
--   - vault.create_secret ist SECURITY DEFINER (Owner supabase_admin); vorher
--     nur an postgres/service_role gegrantet (information_schema.
--     role_routine_grants). Eine neue, engere Rolle braucht ein explizites
--     GRANT EXECUTE, sonst scheitert die Aufnahme am allerersten Schritt.
--   - `host all all 127.0.0.1/32 trust` erlaubt jeder LOGIN-Rolle
--     passwortlosen Zugriff ueber TCP-Loopback (pg_hba.conf, gemessen);
--     anon/authenticated/service_role sind bewusst NOLOGIN (nur ueber
--     PostgREST/SET ROLE erreichbar) -- unsere neue Rolle braucht LOGIN,
--     sonst ist sie fuer einen eigenstaendigen Python-Prozess nutzlos.
--
-- Ergebnis: plugin_setup_agent bekommt NUR
--   - USAGE auf plugin_setup und vault
--   - INSERT auf plugin_setup.einrichtungen (keine SELECT/UPDATE/DELETE --
--     der Agent liest den Zustand nicht zurueck, er ruft nur die drei
--     sanktionierten Funktionen; jeder rohe UPDATE-Versuch scheitert daher
--     doppelt: keine Tabellenrechte UND (selbst mit Rechten) erzwungene RLS
--     ohne eine einzige permissive Policy)
--   - EXECUTE auf verifizieren/fehlschlagen/uebernommen
--   - EXECUTE auf vault.create_secret
--   - Mitgliedschaft fuer `postgres`, damit Tests `SET ROLE plugin_setup_agent`
--     nutzen koennen (Muster: test_eingang.py, `SET ROLE anon`).
--
-- Apply:
--   docker exec -i <supabase-db-container> psql -U postgres -d postgres \
--     -v ON_ERROR_STOP=1 -f - < 0003_least_privilege_role.sql
--
-- Idempotent: DO-Block prueft pg_roles vor CREATE ROLE; alle GRANTs sind von
-- Natur aus wiederholbar.
--
-- Rollback:
--   REVOKE plugin_setup_agent FROM postgres;
--   DROP OWNED BY plugin_setup_agent; DROP ROLE IF EXISTS plugin_setup_agent;

BEGIN;

DO $create_role$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'plugin_setup_agent') THEN
        CREATE ROLE plugin_setup_agent LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOINHERIT NOBYPASSRLS NOREPLICATION;
    END IF;
END;
$create_role$;

GRANT USAGE ON SCHEMA plugin_setup TO plugin_setup_agent;
GRANT USAGE ON SCHEMA vault TO plugin_setup_agent;

-- Bewusst nur INSERT -- kein SELECT, kein UPDATE, kein DELETE. Der Agent
-- braucht den Zustand nie zurueckzulesen (er ruft nur die drei Funktionen
-- unten); jede Zustandsaenderung nach dem Insert laeuft ausschliesslich
-- ueber sie.
GRANT INSERT ON plugin_setup.einrichtungen TO plugin_setup_agent;

-- 0001_plugin_setup.sql aktiviert RLS + FORCE ROW LEVEL SECURITY ohne eine
-- einzige Policy -- das sperrt strukturell auch den INSERT dieser Rolle aus
-- (gemessen 11.09.2026: "new row violates row-level security policy" trotz
-- des GRANT INSERT oben). Eine einzige, eng auf plugin_setup_agent
-- beschraenkte Permissive-Policy NUR fuer INSERT -- keine fuer SELECT/
-- UPDATE/DELETE, die bleiben ohne jede Policy weiterhin dicht.
DROP POLICY IF EXISTS plugin_setup_agent_insert ON plugin_setup.einrichtungen;
CREATE POLICY plugin_setup_agent_insert ON plugin_setup.einrichtungen
    FOR INSERT TO plugin_setup_agent WITH CHECK (true);

GRANT EXECUTE ON FUNCTION plugin_setup.verifizieren(text) TO plugin_setup_agent;
GRANT EXECUTE ON FUNCTION plugin_setup.fehlschlagen(text, text) TO plugin_setup_agent;
GRANT EXECUTE ON FUNCTION plugin_setup.uebernommen(text) TO plugin_setup_agent;

GRANT EXECUTE ON FUNCTION vault.create_secret(text, text, text, uuid) TO plugin_setup_agent;

-- Nur fuer Tests: `SET ROLE plugin_setup_agent` (Muster test_eingang.py,
-- `SET ROLE anon`) braucht Mitgliedschaft der verbindenden Rolle (postgres).
-- Erweitert keine Rechte der Rolle selbst.
GRANT plugin_setup_agent TO postgres;

COMMIT;

-- ============================================================================
-- Verifikation (manuell):
--   SELECT rolname, rolcanlogin, rolbypassrls FROM pg_roles
--    WHERE rolname = 'plugin_setup_agent';
--   -- erwartet: t, f
--   SET ROLE plugin_setup_agent;
--   UPDATE plugin_setup.einrichtungen SET status = 'uebernommen';
--   -- erwartet: FEHLER permission denied for table einrichtungen
-- ============================================================================

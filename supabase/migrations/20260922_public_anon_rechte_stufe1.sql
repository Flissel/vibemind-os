-- ============================================================================
-- public-Schema: Stufe 1 der Rechte-Begrenzung (2026-09-22)
-- ============================================================================
-- Entwurf und Begründung: docs/operations/2026-09-22-public-schema-rechte-und-rls.md
--
-- Befund, der dahintersteht: die Rolle `anon` — also JEDE unauthentifizierte
-- PostgREST-Anfrage — besitzt heute auf allen 31 Tabellen in `public` sämtliche
-- Rechte, einschliesslich TRUNCATE. Gleichzeitig sind alle 24 vorhandenen
-- RLS-Policies `allow_all_*` mit `USING (true)`, und `service_role` hat
-- `rolbypassrls = t`. Policies zu verschärfen, ohne die Rechte anzufassen,
-- ändert daher praktisch nichts — dieselbe Einsicht, die
-- spaces/marketing/db/002_rls_baseline.sql für marketing.* formuliert.
--
-- Diese Stufe ist bewusst FUNKTIONAL FOLGENLOS. Entzogen werden nur Rechte, die
-- PostgREST gar nicht ansprechen kann: es kennt SELECT/INSERT/UPDATE/DELETE und
-- RPC, aber weder TRUNCATE noch TRIGGER noch REFERENCES noch MAINTAIN. Lesen und
-- Schreiben bleiben also unverändert möglich; was wegfällt, ist die Fähigkeit,
-- Tabellen zu leeren.
--
-- Der zweite Teil ist der wichtigere: ALTER DEFAULT PRIVILEGES vergibt heute
-- `arwdDxtm` an anon für jede KÜNFTIG angelegte Tabelle in public — einmal
-- entziehen allein würde von der nächsten Migration still rückgängig gemacht.
--
-- Stufe 2 (anon auf Lesen beschränken) steht bewusst NICHT hier: dafür muss
-- erst gemessen werden, welche Dienste heute mit dem anon-Schlüssel schreiben.
-- Das Vorgehen dazu steht im Ops-Dokument.
--
-- Anwenden:
--   docker cp 20260922_public_anon_rechte_stufe1.sql <supabase-db-container>:/tmp/
--   docker exec <supabase-db-container> psql -U supabase_admin -d postgres \
--     -f /tmp/20260922_public_anon_rechte_stufe1.sql
--
-- Gegenproben und Rückweg: siehe Ops-Dokument.
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1) Bestehende Tabellen: die über REST unerreichbaren Rechte entziehen
-- ---------------------------------------------------------------------------
-- `authenticated` bekommt dieselbe Behandlung. Risiko null: auth.users ist leer,
-- es existiert also derzeit überhaupt kein Träger dieser Rolle.

REVOKE TRUNCATE, TRIGGER, REFERENCES, MAINTAIN
    ON ALL TABLES IN SCHEMA public
    FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2) Künftige Tabellen: damit der Entzug nicht bei der nächsten Migration zerfällt
-- ---------------------------------------------------------------------------
-- Zwei Einträge, weil pg_default_acl heute für BEIDE Erzeuger je eine Zeile führt
-- (postgres und supabase_admin). Fehlt einer, erbt die nächste von jenem Rollen-
-- kontext angelegte Tabelle wieder ALL.

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    REVOKE TRUNCATE, TRIGGER, REFERENCES, MAINTAIN ON TABLES FROM anon, authenticated;

ALTER DEFAULT PRIVILEGES FOR ROLE supabase_admin IN SCHEMA public
    REVOKE TRUNCATE, TRIGGER, REFERENCES, MAINTAIN ON TABLES FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3) RLS auf den Tabellen einschalten, denen es fehlt — ohne Verhaltensänderung
-- ---------------------------------------------------------------------------
-- Gemessen am 2026-09-22: 24 von 31 Tabellen hatten RLS, 7 nicht
-- (face_target_blobs, face_targets, face_target_images, ideas_sync_outbox,
-- canvas_sync_outbox, canvas_reformat_jobs, schema_version).
--
-- Bewusst dynamisch statt als Namensliste: wer diese Migration später auf einer
-- anderen Instanz anwendet, hat womöglich einen anderen Tabellenbestand.
--
-- Die Policy bleibt vorerst `allow_all`, ist also genau so permissiv wie die
-- 24 bestehenden. Das ist Absicht — diese Stufe soll nichts brechen. Der Sinn
-- ist der aus der Marketing-Baseline: damit eine spätere Verschärfung nicht an
-- einem vergessenen ENABLE ROW LEVEL SECURITY vorbeiläuft.

DO $$
DECLARE
    t record;
    n_rls int := 0;
    n_pol int := 0;
BEGIN
    FOR t IN
        SELECT c.oid, c.relname
          FROM pg_class c
          JOIN pg_namespace ns ON ns.oid = c.relnamespace
         WHERE ns.nspname = 'public'
           AND c.relkind = 'r'
           AND NOT c.relrowsecurity
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t.relname);
        n_rls := n_rls + 1;

        IF NOT EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = t.oid) THEN
            EXECUTE format(
                'CREATE POLICY %I ON public.%I FOR ALL USING (true) WITH CHECK (true)',
                'allow_all_' || t.relname, t.relname);
            n_pol := n_pol + 1;
        END IF;
    END LOOP;

    RAISE NOTICE 'RLS eingeschaltet auf % Tabellen, % Policies angelegt', n_rls, n_pol;
END $$;

COMMIT;

-- ============================================================================
-- Erwartetes Ergebnis
-- ============================================================================
--   SELECT privilege_type, count(*) FROM information_schema.role_table_grants
--    WHERE grantee='anon' AND table_schema='public' GROUP BY 1 ORDER BY 1;
--   -> DELETE/INSERT/SELECT/UPDATE je 31; TRUNCATE/TRIGGER/REFERENCES fehlen
--
--   SELECT count(*) FILTER (WHERE relrowsecurity) AS mit_rls, count(*) AS gesamt
--     FROM pg_class WHERE relnamespace='public'::regnamespace AND relkind='r';
--   -> 31 / 31
-- ============================================================================

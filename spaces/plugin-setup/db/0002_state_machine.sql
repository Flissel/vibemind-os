-- ============================================================================
-- plugin_setup: den Zustandsautomaten in der Datenbank erzwingen
-- ============================================================================
-- 0001_plugin_setup.sql ist bereits auf der laufenden Instanz angewandt und
-- wird nicht nachträglich umgeschrieben. Dies ist eine zweite, idempotente
-- Migration (Review-Fix-Runde nach Aufgabe 4):
--
--   1. plugin_setup.uebernommen() bekommt die fehlende Wache: nur noch aus
--      Status 'verifiziert' heraus, sonst RAISE EXCEPTION, kein Teilzustand.
--      Vorher prüfte die Funktion nur, dass die referenz_name existiert —
--      ein Aufrufer konnte direkt von 'entgegengenommen' (oder sogar
--      'fehlgeschlagen') auf 'uebernommen' springen: Vault-Secret gelöscht,
--      ohne dass je eine Verifikation bestanden wurde. Das Diagramm in
--      docs/superpowers/specs/2026-09-08-plugin-setup-agent.md hängt
--      Schritt 3 ("uebergeben") ausdrücklich hinter Schritt 2
--      ("VERIFIZIEREN"); fail closed heißt, die Datenbank erzwingt das,
--      nicht die Disziplin des Aufrufers.
--   2. plugin_setup.verifizieren(referenz) und
--      plugin_setup.fehlschlagen(referenz, hinweis) als die einzigen
--      sanktionierten Wege nach 'verifiziert' bzw. 'fehlgeschlagen' — sonst
--      müsste Aufgabe 5 den Status per rohem UPDATE setzen und der
--      Automat wäre nirgends durchgesetzt.
--   3. Vier echte Zeitstempel-Spalten statt des jsonb-Sacks `zeitpunkte`:
--      entgegengenommen_am, verifiziert_am, uebernommen_am,
--      fehlgeschlagen_am. Begründung: der Grund, warum dieser Zustand
--      überhaupt in Supabase liegt und nicht nur in OpenFang, ist
--      Abfragbarkeit ("was ist seit Montag liegengeblieben" soll eine
--      WHERE-Klausel sein, keine JSON-Extraktion). Das Ereignis-Set ist
--      fix (genau die vier Übergänge, die dieser Automat kennt), also
--      sind vier Spalten strikt nützlicher für dieselbe Information.
--      `zeitpunkte` wird ersetzt statt zusätzlich behalten: zwei
--      Darstellungen derselben vier Zeitstempel würden auseinanderlaufen,
--      sobald ein Schreibpfad die eine aktualisiert und die andere
--      vergisst. Heute kostet das nichts — nichts liest zeitpunkte.
--
-- Apply:
--   docker exec -i <supabase-db-container> psql -U postgres -d postgres \
--     -v ON_ERROR_STOP=1 -f - < 0002_state_machine.sql
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, CREATE OR REPLACE FUNCTION, und ein
-- durch information_schema abgesicherter Backfill+DROP COLUMN-Block sind
-- alle zweimal anwendbar (siehe tests/test_eingang.py für den Beweis).

BEGIN;

-- ============================================================================
-- 1) Vier echte Zeitstempel-Spalten
-- ============================================================================

ALTER TABLE plugin_setup.einrichtungen
    ADD COLUMN IF NOT EXISTS entgegengenommen_am timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS verifiziert_am       timestamptz,
    ADD COLUMN IF NOT EXISTS uebernommen_am       timestamptz,
    ADD COLUMN IF NOT EXISTS fehlgeschlagen_am    timestamptz;

-- Backfill aus dem jsonb-Sack + Spalte entfernen -- nur, falls `zeitpunkte`
-- auf dieser Instanz noch existiert (erste Anwendung). Der IF-EXISTS-Check
-- läuft zur Laufzeit; PL/pgSQL prüft die referenzierten Spalten der
-- UPDATE/ALTER-Anweisungen erst bei tatsächlicher Ausführung, nicht beim
-- Kompilieren des Blocks -- deshalb ist das bei der zweiten Anwendung
-- (Spalte längst weg) sicher ein No-Op statt eines Fehlers.
DO $migrate_zeitpunkte$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'plugin_setup'
          AND table_name = 'einrichtungen'
          AND column_name = 'zeitpunkte'
    ) THEN
        UPDATE plugin_setup.einrichtungen
        SET entgegengenommen_am = COALESCE((zeitpunkte->>'entgegengenommen')::timestamptz, entgegengenommen_am),
            verifiziert_am      = COALESCE(verifiziert_am, (zeitpunkte->>'verifiziert')::timestamptz),
            uebernommen_am      = COALESCE(uebernommen_am, (zeitpunkte->>'uebernommen')::timestamptz),
            fehlgeschlagen_am   = COALESCE(fehlgeschlagen_am, (zeitpunkte->>'fehlgeschlagen')::timestamptz);

        ALTER TABLE plugin_setup.einrichtungen DROP COLUMN zeitpunkte;
    END IF;
END;
$migrate_zeitpunkte$;

-- ============================================================================
-- 2) plugin_setup.verifizieren(referenz) -- einziger sanktionierter Weg
--    nach 'verifiziert'. Nur aus 'entgegengenommen', sonst fail closed.
-- ============================================================================

CREATE OR REPLACE FUNCTION plugin_setup.verifizieren(referenz text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = plugin_setup, pg_temp
AS $fn$
DECLARE
    v_status text;
BEGIN
    SELECT status INTO v_status
    FROM plugin_setup.einrichtungen
    WHERE referenz_name = referenz
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'plugin_setup.verifizieren: unbekannte referenz_name %', referenz
            USING ERRCODE = 'no_data_found';
    END IF;

    IF v_status <> 'entgegengenommen' THEN
        RAISE EXCEPTION 'plugin_setup.verifizieren: referenz % ist im Status %, erwartet entgegengenommen',
            referenz, v_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE plugin_setup.einrichtungen
    SET status = 'verifiziert',
        verifiziert_am = now()
    WHERE referenz_name = referenz;
END;
$fn$;

REVOKE ALL ON FUNCTION plugin_setup.verifizieren(text) FROM PUBLIC, anon, authenticated;

-- ============================================================================
-- 3) plugin_setup.fehlschlagen(referenz, hinweis) -- einziger sanktionierter
--    Weg nach 'fehlgeschlagen'. Nur aus 'entgegengenommen', sonst fail
--    closed. `hinweis` ist für einen Statuscode / eine kurze Ursache
--    gedacht -- NIEMALS ein Wert oder Antwortkörper (D3: "mit dem
--    Statuscode, nie mit dem Antwortkörper"); diese Disziplin liegt beim
--    Aufrufer, genau wie bei jedem anderen Schreibpfad in diese Tabelle.
--    Das Vault-Secret bleibt erhalten -- nur uebernommen() löscht es
--    (genau eine Verwahrstelle nach der Übernahme, nicht vorher), damit ein
--    Fehlschlag den Wert nicht vernichtet.
--    ACHTUNG, bewusste Grenze: es gibt KEINE Kante von 'fehlgeschlagen'
--    zurück nach 'verifiziert'. verifizieren() nimmt ausschliesslich
--    'entgegengenommen' an. Das Diagramm der Spec kennt diese Kante nicht,
--    und fail closed heisst, den Automaten nicht auf Verdacht zu
--    verbreitern. Ein erneuter Versuch braucht heute eine neue Aufnahme;
--    falls Aufgabe 6 einen echten Wiederholungsweg braucht, bekommt der
--    eine eigene Funktion mit eigener Wache und eigenem Test.
-- ============================================================================

CREATE OR REPLACE FUNCTION plugin_setup.fehlschlagen(referenz text, p_hinweis text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = plugin_setup, pg_temp
AS $fn$
DECLARE
    v_status text;
BEGIN
    SELECT status INTO v_status
    FROM plugin_setup.einrichtungen
    WHERE referenz_name = referenz
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'plugin_setup.fehlschlagen: unbekannte referenz_name %', referenz
            USING ERRCODE = 'no_data_found';
    END IF;

    IF v_status <> 'entgegengenommen' THEN
        RAISE EXCEPTION 'plugin_setup.fehlschlagen: referenz % ist im Status %, erwartet entgegengenommen',
            referenz, v_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    UPDATE plugin_setup.einrichtungen
    SET status = 'fehlgeschlagen',
        fehlgeschlagen_am = now(),
        hinweis = p_hinweis
    WHERE referenz_name = referenz;
END;
$fn$;

REVOKE ALL ON FUNCTION plugin_setup.fehlschlagen(text, text) FROM PUBLIC, anon, authenticated;

-- ============================================================================
-- 4) plugin_setup.uebernommen(referenz) -- neu definiert mit der Wache.
--    Nur aus 'verifiziert'. Das ist der eigentliche Fix aus dem Review.
-- ============================================================================

CREATE OR REPLACE FUNCTION plugin_setup.uebernommen(referenz text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = plugin_setup, vault, pg_temp
AS $fn$
DECLARE
    v_status   text;
    v_vault_id uuid;
BEGIN
    SELECT status, vault_secret_id INTO v_status, v_vault_id
    FROM plugin_setup.einrichtungen
    WHERE referenz_name = referenz
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'plugin_setup.uebernommen: unbekannte referenz_name %', referenz
            USING ERRCODE = 'no_data_found';
    END IF;

    IF v_status <> 'verifiziert' THEN
        RAISE EXCEPTION 'plugin_setup.uebernommen: referenz % ist im Status %, erwartet verifiziert',
            referenz, v_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF v_vault_id IS NOT NULL THEN
        DELETE FROM vault.secrets WHERE id = v_vault_id;
    END IF;

    UPDATE plugin_setup.einrichtungen
    SET vault_secret_id = NULL,
        status = 'uebernommen',
        uebernommen_am = now()
    WHERE referenz_name = referenz;
END;
$fn$;

REVOKE ALL ON FUNCTION plugin_setup.uebernommen(text) FROM PUBLIC, anon, authenticated;

COMMIT;

-- ============================================================================
-- Verifikation (manuell):
--   SELECT column_name FROM information_schema.columns
--    WHERE table_schema = 'plugin_setup' AND table_name = 'einrichtungen'
--    ORDER BY ordinal_position;
--   -- erwartet: zeitpunkte NICHT mehr dabei; die vier *_am-Spalten dabei
--   SELECT proname FROM pg_proc
--    WHERE pronamespace = 'plugin_setup'::regnamespace ORDER BY proname;
--   -- erwartet: fehlschlagen, uebernommen, verifizieren
-- ============================================================================

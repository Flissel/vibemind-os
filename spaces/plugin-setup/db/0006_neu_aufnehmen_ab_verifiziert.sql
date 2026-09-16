-- ============================================================================
-- plugin_setup: neu_aufnehmen() zusaetzlich aus 'verifiziert' freigeben
-- ============================================================================
-- Befund (16.09.2026, erster ECHTER Durchgang durch das Eingabefenster,
-- s. .superpowers/sdd/sackgasse-brief.md): `verifiziert` war beim Entwurf
-- von 0002/0005 als DURCHGANGS-Zustand gedacht -- er sollte binnen
-- Sekunden in `uebernommen` uebergehen, sobald OpenFang den Wert
-- entgegennimmt. Der erste echte Lauf zeigt: lehnt OpenFang ab (409), oder
-- schlaegt der anschliessende Supabase-Uebergang fehl, bleibt die Zeile
-- auf 'verifiziert' stehen -- und das ist ein RUHE-Zustand, der einzige
-- ohne Ausgang. `fehlschlagen()` nimmt nur 'entgegengenommen' an,
-- `uebernommen()` nur 'verifiziert' (und setzt voraus, dass OpenFang den
-- Wert tatsaechlich HAT), `neu_aufnehmen()` bisher nur 'fehlgeschlagen'.
-- Der Betreiber kam ohne Handgriff in der Datenbank nicht weiter.
--
-- FIX: `neu_aufnehmen(referenz)` (0005_fenster_oberflaeche.sql) akzeptiert
-- zusaetzlich 'verifiziert' als Ausgangszustand. Zwei Unterfaelle, beide
-- durch denselben Weg abgedeckt, aus verschiedenen Gruenden richtig:
--
--   1. OpenFang hat abgelehnt (409, oder gar nicht erreichbar). Der Wert
--      liegt NUR in der Supabase-Kopie. Loeschen heisst: der Betreiber
--      tippt neu -- derselbe Preis, den neu_aufnehmen() fuer den
--      'fehlgeschlagen'-Fall schon immer traegt.
--   2. OpenFang hat angenommen, aber der Supabase-Uebergang (uebernommen())
--      scheiterte -- "zwei_verwahrstellen". Der Wert liegt dann in BEIDEN
--      Verwahrstellen. Die Supabase-Kopie zu loeschen ist hier nicht nur
--      zulaessig, es ist GENAU die Abhilfe, die D2 verlangt (zwei
--      Verwahrstellen sind das, was dort verboten ist) -- verloren geht
--      die Spur in Supabase, nicht der Wert, der bei OpenFang bleibt.
--
-- In beiden Faellen gilt dieselbe Zusicherung wie beim bestehenden
-- 'fehlgeschlagen'-Pfad: es geht nichts verloren, das nur dort liegt und
-- gebraucht wird.
--
-- WARUM NICHT eine Kante 'verifiziert' -> 'fehlgeschlagen': fehlschlagen()
-- ist der sanktionierte Weg fuer "der Anbieter hat abgelehnt" und schreibt
-- einen Hinweis fuer die Fehlersuche. Hier hat der Anbieter (im Unterfall 1)
-- entweder gar nicht sauber geantwortet, oder (Unterfall 2) sogar
-- ZUGESTIMMT -- gescheitert ist die Uebergabe, nicht die Verifikation.
-- Diesen Fall in 'fehlgeschlagen' zu kippen, wuerde die Bedeutung des
-- Status verwaessern, und 0002_state_machine.sql begruendet ausdruecklich,
-- dass verifizieren()/fehlschlagen() die EINZIGEN sanktionierten Wege in
-- ihre Zielzustaende sind -- das gilt unveraendert weiter, diese Migration
-- ruehrt daran nicht.
--
-- UNVERAENDERT abgelehnt bleiben 'entgegengenommen' (wuerde eine laufende
-- Aufnahme verwerfen) und 'uebernommen' (wuerde eine fertige Einrichtung
-- stillschweigend vergessen) -- die Begruendungen stehen weiterhin im
-- Kommentarkopf von 0005_fenster_oberflaeche.sql und gelten unveraendert.
--
-- Eigene Datei statt eines Edits an 0005: 0001-0005 sind bereits auf der
-- laufenden Instanz angewandt (s. Kommentarkopf von 0005). Idempotent wie
-- die uebrigen: CREATE OR REPLACE FUNCTION plus wiederholbare GRANTs.
--
-- Apply (nur Schema plugin_setup):
--   docker exec -i <supabase-db-container> psql -U postgres -d postgres \
--     -v ON_ERROR_STOP=1 -f - < db/0006_neu_aufnehmen_ab_verifiziert.sql

BEGIN;

CREATE OR REPLACE FUNCTION plugin_setup.neu_aufnehmen(referenz text)
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
        RAISE EXCEPTION 'plugin_setup.neu_aufnehmen: unbekannte referenz_name %', referenz
            USING ERRCODE = 'no_data_found';
    END IF;

    IF v_status NOT IN ('fehlgeschlagen', 'verifiziert') THEN
        RAISE EXCEPTION 'plugin_setup.neu_aufnehmen: referenz % ist im Status %, erwartet fehlgeschlagen oder verifiziert',
            referenz, v_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF v_vault_id IS NOT NULL THEN
        DELETE FROM vault.secrets WHERE id = v_vault_id;
    END IF;

    DELETE FROM plugin_setup.einrichtungen WHERE referenz_name = referenz;
END;
$fn$;

REVOKE ALL ON FUNCTION plugin_setup.neu_aufnehmen(text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION plugin_setup.neu_aufnehmen(text) TO plugin_setup_agent;

COMMIT;

-- ============================================================================
-- Verifikation (manuell):
--   SELECT prosrc FROM pg_proc
--    WHERE pronamespace = 'plugin_setup'::regnamespace AND proname = 'neu_aufnehmen';
--   -- erwartet: "NOT IN ('fehlgeschlagen', 'verifiziert')" im Wachen-Ausdruck
-- ============================================================================

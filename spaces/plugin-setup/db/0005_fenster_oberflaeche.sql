-- ============================================================================
-- plugin_setup: die zwei Funktionen, die das Eingabefenster braucht
-- ============================================================================
-- Eigene Datei, weil 0001-0004 bereits auf der laufenden Instanz angewandt
-- sind. Idempotent: CREATE OR REPLACE FUNCTION und wiederholbare GRANTs.
--
-- 1. zustand(referenz) -- lesen OHNE SELECT-Recht. Die Rolle
--    plugin_setup_agent hat absichtlich kein SELECT auf der Tabelle
--    (0004, belegt durch tests/test_ablage.py). einrichtung_status braucht
--    aber den Zustand. Diese Funktion gibt GENAU zwei Spalten heraus --
--    status und hinweis -- und nie vault_secret_id, nie einen Wert.
--
-- 2. neu_aufnehmen(referenz) -- ein Fehlschlag muss wiederholbar sein.
--    Ohne sie ist eine Referenz nach einem Tippfehler dauerhaft verbrannt:
--    die Zeile steht auf 'fehlgeschlagen', und ein zweiter Versuch
--    kollidiert an der UNIQUE-Constraint auf referenz_name.
--    NUR aus 'fehlgeschlagen' heraus -- aus 'entgegengenommen' waere es ein
--    Weg, eine laufende Aufnahme zu verwerfen, und aus 'uebernommen' ein Weg,
--    eine bereits uebergebene Einrichtung stillschweigend zu vergessen.
--    Sie LOESCHT Zeile und Vault-Kopie, statt den Status zurueckzusetzen:
--    dann bleibt `entgegennehmen` ein reines INSERT und muss nicht zwei
--    Formen kennen. Preis, bewusst getragen: der Fehlschlag-Eintrag geht
--    verloren. Der Agent hat ihn vorher ueber einrichtung_status gesehen.

BEGIN;

CREATE OR REPLACE FUNCTION plugin_setup.zustand(referenz text)
RETURNS TABLE(status text, hinweis text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = plugin_setup, pg_temp
AS $fn$
BEGIN
    RETURN QUERY
    SELECT e.status, COALESCE(e.hinweis, '')
    FROM plugin_setup.einrichtungen e
    WHERE e.referenz_name = referenz;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'plugin_setup.zustand: unbekannte referenz_name %', referenz
            USING ERRCODE = 'no_data_found';
    END IF;
END;
$fn$;

REVOKE ALL ON FUNCTION plugin_setup.zustand(text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION plugin_setup.zustand(text) TO plugin_setup_agent;

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

    IF v_status <> 'fehlgeschlagen' THEN
        RAISE EXCEPTION 'plugin_setup.neu_aufnehmen: referenz % ist im Status %, erwartet fehlgeschlagen',
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

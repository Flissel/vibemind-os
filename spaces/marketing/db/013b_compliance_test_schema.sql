-- 013b_compliance_test_schema.sql — Verbotsliste fuer sales-claws TESTLAEUFE
--
-- sales-claws Suite laeuft gegen das Schema sales_test derselben Postgres.
-- Ohne eigene Verbotsliste schrieb sie Test-Kennungen in die
-- PRODUKTIONS-Tabelle compliance.sperrliste (gemessen 03.09.2026) und
-- blockierte damit spaetere Tests — und jeden echten Kontakt mit derselben
-- Nummer. sales-mcp/sperrliste.py waehlt bei SALES_DB_SCHEMA=sales_test
-- automatisch dieses Schema. Gleiche DDL wie 013, OHNE Marketing-Trigger.
CREATE SCHEMA IF NOT EXISTS compliance_test;

CREATE TABLE IF NOT EXISTS compliance_test.sperrliste (
    kennung          text PRIMARY KEY,
    quelle           text NOT NULL,
    grund            text NOT NULL DEFAULT '',
    seit             timestamptz NOT NULL DEFAULT now(),
    aufgehoben_am    timestamptz,
    aufgehoben_grund text,
    CONSTRAINT kennung_form CHECK (kennung ~ '^(email:[^A-Z\s]+|tel:\+[0-9]{6,})$')
);

CREATE OR REPLACE FUNCTION compliance_test.sperren(p_kennung text, p_quelle text, p_grund text)
RETURNS void LANGUAGE sql AS $$
    INSERT INTO compliance_test.sperrliste (kennung, quelle, grund)
    VALUES (p_kennung, p_quelle, coalesce(p_grund, ''))
    ON CONFLICT (kennung) DO UPDATE
        SET quelle = EXCLUDED.quelle, grund = EXCLUDED.grund, seit = now(),
            aufgehoben_am = NULL, aufgehoben_grund = NULL
        WHERE compliance_test.sperrliste.aufgehoben_am IS NOT NULL;
$$;

CREATE OR REPLACE FUNCTION compliance_test.ist_gesperrt(p_kennung text)
RETURNS text LANGUAGE sql STABLE AS $$
    SELECT quelle || ': ' || grund FROM compliance_test.sperrliste
    WHERE kennung = p_kennung AND aufgehoben_am IS NULL;
$$;

-- Testlaeufe duerfen hier auch aufraeumen (TRUNCATE), anders als in compliance.
GRANT USAGE ON SCHEMA compliance_test TO sales_app;
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON compliance_test.sperrliste TO sales_app;
GRANT EXECUTE ON FUNCTION compliance_test.sperren(text, text, text) TO sales_app;
GRANT EXECUTE ON FUNCTION compliance_test.ist_gesperrt(text) TO sales_app;

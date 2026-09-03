-- verify_041_042.sql — Rechte- und Existenzpruefung fuer F2/F3. Jede
-- Verletzung bricht mit Exception ab (psql -v ON_ERROR_STOP=1).
DO $$
BEGIN
    IF NOT has_function_privilege('sales_app', 'marketing.vorschlag_aus_sales(text,text,jsonb)', 'EXECUTE') THEN
        RAISE EXCEPTION 'sales_app darf vorschlag_aus_sales nicht rufen';
    END IF;
    IF has_table_privilege('sales_app', 'marketing.audience_proposals', 'SELECT')
       OR has_table_privilege('sales_app', 'marketing.lead_candidates', 'SELECT')
       OR has_table_privilege('sales_app', 'marketing.audience_proposals', 'INSERT') THEN
        RAISE EXCEPTION 'sales_app hat Tabellenrechte auf marketing.* — verboten (Least Privilege)';
    END IF;
    IF NOT has_schema_privilege('sales_app', 'marketing', 'USAGE') THEN
        RAISE EXCEPTION 'sales_app fehlt USAGE auf Schema marketing';
    END IF;
END $$;
SELECT 'verify 041: ok' AS ergebnis;

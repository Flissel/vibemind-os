-- Rueckweg zu 20261120_ideen_aenderungen.sql.
-- Entfernt Ausloeser, Funktionen und das Protokoll. Die Fachtabellen bleiben
-- unberuehrt. ACHTUNG: Das Protokoll ist danach weg, "zurueck" damit auch fuer
-- alle bisherigen Aenderungen.

DO $$
DECLARE
    v_tabellen text[] := ARRAY[
        'public.ideas',
        'public.canvas_nodes',
        'public.canvas_edges',
        'public.projects',
        'public.discovered_edges',
        'public.exploration_sessions',
        'public.shuttles',
        'public.research_report_artifacts',
        'public.mermaid_diagrams',
        'public.swe_design_runs',
        'marketing.broadcast_proposals',
        'marketing.crowdfunding_batches'
    ];
    v_tabelle text;
BEGIN
    FOREACH v_tabelle IN ARRAY v_tabellen LOOP
        IF to_regclass(v_tabelle) IS NOT NULL THEN
            EXECUTE format('DROP TRIGGER IF EXISTS zz_aenderung ON %s', to_regclass(v_tabelle));
        END IF;
    END LOOP;
END
$$;

DROP FUNCTION IF EXISTS public.ideen_zurueck(text);
DROP FUNCTION IF EXISTS public.ideen_aenderungen_aufraeumen(integer);
DROP FUNCTION IF EXISTS public.ideen_aenderung_aufzeichnen();
DROP FUNCTION IF EXISTS public.ideen_aenderung_technische_spalten();
DROP TABLE IF EXISTS public.ideen_aenderungen;

-- "Zurueck" fuer Ideen und Bubbles, Stufe A: Aenderungsprotokoll in der Datenbank.
--
-- Jede Aenderung an Bubbles, Ideen und allem, was per Fremdschluessel daran haengt,
-- wird mit altem und neuem Stand aufgezeichnet. `ideen_zurueck()` nimmt die juengste
-- Aenderung eines Agenten wieder zurueck.
--
-- Gruppe und Quelle kommen aus dem Anfrage-Kopf `X-Aenderung-Id`, den PostgREST als
-- Einstellung `request.headers` bereitstellt. Ohne Kopf gilt die Aenderung als `hand`
-- und jede Transaktion ist ihre eigene Gruppe.
--
-- Mehrfach ausfuehrbar. Rueckweg: rueckweg/20261120_ideen_aenderungen_rueckweg.sql

CREATE TABLE IF NOT EXISTS public.ideen_aenderungen (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    erstellt_am         timestamptz NOT NULL DEFAULT clock_timestamp(),
    gruppe              text NOT NULL,
    quelle              text NOT NULL CHECK (quelle IN ('agent', 'hand', 'zurueck')),
    tabelle             text NOT NULL,
    vorgang             text NOT NULL CHECK (vorgang IN ('INSERT', 'UPDATE', 'DELETE')),
    zeilen_id           text NOT NULL,
    vorher              jsonb,
    nachher             jsonb,
    zurueckgenommen_am  timestamptz
);

CREATE INDEX IF NOT EXISTS idx_ideen_aenderungen_gruppe
    ON public.ideen_aenderungen (gruppe);
CREATE INDEX IF NOT EXISTS idx_ideen_aenderungen_offen
    ON public.ideen_aenderungen (id DESC)
    WHERE zurueckgenommen_am IS NULL AND quelle = 'agent';
CREATE INDEX IF NOT EXISTS idx_ideen_aenderungen_zeile
    ON public.ideen_aenderungen (tabelle, zeilen_id);

ALTER TABLE public.ideen_aenderungen ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.ideen_aenderungen FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON public.ideen_aenderungen FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON public.ideen_aenderungen FROM authenticated;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT SELECT ON public.ideen_aenderungen TO service_role;
    END IF;
END
$$;

-- Spalten, die Abgleich und Hilfsdienste nachschreiben. Eine Aenderung nur an ihnen
-- ist keine fachliche Aenderung: sie wird nicht aufgezeichnet und zaehlt beim
-- Vergleich "wurde die Zeile seitdem noch einmal geaendert" nicht mit.
CREATE OR REPLACE FUNCTION public.ideen_aenderung_technische_spalten()
RETURNS text[]
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT ARRAY['updated_at', 'last_synced_at', 'embedding_hash', 'embedding_vector',
                 'reformat_pending', 'last_formatted']
$$;

CREATE OR REPLACE FUNCTION public.ideen_aenderung_aufzeichnen()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_technisch text[] := public.ideen_aenderung_technische_spalten();
    v_alt       jsonb;
    v_neu       jsonb;
    v_gruppe    text;
    v_quelle    text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        v_alt := to_jsonb(OLD);
    END IF;
    IF TG_OP <> 'DELETE' THEN
        v_neu := to_jsonb(NEW);
    END IF;

    IF TG_OP = 'UPDATE' AND (v_alt - v_technisch) = (v_neu - v_technisch) THEN
        RETURN NULL;
    END IF;

    -- Laeuft gerade eine Zuruecknahme, gehoert die Zeile zu ihr. So bleibt auch
    -- aufgezeichnet, was eine Zuruecknahme getan hat, und sie nimmt sich nie selbst
    -- zurueck (ideen_zurueck waehlt ohne Angabe nur quelle = 'agent').
    v_gruppe := nullif(current_setting('ideen.zuruecknahme', true), '');
    IF v_gruppe IS NOT NULL THEN
        v_quelle := 'zurueck';
    ELSE
        BEGIN
            v_gruppe := nullif(btrim(
                nullif(current_setting('request.headers', true), '')::jsonb ->> 'x-aenderung-id'
            ), '');
        EXCEPTION WHEN OTHERS THEN
            v_gruppe := NULL;
        END;
        IF v_gruppe IS NOT NULL THEN
            v_quelle := 'agent';
            v_gruppe := left(v_gruppe, 200);
        ELSE
            v_quelle := 'hand';
            v_gruppe := 'hand-' || txid_current()::text;
        END IF;
    END IF;

    INSERT INTO public.ideen_aenderungen (gruppe, quelle, tabelle, vorgang, zeilen_id, vorher, nachher)
    VALUES (
        v_gruppe,
        v_quelle,
        format('%I.%I', TG_TABLE_SCHEMA, TG_TABLE_NAME),
        TG_OP,
        coalesce(v_neu, v_alt) ->> 'id',
        v_alt,
        v_neu
    );
    RETURN NULL;
END
$$;

REVOKE ALL ON FUNCTION public.ideen_aenderung_aufzeichnen() FROM PUBLIC;

-- Reihenfolge im Protokoll (gemessen, test_loeschweitergabe_elternteil_steht_vor_den_kindern):
-- Beim Loeschen mit Weitergabe steht das Elternteil VOR den mitgeloeschten und den
-- auf leer gesetzten Kindern, weil deren Ausloeser erst nach denen des Elternteils
-- an die Reihe kommen. Die Zuruecknahme verlaesst sich deshalb nicht auf die
-- Reihenfolge, siehe ideen_zurueck.
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
    v_tabelle  text;
    v_klasse   regclass;
    v_schluessel text[];
BEGIN
    FOREACH v_tabelle IN ARRAY v_tabellen LOOP
        v_klasse := to_regclass(v_tabelle);
        IF v_klasse IS NULL THEN
            RAISE EXCEPTION 'ideen_aenderungen: Tabelle % fehlt', v_tabelle;
        END IF;
        SELECT array_agg(a.attname::text ORDER BY a.attnum)
          INTO v_schluessel
          FROM pg_index i
          JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
         WHERE i.indrelid = v_klasse AND i.indisprimary;
        IF v_schluessel IS DISTINCT FROM ARRAY['id'] THEN
            RAISE EXCEPTION 'ideen_aenderungen: % braucht den Primaerschluessel (id), hat %',
                v_tabelle, v_schluessel;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_attribute
                    WHERE attrelid = v_klasse AND attnum > 0 AND NOT attisdropped
                      AND attgenerated <> '') THEN
            RAISE EXCEPTION 'ideen_aenderungen: % hat berechnete Spalten, Wiederanlegen ungeprueft',
                v_tabelle;
        END IF;
        EXECUTE format('DROP TRIGGER IF EXISTS zz_aenderung ON %s', v_klasse);
        EXECUTE format(
            'CREATE TRIGGER zz_aenderung AFTER INSERT OR UPDATE OR DELETE ON %s '
            'FOR EACH ROW EXECUTE FUNCTION public.ideen_aenderung_aufzeichnen()',
            v_klasse
        );
    END LOOP;
END
$$;

-- Nimmt eine Aenderung in einem Zug zurueck.
--   ohne Angabe: die juengste offene Gruppe eines Agenten
--   mit Angabe:  genau diese Gruppe, gleich welcher Quelle
-- Bricht ganz ab (keine Teilwirkung), wenn eine betroffene Zeile seitdem noch
-- einmal geaendert wurde oder die Zuruecknahme Zeilen ausserhalb der Gruppe
-- veraendern wuerde.
CREATE OR REPLACE FUNCTION public.ideen_zurueck(p_gruppe text DEFAULT NULL)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_technisch  text[] := public.ideen_aenderung_technische_spalten();
    v_gruppe     text;
    v_zgruppe    text;
    v_start      bigint;
    v_erstellt   timestamptz;
    v_anzahl     integer := 0;
    v_eintraege  jsonb := '[]'::jsonb;
    v_jetzt      jsonb;
    v_spalten    text;
    v_fremd      record;
    r            public.ideen_aenderungen%ROWTYPE;
    v_offen      bigint[];
    v_naechste   bigint[];
    v_gesperrt   text[];
    v_schluessel text;
    v_fortschritt boolean;
    v_id         bigint;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('public.ideen_zurueck'));

    IF p_gruppe IS NULL THEN
        SELECT a.gruppe INTO v_gruppe
          FROM public.ideen_aenderungen a
         WHERE a.quelle = 'agent' AND a.zurueckgenommen_am IS NULL
         ORDER BY a.id DESC
         LIMIT 1;
        IF v_gruppe IS NULL THEN
            RETURN jsonb_build_object('gruppe', NULL, 'anzahl', 0, 'eintraege', '[]'::jsonb);
        END IF;
    ELSE
        SELECT a.gruppe INTO v_gruppe
          FROM public.ideen_aenderungen a
         WHERE a.gruppe = p_gruppe AND a.zurueckgenommen_am IS NULL
         LIMIT 1;
        IF v_gruppe IS NULL THEN
            RAISE EXCEPTION 'aenderung_unbekannt: %', p_gruppe;
        END IF;
    END IF;

    SELECT coalesce(max(id), 0) INTO v_start FROM public.ideen_aenderungen;
    SELECT min(erstellt_am) INTO v_erstellt FROM public.ideen_aenderungen WHERE gruppe = v_gruppe;
    v_zgruppe := 'zurueck:' || v_gruppe;
    PERFORM set_config('ideen.zuruecknahme', v_zgruppe, true);

    -- Grundsaetzlich rueckwaerts. Bei Loeschweitergabe steht aber das Elternteil vor
    -- seinen Kindern im Protokoll: Ein Kind laesst sich dann noch nicht wieder anlegen.
    -- Solche Eintraege (Fremdschluessel verletzt) werden zurueckgestellt und im
    -- naechsten Durchgang erneut versucht. Ist eine Zeile zurueckgestellt, warten auch
    -- ihre aelteren Eintraege, damit je Zeile die Reihenfolge rueckwaerts bleibt.
    SELECT array_agg(a.id ORDER BY a.id DESC) INTO v_offen
      FROM public.ideen_aenderungen a
     WHERE a.gruppe = v_gruppe AND a.zurueckgenommen_am IS NULL;

    LOOP
        v_naechste := ARRAY[]::bigint[];
        v_gesperrt := ARRAY[]::text[];
        v_fortschritt := false;

        FOREACH v_id IN ARRAY v_offen LOOP
            SELECT * INTO r FROM public.ideen_aenderungen WHERE id = v_id;
            v_schluessel := r.tabelle || ' ' || r.zeilen_id;
            IF v_schluessel = ANY (v_gesperrt) THEN
                v_naechste := v_naechste || v_id;
                CONTINUE;
            END IF;

            BEGIN
                EXECUTE format('SELECT to_jsonb(t) FROM %s t WHERE t.id::text = $1', r.tabelle)
                   INTO v_jetzt USING r.zeilen_id;

                IF r.tabelle LIKE 'marketing.%' THEN
                    -- Marketing: nur der Verweis auf die Bubble kommt zurueck. An status
                    -- haengen dort Ausloeser, die bei 'approved' den Versand anstossen;
                    -- eine Zuruecknahme schreibt deshalb nie einen Status und legt
                    -- dort nichts an und loescht nichts.
                    IF r.vorgang <> 'UPDATE'
                       OR (r.vorher - v_technisch - 'bubble_id') <> (r.nachher - v_technisch - 'bubble_id') THEN
                        RAISE EXCEPTION 'aenderung_nicht_zuruecknehmbar: % %', r.tabelle, r.zeilen_id;
                    END IF;
                    IF v_jetzt IS NULL
                       OR (v_jetzt ->> 'bubble_id') IS DISTINCT FROM (r.nachher ->> 'bubble_id') THEN
                        RAISE EXCEPTION 'aenderung_konflikt: % %', r.tabelle, r.zeilen_id;
                    END IF;
                    EXECUTE format('UPDATE %s t SET bubble_id = $1 WHERE t.id::text = $2', r.tabelle)
                      USING r.vorher ->> 'bubble_id', r.zeilen_id;
                ELSIF r.vorgang = 'DELETE' THEN
                    IF v_jetzt IS NOT NULL THEN
                        RAISE EXCEPTION 'aenderung_konflikt: % %', r.tabelle, r.zeilen_id;
                    END IF;
                    EXECUTE format(
                        'INSERT INTO %1$s OVERRIDING SYSTEM VALUE '
                        'SELECT * FROM jsonb_populate_record(NULL::%1$s, $1)',
                        r.tabelle
                    ) USING r.vorher;
                ELSE
                    IF v_jetzt IS NULL OR (v_jetzt - v_technisch) <> (r.nachher - v_technisch) THEN
                        RAISE EXCEPTION 'aenderung_konflikt: % %', r.tabelle, r.zeilen_id;
                    END IF;
                    IF r.vorgang = 'INSERT' THEN
                        EXECUTE format('DELETE FROM %s t WHERE t.id::text = $1', r.tabelle)
                          USING r.zeilen_id;
                    ELSE
                        SELECT string_agg(quote_ident(a.attname), ', ' ORDER BY a.attnum)
                          INTO v_spalten
                          FROM pg_attribute a
                         WHERE a.attrelid = r.tabelle::regclass
                           AND a.attnum > 0 AND NOT a.attisdropped AND a.attname <> 'id';
                        EXECUTE format(
                            'UPDATE %1$s t SET (%2$s) = '
                            '(SELECT %2$s FROM jsonb_populate_record(NULL::%1$s, $1)) '
                            'WHERE t.id::text = $2',
                            r.tabelle, v_spalten
                        ) USING r.vorher, r.zeilen_id;
                    END IF;
                END IF;

                v_fortschritt := true;
                v_anzahl := v_anzahl + 1;
                v_eintraege := v_eintraege || jsonb_build_object(
                    'tabelle', r.tabelle,
                    'vorgang', r.vorgang,
                    'zeilen_id', r.zeilen_id,
                    'titel', coalesce(r.vorher, r.nachher) ->> 'title',
                    'name', coalesce(r.vorher, r.nachher) ->> 'name'
                );
            EXCEPTION WHEN foreign_key_violation THEN
                v_naechste := v_naechste || v_id;
                v_gesperrt := v_gesperrt || v_schluessel;
            END;
        END LOOP;

        EXIT WHEN cardinality(v_naechste) = 0;
        IF NOT v_fortschritt THEN
            -- kein Eintrag liess sich mehr anwenden: ein Verweis zeigt ins Leere
            SELECT * INTO r FROM public.ideen_aenderungen WHERE id = v_naechste[1];
            RAISE EXCEPTION 'aenderung_konflikt: % %', r.tabelle, r.zeilen_id;
        END IF;
        v_offen := v_naechste;
    END LOOP;

    -- Hat die Zuruecknahme Zeilen beruehrt, die nicht zur Gruppe gehoeren (etwa durch
    -- Loeschweitergabe auf spaeter Angelegtes), wird alles verworfen.
    SELECT z.tabelle, z.zeilen_id INTO v_fremd
      FROM public.ideen_aenderungen z
     WHERE z.id > v_start AND z.gruppe = v_zgruppe
       AND NOT EXISTS (
           SELECT 1 FROM public.ideen_aenderungen g
            WHERE g.gruppe = v_gruppe AND g.tabelle = z.tabelle AND g.zeilen_id = z.zeilen_id
       )
     ORDER BY z.id
     LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'aenderung_konflikt: % %', v_fremd.tabelle, v_fremd.zeilen_id;
    END IF;

    UPDATE public.ideen_aenderungen
       SET zurueckgenommen_am = clock_timestamp()
     WHERE gruppe = v_gruppe AND zurueckgenommen_am IS NULL;

    PERFORM set_config('ideen.zuruecknahme', '', true);

    RETURN jsonb_build_object(
        'gruppe', v_gruppe,
        'erstellt_am', v_erstellt,
        'anzahl', v_anzahl,
        'eintraege', v_eintraege
    );
END
$$;

REVOKE ALL ON FUNCTION public.ideen_zurueck(text) FROM PUBLIC;

-- Raeumt Protokollzeilen ab, die aelter sind als p_tage. Kein Zeitplan in dieser Stufe.
CREATE OR REPLACE FUNCTION public.ideen_aenderungen_aufraeumen(p_tage integer DEFAULT 30)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_anzahl integer;
BEGIN
    IF p_tage IS NULL OR p_tage < 1 THEN
        RAISE EXCEPTION 'ideen_aenderungen_aufraeumen: p_tage muss mindestens 1 sein';
    END IF;
    DELETE FROM public.ideen_aenderungen
     WHERE erstellt_am < now() - make_interval(days => p_tage);
    GET DIAGNOSTICS v_anzahl = ROW_COUNT;
    RETURN v_anzahl;
END
$$;

REVOKE ALL ON FUNCTION public.ideen_aenderungen_aufraeumen(integer) FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON FUNCTION public.ideen_zurueck(text) FROM anon;
        REVOKE ALL ON FUNCTION public.ideen_aenderungen_aufraeumen(integer) FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON FUNCTION public.ideen_zurueck(text) FROM authenticated;
        REVOKE ALL ON FUNCTION public.ideen_aenderungen_aufraeumen(integer) FROM authenticated;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        GRANT EXECUTE ON FUNCTION public.ideen_zurueck(text) TO service_role;
        GRANT EXECUTE ON FUNCTION public.ideen_aenderungen_aufraeumen(integer) TO service_role;
    END IF;
END
$$;

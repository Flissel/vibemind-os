-- 041_marketing_vorschlag_aus_sales.sql — F2: Recherche-Leads aus sales-claw
-- als Vorschlag in Marketings Staging (Spec 2026-09-03, E1/E5).
--
-- SECURITY DEFINER: sales_app darf diese Funktion rufen, aber KEINE Tabelle
-- in marketing.* lesen oder schreiben. Die Funktion prueft ihre Eingaben
-- selbst (Kappung, E-Mail-Form, Sperrliste) und schreibt ausschliesslich
-- audience_proposals + lead_candidates mit status pending_review.
GRANT USAGE ON SCHEMA marketing TO sales_app;

CREATE OR REPLACE FUNCTION marketing.vorschlag_aus_sales(
    p_name text, p_rationale text, p_kandidaten jsonb)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = marketing, compliance, pg_temp
AS $$
DECLARE
    v_proposal  uuid;
    v_k         jsonb;
    v_email     text;
    v_seen      text[] := '{}';
    v_ok        int := 0;
    v_gesperrt  int := 0;
    v_ungueltig int := 0;
BEGIN
    IF p_name IS NULL OR length(btrim(p_name)) = 0 THEN
        RAISE EXCEPTION 'vorschlag_aus_sales: name fehlt';
    END IF;
    IF p_kandidaten IS NULL OR jsonb_typeof(p_kandidaten) <> 'array' THEN
        RAISE EXCEPTION 'vorschlag_aus_sales: kandidaten muss ein JSON-Array sein';
    END IF;
    IF jsonb_array_length(p_kandidaten) > 500 THEN
        RAISE EXCEPTION 'vorschlag_aus_sales: mehr als 500 Kandidaten (Kappung wie _PROPOSAL_CANDIDATE_CAP)';
    END IF;

    -- Erst zaehlen, dann anlegen: ein leerer Vorschlag entsteht nie.
    FOR v_k IN SELECT * FROM jsonb_array_elements(p_kandidaten) LOOP
        v_email := lower(btrim(coalesce(v_k->>'email', '')));
        IF v_email = '' OR position('@' in v_email) = 0 THEN
            v_ungueltig := v_ungueltig + 1; CONTINUE;
        END IF;
        IF v_email = ANY(v_seen) THEN CONTINUE; END IF;
        v_seen := v_seen || v_email;
        IF compliance.ist_gesperrt('email:' || v_email) IS NOT NULL THEN
            v_gesperrt := v_gesperrt + 1; CONTINUE;
        END IF;
        v_ok := v_ok + 1;
    END LOOP;

    IF v_ok = 0 THEN
        RETURN jsonb_build_object('proposal_id', NULL, 'angenommen', 0,
                                  'uebersprungen_gesperrt', v_gesperrt,
                                  'uebersprungen_ungueltig', v_ungueltig);
    END IF;

    INSERT INTO marketing.audience_proposals
        (name, description, filter_dsl, rationale, source, status, hand_notes)
    VALUES (btrim(p_name),
            'Recherche-Leads aus sales-claw — ohne Einwilligung, nur fuer das Staging.',
            '{"quelle":"sales-claw"}'::jsonb,
            coalesce(p_rationale, ''),
            'hand:sales-claw', 'pending_review',
            format('sales-claw: %s uebernommen, %s gesperrt uebersprungen, %s ungueltig uebersprungen',
                   v_ok, v_gesperrt, v_ungueltig))
    RETURNING id INTO v_proposal;

    v_seen := '{}';
    FOR v_k IN SELECT * FROM jsonb_array_elements(p_kandidaten) LOOP
        v_email := lower(btrim(coalesce(v_k->>'email', '')));
        IF v_email = '' OR position('@' in v_email) = 0 OR v_email = ANY(v_seen) THEN CONTINUE; END IF;
        v_seen := v_seen || v_email;
        IF compliance.ist_gesperrt('email:' || v_email) IS NOT NULL THEN CONTINUE; END IF;
        INSERT INTO marketing.lead_candidates
            (proposal_id, email, display_name, company, domain, confidence,
             discovery_source, discovery_query, raw_enrichment)
        VALUES (v_proposal, v_email,
                coalesce(v_k->>'display_name', ''), coalesce(v_k->>'company', ''),
                split_part(v_email, '@', 2), 0.5,
                'sales-claw:recherche', coalesce(v_k->>'notiz', ''),
                coalesce(v_k->'raw', '{}'::jsonb))
        ON CONFLICT (proposal_id, email) DO NOTHING;
    END LOOP;

    RETURN jsonb_build_object('proposal_id', v_proposal, 'angenommen', v_ok,
                              'uebersprungen_gesperrt', v_gesperrt,
                              'uebersprungen_ungueltig', v_ungueltig);
END
$$;

REVOKE ALL ON FUNCTION marketing.vorschlag_aus_sales(text, text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION marketing.vorschlag_aus_sales(text, text, jsonb) TO sales_app;

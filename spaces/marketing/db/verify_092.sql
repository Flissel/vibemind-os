-- verify_092.sql — Postkanaele fuer Versandauftraege. Nur ueber migration_probe
-- (eine Transaktion + ROLLBACK). Probe-Texte tragen probe_092. Jeder Fall wirft
-- 'verify_092: ...', wenn er scheitert.

-- (a) CHECK kennt den neuen Kanal, ein unbekannter scheitert direkt
DO $$
DECLARE v_def text;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO v_def FROM pg_constraint
     WHERE conname = 'versandauftraege_kanal_check'
       AND conrelid = 'marketing.versandauftraege'::regclass;
    IF v_def IS NULL OR v_def NOT LIKE '%bluesky_post%' THEN
        RAISE EXCEPTION 'verify_092: (a) CHECK kennt bluesky_post nicht: %', v_def;
    END IF;
    BEGIN
        INSERT INTO marketing.versandauftraege
            (kanal, empfaenger, kennung, betreff, nachricht, textsumme)
        VALUES ('instagram_post', '', 'post:eigenes-profil', 'probe_092', 'probe_092', 'x');
        RAISE EXCEPTION 'verify_092: (a) instagram_post wurde direkt eingefuegt';
    EXCEPTION WHEN check_violation THEN
        NULL;
    END;
END $$;

-- (b) bis (i)
DO $$
DECLARE
    v_a jsonb;
    v_b jsonb;
    v_id uuid;
    v_kennung text;
BEGIN
    -- (b) Beitrag ohne Empfaenger
    v_a := marketing.versandauftrag_anlegen('bluesky_post', '', 'Hallo probe_092', 'Herbst probe_092');
    IF NOT coalesce((v_a->>'ok')::boolean, false) THEN
        RAISE EXCEPTION 'verify_092: (b) bluesky_post abgelehnt: %', v_a;
    END IF;
    v_id := (v_a->>'id')::uuid;
    SELECT kennung INTO v_kennung FROM marketing.versandauftraege WHERE id = v_id;
    IF v_kennung IS DISTINCT FROM 'post:eigenes-profil' THEN
        RAISE EXCEPTION 'verify_092: (b) Kennung ist %', v_kennung;
    END IF;

    -- (c) Wiederholung
    v_b := marketing.versandauftrag_anlegen('bluesky_post', '', 'Hallo probe_092', 'Herbst probe_092');
    IF NOT coalesce((v_b->>'wiederholung')::boolean, false) OR (v_b->>'id')::uuid <> v_id THEN
        RAISE EXCEPTION 'verify_092: (c) keine Wiederholung erkannt: %', v_b;
    END IF;

    -- (d) Empfaenger bei einem Postkanal
    v_b := marketing.versandauftrag_anlegen('mastodon_post', 'a@b.de', 'Hallo probe_092', 'Thema probe_092');
    IF coalesce((v_b->>'ok')::boolean, true) OR v_b->>'grund' NOT LIKE '%mastodon_post%' THEN
        RAISE EXCEPTION 'verify_092: (d) Empfaenger nicht abgelehnt oder Grund ohne Kanal: %', v_b;
    END IF;

    -- (e) devto ohne Betreff (Titel)
    v_b := marketing.versandauftrag_anlegen('devto_post', '', 'Text probe_092', '');
    IF coalesce((v_b->>'ok')::boolean, true) THEN
        RAISE EXCEPTION 'verify_092: (e) devto_post ohne Betreff angenommen: %', v_b;
    END IF;

    -- (f) unzulaessiger Kanal
    v_b := marketing.versandauftrag_anlegen('instagram_post', '', 'Text probe_092', 'Thema');
    IF coalesce((v_b->>'ok')::boolean, true) OR v_b->>'grund' NOT LIKE '%Unzulaessiger Kanal%' THEN
        RAISE EXCEPTION 'verify_092: (f) instagram_post: %', v_b;
    END IF;

    -- (g) linkedin_post wie vorher
    v_b := marketing.versandauftrag_anlegen('linkedin_post', '', 'Text probe_092 li', 'Thema probe_092');
    IF NOT coalesce((v_b->>'ok')::boolean, false) THEN
        RAISE EXCEPTION 'verify_092: (g) linkedin_post ohne Empfaenger abgelehnt: %', v_b;
    END IF;
    v_b := marketing.versandauftrag_anlegen('linkedin_post', 'a@b.de', 'Text probe_092 li2', 'Thema probe_092');
    IF coalesce((v_b->>'ok')::boolean, true) OR v_b->>'grund' NOT LIKE '%linkedin%' THEN
        RAISE EXCEPTION 'verify_092: (g) linkedin_post mit Empfaenger: %', v_b;
    END IF;

    -- (h) sales_app darf nicht anlegen
    IF has_function_privilege('sales_app',
           'marketing.versandauftrag_anlegen(text,text,text,text,text,text,text)', 'EXECUTE') THEN
        RAISE EXCEPTION 'verify_092: (h) sales_app darf versandauftrag_anlegen rufen';
    END IF;

    -- (i) sales-claw sieht den Auftrag mit dem neuen Kanal
    IF NOT EXISTS (SELECT 1 FROM marketing.versandauftraege_offen(50) o
                    WHERE o.id = v_id AND o.kanal = 'bluesky_post') THEN
        RAISE EXCEPTION 'verify_092: (i) versandauftraege_offen liefert den Auftrag nicht';
    END IF;
END $$;
SELECT 'verify 092: ok' AS ergebnis;

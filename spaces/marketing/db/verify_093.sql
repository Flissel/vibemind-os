-- verify_093.sql — Postkanal youtube_post. Nur ueber migration_probe (eine
-- Transaktion + ROLLBACK). Probe-Texte tragen probe_093. Jeder Fall wirft
-- 'verify_093: ...', wenn er scheitert. Zusammen mit verify_092 proben: die
-- Faelle dort muessen nach 093 unveraendert gelten.

-- (a) CHECK kennt youtube_post und weiter alle Kanaele aus 092
DO $$
DECLARE v_def text;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO v_def FROM pg_constraint
     WHERE conname = 'versandauftraege_kanal_check'
       AND conrelid = 'marketing.versandauftraege'::regclass;
    IF v_def IS NULL OR v_def NOT LIKE '%youtube_post%' OR v_def NOT LIKE '%devto_post%'
       OR v_def NOT LIKE '%linkedin_post%' THEN
        RAISE EXCEPTION 'verify_093: (a) CHECK unvollstaendig: %', v_def;
    END IF;
END $$;

-- (b) bis (g)
DO $$
DECLARE
    v_a jsonb;
    v_b jsonb;
    v_id uuid;
    v_zeile record;
BEGIN
    -- (b) youtube_post mit Reel und Titel, ohne Empfaenger
    v_a := marketing.versandauftrag_anlegen('youtube_post', '', 'Beschreibung probe_093',
                                            'Titel probe_093', 'vid-probe093-reel.mp4');
    IF NOT coalesce((v_a->>'ok')::boolean, false) THEN
        RAISE EXCEPTION 'verify_093: (b) youtube_post abgelehnt: %', v_a;
    END IF;
    v_id := (v_a->>'id')::uuid;
    SELECT kennung, medien_datei, betreff INTO v_zeile
      FROM marketing.versandauftraege WHERE id = v_id;
    IF v_zeile.kennung IS DISTINCT FROM 'post:eigenes-profil'
       OR v_zeile.medien_datei IS DISTINCT FROM 'vid-probe093-reel.mp4'
       OR v_zeile.betreff IS DISTINCT FROM 'Titel probe_093' THEN
        RAISE EXCEPTION 'verify_093: (b) Zeile falsch: %', v_zeile;
    END IF;

    -- (c) youtube_post ohne Video
    v_b := marketing.versandauftrag_anlegen('youtube_post', '', 'Beschreibung probe_093 c',
                                            'Titel probe_093');
    IF coalesce((v_b->>'ok')::boolean, true) OR v_b->>'grund' NOT LIKE '%Video%' THEN
        RAISE EXCEPTION 'verify_093: (c) youtube_post ohne Video: %', v_b;
    END IF;

    -- (d) youtube_post ohne Titel
    v_b := marketing.versandauftrag_anlegen('youtube_post', '', 'Beschreibung probe_093 d',
                                            '', 'vid-probe093-reel.mp4');
    IF coalesce((v_b->>'ok')::boolean, true) OR v_b->>'grund' NOT LIKE '%Titel%' THEN
        RAISE EXCEPTION 'verify_093: (d) youtube_post ohne Titel: %', v_b;
    END IF;

    -- (e) youtube_post mit Empfaenger
    v_b := marketing.versandauftrag_anlegen('youtube_post', 'a@b.de', 'Beschreibung probe_093 e',
                                            'Titel', 'vid-probe093-reel.mp4');
    IF coalesce((v_b->>'ok')::boolean, true) OR v_b->>'grund' NOT LIKE '%youtube_post%' THEN
        RAISE EXCEPTION 'verify_093: (e) Empfaenger nicht abgelehnt: %', v_b;
    END IF;

    -- (f) Die Absage fuer einen unbekannten Kanal nennt youtube_post
    v_b := marketing.versandauftrag_anlegen('instagram_post', '', 'Text probe_093', 'Thema');
    IF coalesce((v_b->>'ok')::boolean, true) OR v_b->>'grund' NOT LIKE '%youtube_post%' THEN
        RAISE EXCEPTION 'verify_093: (f) instagram_post: %', v_b;
    END IF;

    -- (g) sales-claw sieht den Auftrag mit dem neuen Kanal
    IF NOT EXISTS (SELECT 1 FROM marketing.versandauftraege_offen(50) o
                    WHERE o.id = v_id AND o.kanal = 'youtube_post') THEN
        RAISE EXCEPTION 'verify_093: (g) versandauftraege_offen liefert den Auftrag nicht';
    END IF;
END $$;
SELECT 'verify 093: ok' AS ergebnis;

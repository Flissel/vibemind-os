-- Nachweise fuer 097 (Reels aus dem Auftragsbuch ohne Bilder-Halt), nur ueber migration_probe
-- (096 + 097 + dieses Skript in einer Transaktion + ROLLBACK). Probe-Firma probe_097. Jeder Fall wirft
-- 'verify_097: ...', wenn er scheitert. Die Bilder laufen ueber den Funktionsweg aus 090
-- (reel_bild_naechster -> reel_bild_fertig / reel_bild_fehler), nie per UPDATE.
DO $$ BEGIN
  INSERT INTO marketing.mandanten (id, name, aktiv) VALUES ('probe_097', 'Probe 097', true);
END $$;

-- (1) mit auftrag, 2 von 4 Bildern fertig -> nach dem letzten Bild offen, briefing.bilder gesetzt
DO $$ DECLARE a uuid; j jsonb; i int; r marketing.video_auftraege; t text;
BEGIN
  a := marketing.video_auftrag_anlegen('probe_097', 'bilder', 'KI mit Auftrag',
         '{"ki":{"anzahl":4},"auftrag":"00000000-0000-0000-0000-000000000097"}', 'agent', 'ideen');
  UPDATE marketing.video_auftraege SET erstellt_am = '1999-01-01' WHERE id = a;
  PERFORM marketing.video_ideen_naechster('10 minutes');
  PERFORM marketing.video_ideen_setzen(a, '["eins","zwei","drei","vier"]');
  FOR i IN 1..4 LOOP
    j := marketing.reel_bild_naechster('10 minutes');
    IF (j->>'video_auftrag')::uuid IS DISTINCT FROM a OR (j->>'nr')::int <> i THEN
      RAISE EXCEPTION 'verify_097: (1) reel_bild_naechster liefert falsch: %', j; END IF;
    IF i IN (1, 4) THEN
      t := 'ki-' || left(a::text, 8) || '-' || i || '-1.jpg';
      IF NOT marketing.reel_bild_fertig((j->>'id')::uuid, t) THEN
        RAISE EXCEPTION 'verify_097: (1) reel_bild_fertig Nr % liefert false', i; END IF;
    ELSE
      IF NOT marketing.reel_bild_fehler((j->>'id')::uuid, 'Probe') THEN
        RAISE EXCEPTION 'verify_097: (1) reel_bild_fehler Nr % liefert false', i; END IF;
    END IF;
    IF i < 4 AND (SELECT status FROM marketing.video_auftraege WHERE id = a) <> 'bilder_laufen' THEN
      RAISE EXCEPTION 'verify_097: (1) vor dem letzten Bild muss bilder_laufen bleiben (Nr %)', i; END IF;
  END LOOP;
  SELECT * INTO r FROM marketing.video_auftraege WHERE id = a;
  IF r.status <> 'offen' THEN
    RAISE EXCEPTION 'verify_097: (1) mit auftrag muss offen sein, ist %', r.status; END IF;
  IF r.briefing->'bilder' IS DISTINCT FROM
     jsonb_build_array('ki-' || left(a::text, 8) || '-1-1.jpg', 'ki-' || left(a::text, 8) || '-4-1.jpg') THEN
    RAISE EXCEPTION 'verify_097: (1) briefing.bilder falsch: %', r.briefing->'bilder'; END IF;
  IF r.briefing->>'auftrag' IS NULL THEN
    RAISE EXCEPTION 'verify_097: (1) briefing.auftrag ging verloren'; END IF;
END $$;

-- (2) ohne auftrag -> wie bisher bilder_pruefen, keine Bilderliste
DO $$ DECLARE a uuid; j jsonb; i int; r marketing.video_auftraege; t text;
BEGIN
  a := marketing.video_auftrag_anlegen('probe_097', 'bilder', 'KI ohne Auftrag',
         '{"ki":{"anzahl":4}}', 'betreiber', 'ideen');
  UPDATE marketing.video_auftraege SET erstellt_am = '1999-01-02' WHERE id = a;
  PERFORM marketing.video_ideen_naechster('10 minutes');
  PERFORM marketing.video_ideen_setzen(a, '["eins","zwei","drei","vier"]');
  FOR i IN 1..4 LOOP
    j := marketing.reel_bild_naechster('10 minutes');
    IF (j->>'video_auftrag')::uuid IS DISTINCT FROM a OR (j->>'nr')::int <> i THEN
      RAISE EXCEPTION 'verify_097: (2) reel_bild_naechster liefert falsch: %', j; END IF;
    IF i IN (1, 4) THEN
      t := 'ki-' || left(a::text, 8) || '-' || i || '-1.jpg';
      PERFORM marketing.reel_bild_fertig((j->>'id')::uuid, t);
    ELSE
      PERFORM marketing.reel_bild_fehler((j->>'id')::uuid, 'Probe');
    END IF;
  END LOOP;
  SELECT * INTO r FROM marketing.video_auftraege WHERE id = a;
  IF r.status <> 'bilder_pruefen' THEN
    RAISE EXCEPTION 'verify_097: (2) ohne auftrag muss bilder_pruefen sein, ist %', r.status; END IF;
  IF r.briefing ? 'bilder' THEN
    RAISE EXCEPTION 'verify_097: (2) ohne auftrag darf briefing.bilder nicht gesetzt sein'; END IF;
END $$;

-- (3) mit auftrag, 0 Bilder fertig -> fehler mit Befund
DO $$ DECLARE a uuid; j jsonb; i int; r marketing.video_auftraege;
BEGIN
  a := marketing.video_auftrag_anlegen('probe_097', 'bilder', 'KI ohne Bilder',
         '{"ki":{"anzahl":3},"auftrag":"00000000-0000-0000-0000-000000000097"}', 'agent', 'ideen');
  UPDATE marketing.video_auftraege SET erstellt_am = '1999-01-03' WHERE id = a;
  PERFORM marketing.video_ideen_naechster('10 minutes');
  PERFORM marketing.video_ideen_setzen(a, '["eins","zwei","drei"]');
  FOR i IN 1..3 LOOP
    j := marketing.reel_bild_naechster('10 minutes');
    IF (j->>'video_auftrag')::uuid IS DISTINCT FROM a OR (j->>'nr')::int <> i THEN
      RAISE EXCEPTION 'verify_097: (3) reel_bild_naechster liefert falsch: %', j; END IF;
    PERFORM marketing.reel_bild_fehler((j->>'id')::uuid, 'Probe');
  END LOOP;
  SELECT * INTO r FROM marketing.video_auftraege WHERE id = a;
  IF r.status <> 'fehler' OR r.befund NOT LIKE '%Keine KI-Bilder%' THEN
    RAISE EXCEPTION 'verify_097: (3) 0 fertige Bilder muss fehler mit Befund sein: % / %', r.status, r.befund; END IF;
END $$;

-- Nachweise fuer 090 (Reels mit KI-Bildern), nur ueber migration_probe (eine Transaktion + ROLLBACK).
-- Probe-Firma probe_090 (und probe_090b als fremde Firma). Jeder Fall wirft 'verify_090: ...', wenn er
-- scheitert. erstellt_am liegt weit zurueck, damit echte Auftraege der Live-DB nicht zuerst kommen.
DO $$ BEGIN
  INSERT INTO marketing.mandanten (id, name, aktiv) VALUES ('probe_090', 'Probe 090', true),
                                                         ('probe_090b', 'Probe 090 fremd', true);
END $$;

-- (a) Status-Check und Tabelle; video_naechster uebergeht ideen, bilder_laufen und bilder_pruefen
DO $$ DECLARE a uuid; s text; n int; geworfen boolean := false; j jsonb;
BEGIN
  SELECT count(*) INTO n FROM information_schema.columns
   WHERE table_schema = 'marketing' AND table_name = 'reel_bilder'
     AND column_name IN ('id', 'video_auftrag', 'nr', 'idee', 'status', 'datei', 'versuch',
                         'vergeben_bis', 'befund', 'erstellt_am', 'geaendert_am');
  IF n <> 11 THEN RAISE EXCEPTION 'verify_090: reel_bilder hat % von 11 Spalten', n; END IF;
  SELECT count(*) INTO n FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
   WHERE ns.nspname = 'marketing' AND p.proname = 'video_auftrag_anlegen';
  IF n <> 1 THEN RAISE EXCEPTION 'verify_090: video_auftrag_anlegen gibt es % mal (soll 1)', n; END IF;
  -- alte Aufrufe mit fuenf Argumenten gehen weiter und legen offen an
  a := marketing.video_auftrag_anlegen('probe_090', 'bilder', 'Alt', '{"bilder":["x.jpg"]}', 'betreiber');
  IF (SELECT status FROM marketing.video_auftraege WHERE id = a) <> 'offen' THEN
    RAISE EXCEPTION 'verify_090: Anlegen ohne p_status muss offen sein'; END IF;
  DELETE FROM marketing.video_auftraege WHERE id = a;
  FOREACH s IN ARRAY ARRAY['ideen', 'bilder_laufen', 'bilder_pruefen'] LOOP
    INSERT INTO marketing.video_auftraege (mandant, art, titel, briefing, status, angelegt_von, erstellt_am)
    VALUES ('probe_090', 'bilder', 'KI ' || s, '{"ki":{"anzahl":3}}', s, 'betreiber', '1999-01-01');
  END LOOP;
  BEGIN
    INSERT INTO marketing.video_auftraege (mandant, art, titel, briefing, status, angelegt_von)
    VALUES ('probe_090', 'bilder', 'Quatsch', '{}', 'bilder_irgendwas', 'betreiber');
  EXCEPTION WHEN check_violation THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: unbekannter Status muss am Check scheitern'; END IF;
  -- Review Focus 1: der Video-Claw greift nie zu frueh zu
  j := marketing.video_naechster('10 minutes');
  IF j IS NOT NULL AND j->>'mandant' = 'probe_090' THEN
    RAISE EXCEPTION 'verify_090: video_naechster hat einen KI-Auftrag genommen: %', j; END IF;
  IF (SELECT count(*) FROM marketing.video_auftraege
       WHERE mandant = 'probe_090' AND status IN ('ideen', 'bilder_laufen', 'bilder_pruefen')) <> 3 THEN
    RAISE EXCEPTION 'verify_090: video_naechster hat einen KI-Status veraendert'; END IF;
  -- Datei-Check der Tabelle
  geworfen := false;
  BEGIN
    INSERT INTO marketing.reel_bilder (video_auftrag, nr, idee, datei)
    SELECT id, 1, 'x', '../etc/passwd' FROM marketing.video_auftraege
     WHERE mandant = 'probe_090' AND status = 'bilder_laufen';
  EXCEPTION WHEN check_violation THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: falscher Dateiname muss am Check scheitern'; END IF;
  DELETE FROM marketing.video_auftraege WHERE mandant = 'probe_090';
  -- ideen nur mit art bilder und ki.anzahl 3..5; andere Status nicht
  geworfen := false;
  BEGIN PERFORM marketing.video_auftrag_anlegen('probe_090', 'bilder', 'X', '{"ki":{"anzahl":6}}', 'agent', 'ideen');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: ideen mit anzahl 6 muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.video_auftrag_anlegen('probe_090', 'material', 'X', '{"ki":{"anzahl":4}}', 'agent', 'ideen');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: ideen mit art material muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.video_auftrag_anlegen('probe_090', 'bilder', 'X', '{"ki":{"anzahl":4}}', 'agent', 'fertig');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: Anlegen mit Status fertig muss werfen'; END IF;
END $$;

-- (b) Durchlauf: anlegen (ideen) -> ideen_naechster -> ideen_setzen (falsche Anzahl wirft) ->
-- 4x reel_bild_naechster, drei fertig, eins fehler -> Auftrag bilder_pruefen
DO $$ DECLARE a uuid; j jsonb; i int; bid uuid; geworfen boolean := false; r marketing.video_auftraege;
  ids uuid[] := '{}';
BEGIN
  a := marketing.video_auftrag_anlegen('probe_090', 'bilder', 'KI-Reel',
         '{"thema":"Herbst","ki":{"anzahl":4},"format":"quadrat"}', 'betreiber', 'ideen');
  UPDATE marketing.video_auftraege SET erstellt_am = '2000-01-01' WHERE id = a;
  IF (SELECT status FROM marketing.video_auftraege WHERE id = a) <> 'ideen' THEN
    RAISE EXCEPTION 'verify_090: Anlegen mit ideen muss ideen sein'; END IF;
  j := marketing.video_ideen_naechster('10 minutes');
  IF (j->>'id')::uuid IS DISTINCT FROM a OR j->>'mandant' <> 'probe_090' OR j->>'titel' <> 'KI-Reel'
     OR j->'briefing'->'ki'->>'anzahl' <> '4' THEN
    RAISE EXCEPTION 'verify_090: ideen_naechster liefert falsch: %', j; END IF;
  -- Sperre: kein Doppelabruf in der Frist
  j := marketing.video_ideen_naechster('10 minutes');
  IF j IS NOT NULL AND (j->>'id')::uuid = a THEN
    RAISE EXCEPTION 'verify_090: ideen_naechster liefert den Auftrag doppelt'; END IF;
  -- falsche Anzahl, leere oder zu lange Idee, kein Array -> Ausnahme, keine halben Zeilen
  BEGIN PERFORM marketing.video_ideen_setzen(a, '["eins","zwei","drei"]');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: ideen_setzen mit falscher Anzahl muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.video_ideen_setzen(a, '["eins","zwei","drei","  "]');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: ideen_setzen mit leerer Idee muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.video_ideen_setzen(a, jsonb_build_array('a', 'b', 'c', repeat('x', 601)));
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: ideen_setzen mit 601 Zeichen muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.video_ideen_setzen(a, '{"a":1}');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: ideen_setzen ohne Array muss werfen'; END IF;
  IF EXISTS (SELECT 1 FROM marketing.reel_bilder WHERE video_auftrag = a) THEN
    RAISE EXCEPTION 'verify_090: gescheitertes ideen_setzen hat Zeilen hinterlassen'; END IF;

  IF marketing.video_ideen_setzen(a, '[" Idee eins ","Idee zwei","Idee drei","Idee vier"]') <> 4 THEN
    RAISE EXCEPTION 'verify_090: ideen_setzen muss 4 liefern'; END IF;
  SELECT * INTO r FROM marketing.video_auftraege WHERE id = a;
  IF r.status <> 'bilder_laufen' OR r.vergeben_bis IS NOT NULL OR r.versuche <> 0 THEN
    RAISE EXCEPTION 'verify_090: nach ideen_setzen falscher Auftrag: %', row_to_json(r); END IF;
  IF (SELECT idee FROM marketing.reel_bilder WHERE video_auftrag = a AND nr = 1) <> 'Idee eins' THEN
    RAISE EXCEPTION 'verify_090: Idee nicht getrimmt'; END IF;
  -- zweimal setzen geht nicht (Status ist nicht mehr ideen)
  geworfen := false;
  BEGIN PERFORM marketing.video_ideen_setzen(a, '["a","b","c","d"]');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: ideen_setzen zweimal muss werfen'; END IF;

  FOR i IN 1..4 LOOP
    j := marketing.reel_bild_naechster('10 minutes');
    IF (j->>'video_auftrag')::uuid IS DISTINCT FROM a OR (j->>'nr')::int <> i OR (j->>'versuch')::int <> 1
       OR j->>'mandant' <> 'probe_090' OR j->>'format' <> 'quadrat' OR j->>'titel' <> 'KI-Reel'
       OR j->>'idee' IS NULL THEN
      RAISE EXCEPTION 'verify_090: reel_bild_naechster Nr % liefert falsch: %', i, j; END IF;
    ids := ids || (j->>'id')::uuid;
  END LOOP;
  j := marketing.reel_bild_naechster('10 minutes');
  IF j IS NOT NULL AND (j->>'video_auftrag')::uuid = a THEN
    RAISE EXCEPTION 'verify_090: reel_bild_naechster liefert ein Bild doppelt'; END IF;
  IF NOT marketing.reel_bild_verlaengern(ids[1], '10 minutes') THEN
    RAISE EXCEPTION 'verify_090: verlaengern in Arbeit muss true geben'; END IF;

  -- Datei mit falschem Praefix (fremder Auftrag, falsche Nr, falscher Versuch) wirft
  geworfen := false;
  BEGIN PERFORM marketing.reel_bild_fertig(ids[1], 'ki-ffffffff-1-1.jpg');
    IF left(a::text, 8) = 'ffffffff' THEN RAISE EXCEPTION 'zufall'; END IF;
  EXCEPTION WHEN raise_exception OR check_violation THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: fremdes Praefix muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.reel_bild_fertig(ids[1], 'ki-' || left(a::text, 8) || '-2-1.jpg');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: falsche Nr im Namen muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.reel_bild_fertig(ids[1], 'nl-' || left(a::text, 8) || '-1-1.jpg');
  EXCEPTION WHEN raise_exception OR check_violation THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: Newsletter-Praefix muss werfen'; END IF;

  FOR i IN 1..3 LOOP
    IF NOT marketing.reel_bild_fertig(ids[i], 'ki-' || left(a::text, 8) || '-' || i || '-1.jpg') THEN
      RAISE EXCEPTION 'verify_090: reel_bild_fertig Nr % muss true geben', i; END IF;
    IF (SELECT status FROM marketing.video_auftraege WHERE id = a) <> 'bilder_laufen' THEN
      RAISE EXCEPTION 'verify_090: Auftrag wechselt zu frueh (nach Bild %)', i; END IF;
  END LOOP;
  -- zweimal fertig: nicht mehr in Arbeit -> false
  IF marketing.reel_bild_fertig(ids[1], 'ki-' || left(a::text, 8) || '-1-1.jpg') THEN
    RAISE EXCEPTION 'verify_090: fertig zweimal muss false geben'; END IF;
  IF marketing.reel_bild_verlaengern(ids[1], '10 minutes') THEN
    RAISE EXCEPTION 'verify_090: verlaengern bei fertig muss false geben'; END IF;
  IF NOT marketing.reel_bild_fehler(ids[4], '  ComfyUI Zeitlimit  ') THEN
    RAISE EXCEPTION 'verify_090: reel_bild_fehler muss true geben'; END IF;
  IF (SELECT befund FROM marketing.reel_bilder WHERE id = ids[4]) <> 'ComfyUI Zeitlimit'
     OR (SELECT status FROM marketing.reel_bilder WHERE id = ids[4]) <> 'fehler' THEN
    RAISE EXCEPTION 'verify_090: Fehlerbild falsch vermerkt'; END IF;
  IF (SELECT status FROM marketing.video_auftraege WHERE id = a) <> 'bilder_pruefen' THEN
    RAISE EXCEPTION 'verify_090: nach 3 fertig + 1 fehler muss der Auftrag bilder_pruefen sein'; END IF;
  IF marketing.reel_bild_fehler(ids[4], 'nochmal') THEN
    RAISE EXCEPTION 'verify_090: fehler bei fehler muss false geben'; END IF;
END $$;

-- (b2) Ideen-Fehler: zwei Versuche, dann fehler; Bild-Frist: zwei Vergaben, dann fehler
DO $$ DECLARE a uuid; j jsonb; s text; bid uuid; n int;
BEGIN
  a := marketing.video_auftrag_anlegen('probe_090', 'bilder', 'Ideenlos', '{"ki":{"anzahl":3}}', 'agent', 'ideen');
  UPDATE marketing.video_auftraege SET erstellt_am = '2000-01-01' WHERE id = a;
  j := marketing.video_ideen_naechster('10 minutes');
  IF (j->>'id')::uuid IS DISTINCT FROM a THEN RAISE EXCEPTION 'verify_090: ideen_naechster b2: %', j; END IF;
  s := marketing.video_ideen_fehler(a, 'Shim 429');
  IF s <> 'ideen' THEN RAISE EXCEPTION 'verify_090: erster Ideen-Fehler muss ideen bleiben, war %', s; END IF;
  j := marketing.video_ideen_naechster('10 minutes');
  IF (j->>'id')::uuid IS DISTINCT FROM a THEN
    RAISE EXCEPTION 'verify_090: nach erstem Fehler muss der Auftrag wieder kommen: %', j; END IF;
  s := marketing.video_ideen_fehler(a, 'Ollama tot');
  IF s <> 'fehler' OR (SELECT befund FROM marketing.video_auftraege WHERE id = a)
                      NOT LIKE 'Bildideen konnten nicht erzeugt werden%' THEN
    RAISE EXCEPTION 'verify_090: zweiter Ideen-Fehler muss fehler sein (%)', s; END IF;
  IF marketing.video_ideen_fehler(a, 'x') IS NOT NULL THEN
    RAISE EXCEPTION 'verify_090: Ideen-Fehler ausserhalb ideen muss NULL geben'; END IF;

  -- abgelaufene Ideen-Frist nach zwei Vergaben -> fehler beim naechsten Abruf
  a := marketing.video_auftrag_anlegen('probe_090', 'bilder', 'Haengt', '{"ki":{"anzahl":3}}', 'agent', 'ideen');
  UPDATE marketing.video_auftraege SET erstellt_am = '2000-01-01', versuche = 2,
         vergeben_bis = now() - interval '1 minute' WHERE id = a;
  j := marketing.video_ideen_naechster('10 minutes');
  IF j IS NOT NULL AND (j->>'id')::uuid = a THEN
    RAISE EXCEPTION 'verify_090: Auftrag nach zwei Ideen-Vergaben darf nicht mehr kommen'; END IF;
  IF (SELECT status FROM marketing.video_auftraege WHERE id = a) <> 'fehler' THEN
    RAISE EXCEPTION 'verify_090: abgelaufene Ideen nach zwei Vergaben muessen fehler sein'; END IF;

  -- Bild: zwei Vergaben ohne Ergebnis -> fehler, Auftrag rollt auf bilder_pruefen
  a := marketing.video_auftrag_anlegen('probe_090', 'bilder', 'Bildlos', '{"ki":{"anzahl":3}}', 'agent', 'ideen');
  UPDATE marketing.video_auftraege SET erstellt_am = '1999-06-01' WHERE id = a;
  PERFORM marketing.video_ideen_setzen(a, '["a","b","c"]');
  FOR n IN 1..3 LOOP
    j := marketing.reel_bild_naechster('10 minutes');
    IF (j->>'video_auftrag')::uuid IS DISTINCT FROM a THEN
      RAISE EXCEPTION 'verify_090: b2 reel_bild_naechster: %', j; END IF;
    IF n < 3 THEN PERFORM marketing.reel_bild_fertig((j->>'id')::uuid,
                    'ki-' || left(a::text, 8) || '-' || (j->>'nr') || '-1.jpg'); END IF;
    bid := (j->>'id')::uuid;
  END LOOP;
  IF (j->>'format') <> 'reel' THEN RAISE EXCEPTION 'verify_090: ohne briefing.format muss reel kommen'; END IF;
  UPDATE marketing.reel_bilder SET vergeben_bis = now() - interval '1 minute' WHERE id = bid;
  j := marketing.reel_bild_naechster('10 minutes');
  IF (j->>'id')::uuid IS DISTINCT FROM bid THEN
    RAISE EXCEPTION 'verify_090: abgelaufenes Bild muss wieder kommen: %', j; END IF;
  UPDATE marketing.reel_bilder SET vergeben_bis = now() - interval '1 minute' WHERE id = bid;
  j := marketing.reel_bild_naechster('10 minutes');
  IF j IS NOT NULL AND (j->>'id')::uuid = bid THEN
    RAISE EXCEPTION 'verify_090: Bild nach zwei Vergaben darf nicht mehr kommen'; END IF;
  IF (SELECT status FROM marketing.reel_bilder WHERE id = bid) <> 'fehler'
     OR (SELECT status FROM marketing.video_auftraege WHERE id = a) <> 'bilder_pruefen' THEN
    RAISE EXCEPTION 'verify_090: zwei Vergaben ohne Ergebnis muessen fehler + bilder_pruefen geben'; END IF;
END $$;

-- (c) reel_bild_neu: alte Zeile verworfen, neue mit versuch 2, Auftrag bilder_laufen; danach wieder
-- fertig -> bilder_pruefen. Der neue Dateiname ueberschreibt nichts (Review Focus 2).
DO $$ DECLARE a uuid; alt uuid; neu uuid; j jsonb; n int; geworfen boolean := false;
BEGIN
  SELECT id INTO a FROM marketing.video_auftraege WHERE mandant = 'probe_090' AND titel = 'KI-Reel';
  SELECT id INTO alt FROM marketing.reel_bilder WHERE video_auftrag = a AND nr = 2;
  neu := marketing.reel_bild_neu(alt, 'probe_090', '  Andere Idee  ');
  IF neu IS NULL OR neu = alt THEN RAISE EXCEPTION 'verify_090: reel_bild_neu ohne neue Zeile'; END IF;
  IF (SELECT status FROM marketing.reel_bilder WHERE id = alt) <> 'verworfen'
     OR (SELECT datei FROM marketing.reel_bilder WHERE id = alt) IS DISTINCT FROM 'ki-' || left(a::text, 8) || '-2-1.jpg' THEN
    RAISE EXCEPTION 'verify_090: alte Zeile nicht verworfen oder Datei verloren'; END IF;
  IF (SELECT row(nr, versuch, status, idee, datei)::text FROM marketing.reel_bilder WHERE id = neu)
     <> row(2, 2, 'offen', 'Andere Idee', NULL::text)::text THEN
    RAISE EXCEPTION 'verify_090: neue Zeile falsch: %',
      (SELECT row_to_json(b) FROM marketing.reel_bilder b WHERE id = neu); END IF;
  IF (SELECT status FROM marketing.video_auftraege WHERE id = a) <> 'bilder_laufen' THEN
    RAISE EXCEPTION 'verify_090: nach reel_bild_neu muss der Auftrag bilder_laufen sein'; END IF;
  -- waehrend bilder_laufen geht kein weiteres neu
  BEGIN PERFORM marketing.reel_bild_neu((SELECT id FROM marketing.reel_bilder WHERE video_auftrag = a AND nr = 1),
                                        'probe_090');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: neu bei bilder_laufen muss werfen'; END IF;
  -- Fehlerbild neu ohne andere Idee: Idee bleibt
  UPDATE marketing.video_auftraege SET status = 'bilder_pruefen' WHERE id = a;  -- nur fuer den Fall
  alt := (SELECT id FROM marketing.reel_bilder WHERE video_auftrag = a AND nr = 4 AND status = 'fehler');
  neu := marketing.reel_bild_neu(alt, 'probe_090');
  IF (SELECT row(nr, versuch, idee)::text FROM marketing.reel_bilder WHERE id = neu)
     <> row(4, 2, 'Idee vier')::text THEN
    RAISE EXCEPTION 'verify_090: Fehlerbild neu ohne Idee falsch'; END IF;
  SELECT count(*) INTO n FROM marketing.reel_bilder WHERE video_auftrag = a AND status <> 'verworfen';
  IF n <> 4 THEN RAISE EXCEPTION 'verify_090: aktive Bilder % statt 4', n; END IF;

  j := marketing.reel_bild_naechster('10 minutes');
  IF (j->>'id')::uuid IS DISTINCT FROM (SELECT id FROM marketing.reel_bilder WHERE video_auftrag = a AND nr = 2
                                          AND status <> 'verworfen')
     OR (j->>'versuch')::int <> 2 OR j->>'idee' <> 'Andere Idee' THEN
    RAISE EXCEPTION 'verify_090: naechster nach neu: %', j; END IF;
  -- der alte Dateiname gilt fuer den neuen Versuch nicht
  geworfen := false;
  BEGIN PERFORM marketing.reel_bild_fertig((j->>'id')::uuid, 'ki-' || left(a::text, 8) || '-2-1.jpg');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: alter Dateiname beim neuen Versuch muss werfen'; END IF;
  PERFORM marketing.reel_bild_fertig((j->>'id')::uuid, 'ki-' || left(a::text, 8) || '-2-2.jpg');
  IF (SELECT status FROM marketing.video_auftraege WHERE id = a) <> 'bilder_laufen' THEN
    RAISE EXCEPTION 'verify_090: Auftrag darf erst nach allen aktiven Bildern wechseln'; END IF;
  j := marketing.reel_bild_naechster('10 minutes');
  IF (j->>'nr')::int <> 4 THEN RAISE EXCEPTION 'verify_090: naechster nach neu (Nr 4): %', j; END IF;
  PERFORM marketing.reel_bild_fertig((j->>'id')::uuid, 'ki-' || left(a::text, 8) || '-4-2.jpg');
  IF (SELECT status FROM marketing.video_auftraege WHERE id = a) <> 'bilder_pruefen' THEN
    RAISE EXCEPTION 'verify_090: nach neu und fertig muss der Auftrag bilder_pruefen sein'; END IF;
END $$;

-- (d) video_ki_freigeben: briefing.bilder = fertige Bilder in nr-Reihenfolge, Status offen.
-- Teilweiser Fehlschlag (Review Focus 5): nur die fertigen kommen mit.
DO $$ DECLARE a uuid; p text; r marketing.video_auftraege; j jsonb;
BEGIN
  SELECT id INTO a FROM marketing.video_auftraege WHERE mandant = 'probe_090' AND titel = 'KI-Reel';
  p := 'ki-' || left(a::text, 8);
  -- Bild 3 scheitert beim neuen Versuch: freigeben nimmt nur 1, 2, 4
  PERFORM marketing.reel_bild_neu((SELECT id FROM marketing.reel_bilder WHERE video_auftrag = a AND nr = 3),
                                  'probe_090');
  j := marketing.reel_bild_naechster('10 minutes');
  PERFORM marketing.reel_bild_fehler((j->>'id')::uuid, 'Zeitlimit');
  IF NOT marketing.video_ki_freigeben(a, 'probe_090') THEN
    RAISE EXCEPTION 'verify_090: freigeben muss true geben'; END IF;
  SELECT * INTO r FROM marketing.video_auftraege WHERE id = a;
  IF r.status <> 'offen' OR r.briefing->'bilder' <> jsonb_build_array(p || '-1-1.jpg', p || '-2-2.jpg', p || '-4-2.jpg')
     OR r.briefing->>'thema' <> 'Herbst' OR r.briefing->>'format' <> 'quadrat' OR r.versuche <> 0
     OR r.vergeben_bis IS NOT NULL THEN
    RAISE EXCEPTION 'verify_090: freigeben falsch: %', row_to_json(r); END IF;
  -- jetzt nimmt der Video-Claw ihn
  UPDATE marketing.video_auftraege SET erstellt_am = '1990-01-01' WHERE id = a;
  j := marketing.video_naechster('10 minutes');
  IF (j->>'id')::uuid IS DISTINCT FROM a OR (j->>'versuch')::int <> 1 THEN
    RAISE EXCEPTION 'verify_090: nach freigeben muss video_naechster den Auftrag liefern: %', j; END IF;
END $$;

-- (e) falscher Status, fremde Firma, ohne fertiges Bild -> Ausnahme
DO $$ DECLARE a uuid; b uuid; bid uuid; j jsonb; geworfen boolean;
BEGIN
  a := marketing.video_auftrag_anlegen('probe_090', 'bilder', 'Fremd', '{"ki":{"anzahl":3}}', 'betreiber', 'ideen');
  UPDATE marketing.video_auftraege SET erstellt_am = '2000-01-01' WHERE id = a;
  -- freigeben bei ideen (falscher Status)
  geworfen := false;
  BEGIN PERFORM marketing.video_ki_freigeben(a, 'probe_090');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: freigeben bei ideen muss werfen'; END IF;
  -- ideen_setzen bei einem offenen Auftrag (falscher Status)
  b := marketing.video_auftrag_anlegen('probe_090', 'bilder', 'Offen', '{"bilder":["x.jpg"]}', 'betreiber');
  geworfen := false;
  BEGIN PERFORM marketing.video_ideen_setzen(b, '["a","b","c"]');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: ideen_setzen bei offen muss werfen'; END IF;
  DELETE FROM marketing.video_auftraege WHERE id = b;

  PERFORM marketing.video_ideen_setzen(a, '["a","b","c"]');
  FOR i IN 1..3 LOOP
    j := marketing.reel_bild_naechster('10 minutes');
    PERFORM marketing.reel_bild_fehler((j->>'id')::uuid, 'kaputt');
  END LOOP;
  IF (SELECT status FROM marketing.video_auftraege WHERE id = a) <> 'bilder_pruefen' THEN
    RAISE EXCEPTION 'verify_090: drei Fehlerbilder muessen bilder_pruefen geben'; END IF;
  -- kein fertiges Bild -> freigeben wirft
  geworfen := false;
  BEGIN PERFORM marketing.video_ki_freigeben(a, 'probe_090');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: freigeben ohne fertiges Bild muss werfen'; END IF;
  -- fremde Firma
  bid := (SELECT id FROM marketing.reel_bilder WHERE video_auftrag = a AND nr = 1);
  geworfen := false;
  BEGIN PERFORM marketing.reel_bild_neu(bid, 'probe_090b');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: reel_bild_neu fuer fremde Firma muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.video_ki_freigeben(a, 'probe_090b');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: freigeben fuer fremde Firma muss werfen'; END IF;
  IF (SELECT status FROM marketing.reel_bilder WHERE id = bid) <> 'fehler' THEN
    RAISE EXCEPTION 'verify_090: fremde Firma hat das Bild veraendert'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.reel_bild_neu(gen_random_uuid(), 'probe_090');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: reel_bild_neu fuer unbekanntes Bild muss werfen'; END IF;
  -- zu lange andere Idee
  geworfen := false;
  BEGIN PERFORM marketing.reel_bild_neu(bid, 'probe_090', repeat('x', 601));
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: andere Idee mit 601 Zeichen muss werfen'; END IF;
  -- verworfenes Bild kann nicht neu
  PERFORM marketing.reel_bild_neu(bid, 'probe_090');
  UPDATE marketing.video_auftraege SET status = 'bilder_pruefen' WHERE id = a;  -- nur fuer den Fall
  geworfen := false;
  BEGIN PERFORM marketing.reel_bild_neu(bid, 'probe_090');
  EXCEPTION WHEN raise_exception OR unique_violation THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_090: verworfenes Bild neu muss werfen'; END IF;
END $$;

-- (f) video_abbrechen bei ideen, bilder_laufen, bilder_pruefen; abgebrochene Auftraege liefern keine
-- Bilder mehr, und ein laufendes Bild merkt es (verlaengern/fertig false)
DO $$ DECLARE a uuid; s text; j jsonb;
BEGIN
  FOREACH s IN ARRAY ARRAY['ideen', 'bilder_laufen', 'bilder_pruefen'] LOOP
    INSERT INTO marketing.video_auftraege (mandant, art, titel, briefing, status, angelegt_von)
    VALUES ('probe_090', 'bilder', 'Abbruch ' || s, '{"ki":{"anzahl":3}}', s, 'betreiber') RETURNING id INTO a;
    IF marketing.video_abbrechen(a) <> 'abgebrochen' THEN
      RAISE EXCEPTION 'verify_090: abbrechen bei % geht nicht', s; END IF;
  END LOOP;
  a := marketing.video_auftrag_anlegen('probe_090', 'bilder', 'Abbruch laufend', '{"ki":{"anzahl":3}}', 'betreiber', 'ideen');
  UPDATE marketing.video_auftraege SET erstellt_am = '2000-01-01' WHERE id = a;
  PERFORM marketing.video_ideen_setzen(a, '["a","b","c"]');
  j := marketing.reel_bild_naechster('10 minutes');
  IF (j->>'video_auftrag')::uuid IS DISTINCT FROM a THEN RAISE EXCEPTION 'verify_090: f naechster: %', j; END IF;
  PERFORM marketing.video_abbrechen(a);
  IF marketing.reel_bild_verlaengern((j->>'id')::uuid, '10 minutes') THEN
    RAISE EXCEPTION 'verify_090: verlaengern nach Abbruch muss false geben'; END IF;
  IF marketing.reel_bild_fertig((j->>'id')::uuid, 'ki-' || left(a::text, 8) || '-1-1.jpg') THEN
    RAISE EXCEPTION 'verify_090: fertig nach Abbruch muss false geben'; END IF;
  j := marketing.reel_bild_naechster('10 minutes');
  IF j IS NOT NULL AND (j->>'video_auftrag')::uuid = a THEN
    RAISE EXCEPTION 'verify_090: abgebrochener Auftrag liefert noch Bilder'; END IF;
  IF (SELECT status FROM marketing.video_auftraege WHERE id = a) <> 'abgebrochen' THEN
    RAISE EXCEPTION 'verify_090: Bild-Funktionen haben den Abbruch ueberschrieben'; END IF;
END $$;

-- (g) Rechte: niemand ausser dem Eigentuemer
DO $$ DECLARE f text;
BEGIN
  FOREACH f IN ARRAY ARRAY[
      'marketing.video_auftrag_anlegen(text,text,text,jsonb,text,text)',
      'marketing.video_ideen_naechster(interval)', 'marketing.video_ideen_setzen(uuid,jsonb)',
      'marketing.video_ideen_fehler(uuid,text)', 'marketing.reel_bild_naechster(interval)',
      'marketing.reel_bild_verlaengern(uuid,interval)', 'marketing.reel_bild_fertig(uuid,text)',
      'marketing.reel_bild_fehler(uuid,text)', 'marketing.reel_bild_neu(uuid,text,text)',
      'marketing.video_ki_freigeben(uuid,text)', 'marketing.video_abbrechen(uuid)'] LOOP
    IF has_function_privilege('public', f, 'EXECUTE') THEN
      RAISE EXCEPTION 'verify_090: % ist fuer PUBLIC ausfuehrbar', f; END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sales_app')
       AND has_function_privilege('sales_app', f, 'EXECUTE') THEN
      RAISE EXCEPTION 'verify_090: sales_app darf % ausfuehren', f; END IF;
  END LOOP;
  RAISE NOTICE 'verify_090: ok';
END $$;

SELECT 'verify_090: ok' AS ergebnis;

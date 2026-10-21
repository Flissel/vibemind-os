-- Nachweise fuer 082 (Reels: Vorgaenger, Aenderungswunsch, Archiv, Vorschaubild), nur ueber migration_probe
-- (eine Transaktion + ROLLBACK). Probe-Firma probe_082. Jeder Fall wirft 'verify_082: ...', wenn er scheitert.
DO $$ BEGIN
  INSERT INTO marketing.mandanten (id, name, aktiv) VALUES ('probe_082', 'Probe 082', true);
END $$;

-- (a) Spalten existieren, Checks gelten: Wunsch mit 501 Zeichen und falscher Poster-Name werden abgelehnt
DO $$ DECLARE a uuid; geworfen boolean := false; n int;
BEGIN
  SELECT count(*) INTO n FROM information_schema.columns
   WHERE table_schema = 'marketing' AND table_name = 'video_auftraege'
     AND column_name IN ('vorgaenger', 'aenderungswunsch', 'archiviert', 'poster');
  IF n <> 4 THEN RAISE EXCEPTION 'verify_082: es fehlen Spalten (% von 4)', n; END IF;
  a := marketing.video_auftrag_anlegen('probe_082', 'bilder', 'Checks', '{}', 'betreiber');
  IF (SELECT aenderungswunsch <> '' OR archiviert OR poster IS NOT NULL OR vorgaenger IS NOT NULL
        FROM marketing.video_auftraege WHERE id = a) THEN
    RAISE EXCEPTION 'verify_082: Vorgaben der neuen Spalten falsch'; END IF;
  UPDATE marketing.video_auftraege SET aenderungswunsch = repeat('w', 500) WHERE id = a;
  BEGIN
    UPDATE marketing.video_auftraege SET aenderungswunsch = repeat('w', 501) WHERE id = a;
  EXCEPTION WHEN check_violation THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_082: Wunsch mit 501 Zeichen muss am Check scheitern'; END IF;
  geworfen := false;
  BEGIN
    UPDATE marketing.video_auftraege SET poster = '../etc/passwd' WHERE id = a;
  EXCEPTION WHEN check_violation THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_082: falscher Poster-Name muss am Check scheitern'; END IF;
  geworfen := false;
  BEGIN
    UPDATE marketing.video_auftraege SET poster = 'vid-0123abcd-x.mp4' WHERE id = a;
  EXCEPTION WHEN check_violation THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_082: Poster muss .jpg sein'; END IF;
END $$;

-- (b) video_nochmal: fertig+Wunsch -> neuer offener Auftrag; fertig ohne Wunsch -> Ausnahme;
-- fehler ohne Wunsch -> neuer Auftrag; in_arbeit/offen -> Ausnahme
DO $$ DECLARE alt uuid; neu uuid; f uuid; l uuid; r marketing.video_auftraege; geworfen boolean;
BEGIN
  INSERT INTO marketing.video_auftraege (mandant, art, titel, briefing, status, datei, angelegt_von)
  VALUES ('probe_082', 'kurzfassung', 'Herbst', '{"thema":"bAV","stimme":"standard"}', 'fertig',
          'vid-0123abcd-herbst.mp4', 'agent') RETURNING id INTO alt;
  neu := marketing.video_nochmal(alt, '  kuerzer, anderer Einstieg  ', 'betreiber');
  SELECT * INTO r FROM marketing.video_auftraege WHERE id = neu;
  IF neu IS NULL OR neu = alt OR r.status <> 'offen' OR r.vorgaenger IS DISTINCT FROM alt
     OR r.aenderungswunsch <> 'kuerzer, anderer Einstieg' OR r.angelegt_von <> 'betreiber'
     OR r.mandant <> 'probe_082' OR r.art <> 'kurzfassung' OR r.titel <> 'Herbst'
     OR r.briefing <> '{"thema":"bAV","stimme":"standard"}'::jsonb OR r.datei IS NOT NULL THEN
    RAISE EXCEPTION 'verify_082: nochmal aus fertig hat falschen Auftrag angelegt: %', row_to_json(r); END IF;
  IF (SELECT status FROM marketing.video_auftraege WHERE id = alt) <> 'fertig' THEN
    RAISE EXCEPTION 'verify_082: Status des alten Auftrags hat sich geaendert'; END IF;

  geworfen := false;
  BEGIN PERFORM marketing.video_nochmal(alt, '   ', 'betreiber');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_082: fertig ohne Wunsch muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.video_nochmal(alt, NULL, 'betreiber');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_082: fertig mit NULL-Wunsch muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.video_nochmal(alt, repeat('w', 501), 'betreiber');
  EXCEPTION WHEN raise_exception OR check_violation THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_082: Wunsch mit 501 Zeichen muss werfen'; END IF;
  geworfen := false;
  -- eigener fertiger Auftrag ohne laufenden Bau, sonst greift die Doppelklick-Sperre zuerst
  INSERT INTO marketing.video_auftraege (mandant, art, titel, briefing, status, datei, angelegt_von)
  VALUES ('probe_082', 'bilder', 'Von', '{}', 'fertig', 'vid-0123abcd-von.mp4', 'agent') RETURNING id INTO l;
  BEGIN PERFORM marketing.video_nochmal(l, 'x', 'jemand');
  EXCEPTION WHEN check_violation THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_082: angelegt_von ausserhalb agent|betreiber muss werfen'; END IF;

  INSERT INTO marketing.video_auftraege (mandant, art, titel, briefing, status, befund, angelegt_von)
  VALUES ('probe_082', 'material', 'Kaputt', '{}', 'fehler', 'Laura 503', 'betreiber') RETURNING id INTO f;
  neu := marketing.video_nochmal(f, '', 'agent');
  SELECT * INTO r FROM marketing.video_auftraege WHERE id = neu;
  IF r.status <> 'offen' OR r.vorgaenger IS DISTINCT FROM f OR r.aenderungswunsch <> ''
     OR r.angelegt_von <> 'agent' THEN
    RAISE EXCEPTION 'verify_082: nochmal aus fehler ohne Wunsch: %', row_to_json(r); END IF;
  -- Doppelklick: fuer f laeuft schon ein Bau (neu ist offen) -> Ausnahme, kein zweiter Auftrag
  geworfen := false;
  BEGIN PERFORM marketing.video_nochmal(f, NULL, 'agent');
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'Es läuft schon ein neuer Bau für dieses Reel%' THEN RAISE; END IF;
    geworfen := true;
  END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_082: zweites nochmal bei laufendem Bau muss werfen'; END IF;
  IF (SELECT count(*) FROM marketing.video_auftraege WHERE vorgaenger = f) <> 1 THEN
    RAISE EXCEPTION 'verify_082: Doppelklick hat einen zweiten Auftrag angelegt'; END IF;
  -- auch in_arbeit sperrt; ist der Bau erledigt (fehler), geht es wieder, auch mit NULL-Wunsch
  UPDATE marketing.video_auftraege SET status = 'in_arbeit', vergeben_bis = now() + interval '5 minutes'
   WHERE id = neu;
  geworfen := false;
  BEGIN PERFORM marketing.video_nochmal(f, '', 'agent');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_082: nochmal bei Bau in_arbeit muss werfen'; END IF;
  UPDATE marketing.video_auftraege SET status = 'fehler', vergeben_bis = NULL WHERE id = neu;
  IF marketing.video_nochmal(f, NULL, 'agent') IS NULL THEN
    RAISE EXCEPTION 'verify_082: nochmal aus fehler mit NULL-Wunsch muss gehen'; END IF;

  -- offen und abgebrochen -> Ausnahme
  geworfen := false;
  BEGIN PERFORM marketing.video_nochmal(
          marketing.video_auftrag_anlegen('probe_082', 'bilder', 'Offen', '{}', 'betreiber'), 'x', 'betreiber');
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'Nur fertige oder fehlgeschlagene Reels lassen sich neu bauen%' THEN RAISE; END IF;
    geworfen := true;
  END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_082: nochmal bei offen muss werfen'; END IF;
  geworfen := false;
  INSERT INTO marketing.video_auftraege (mandant, art, titel, briefing, status, angelegt_von)
  VALUES ('probe_082', 'bilder', 'Weg', '{}', 'abgebrochen', 'betreiber') RETURNING id INTO l;
  BEGIN PERFORM marketing.video_nochmal(l, 'x', 'betreiber');
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'Nur fertige oder fehlgeschlagene Reels lassen sich neu bauen%' THEN RAISE; END IF;
    geworfen := true;
  END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_082: nochmal bei abgebrochen muss werfen'; END IF;

  INSERT INTO marketing.video_auftraege (mandant, art, titel, briefing, status, vergeben_bis, angelegt_von)
  VALUES ('probe_082', 'bilder', 'Laeuft', '{}', 'in_arbeit', now() + interval '10 minutes', 'betreiber')
  RETURNING id INTO l;
  geworfen := false;
  BEGIN PERFORM marketing.video_nochmal(l, 'anders', 'betreiber');
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'Nur fertige oder fehlgeschlagene Reels lassen sich neu bauen%' THEN RAISE; END IF;
    geworfen := true;
  END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_082: nochmal bei in_arbeit muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.video_nochmal(gen_random_uuid(), 'anders', 'betreiber');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_082: nochmal bei unbekanntem Auftrag muss werfen'; END IF;
END $$;

-- (b2) Neu versuchen einer gescheiterten Ueberarbeitung wiederholt dieselbe Ueberarbeitung (Vorgaenger und
-- Wunsch vom gescheiterten Auftrag); mit neuem Wunsch wird der gescheiterte selbst der Vorgaenger.
DO $$ DECLARE orig uuid; rev uuid; neu uuid; r marketing.video_auftraege; geworfen boolean := false;
BEGIN
  INSERT INTO marketing.video_auftraege (mandant, art, titel, briefing, status, datei, angelegt_von)
  VALUES ('probe_082', 'bilder', 'Original', '{"thema":"o"}', 'fertig', 'vid-0123abcd-original.mp4', 'agent')
  RETURNING id INTO orig;
  rev := marketing.video_nochmal(orig, 'kuerzer', 'betreiber');
  UPDATE marketing.video_auftraege SET status = 'fehler', befund = 'Laura 503' WHERE id = rev;

  neu := marketing.video_nochmal(rev, '  ', 'betreiber');
  SELECT * INTO r FROM marketing.video_auftraege WHERE id = neu;
  IF r.vorgaenger IS DISTINCT FROM orig OR r.aenderungswunsch <> 'kuerzer' OR r.status <> 'offen' THEN
    RAISE EXCEPTION 'verify_082: Neu versuchen einer Ueberarbeitung: %', row_to_json(r); END IF;
  -- Sperre greift auf den berechneten Vorgaenger (orig): weder rev noch orig erlauben einen zweiten Bau
  BEGIN PERFORM marketing.video_nochmal(rev, '', 'betreiber');
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'Es läuft schon ein neuer Bau für dieses Reel%' THEN RAISE; END IF;
    geworfen := true;
  END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_082: Neu versuchen doppelt muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.video_nochmal(orig, 'anders', 'betreiber');
  EXCEPTION WHEN raise_exception THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_082: nochmal am Original bei laufendem Bau muss werfen'; END IF;

  -- mit Wunsch: Vorgaenger ist der gescheiterte Auftrag selbst
  neu := marketing.video_nochmal(rev, 'ganz anders', 'agent');
  SELECT * INTO r FROM marketing.video_auftraege WHERE id = neu;
  IF r.vorgaenger IS DISTINCT FROM rev OR r.aenderungswunsch <> 'ganz anders' THEN
    RAISE EXCEPTION 'verify_082: fehler mit Wunsch muss den Gescheiterten als Vorgaenger nehmen: %',
      row_to_json(r); END IF;
END $$;

-- (c) video_archivieren: fertig/fehler/abgebrochen -> true und gesetzt (auch wieder zurueck);
-- offen/in_arbeit -> false und unveraendert
DO $$ DECLARE fe uuid; o uuid;
BEGIN
  INSERT INTO marketing.video_auftraege (mandant, art, titel, briefing, status, datei, angelegt_von)
  VALUES ('probe_082', 'bilder', 'Archiv', '{}', 'fertig', 'vid-0123abcd-archiv.mp4', 'agent') RETURNING id INTO fe;
  IF NOT marketing.video_archivieren(fe, true) THEN
    RAISE EXCEPTION 'verify_082: archivieren bei fertig muss true geben'; END IF;
  IF NOT (SELECT archiviert FROM marketing.video_auftraege WHERE id = fe) THEN
    RAISE EXCEPTION 'verify_082: archiviert nicht gesetzt'; END IF;
  -- getrennte Anweisungen: eine Unterabfrage im selben Ausdruck saehe die Aenderung der Funktion nicht
  IF NOT marketing.video_archivieren(fe, false) THEN
    RAISE EXCEPTION 'verify_082: wieder anzeigen muss true geben'; END IF;
  IF (SELECT archiviert FROM marketing.video_auftraege WHERE id = fe) THEN
    RAISE EXCEPTION 'verify_082: wieder anzeigen geht nicht'; END IF;
  o := marketing.video_auftrag_anlegen('probe_082', 'bilder', 'Offen', '{}', 'betreiber');
  IF marketing.video_archivieren(o, true) THEN
    RAISE EXCEPTION 'verify_082: archivieren bei offen muss false geben'; END IF;
  IF (SELECT archiviert FROM marketing.video_auftraege WHERE id = o) THEN
    RAISE EXCEPTION 'verify_082: offener Auftrag wurde doch archiviert'; END IF;
  IF marketing.video_archivieren(gen_random_uuid(), true) THEN
    RAISE EXCEPTION 'verify_082: unbekannter Auftrag muss false geben'; END IF;
  -- NULL aendert nichts: archivierter bleibt archiviert, Rueckgabe false
  PERFORM marketing.video_archivieren(fe, true);
  IF marketing.video_archivieren(fe, NULL) THEN
    RAISE EXCEPTION 'verify_082: archivieren mit NULL muss false geben'; END IF;
  IF NOT (SELECT archiviert FROM marketing.video_auftraege WHERE id = fe) THEN
    RAISE EXCEPTION 'verify_082: archivieren mit NULL hat entarchiviert'; END IF;
  -- fehler und abgebrochen -> true; in_arbeit -> false und unveraendert
  INSERT INTO marketing.video_auftraege (mandant, art, titel, briefing, status, angelegt_von)
  VALUES ('probe_082', 'bilder', 'Fehl', '{}', 'fehler', 'agent') RETURNING id INTO o;
  IF NOT marketing.video_archivieren(o, true) THEN
    RAISE EXCEPTION 'verify_082: archivieren bei fehler muss true geben'; END IF;
  INSERT INTO marketing.video_auftraege (mandant, art, titel, briefing, status, angelegt_von)
  VALUES ('probe_082', 'bilder', 'Abgebr', '{}', 'abgebrochen', 'agent') RETURNING id INTO o;
  IF NOT marketing.video_archivieren(o, true) THEN
    RAISE EXCEPTION 'verify_082: archivieren bei abgebrochen muss true geben'; END IF;
  INSERT INTO marketing.video_auftraege (mandant, art, titel, briefing, status, vergeben_bis, angelegt_von)
  VALUES ('probe_082', 'bilder', 'Laeuft', '{}', 'in_arbeit', now() + interval '5 minutes', 'agent')
  RETURNING id INTO o;
  IF marketing.video_archivieren(o, true) THEN
    RAISE EXCEPTION 'verify_082: archivieren bei in_arbeit muss false geben'; END IF;
  IF (SELECT archiviert FROM marketing.video_auftraege WHERE id = o) THEN
    RAISE EXCEPTION 'verify_082: in_arbeit wurde doch archiviert'; END IF;
END $$;

-- (d) video_poster: falsches Praefix -> false (oder Ausnahme), passender Name -> true; offen -> false
DO $$ DECLARE fe uuid; o uuid; p text; ok boolean;
BEGIN
  INSERT INTO marketing.video_auftraege (mandant, art, titel, briefing, status, datei, angelegt_von)
  VALUES ('probe_082', 'bilder', 'Poster', '{}', 'fertig', 'vid-0123abcd-poster.mp4', 'agent') RETURNING id INTO fe;
  BEGIN
    ok := marketing.video_poster(fe, 'vid-ffffffff-fremd.jpg');
    IF left(fe::text, 8) = 'ffffffff' THEN ok := false; END IF;
  EXCEPTION WHEN others THEN ok := false; END;
  IF ok THEN RAISE EXCEPTION 'verify_082: Poster mit fremdem Praefix darf nicht gehen'; END IF;
  IF (SELECT poster FROM marketing.video_auftraege WHERE id = fe) IS NOT NULL THEN
    RAISE EXCEPTION 'verify_082: fremdes Poster wurde doch gespeichert'; END IF;
  p := 'vid-' || left(fe::text, 8) || '-poster.jpg';
  IF NOT marketing.video_poster(fe, p) THEN
    RAISE EXCEPTION 'verify_082: passender Poster-Name muss true geben'; END IF;
  IF (SELECT poster FROM marketing.video_auftraege WHERE id = fe) IS DISTINCT FROM p THEN
    RAISE EXCEPTION 'verify_082: Poster nicht gespeichert'; END IF;
  o := marketing.video_auftrag_anlegen('probe_082', 'bilder', 'Offen', '{}', 'betreiber');
  IF marketing.video_poster(o, 'vid-' || left(o::text, 8) || '-x.jpg') THEN
    RAISE EXCEPTION 'verify_082: Poster bei offen muss false geben'; END IF;
END $$;

-- (e) video_naechster liefert fuer den neuen Auftrag vorgaenger {id,titel,protokoll,datei} und
-- aenderungswunsch; ohne Vorgaenger ist vorgaenger null. erstellt_am weit zurueck, damit echte offene
-- Auftraege der Live-DB nicht zuerst kommen (alles wird zurueckgerollt).
DO $$ DECLARE alt uuid; neu uuid; ohne uuid; j jsonb;
BEGIN
  INSERT INTO marketing.video_auftraege (mandant, art, titel, briefing, status, datei, protokoll, angelegt_von)
  VALUES ('probe_082', 'bilder', 'Vorlage', '{"thema":"x"}', 'fertig', 'vid-0123abcd-vorlage.mp4',
          '[{"am":"2026-10-21","text":"drei Bilder"}]', 'agent') RETURNING id INTO alt;
  neu := marketing.video_nochmal(alt, 'mehr Bilder vom Team', 'betreiber');
  UPDATE marketing.video_auftraege SET erstellt_am = '2000-01-01' WHERE id = neu;
  j := marketing.video_naechster('10 minutes');
  IF (j->>'id')::uuid IS DISTINCT FROM neu THEN
    RAISE EXCEPTION 'verify_082: naechster liefert nicht den neuen Auftrag: %', j; END IF;
  IF j->>'aenderungswunsch' IS DISTINCT FROM 'mehr Bilder vom Team'
     OR j->'vorgaenger'->>'titel' IS DISTINCT FROM 'Vorlage'
     OR (j->'vorgaenger'->>'id')::uuid IS DISTINCT FROM alt
     OR j->'vorgaenger'->>'datei' IS DISTINCT FROM 'vid-0123abcd-vorlage.mp4'
     OR j->'vorgaenger'->'protokoll' IS DISTINCT FROM '[{"am":"2026-10-21","text":"drei Bilder"}]'::jsonb THEN
    RAISE EXCEPTION 'verify_082: naechster ohne Vorgaenger/Wunsch: %', j; END IF;
  -- bestehende Schluessel unveraendert
  IF NOT (j ?& ARRAY['id', 'mandant', 'art', 'titel', 'briefing', 'versuch'])
     OR (j->>'versuch')::int <> 1 OR j->>'mandant' <> 'probe_082' THEN
    RAISE EXCEPTION 'verify_082: alte Schluessel von naechster fehlen: %', j; END IF;

  ohne := marketing.video_auftrag_anlegen('probe_082', 'bilder', 'Erstes', '{}', 'betreiber');
  UPDATE marketing.video_auftraege SET erstellt_am = '2000-01-02' WHERE id = ohne;
  j := marketing.video_naechster('10 minutes');
  IF (j->>'id')::uuid IS DISTINCT FROM ohne OR jsonb_typeof(j->'vorgaenger') <> 'null'
     OR j->>'aenderungswunsch' IS DISTINCT FROM '' THEN
    RAISE EXCEPTION 'verify_082: naechster ohne Vorgaenger: %', j; END IF;
END $$;

-- (f) Rechte: niemand ausser dem Eigentuemer
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sales_app') THEN
    IF has_function_privilege('sales_app', 'marketing.video_nochmal(uuid,text,text)', 'EXECUTE')
       OR has_function_privilege('sales_app', 'marketing.video_archivieren(uuid,boolean)', 'EXECUTE')
       OR has_function_privilege('sales_app', 'marketing.video_poster(uuid,text)', 'EXECUTE')
       OR has_function_privilege('sales_app', 'marketing.video_naechster(interval)', 'EXECUTE') THEN
      RAISE EXCEPTION 'verify_082: sales_app darf die Video-Funktionen nicht ausfuehren'; END IF;
  END IF;
  RAISE NOTICE 'verify_082: ok';
END $$;

SELECT 'verify_082: ok' AS ergebnis;

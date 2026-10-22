-- Nachweise fuer 086 (Einrichtung neuer Marken), nur ueber migration_probe (eine Transaktion + ROLLBACK).
-- Probe-Firmen probe_086_* (die meisten legt pult_mandant_einrichten selbst an). Jeder Fall wirft
-- 'verify_086: ...', wenn er scheitert.
CREATE TEMP TABLE _p086 ON COMMIT DROP AS SELECT NULL::text AS k, NULL::text AS v LIMIT 0;

-- (a) Spalten und CHECKs
DO $$ DECLARE geworfen boolean; BEGIN
  IF (SELECT count(*) FROM information_schema.columns
       WHERE table_schema = 'marketing' AND table_name = 'mandanten'
         AND ((column_name = 'einrichtung' AND data_type = 'text')
              OR (column_name = 'einrichtung_stand' AND data_type = 'jsonb' AND is_nullable = 'NO')
              OR (column_name = 'eingerichtet_von' AND data_type = 'text'))) <> 3 THEN
    RAISE EXCEPTION 'verify_086: Spalten einrichtung/einrichtung_stand/eingerichtet_von fehlen oder falsch'; END IF;
  INSERT INTO marketing.mandanten (id, name) VALUES ('probe_086_s', 'Probe 086 S');
  IF (SELECT einrichtung_stand FROM marketing.mandanten WHERE id = 'probe_086_s') IS DISTINCT FROM '{}'::jsonb THEN
    RAISE EXCEPTION 'verify_086: einrichtung_stand muss {} vorgeben'; END IF;
  geworfen := false;
  BEGIN UPDATE marketing.mandanten SET einrichtung = 'irgendwas' WHERE id = 'probe_086_s';
  EXCEPTION WHEN check_violation THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_086: unbekannte einrichtung muss am CHECK scheitern'; END IF;
  UPDATE marketing.mandanten SET einrichtung = 'laeuft' WHERE id = 'probe_086_s';
  geworfen := false;
  BEGIN UPDATE marketing.mandanten SET aktiv = true WHERE id = 'probe_086_s';
  EXCEPTION WHEN check_violation THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_086: aktiv mit einrichtung laeuft muss am CHECK scheitern'; END IF;
  geworfen := false;
  BEGIN INSERT INTO marketing.mandanten (id, name, aktiv, einrichtung) VALUES ('probe_086_t', 'T', true, 'abgebrochen');
  EXCEPTION WHEN check_violation THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_086: aktiv mit einrichtung abgebrochen muss am CHECK scheitern'; END IF;
  DELETE FROM marketing.mandanten WHERE id = 'probe_086_s';
  IF to_regprocedure('marketing._mandant_id_aus_name(text)') IS NULL
     OR to_regprocedure('marketing.pult_mandant_einrichten(text, text)') IS NULL
     OR to_regprocedure('marketing.pult_mandant_pflichtteil_setzen(text, jsonb, jsonb)') IS NULL
     OR to_regprocedure('marketing.pult_mandant_aktivieren(text, text)') IS NULL
     OR to_regprocedure('marketing.pult_mandant_einrichtung_abbrechen(text, text)') IS NULL THEN
    RAISE EXCEPTION 'verify_086: Funktionen fehlen'; END IF;
  IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
              WHERE n.nspname = 'marketing' AND p.prosecdef
                AND (p.proname LIKE 'pult_mandant_%' OR p.proname = '_mandant_id_aus_name')) THEN
    RAISE EXCEPTION 'verify_086: kein SECURITY DEFINER'; END IF;
END $$;

-- (k) bestehende Mandanten unveraendert: wer nicht aus einer Einrichtung stammt (eingerichtet_von NULL), hat
-- weder Einrichtung noch Stand; die Grund-Firma bleibt aktiv. Echte Einrichtungen (/marke_neu) zaehlen nicht -
-- sonst waere verify_086 nach der ersten Nutzung auf der Live-DB dauerhaft rot.
-- (laeuft vor allen Probe-Firmen dieser Datei; probe_086_s aus (a) ist schon wieder weg)
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                  WHERE table_schema = 'marketing' AND table_name = 'mandanten' AND column_name = 'einrichtung') THEN
    RAISE EXCEPTION 'verify_086: Spalte mandanten.einrichtung fehlt'; END IF;
  IF EXISTS (SELECT 1 FROM marketing.mandanten
              WHERE eingerichtet_von IS NULL AND (einrichtung IS NOT NULL OR einrichtung_stand <> '{}'::jsonb)) THEN
    RAISE EXCEPTION 'verify_086: bestehende Mandanten duerfen keine Einrichtung haben'; END IF;
  IF NOT (SELECT aktiv FROM marketing.mandanten WHERE id = 'vibemind') THEN
    RAISE EXCEPTION 'verify_086: vibemind muss aktiv bleiben'; END IF;
END $$;

-- (b) id aus dem Namen
DO $$ DECLARE v text; BEGIN
  v := marketing._mandant_id_aus_name('Müller & Söhne GmbH');
  IF v IS DISTINCT FROM 'mueller_soehne_gmbh' THEN RAISE EXCEPTION 'verify_086: Müller & Söhne GmbH -> %', v; END IF;
  v := marketing._mandant_id_aus_name('3D Druck');
  IF v IS DISTINCT FROM 'm_3d_druck' THEN RAISE EXCEPTION 'verify_086: 3D Druck -> %', v; END IF;
  v := marketing._mandant_id_aus_name('A');
  IF v IS DISTINCT FROM 'a_marke' THEN RAISE EXCEPTION 'verify_086: A -> %', v; END IF;
  v := marketing._mandant_id_aus_name('  Straße -- Übung!  ');
  IF v IS DISTINCT FROM 'strasse_uebung' THEN RAISE EXCEPTION 'verify_086: Straße -- Übung! -> %', v; END IF;
  v := marketing._mandant_id_aus_name(repeat('Abc', 20));
  IF char_length(v) <> 31 OR v !~ '^[a-z][a-z0-9_]{1,30}$' THEN
    RAISE EXCEPTION 'verify_086: 60 Zeichen -> % (Laenge %)', v, char_length(v); END IF;
END $$;

-- (c) einrichten, fortsetzen, abbrechen, wieder fortsetzen
DO $$ DECLARE r jsonb; m marketing.mandanten; msg text; BEGIN
  r := marketing.pult_mandant_einrichten('Probe 086 A', ' Felix ');
  IF r IS DISTINCT FROM '{"id":"probe_086_a","name":"Probe 086 A","fortgesetzt":false}'::jsonb THEN
    RAISE EXCEPTION 'verify_086: einrichten gab %', r; END IF;
  SELECT * INTO m FROM marketing.mandanten WHERE id = 'probe_086_a';
  IF m.aktiv OR m.einrichtung IS DISTINCT FROM 'laeuft' OR m.eingerichtet_von IS DISTINCT FROM 'Felix'
     OR m.verteiler <> '{}'::text[] OR m.einrichtung_stand <> '{}'::jsonb
     OR m.pflichtteil IS DISTINCT FROM '{"impressum":"","abmelde_hinweis":"","vermittlerangaben":""}'::jsonb THEN
    RAISE EXCEPTION 'verify_086: neue Zeile falsch: %', row_to_json(m); END IF;
  r := marketing.pult_mandant_einrichten('probe 086 a ', 'Felix');
  IF r IS DISTINCT FROM '{"id":"probe_086_a","name":"Probe 086 A","fortgesetzt":true}'::jsonb THEN
    RAISE EXCEPTION 'verify_086: fortsetzen gab %', r; END IF;
  IF (SELECT count(*) FROM marketing.mandanten WHERE lower(btrim(name)) = 'probe 086 a') <> 1 THEN
    RAISE EXCEPTION 'verify_086: fortsetzen hat eine zweite Zeile angelegt'; END IF;
  IF NOT marketing.pult_mandant_einrichtung_abbrechen('probe_086_a', 'Felix') THEN
    RAISE EXCEPTION 'verify_086: abbrechen muss true geben'; END IF;
  IF (SELECT einrichtung FROM marketing.mandanten WHERE id = 'probe_086_a') IS DISTINCT FROM 'abgebrochen' THEN
    RAISE EXCEPTION 'verify_086: nach Abbruch muss einrichtung abgebrochen sein'; END IF;
  msg := NULL;
  BEGIN PERFORM marketing.pult_mandant_einrichtung_abbrechen('probe_086_a', 'Felix');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS DISTINCT FROM 'Keine laufende Einrichtung' THEN
    RAISE EXCEPTION 'verify_086: zweiter Abbruch muss "Keine laufende Einrichtung" werfen, war: %', msg; END IF;
  r := marketing.pult_mandant_einrichten('PROBE 086 A', 'Anna');
  IF NOT (r->'fortgesetzt')::boolean OR r->>'id' <> 'probe_086_a' THEN
    RAISE EXCEPTION 'verify_086: nach Abbruch fortsetzen gab %', r; END IF;
  IF (SELECT einrichtung FROM marketing.mandanten WHERE id = 'probe_086_a') IS DISTINCT FROM 'laeuft' THEN
    RAISE EXCEPTION 'verify_086: fortgesetzt muss einrichtung laeuft sein'; END IF;
  msg := NULL;
  BEGIN PERFORM marketing.pult_mandant_einrichten('Probe 086 Z', '  ');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS NULL THEN RAISE EXCEPTION 'verify_086: einrichten ohne Namen des Betreibers muss werfen'; END IF;
END $$;

-- (d) verschiedene Namen, gleiche id -> _2, _3
DO $$ DECLARE r jsonb; BEGIN
  r := marketing.pult_mandant_einrichten('Probe 086 D', 'Felix');
  IF r->>'id' <> 'probe_086_d' THEN RAISE EXCEPTION 'verify_086: (d) erste id %', r; END IF;
  r := marketing.pult_mandant_einrichten('Probe-086-D', 'Felix');
  IF r->>'id' <> 'probe_086_d_2' OR (r->'fortgesetzt')::boolean THEN
    RAISE EXCEPTION 'verify_086: (d) zweite id %', r; END IF;
  r := marketing.pult_mandant_einrichten('Probe_086_D', 'Felix');
  IF r->>'id' <> 'probe_086_d_3' THEN RAISE EXCEPTION 'verify_086: (d) dritte id %', r; END IF;
  -- bei 31 Zeichen wird VOR dem Anhang gekuerzt
  r := marketing.pult_mandant_einrichten('Probe 086 ' || repeat('x', 40), 'Felix');
  r := marketing.pult_mandant_einrichten('Probe 086-' || repeat('x', 40), 'Felix');
  IF r->>'id' <> left('probe_086_' || repeat('x', 40), 29) || '_2' THEN
    RAISE EXCEPTION 'verify_086: (d) lange id %', r; END IF;
END $$;

-- (e) Name einer aktiven Marke, (e2) Name einer inaktiven Marke ohne Einrichtung
DO $$ DECLARE msg text; n text; BEGIN
  FOREACH n IN ARRAY ARRAY['vibemind', ' VibeMind '] LOOP
    msg := NULL;
    BEGIN PERFORM marketing.pult_mandant_einrichten(n, 'Felix');
    EXCEPTION WHEN others THEN msg := SQLERRM; END;
    IF msg IS DISTINCT FROM 'Diese Marke gibt es schon – bitte im normalen Marken-Chat weiterarbeiten' THEN
      RAISE EXCEPTION 'verify_086: aktiver Name % muss abgelehnt werden, war: %', n, msg; END IF;
  END LOOP;
  INSERT INTO marketing.mandanten (id, name, aktiv) VALUES ('probe_086_g', 'Probe 086 G', false);
  msg := NULL;
  BEGIN PERFORM marketing.pult_mandant_einrichten('probe 086 g', 'Felix');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS DISTINCT FROM 'Eine inaktive Marke mit diesem Namen gibt es schon' THEN
    RAISE EXCEPTION 'verify_086: inaktiver Name muss abgelehnt werden, war: %', msg; END IF;
END $$;

-- (h) ungueltige Namen
DO $$ DECLARE msg text; n text; BEGIN
  FOREACH n IN ARRAY ARRAY['A/B', '   ', 'x:y', repeat('x', 81), 'a', '..', 'a<b', 'a"b', 'a\b', 'a|b', 'a?b',
                           'a*b', 'a>b', E'a\tb', E'a\nb', E'a\u007fb'] LOOP
    msg := NULL;
    BEGIN PERFORM marketing.pult_mandant_einrichten(n, 'Felix');
    EXCEPTION WHEN others THEN msg := SQLERRM; END;
    IF msg IS DISTINCT FROM 'Ungültiger Firmenname' THEN
      RAISE EXCEPTION 'verify_086: Name % muss abgelehnt werden, war: %', n, msg; END IF;
  END LOOP;
  msg := NULL;
  BEGIN PERFORM marketing.pult_mandant_einrichten(NULL, 'Felix');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS DISTINCT FROM 'Ungültiger Firmenname' THEN
    RAISE EXCEPTION 'verify_086: Name NULL muss abgelehnt werden, war: %', msg; END IF;
  -- genau 80 Zeichen gehen
  PERFORM marketing.pult_mandant_einrichten('Probe 086 H ' || repeat('h', 68), 'Felix');
END $$;

-- (f) Marken-Chat waehrend der Einrichtung: laeuft ja, abgebrochen und inaktiv ohne Einrichtung nein
DO $$ DECLARE msg text; aid uuid; v uuid; m text; BEGIN
  PERFORM marketing.pult_mandant_einrichten('Probe 086 F', 'Felix');
  PERFORM marketing.pult_mandant_einrichten('Probe 086 FA', 'Felix');
  PERFORM marketing.pult_mandant_einrichtung_abbrechen('probe_086_fa', 'Felix');
  aid := marketing.pult_marke_anlegen('probe_086_f', 'Wir heissen Probe F', '{}');
  IF aid IS NULL THEN RAISE EXCEPTION 'verify_086: anlegen bei laeuft muss gehen'; END IF;
  UPDATE marketing.marken_auftraege SET status = 'fertig' WHERE id = aid;
  aid := marketing.pult_marke_bearbeitung_anlegen('probe_086_f', '{"werte":{}}');
  UPDATE marketing.marken_auftraege SET status = 'fertig' WHERE id = aid;
  FOREACH m IN ARRAY ARRAY['probe_086_fa', 'probe_086_g'] LOOP
    msg := NULL;
    BEGIN PERFORM marketing.pult_marke_anlegen(m, 'Hallo', '{}');
    EXCEPTION WHEN others THEN msg := SQLERRM; END;
    IF msg IS DISTINCT FROM 'Unbekannte oder inaktive Firma' THEN
      RAISE EXCEPTION 'verify_086: anlegen bei % muss scheitern, war: %', m, msg; END IF;
    msg := NULL;
    BEGIN PERFORM marketing.pult_marke_bearbeitung_anlegen(m, '{}');
    EXCEPTION WHEN others THEN msg := SQLERRM; END;
    IF msg IS DISTINCT FROM 'Unbekannte oder inaktive Firma' THEN
      RAISE EXCEPTION 'verify_086: bearbeitung bei % muss scheitern, war: %', m, msg; END IF;
  END LOOP;

  -- uebernehmen: laeuft ja
  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, status)
  VALUES ('probe_086_f', 'chat', 'f', 'fertig') RETURNING id INTO aid;
  INSERT INTO marketing.marken_vorschlaege (mandant, auftrag, vorschlag)
  VALUES ('probe_086_f', aid, '{"abschnitte":{"Ton":"x"}}') RETURNING id INTO v;
  aid := marketing.pult_marke_uebernehmen(v, 'Felix', 'probe_086_f');
  IF (SELECT art FROM marketing.marken_auftraege WHERE id = aid) IS DISTINCT FROM 'uebernehmen' THEN
    RAISE EXCEPTION 'verify_086: uebernehmen bei laeuft muss einen Uebernahme-Auftrag anlegen'; END IF;
  -- uebernehmen: abgebrochen und inaktiv ohne Einrichtung nein
  FOREACH m IN ARRAY ARRAY['probe_086_fa', 'probe_086_g'] LOOP
    INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, status)
    VALUES (m, 'chat', 'f', 'fertig') RETURNING id INTO aid;
    INSERT INTO marketing.marken_vorschlaege (mandant, auftrag, vorschlag)
    VALUES (m, aid, '{"abschnitte":{"Ton":"x"}}') RETURNING id INTO v;
    msg := NULL;
    BEGIN PERFORM marketing.pult_marke_uebernehmen(v, 'Felix', m);
    EXCEPTION WHEN others THEN msg := SQLERRM; END;
    IF msg IS DISTINCT FROM 'Unbekannte oder inaktive Firma' THEN
      RAISE EXCEPTION 'verify_086: uebernehmen bei % muss scheitern, war: %', m, msg; END IF;
    IF (SELECT status FROM marketing.marken_vorschlaege WHERE id = v) <> 'offen' THEN
      RAISE EXCEPTION 'verify_086: gescheitertes uebernehmen hat den Vorschlag veraendert'; END IF;
    -- Rueckgaengig ebenso (angenommener direkter Vorschlag)
    UPDATE marketing.marken_vorschlaege
       SET status = 'angenommen', entschieden_von = 'Felix', entschieden_am = now(),
           vorschlag = '{"abschnitte":{"Ton":"x"},"direkt":true,"aenderungen":[{"feld":"Ton","vorher":"a","nachher":"x"}]}'
     WHERE id = v;
    msg := NULL;
    BEGIN PERFORM marketing.pult_marke_rueckgaengig(v, 'Felix', m);
    EXCEPTION WHEN others THEN msg := SQLERRM; END;
    IF msg IS DISTINCT FROM 'Unbekannte oder inaktive Firma' THEN
      RAISE EXCEPTION 'verify_086: rueckgaengig bei % muss scheitern, war: %', m, msg; END IF;
  END LOOP;
END $$;

-- (g) Pflichtteil und Stand setzen
DO $$ DECLARE r jsonb; msg text; schlecht jsonb; BEGIN
  r := marketing.pult_mandant_pflichtteil_setzen('probe_086_a', '{"impressum":"Probe GmbH, Musterweg 1"}');
  IF r->'pflichtteil' IS DISTINCT FROM
       '{"impressum":"Probe GmbH, Musterweg 1","abmelde_hinweis":"","vermittlerangaben":""}'::jsonb
     OR r->'stand' IS DISTINCT FROM '{}'::jsonb THEN
    RAISE EXCEPTION 'verify_086: (g) erstes Setzen gab %', r; END IF;
  r := marketing.pult_mandant_pflichtteil_setzen('probe_086_a',
         '{"abmelde_hinweis":"Abmelden: {abmeldelink}"}', '{"uebersprungen":[3,5]}');
  IF r->'pflichtteil' IS DISTINCT FROM
       '{"impressum":"Probe GmbH, Musterweg 1","abmelde_hinweis":"Abmelden: {abmeldelink}","vermittlerangaben":""}'::jsonb
     OR r->'stand' IS DISTINCT FROM '{"uebersprungen":[3,5]}'::jsonb THEN
    RAISE EXCEPTION 'verify_086: (g) zweites Setzen gab %', r; END IF;
  r := marketing.pult_mandant_pflichtteil_setzen('probe_086_a', NULL, '{"vermittler":false}');
  IF r->'stand' IS DISTINCT FROM '{"uebersprungen":[3,5],"vermittler":false}'::jsonb THEN
    RAISE EXCEPTION 'verify_086: (g) Stand mischen gab %', r; END IF;
  IF (SELECT einrichtung_stand FROM marketing.mandanten WHERE id = 'probe_086_a')
       IS DISTINCT FROM '{"uebersprungen":[3,5],"vermittler":false}'::jsonb THEN
    RAISE EXCEPTION 'verify_086: (g) Stand nicht gespeichert'; END IF;
  -- leerer Abmeldehinweis ist erlaubt (Zwischenstand), vermittler null auch
  PERFORM marketing.pult_mandant_pflichtteil_setzen('probe_086_a', '{"vermittlerangaben":""}', '{"vermittler":null}');

  msg := NULL;
  BEGIN PERFORM marketing.pult_mandant_pflichtteil_setzen('probe_086_a', '{"abmelde_hinweis":"Abmelden: hier"}');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS DISTINCT FROM 'Der Abmeldehinweis braucht {abmeldelink}' THEN
    RAISE EXCEPTION 'verify_086: (g) ohne {abmeldelink} muss werfen, war: %', msg; END IF;

  FOREACH schlecht IN ARRAY ARRAY['{"impressum":"x","fax":"1"}', '{"impressum":5}', '{"impressum":null}',
                                  '["impressum"]', '"x"']::jsonb[] LOOP
    msg := NULL;
    BEGIN PERFORM marketing.pult_mandant_pflichtteil_setzen('probe_086_a', schlecht);
    EXCEPTION WHEN others THEN msg := SQLERRM; END;
    IF msg IS NULL THEN RAISE EXCEPTION 'verify_086: (g) Pflichtteil % muss werfen', schlecht; END IF;
  END LOOP;
  FOREACH schlecht IN ARRAY ARRAY[jsonb_build_object('impressum', repeat('i', 2001)),
                                  jsonb_build_object('abmelde_hinweis', '{abmeldelink}' || repeat('a', 488)),
                                  jsonb_build_object('vermittlerangaben', repeat('v', 2001))] LOOP
    msg := NULL;
    BEGIN PERFORM marketing.pult_mandant_pflichtteil_setzen('probe_086_a', schlecht);
    EXCEPTION WHEN others THEN msg := SQLERRM; END;
    IF msg IS NULL THEN RAISE EXCEPTION 'verify_086: (g) zu langer Pflichtteil muss werfen'; END IF;
  END LOOP;
  -- genau an den Grenzen geht es
  PERFORM marketing.pult_mandant_pflichtteil_setzen('probe_086_a', jsonb_build_object(
    'impressum', repeat('i', 2000), 'abmelde_hinweis', '{abmeldelink}' || repeat('a', 487),
    'vermittlerangaben', repeat('v', 2000)));

  FOREACH schlecht IN ARRAY ARRAY['{"uebersprungen":[2]}', '{"uebersprungen":[7]}', '{"uebersprungen":[0]}',
                                  '{"uebersprungen":["3"]}', '{"uebersprungen":3}', '{"uebersprungen":[3.5]}',
                                  '{"uebersprungen":[3.0]}', '{"uebersprungen":[3,3]}', '{"uebersprungen":[1,4,1]}',
                                  '{"vermittler":"ja"}', '{"schritt":2}', '[]']::jsonb[] LOOP
    msg := NULL;
    BEGIN PERFORM marketing.pult_mandant_pflichtteil_setzen('probe_086_a', NULL, schlecht);
    EXCEPTION WHEN others THEN msg := SQLERRM; END;
    IF msg IS NULL THEN RAISE EXCEPTION 'verify_086: (g) Stand % muss werfen', schlecht; END IF;
  END LOOP;
  IF (SELECT einrichtung_stand->'uebersprungen' FROM marketing.mandanten WHERE id = 'probe_086_a')
       IS DISTINCT FROM '[3,5]'::jsonb THEN
    RAISE EXCEPTION 'verify_086: (g) abgelehnter Stand hat doch geschrieben'; END IF;

  -- nur bei laufender Einrichtung (aktive Marke, abgebrochene, unbekannte)
  FOREACH msg IN ARRAY ARRAY['vibemind', 'probe_086_fa', 'probe_086_g'] LOOP
    DECLARE m text := msg; f text; BEGIN
      f := NULL;
      BEGIN PERFORM marketing.pult_mandant_pflichtteil_setzen(m, '{"impressum":"x"}');
      EXCEPTION WHEN others THEN f := SQLERRM; END;
      IF f IS DISTINCT FROM 'Keine laufende Einrichtung' THEN
        RAISE EXCEPTION 'verify_086: (g) Setzen bei % muss "Keine laufende Einrichtung" werfen, war: %', m, f; END IF;
    END;
  END LOOP;
  msg := NULL;
  BEGIN PERFORM marketing.pult_mandant_pflichtteil_setzen('probe_086_gibtsnicht', '{"impressum":"x"}');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS NULL THEN RAISE EXCEPTION 'verify_086: (g) unbekannte Firma muss werfen'; END IF;
END $$;

-- (i) aktivieren
DO $$ DECLARE msg text; aid uuid; m marketing.mandanten; BEGIN
  PERFORM marketing.pult_mandant_einrichten('Probe 086 I', 'Felix');
  -- ohne Impressum
  msg := NULL;
  BEGIN PERFORM marketing.pult_mandant_aktivieren('probe_086_i', 'Felix');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS NULL OR position('Impressum' IN msg) = 0 THEN
    RAISE EXCEPTION 'verify_086: (i) aktivieren % muss werfen (%), war: %', 'ohne Impressum', 'Impressum', msg; END IF;
  -- ohne Abmeldehinweis
  PERFORM marketing.pult_mandant_pflichtteil_setzen('probe_086_i', '{"impressum":"Probe I GmbH"}');
  msg := NULL;
  BEGIN PERFORM marketing.pult_mandant_aktivieren('probe_086_i', 'Felix');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS DISTINCT FROM 'Der Abmeldehinweis braucht {abmeldelink}' THEN
    RAISE EXCEPTION 'verify_086: (i) aktivieren % muss werfen (%), war: %', 'ohne Abmeldehinweis', '{abmeldelink}', msg; END IF;
  -- vermittler fehlt bzw. null
  PERFORM marketing.pult_mandant_pflichtteil_setzen('probe_086_i',
    '{"abmelde_hinweis":"Du bekommst diese Mail, weil du dich eingetragen hast. Abmelden: {abmeldelink}"}');
  msg := NULL;
  BEGIN PERFORM marketing.pult_mandant_aktivieren('probe_086_i', 'Felix');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS NULL OR position('Vermittler' IN msg) = 0 THEN
    RAISE EXCEPTION 'verify_086: (i) aktivieren % muss werfen (%), war: %', 'ohne vermittler', 'Vermittler', msg; END IF;
  PERFORM marketing.pult_mandant_pflichtteil_setzen('probe_086_i', NULL, '{"vermittler":null}');
  msg := NULL;
  BEGIN PERFORM marketing.pult_mandant_aktivieren('probe_086_i', 'Felix');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS NULL OR position('Vermittler' IN msg) = 0 THEN
    RAISE EXCEPTION 'verify_086: (i) aktivieren % muss werfen (%), war: %', 'vermittler null', 'Vermittler', msg; END IF;
  -- vermittler true ohne Angaben
  PERFORM marketing.pult_mandant_pflichtteil_setzen('probe_086_i', NULL, '{"vermittler":true}');
  msg := NULL;
  BEGIN PERFORM marketing.pult_mandant_aktivieren('probe_086_i', 'Felix');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS NULL OR position('Vermittlerangaben' IN msg) = 0 THEN
    RAISE EXCEPTION 'verify_086: (i) aktivieren % muss werfen (%), war: %', 'vermittler true ohne Angaben', 'Vermittlerangaben', msg; END IF;
  PERFORM marketing.pult_mandant_pflichtteil_setzen('probe_086_i', '{"vermittlerangaben":"Reg.-Nr. D-ABC"}');
  -- Assistent arbeitet (offener Chat) -> abgelehnt; ein wartender Wissens-Lauf blockiert nicht
  aid := marketing.pult_marke_anlegen('probe_086_i', 'Hallo', '{}');
  msg := NULL;
  BEGIN PERFORM marketing.pult_mandant_aktivieren('probe_086_i', 'Felix');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS DISTINCT FROM 'Der Assistent arbeitet gerade' THEN
    RAISE EXCEPTION 'verify_086: (i) aktivieren % muss werfen (%), war: %', 'mit offenem Chat', 'Assistent', msg; END IF;
  UPDATE marketing.marken_auftraege SET status = 'fertig' WHERE id = aid;
  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, kontext)
  VALUES ('probe_086_i', 'wissen', 'Wissen', '{"seit": 0}');
  -- ohne Namen des Betreibers
  msg := NULL;
  BEGIN PERFORM marketing.pult_mandant_aktivieren('probe_086_i', ' ');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS NULL THEN RAISE EXCEPTION 'verify_086: (i) aktivieren ohne Namen muss werfen'; END IF;
  -- vollstaendig
  IF NOT marketing.pult_mandant_aktivieren('probe_086_i', 'Felix') THEN
    RAISE EXCEPTION 'verify_086: (i) aktivieren muss true geben'; END IF;
  SELECT * INTO m FROM marketing.mandanten WHERE id = 'probe_086_i';
  IF NOT m.aktiv OR m.einrichtung IS NOT NULL THEN
    RAISE EXCEPTION 'verify_086: (i) nach Aktivierung falsch: %', row_to_json(m); END IF;
  msg := NULL;
  BEGIN PERFORM marketing.pult_mandant_aktivieren('probe_086_i', 'Felix');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS DISTINCT FROM 'Keine laufende Einrichtung' THEN
    RAISE EXCEPTION 'verify_086: (i) aktivieren % muss werfen (%), war: %', 'zweimal', 'Keine laufende Einrichtung', msg; END IF;
  -- jetzt aktive Marke: gleicher Name nicht mehr einrichtbar, Abbruch geht nicht
  msg := NULL;
  BEGIN PERFORM marketing.pult_mandant_einrichten('probe 086 i', 'Felix');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS DISTINCT FROM 'Diese Marke gibt es schon – bitte im normalen Marken-Chat weiterarbeiten' THEN
    RAISE EXCEPTION 'verify_086: (i) aktivierte Marke darf nicht neu eingerichtet werden, war: %', msg; END IF;
  msg := NULL;
  BEGIN PERFORM marketing.pult_mandant_einrichtung_abbrechen('probe_086_i', 'Felix');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS DISTINCT FROM 'Keine laufende Einrichtung' THEN
    RAISE EXCEPTION 'verify_086: (i) Abbruch einer aktiven Marke muss werfen, war: %', msg; END IF;
  -- abgebrochene Einrichtung laesst sich nicht aktivieren
  msg := NULL;
  BEGIN PERFORM marketing.pult_mandant_aktivieren('probe_086_fa', 'Felix');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS DISTINCT FROM 'Keine laufende Einrichtung' THEN
    RAISE EXCEPTION 'verify_086: (i) aktivieren % muss werfen (%), war: %', 'abgebrochen', 'Keine laufende Einrichtung', msg; END IF;
END $$;

-- (j) pult_marke_naechster liefert einrichtung und bei laeuft pflichtteil + einrichtung_stand.
-- Wartende und laufende Auftraege anderer Firmen in DIESER Transaktion beiseite (wie verify_066).
DO $$ DECLARE r jsonb; aid uuid; BEGIN
  UPDATE marketing.marken_auftraege SET status = 'fehler'
   WHERE status IN ('offen','in_arbeit') AND art <> 'wissen';
  aid := marketing.pult_marke_anlegen('probe_086_a', 'Weiter mit Schritt 3', '{}');
  r := marketing.pult_marke_naechster(interval '5 minutes', NULL::text[]);
  IF r->>'id' IS DISTINCT FROM aid::text THEN RAISE EXCEPTION 'verify_086: (j) falscher Auftrag %', r; END IF;
  IF r->>'einrichtung' IS DISTINCT FROM 'laeuft'
     OR r->'pflichtteil' IS DISTINCT FROM (SELECT pflichtteil FROM marketing.mandanten WHERE id = 'probe_086_a')
     OR r->'einrichtung_stand' IS DISTINCT FROM
          (SELECT einrichtung_stand FROM marketing.mandanten WHERE id = 'probe_086_a')
     OR r->>'firma' IS DISTINCT FROM 'Probe 086 A' OR r->>'mandant' IS DISTINCT FROM 'probe_086_a' THEN
    RAISE EXCEPTION 'verify_086: (j) Rueckgabe bei laeuft falsch: %', r; END IF;
  UPDATE marketing.marken_auftraege SET status = 'fertig', vergeben_bis = NULL WHERE id = aid;
  -- aktive Firma: einrichtung null, kein pflichtteil
  aid := marketing.pult_marke_anlegen('probe_086_i', 'Hallo aktiv', '{}');
  r := marketing.pult_marke_naechster(interval '5 minutes');
  IF r->>'id' IS DISTINCT FROM aid::text OR NOT (r ? 'einrichtung') OR r->'einrichtung' <> 'null'::jsonb
     OR r ? 'pflichtteil' OR r ? 'einrichtung_stand' THEN
    RAISE EXCEPTION 'verify_086: (j) Rueckgabe bei aktiver Firma falsch: %', r; END IF;
  IF NOT (r ?& ARRAY['id','art','mandant','firma','nachricht','kontext','verlauf','vorschlag']) THEN
    RAISE EXCEPTION 'verify_086: (j) alte Schluessel fehlen: %', r; END IF;
END $$;

DO $$ BEGIN RAISE NOTICE 'verify_086: ok'; END $$;
SELECT 'verify_086: ok' AS ergebnis;

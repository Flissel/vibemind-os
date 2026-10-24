-- Nachweise fuer 091 (Pflichtangaben als Vorschlag im Marken-Chat), nur ueber migration_probe (eine Transaktion +
-- ROLLBACK). Probe-Firmen probe_091_*. Jeder Fall wirft 'verify_091: ...', wenn er scheitert.

-- (a) Funktionen da, kein SECURITY DEFINER
DO $$ BEGIN
  IF to_regprocedure('marketing.pult_marke_naechster(interval, text[])') IS NULL
     OR to_regprocedure('marketing.pult_marke_uebernehmen(uuid, text, text)') IS NULL THEN
    RAISE EXCEPTION 'verify_091: Funktionen fehlen'; END IF;
  IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
              WHERE n.nspname = 'marketing' AND p.prosecdef
                AND p.proname IN ('pult_marke_naechster', 'pult_marke_uebernehmen')) THEN
    RAISE EXCEPTION 'verify_091: kein SECURITY DEFINER'; END IF;
END $$;

-- (b) aktive Firma: uebernehmen mit pflichtteil mischt in mandanten.pflichtteil (gleiche Transaktion)
DO $$ DECLARE aid uuid; v uuid; neu uuid; BEGIN
  INSERT INTO marketing.mandanten (id, name, aktiv, pflichtteil)
  VALUES ('probe_091_a', 'Probe 091 A', true,
          '{"impressum":"Alt GmbH","abmelde_hinweis":"","vermittlerangaben":"Reg-Nr. 1"}');
  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, status)
  VALUES ('probe_091_a', 'chat', 'Impressum: Neu GmbH', 'fertig') RETURNING id INTO aid;
  INSERT INTO marketing.marken_vorschlaege (mandant, auftrag, vorschlag)
  VALUES ('probe_091_a', aid, '{"abschnitte":{"Ton":"x"},"direkt":false,"aenderungen":[],
          "pflichtteil":{"impressum":"Neu GmbH","abmelde_hinweis":"Abmelden: {abmeldelink}"}}') RETURNING id INTO v;
  neu := marketing.pult_marke_uebernehmen(v, 'Felix', 'probe_091_a');
  IF (SELECT pflichtteil FROM marketing.mandanten WHERE id = 'probe_091_a') IS DISTINCT FROM
     '{"impressum":"Neu GmbH","abmelde_hinweis":"Abmelden: {abmeldelink}","vermittlerangaben":"Reg-Nr. 1"}'::jsonb THEN
    RAISE EXCEPTION 'verify_091: (b) pflichtteil nicht gemischt: %',
      (SELECT pflichtteil FROM marketing.mandanten WHERE id = 'probe_091_a'); END IF;
  IF (SELECT status FROM marketing.marken_vorschlaege WHERE id = v) <> 'angenommen'
     OR (SELECT art FROM marketing.marken_auftraege WHERE id = neu) IS DISTINCT FROM 'uebernehmen' THEN
    RAISE EXCEPTION 'verify_091: (b) Vorschlag nicht angenommen oder kein Uebernahme-Auftrag'; END IF;
  UPDATE marketing.marken_auftraege SET status = 'fertig' WHERE id = neu;
END $$;

-- (c) ohne pflichtteil (fehlend oder JSON-null) bleibt mandanten.pflichtteil unveraendert
DO $$ DECLARE aid uuid; v uuid; neu uuid; vorher jsonb; fall jsonb; BEGIN
  SELECT pflichtteil INTO vorher FROM marketing.mandanten WHERE id = 'probe_091_a';
  FOREACH fall IN ARRAY ARRAY['{"abschnitte":{"Ton":"y"}}', '{"abschnitte":{"Ton":"z"},"pflichtteil":null}']::jsonb[]
  LOOP
    INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, status)
    VALUES ('probe_091_a', 'chat', 'c', 'fertig') RETURNING id INTO aid;
    INSERT INTO marketing.marken_vorschlaege (mandant, auftrag, vorschlag)
    VALUES ('probe_091_a', aid, fall) RETURNING id INTO v;
    neu := marketing.pult_marke_uebernehmen(v, 'Felix', 'probe_091_a');
    IF (SELECT pflichtteil FROM marketing.mandanten WHERE id = 'probe_091_a') IS DISTINCT FROM vorher THEN
      RAISE EXCEPTION 'verify_091: (c) pflichtteil veraendert bei %', fall; END IF;
    IF (SELECT status FROM marketing.marken_vorschlaege WHERE id = v) <> 'angenommen' THEN
      RAISE EXCEPTION 'verify_091: (c) Vorschlag ohne pflichtteil nicht angenommen'; END IF;
    UPDATE marketing.marken_auftraege SET status = 'fertig' WHERE id = neu;
  END LOOP;
END $$;

-- (d) ungueltige Pflichtangaben: abgelehnt mit Grund, nichts uebernommen (pflichtteil, Vorschlag, Auftraege)
DO $$ DECLARE aid uuid; v uuid; vorher jsonb; msg text; n_vorher int; fall record; BEGIN
  SELECT pflichtteil INTO vorher FROM marketing.mandanten WHERE id = 'probe_091_a';
  FOR fall IN SELECT * FROM (VALUES
      ('{"abmelde_hinweis":"Abmelden per Mail"}'::jsonb, 'Der Abmeldehinweis braucht {abmeldelink}'),
      ('{"impressum":"x","foo":"y"}'::jsonb, 'Pflichtangaben kennen nur impressum, abmelde_hinweis, vermittlerangaben'),
      ('{"impressum":1}'::jsonb, 'Pflichtangaben muessen Text sein'),
      ('"Neu GmbH"'::jsonb, 'Pflichtangaben muessen ein Objekt sein'),
      (jsonb_build_object('impressum', repeat('i', 2001)), 'Das Impressum ist zu lang (hoechstens 2000 Zeichen)'),
      (jsonb_build_object('abmelde_hinweis', repeat('a', 490) || '{abmeldelink}'),
       'Der Abmeldehinweis ist zu lang (hoechstens 500 Zeichen)'),
      (jsonb_build_object('vermittlerangaben', repeat('v', 2001)),
       'Die Vermittlerangaben sind zu lang (hoechstens 2000 Zeichen)')
    ) AS t(pflicht, grund)
  LOOP
    UPDATE marketing.marken_vorschlaege SET status = 'ersetzt' WHERE mandant = 'probe_091_a' AND status = 'offen';
    INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, status)
    VALUES ('probe_091_a', 'chat', 'd', 'fertig') RETURNING id INTO aid;
    INSERT INTO marketing.marken_vorschlaege (mandant, auftrag, vorschlag)
    VALUES ('probe_091_a', aid, jsonb_build_object('abschnitte', '{"Ton":"d"}'::jsonb, 'pflichtteil', fall.pflicht))
    RETURNING id INTO v;
    SELECT count(*) INTO n_vorher FROM marketing.marken_auftraege WHERE mandant = 'probe_091_a' AND art = 'uebernehmen';
    msg := NULL;
    BEGIN PERFORM marketing.pult_marke_uebernehmen(v, 'Felix', 'probe_091_a');
    EXCEPTION WHEN others THEN msg := SQLERRM; END;
    IF msg IS DISTINCT FROM fall.grund THEN
      RAISE EXCEPTION 'verify_091: (d) % muss "%" werfen, war: %', fall.pflicht, fall.grund, msg; END IF;
    IF (SELECT pflichtteil FROM marketing.mandanten WHERE id = 'probe_091_a') IS DISTINCT FROM vorher
       OR (SELECT status FROM marketing.marken_vorschlaege WHERE id = v) <> 'offen'
       OR (SELECT count(*) FROM marketing.marken_auftraege
            WHERE mandant = 'probe_091_a' AND art = 'uebernehmen') <> n_vorher THEN
      RAISE EXCEPTION 'verify_091: (d) abgelehntes uebernehmen hat etwas veraendert (%)', fall.pflicht; END IF;
  END LOOP;
END $$;

-- (e) laufende Einrichtung: uebernehmen mischt genauso; abgebrochene/inaktive Firma bleibt abgelehnt (086)
DO $$ DECLARE aid uuid; v uuid; neu uuid; msg text; BEGIN
  PERFORM marketing.pult_mandant_einrichten('Probe 091 E', 'Felix');
  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, status)
  VALUES ('probe_091_e', 'chat', 'e', 'fertig') RETURNING id INTO aid;
  INSERT INTO marketing.marken_vorschlaege (mandant, auftrag, vorschlag)
  VALUES ('probe_091_e', aid, '{"pflichtteil":{"impressum":"E GmbH"},"direkt":false,"aenderungen":[]}')
  RETURNING id INTO v;
  neu := marketing.pult_marke_uebernehmen(v, 'Felix', 'probe_091_e');
  IF (SELECT pflichtteil FROM marketing.mandanten WHERE id = 'probe_091_e') IS DISTINCT FROM
     '{"impressum":"E GmbH","abmelde_hinweis":"","vermittlerangaben":""}'::jsonb THEN
    RAISE EXCEPTION 'verify_091: (e) Einrichtung nicht gemischt: %',
      (SELECT pflichtteil FROM marketing.mandanten WHERE id = 'probe_091_e'); END IF;
  UPDATE marketing.marken_auftraege SET status = 'fertig' WHERE id = neu;
  PERFORM marketing.pult_mandant_einrichtung_abbrechen('probe_091_e', 'Felix');
  INSERT INTO marketing.marken_vorschlaege (mandant, auftrag, vorschlag)
  VALUES ('probe_091_e', aid, '{"pflichtteil":{"impressum":"F GmbH"}}') RETURNING id INTO v;
  msg := NULL;
  BEGIN PERFORM marketing.pult_marke_uebernehmen(v, 'Felix', 'probe_091_e');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS DISTINCT FROM 'Unbekannte oder inaktive Firma'
     OR (SELECT pflichtteil->>'impressum' FROM marketing.mandanten WHERE id = 'probe_091_e') <> 'E GmbH' THEN
    RAISE EXCEPTION 'verify_091: (e) abgebrochene Einrichtung muss ablehnen, war: %', msg; END IF;
END $$;

-- (f) pult_marke_naechster liefert pflichtteil auch fuer aktive Firmen (einrichtung_stand weiter nur bei laeuft).
-- Wartende und laufende Auftraege anderer Firmen in DIESER Transaktion beiseite (wie verify_066/086).
DO $$ DECLARE r jsonb; aid uuid; BEGIN
  UPDATE marketing.marken_auftraege SET status = 'fehler'
   WHERE status IN ('offen','in_arbeit') AND art <> 'wissen';
  aid := marketing.pult_marke_anlegen('probe_091_a', 'Fehlt noch etwas?', '{}');
  r := marketing.pult_marke_naechster(interval '5 minutes', NULL::text[]);
  IF r->>'id' IS DISTINCT FROM aid::text
     OR r->'pflichtteil' IS DISTINCT FROM (SELECT pflichtteil FROM marketing.mandanten WHERE id = 'probe_091_a')
     OR r ? 'einrichtung_stand' OR NOT (r ? 'einrichtung') OR r->'einrichtung' <> 'null'::jsonb THEN
    RAISE EXCEPTION 'verify_091: (f) Rueckgabe bei aktiver Firma falsch: %', r; END IF;
  IF NOT (r ?& ARRAY['id','art','mandant','firma','nachricht','kontext','verlauf','vorschlag']) THEN
    RAISE EXCEPTION 'verify_091: (f) alte Schluessel fehlen: %', r; END IF;
  UPDATE marketing.marken_auftraege SET status = 'fertig', vergeben_bis = NULL WHERE id = aid;
  PERFORM marketing.pult_mandant_einrichten('Probe 091 L', 'Felix');
  aid := marketing.pult_marke_anlegen('probe_091_l', 'Weiter', '{}');
  r := marketing.pult_marke_naechster(interval '5 minutes');
  IF r->>'id' IS DISTINCT FROM aid::text OR r->>'einrichtung' IS DISTINCT FROM 'laeuft'
     OR r->'pflichtteil' IS DISTINCT FROM (SELECT pflichtteil FROM marketing.mandanten WHERE id = 'probe_091_l')
     OR r->'einrichtung_stand' IS DISTINCT FROM
          (SELECT einrichtung_stand FROM marketing.mandanten WHERE id = 'probe_091_l') THEN
    RAISE EXCEPTION 'verify_091: (f) Rueckgabe bei laufender Einrichtung falsch: %', r; END IF;
END $$;

DO $$ BEGIN RAISE NOTICE 'verify_091: ok'; END $$;
SELECT 'verify_091: ok' AS ergebnis;

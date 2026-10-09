-- Nachweise fuer 064, laeuft nur ueber migration_probe (eine Transaktion + ROLLBACK).
-- Eigene Probe-Firmen (probe_marke aktiv, probe_marke_b aktiv mit eigenem Standard-Layout,
-- probe_marke_aus inaktiv), damit echte Firmen und ihre Layouts nicht stoeren. Die Tabellen
-- marken_* sind neu: in der Probe gibt es nur unsere Auftraege, pult_marke_naechster ist also
-- vorhersagbar, solange jeweils nur EIN Auftrag offen ist (now() ist in der Probe konstant).

CREATE TEMP TABLE _p064 ON COMMIT DROP AS SELECT NULL::text AS k, NULL::uuid AS id LIMIT 0;
-- Wartende Auftraege echter Firmen (seit 066 z. B. ein Wissens-Lauf bei ausgeschaltetem PC) in DIESER
-- Transaktion beiseite, damit pult_marke_naechster vorhersagbar bleibt; der ROLLBACK stellt sie wieder her.
UPDATE marketing.marken_auftraege SET status = 'fehler' WHERE status = 'offen';

DO $$ DECLARE v uuid; d jsonb; BEGIN
  INSERT INTO marketing.mandanten (id, name, aktiv) VALUES
    ('probe_marke', 'Probe Marke', true), ('probe_marke_b', 'Probe Marke B', true),
    ('probe_marke_aus', 'Probe Marke aus', false);
  -- probe_marke_b hat schon ein Standard-Newsletter-Layout (Gestalt von 'dunkel')
  INSERT INTO marketing.layout_vorlagen (name, beschreibung, gestalt, status, vorgeschlagen_von,
         entschieden_von, entschieden_am, mandant, inhaltsart, standard)
  SELECT 'probe-marke-b-std', 'Probe', gestalt, 'freigegeben', 'probe', 'probe', now(),
         'probe_marke_b', 'newsletter', true
    FROM marketing.layout_vorlagen WHERE name = 'dunkel';
  -- Newsletter der Probe-Firma in verschiedenen Status + einer einer anderen Firma
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('probe_marke', 'newsletter', 'Probe 064 E')
  RETURNING id INTO v; INSERT INTO _p064 VALUES ('entwurf', v);
  INSERT INTO marketing.inhalte (mandant, art, titel, status, entschieden_von, entschieden_am, grund)
  VALUES ('probe_marke', 'newsletter', 'Probe 064 A', 'abgelehnt', 'probe', now(), 'x')
  RETURNING id INTO v; INSERT INTO _p064 VALUES ('abgelehnt', v);
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('probe_marke', 'post', 'Probe 064 P')
  RETURNING id INTO v; INSERT INTO _p064 VALUES ('post', v);
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('probe_marke_b', 'newsletter', 'Probe 064 B')
  RETURNING id INTO v; INSERT INTO _p064 VALUES ('fremd', v);
END $$;

-- 0) Struktur
DO $$ BEGIN
  ASSERT to_regclass('marketing.marken_auftraege') IS NOT NULL, '0: Tabelle marken_auftraege';
  ASSERT to_regclass('marketing.marken_vorschlaege') IS NOT NULL, '0: Tabelle marken_vorschlaege';
  ASSERT to_regclass('marketing.marken_spiegel') IS NOT NULL, '0: Tabelle marken_spiegel';
  ASSERT to_regclass('marketing.marken_auftraege_ein_laufender') IS NOT NULL, '0: Index ein laufender';
  ASSERT to_regclass('marketing.marken_vorschlaege_ein_offener') IS NOT NULL, '0: Index ein offener';
  ASSERT EXISTS (SELECT 1 FROM information_schema.columns
                  WHERE table_schema = 'marketing' AND table_name = 'inhalte'
                    AND column_name = 'marke_geaendert_am'), '0: inhalte.marke_geaendert_am';
  ASSERT to_regprocedure('marketing.pult_marke_anlegen(text, text, jsonb)') IS NOT NULL, '0: anlegen';
  ASSERT to_regprocedure('marketing.pult_marke_naechster(interval)') IS NOT NULL, '0: naechster';
  ASSERT to_regprocedure('marketing.pult_marke_verlaengern(uuid, interval)') IS NOT NULL, '0: verlaengern';
  ASSERT to_regprocedure('marketing.pult_marke_vorschlag(uuid, jsonb, text, jsonb)') IS NOT NULL, '0: vorschlag';
  ASSERT to_regprocedure('marketing.pult_marke_fertig(uuid, text, jsonb)') IS NOT NULL, '0: fertig';
  ASSERT to_regprocedure('marketing.pult_marke_zurueck(uuid, text)') IS NOT NULL, '0: zurueck';
  ASSERT to_regprocedure('marketing.pult_marke_uebernehmen(uuid, text, text)') IS NOT NULL, '0: uebernehmen';
  ASSERT to_regprocedure('marketing.pult_marke_verwerfen(uuid, text, text)') IS NOT NULL, '0: verwerfen';
  ASSERT to_regprocedure('marketing.pult_marke_spiegeln(text, jsonb, text)') IS NOT NULL, '0: spiegeln';
  ASSERT to_regprocedure('marketing.pult_marke_markieren(text)') IS NOT NULL, '0: markieren';
  ASSERT to_regprocedure('marketing.pult_marke_hinweis_aus(uuid)') IS NOT NULL, '0: hinweis_aus';
  ASSERT to_regprocedure('marketing.pult_marke_profil_hinweise(text, jsonb)') IS NOT NULL, '0: profil_hinweise';
  ASSERT EXISTS (SELECT 1 FROM information_schema.columns
                  WHERE table_schema = 'marketing' AND table_name = 'marken_spiegel'
                    AND column_name = 'hinweise'), '0: marken_spiegel.hinweise';
  ASSERT (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname = 'marketing' AND p.proname = 'pult_gestalt_fehler') = 1, '0: genau eine pult_gestalt_fehler';
  -- Schriftregister: dieselben ids wie claw/schriften.py REGISTER
  ASSERT (SELECT array_agg(s ORDER BY s) FROM unnest(marketing.marke_schriften()) s)
       = ARRAY['bodoni','cormorant','dm-sans','josefin','manrope','montserrat','oxanium',
               'playfair','poppins','rajdhani','young-serif'], '0: Schriftregister';
END $$;

-- 1) Gestalt-Pruefung kennt schriften (optional)
DO $$ DECLARE g jsonb; v text; BEGIN
  SELECT gestalt INTO g FROM marketing.layout_vorlagen WHERE name = 'dunkel';
  ASSERT marketing.pult_gestalt_fehler(g) IS NULL, '1a: ohne schriften gueltig wie bisher';
  ASSERT marketing.pult_gestalt_fehler(g || '{"schriften":{"anzeige":"playfair","text":"manrope"}}') IS NULL,
         '1b: gueltiges Schriftpaar';
  v := marketing.pult_gestalt_fehler(g || '{"schriften":{"anzeige":"comic-sans","text":"manrope"}}');
  ASSERT v = 'schriften.anzeige ist keine Schrift aus dem Register', format('1c: unbekannte Anzeige: %s', v);
  v := marketing.pult_gestalt_fehler(g || '{"schriften":{"anzeige":"playfair","text":"arial"}}');
  ASSERT v = 'schriften.text ist keine Schrift aus dem Register', format('1d: unbekannter Text: %s', v);
  v := marketing.pult_gestalt_fehler(g || '{"schriften":{"anzeige":"playfair"}}');
  ASSERT v = 'schriften muss genau anzeige und text haben', format('1e: text fehlt: %s', v);
  v := marketing.pult_gestalt_fehler(g || '{"schriften":{"anzeige":"playfair","text":"manrope","titel":"poppins"}}');
  ASSERT v = 'schriften muss genau anzeige und text haben', format('1f: Zusatzschluessel: %s', v);
  v := marketing.pult_gestalt_fehler(g || '{"schriften":"playfair"}');
  ASSERT v = 'schriften muss genau anzeige und text haben', format('1g: kein Objekt: %s', v);
  v := marketing.pult_gestalt_fehler(g || '{"schriften":{"anzeige":["playfair"],"text":"manrope"}}');
  ASSERT v = 'schriften.anzeige ist keine Schrift aus dem Register', format('1h: Array statt Text: %s', v);
  -- bisherige Regeln unveraendert
  v := marketing.pult_gestalt_fehler(g || '{"rundung":"abc"}');
  ASSERT v = 'rundung muss eine Zahl von 0 bis 24 sein', format('1i: rundung: %s', v);
END $$;

DO $$ DECLARE
  a uuid; a2 uuid; v1 uuid; v2 uuid; v3 uuid; u uuid; j jsonb; r record; v_fehler text; n int;
BEGIN
  -- 2) Anlegen: nur aktive Firmen, Nachricht Pflicht, ein laufender je Firma
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_anlegen('probe_marke_aus', 'Hallo', '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Unbekannte oder inaktive Firma', format('2a: inaktiv: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_anlegen('gibt_es_nicht', 'Hallo', '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Unbekannte oder inaktive Firma', format('2b: unbekannt: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_anlegen('probe_marke', '   ', '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Ohne Nachricht kein Auftrag', format('2c: leer: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_anlegen('probe_marke', repeat('a', 2001), '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Die Nachricht ist zu lang (hoechstens 2000 Zeichen)', format('2d: zu lang: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_anlegen('probe_marke', 'Hallo', '[]'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Kontext muss ein Objekt sein', format('2e: Kontext: %s', v_fehler);

  a := marketing.pult_marke_anlegen('probe_marke', '  Wir sind ein Steuerbuero  ', '{"anhaenge":["a.png"]}');
  SELECT * INTO r FROM marketing.marken_auftraege WHERE id = a;
  ASSERT r.mandant = 'probe_marke' AND r.art = 'chat' AND r.status = 'offen' AND r.nachricht = 'Wir sind ein Steuerbuero'
     AND r.kontext = '{"anhaenge":["a.png"]}'::jsonb AND r.versuche = 0 AND r.vorschlag IS NULL,
         format('2f: angelegt: %s', row_to_json(r));
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_anlegen('probe_marke', 'Noch was', '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Der Assistent arbeitet gerade', format('2g: zweiter laufender: %s', v_fehler);
  v_fehler := NULL;   -- die Regel selbst (Unique-Index)
  BEGIN INSERT INTO marketing.marken_auftraege (mandant, nachricht) VALUES ('probe_marke', 'direkt');
  EXCEPTION WHEN unique_violation THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler IS NOT NULL, '2h: Unique-Index je Firma';

  -- 3) naechster: Art, Firma, leerer Verlauf; Vergabe
  j := marketing.pult_marke_naechster(interval '5 minutes');
  ASSERT j->>'id' = a::text AND j->>'art' = 'chat' AND j->>'mandant' = 'probe_marke'
     AND j->>'firma' = 'Probe Marke' AND j->>'nachricht' = 'Wir sind ein Steuerbuero'
     AND j->'kontext' = '{"anhaenge":["a.png"]}'::jsonb AND j->'verlauf' = '[]'::jsonb
     AND j->'vorschlag' = 'null'::jsonb, format('3a: naechster: %s', j);
  SELECT * INTO r FROM marketing.marken_auftraege WHERE id = a;
  ASSERT r.status = 'in_arbeit' AND r.versuche = 1 AND r.vergeben_bis = now() + interval '5 minutes',
         format('3b: vergeben: %s', row_to_json(r));
  ASSERT marketing.pult_marke_naechster(interval '5 minutes') IS NULL, '3c: kein weiterer offener';
  ASSERT marketing.pult_marke_verlaengern(a, interval '7 minutes'), '3d: verlaengern';
  ASSERT (SELECT vergeben_bis FROM marketing.marken_auftraege WHERE id = a) = now() + interval '7 minutes', '3d: Frist';

  -- 4) Vorschlag: Auftrag fertig, Vorschlag offen
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_vorschlag(a, '[]', 'x', '[]'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Vorschlag muss ein Objekt sein', format('4a: kein Objekt: %s', v_fehler);
  v1 := marketing.pult_marke_vorschlag(a, '{"akzent":"#336699","schrift_anzeige":"playfair"}',
                                       'Hier ein erster Entwurf', '["Webseite x nicht lesbar: Zeitlimit"]');
  SELECT * INTO r FROM marketing.marken_vorschlaege WHERE id = v1;
  ASSERT r.mandant = 'probe_marke' AND r.auftrag = a AND r.status = 'offen' AND r.vorschlag->>'akzent' = '#336699'
     AND r.erstellt_am = now() AND r.entschieden_von IS NULL, format('4b: Vorschlag: %s', row_to_json(r));
  SELECT * INTO r FROM marketing.marken_auftraege WHERE id = a;
  ASSERT r.status = 'fertig' AND r.vorschlag = v1 AND r.antwort = 'Hier ein erster Entwurf'
     AND r.hinweise = '["Webseite x nicht lesbar: Zeitlimit"]'::jsonb AND r.vergeben_bis IS NULL,
         format('4c: Auftrag fertig: %s', row_to_json(r));
  ASSERT NOT marketing.pult_marke_verlaengern(a, interval '5 minutes'), '4d: fertiger laesst sich nicht verlaengern';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_vorschlag(a, '{}', 'x', '[]'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Auftrag ist nicht (mehr) in Arbeit', format('4e: zweimal: %s', v_fehler);

  -- 5) zweite Runde: Verlauf traegt die erste; neuer Vorschlag ersetzt den aelteren
  a2 := marketing.pult_marke_anlegen('probe_marke', 'Ton ruhiger', '{}');
  j := marketing.pult_marke_naechster(interval '5 minutes');
  ASSERT j->>'id' = a2::text AND jsonb_array_length(j->'verlauf') = 1
     AND j->'verlauf'->0->>'nachricht' = 'Wir sind ein Steuerbuero'
     AND j->'verlauf'->0->>'antwort' = 'Hier ein erster Entwurf', format('5a: Verlauf: %s', j);
  -- C1/R14: ein Chat-Auftrag bringt den offenen Vorschlag der Firma mit (Material zum Verfeinern)
  ASSERT j->'vorschlag'->>'id' = v1::text AND j->'vorschlag'->'vorschlag'->>'akzent' = '#336699'
     AND j->'vorschlag'->'vorschlag'->>'schrift_anzeige' = 'playfair', format('5a2: offener Vorschlag: %s', j);
  v2 := marketing.pult_marke_vorschlag(a2, '{"akzent":"#225588"}', 'Ruhiger', NULL);
  ASSERT (SELECT status FROM marketing.marken_vorschlaege WHERE id = v1) = 'ersetzt', '5b: aelterer ersetzt';
  ASSERT (SELECT status FROM marketing.marken_vorschlaege WHERE id = v2) = 'offen', '5b: neuer offen';
  ASSERT (SELECT hinweise FROM marketing.marken_auftraege WHERE id = a2) = '[]'::jsonb, '5c: Hinweise leer';

  -- 6) Antwort ohne Vorschlag (fertig): Vorschlag bleibt offen, nichts markiert
  a := marketing.pult_marke_anlegen('probe_marke', 'Was meinst du?', '{}');
  j := marketing.pult_marke_naechster(interval '5 minutes');
  ASSERT jsonb_array_length(j->'verlauf') = 2, format('6a: Verlauf 2: %s', j);
  ASSERT j->'vorschlag'->>'id' = v2::text, format('6a2: der neue offene Vorschlag: %s', j);
  ASSERT marketing.pult_marke_fertig(a, 'Nur eine Antwort', '[]') = 0, '6b: fertig ohne Markierung';
  SELECT * INTO r FROM marketing.marken_auftraege WHERE id = a;
  ASSERT r.status = 'fertig' AND r.antwort = 'Nur eine Antwort' AND r.vorschlag IS NULL, format('6c: %s', row_to_json(r));
  ASSERT (SELECT status FROM marketing.marken_vorschlaege WHERE id = v2) = 'offen', '6d: Vorschlag bleibt offen';
  ASSERT (SELECT marke_geaendert_am FROM marketing.inhalte i JOIN _p064 p ON p.id = i.id WHERE p.k = 'entwurf') IS NULL,
         '6e: nichts markiert';

  -- 7) Uebernehmen - Review Focus 3: der aeltere (ersetzte) Vorschlag wird abgelehnt
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_uebernehmen(v1, 'probe', 'probe_marke'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Inzwischen gibt es ein neueres Profil – bitte neu laden', format('7a: aelterer: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_uebernehmen(v2, '  ', 'probe_marke'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Ohne Namen kein Übernehmen', format('7b: ohne Namen: %s', v_fehler);
  -- Minor 4: ein veralteter Tab (inzwischen andere Firma gewaehlt) handelt nie am fremden Vorschlag
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_uebernehmen(v2, 'probe', 'probe_marke_b'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Der Vorschlag gehört zu einer anderen Firma – bitte neu laden', format('7b2: fremde Firma: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_verwerfen(v2, 'probe', 'probe_marke_b'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Der Vorschlag gehört zu einer anderen Firma – bitte neu laden', format('7b3: fremde Firma: %s', v_fehler);
  ASSERT (SELECT status FROM marketing.marken_vorschlaege WHERE id = v2) = 'offen', '7b4: unberuehrt';
  u := marketing.pult_marke_uebernehmen(v2, 'probe', 'probe_marke');
  SELECT * INTO r FROM marketing.marken_vorschlaege WHERE id = v2;
  ASSERT r.status = 'angenommen' AND r.entschieden_von = 'probe' AND r.entschieden_am = now(),
         format('7c: angenommen: %s', row_to_json(r));
  SELECT * INTO r FROM marketing.marken_auftraege WHERE id = u;
  ASSERT r.art = 'uebernehmen' AND r.status = 'offen' AND r.vorschlag = v2 AND r.mandant = 'probe_marke',
         format('7d: Auftrag uebernehmen: %s', row_to_json(r));
  -- zweiter Tab uebernimmt denselben (jetzt angenommenen) Vorschlag
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_uebernehmen(v2, 'probe', 'probe_marke'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Inzwischen gibt es ein neueres Profil – bitte neu laden', format('7e: zweiter Tab: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_anlegen('probe_marke', 'Noch was', '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Der Assistent arbeitet gerade', format('7f: Chat waehrend Uebernehmen: %s', v_fehler);
  -- PC aus: der Uebernehmen-Auftrag wartet (kein 2-Minuten-Tod wie beim Chat)
  UPDATE marketing.marken_auftraege SET erstellt_am = now() - interval '30 minutes' WHERE id = u;
  PERFORM marketing._marke_aufraeumen('probe_marke');
  ASSERT (SELECT status FROM marketing.marken_auftraege WHERE id = u) = 'offen', '7g: Uebernehmen wartet';

  -- 8) naechster liefert bei uebernehmen den Vorschlag; zurueck gibt ihn wieder frei
  j := marketing.pult_marke_naechster(interval '5 minutes');
  ASSERT j->>'id' = u::text AND j->>'art' = 'uebernehmen' AND j->'vorschlag'->>'id' = v2::text
     AND j->'vorschlag'->'vorschlag'->>'akzent' = '#225588', format('8a: naechster uebernehmen: %s', j);
  ASSERT marketing.pult_marke_zurueck(u, 'Rowboat nicht erreichbar') = 'fehler', '8b: zurueck';
  SELECT * INTO r FROM marketing.marken_auftraege WHERE id = u;
  ASSERT r.status = 'fehler' AND r.antwort = 'Rowboat nicht erreichbar' AND r.vergeben_bis IS NULL, format('8c: %s', row_to_json(r));
  SELECT * INTO r FROM marketing.marken_vorschlaege WHERE id = v2;
  ASSERT r.status = 'offen' AND r.entschieden_von IS NULL AND r.entschieden_am IS NULL, format('8d: wieder offen: %s', row_to_json(r));
  ASSERT marketing.pult_marke_zurueck(u, 'nochmal') = 'fehler', '8e: zurueck auf erledigtem: nichts zu tun';

  -- 9) erneut uebernehmen, fertig markiert die Entwuerfe der Firma
  u := marketing.pult_marke_uebernehmen(v2, 'probe', 'probe_marke');
  j := marketing.pult_marke_naechster(interval '5 minutes');
  ASSERT j->>'id' = u::text, format('9a: %s', j);
  n := marketing.pult_marke_fertig(u, 'Übernommen', '[]');
  ASSERT n = 1, format('9b: genau ein Entwurf markiert, sind %s', n);
  ASSERT (SELECT status FROM marketing.marken_auftraege WHERE id = u) = 'fertig', '9c: fertig';
  ASSERT (SELECT status FROM marketing.marken_vorschlaege WHERE id = v2) = 'angenommen', '9d: bleibt angenommen';
  ASSERT (SELECT marke_geaendert_am FROM marketing.inhalte i JOIN _p064 p ON p.id = i.id WHERE p.k = 'entwurf') = now(),
         '9e: Entwurf markiert';
  ASSERT (SELECT marke_geaendert_am FROM marketing.inhalte i JOIN _p064 p ON p.id = i.id WHERE p.k = 'abgelehnt') IS NULL,
         '9f: abgelehnt nicht markiert';
  ASSERT (SELECT marke_geaendert_am FROM marketing.inhalte i JOIN _p064 p ON p.id = i.id WHERE p.k = 'post') IS NULL,
         '9g: Post nicht markiert';
  ASSERT (SELECT marke_geaendert_am FROM marketing.inhalte i JOIN _p064 p ON p.id = i.id WHERE p.k = 'fremd') IS NULL,
         '9h: andere Firma nicht markiert';

  -- 10) hinweis_aus, markieren direkt
  ASSERT marketing.pult_marke_hinweis_aus((SELECT id FROM _p064 WHERE k = 'entwurf')), '10a: hinweis_aus';
  ASSERT (SELECT marke_geaendert_am FROM marketing.inhalte i JOIN _p064 p ON p.id = i.id WHERE p.k = 'entwurf') IS NULL,
         '10b: Markierung zurueckgesetzt';
  ASSERT NOT marketing.pult_marke_hinweis_aus(gen_random_uuid()), '10c: unbekannter Inhalt';
  ASSERT marketing.pult_marke_markieren('probe_marke') = 1, '10d: markieren direkt';
  ASSERT marketing.pult_marke_markieren('probe_marke_aus') = 0, '10e: Firma ohne Entwuerfe';

  -- 11) Verwerfen: nur offene; zweimal ist harmlos; danach kein Uebernehmen
  a := marketing.pult_marke_anlegen('probe_marke', 'Andere Farben', '{}');
  PERFORM marketing.pult_marke_naechster(interval '5 minutes');
  v3 := marketing.pult_marke_vorschlag(a, '{"akzent":"#aa3300"}', 'Kraeftiger', '[]');
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_verwerfen(v3, '', 'probe_marke'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Ohne Namen kein Verwerfen', format('11a: ohne Namen: %s', v_fehler);
  ASSERT marketing.pult_marke_verwerfen(v3, 'probe', 'probe_marke') = 'verworfen', '11b: verwerfen';
  SELECT * INTO r FROM marketing.marken_vorschlaege WHERE id = v3;
  ASSERT r.status = 'verworfen' AND r.entschieden_von = 'probe' AND r.entschieden_am = now(), format('11c: %s', row_to_json(r));
  ASSERT marketing.pult_marke_verwerfen(v3, 'probe', 'probe_marke') = 'verworfen', '11d: zweimal verwerfen';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_verwerfen(v2, 'probe', 'probe_marke'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Inzwischen gibt es ein neueres Profil – bitte neu laden', format('11e: angenommenen verwerfen: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_uebernehmen(v3, 'probe', 'probe_marke'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Inzwischen gibt es ein neueres Profil – bitte neu laden', format('11f: verworfenen uebernehmen: %s', v_fehler);
  v_fehler := NULL;   -- Regel: hoechstens ein offener Vorschlag je Firma
  BEGIN
    INSERT INTO marketing.marken_vorschlaege (mandant, auftrag, vorschlag) VALUES ('probe_marke', a, '{}');
    INSERT INTO marketing.marken_vorschlaege (mandant, auftrag, vorschlag) VALUES ('probe_marke', a, '{}');
  EXCEPTION WHEN unique_violation THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler IS NOT NULL, '11g: Unique-Index offener Vorschlag';

  -- 12) Aufraeumen: ein nicht abgeholter Chat stirbt nach 2 min, abgelaufene Vergabe einmal neu, dann fehler
  a := marketing.pult_marke_anlegen('probe_marke_b', 'Hallo', '{}');
  UPDATE marketing.marken_auftraege SET erstellt_am = now() - interval '3 minutes' WHERE id = a;
  a2 := marketing.pult_marke_anlegen('probe_marke_b', 'Hallo nochmal', '{}');
  SELECT * INTO r FROM marketing.marken_auftraege WHERE id = a;
  ASSERT r.status = 'fehler' AND r.antwort = 'Der Assistent läuft am PC und ist gerade aus', format('12a: %s', row_to_json(r));
  j := marketing.pult_marke_naechster(interval '5 minutes');
  ASSERT j->>'id' = a2::text AND j->'verlauf' = '[]'::jsonb, format('12b: fehler-Runden nicht im Verlauf: %s', j);
  v1 := marketing.pult_marke_vorschlag(a2, '{"akzent":"#123456"}', 'B', '[]');
  u := marketing.pult_marke_uebernehmen(v1, 'probe', 'probe_marke_b');
  PERFORM marketing.pult_marke_naechster(interval '5 minutes');
  UPDATE marketing.marken_auftraege SET vergeben_bis = now() - interval '1 second' WHERE id = u;
  PERFORM marketing._marke_aufraeumen(NULL);
  SELECT * INTO r FROM marketing.marken_auftraege WHERE id = u;
  ASSERT r.status = 'offen' AND r.versuche = 1 AND r.vergeben_bis IS NULL, format('12c: einmal neu: %s', row_to_json(r));
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_fertig(u, 'zu spaet', '[]'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Auftrag ist nicht (mehr) in Arbeit', format('12d: fertig ohne Vergabe: %s', v_fehler);
  PERFORM marketing.pult_marke_naechster(interval '5 minutes');
  UPDATE marketing.marken_auftraege SET vergeben_bis = now() - interval '1 second' WHERE id = u;
  PERFORM marketing._marke_aufraeumen('probe_marke_b');
  SELECT * INTO r FROM marketing.marken_auftraege WHERE id = u;
  ASSERT r.status = 'fehler' AND r.versuche = 2 AND r.antwort = 'Der Assistent ist nicht fertig geworden',
         format('12e: dann fehler: %s', row_to_json(r));
  ASSERT (SELECT status FROM marketing.marken_vorschlaege WHERE id = v1) = 'offen', '12f: Vorschlag wieder offen';
END $$;

-- 13) Spiegel: Standard-Layout anlegen, neue Fassung, Firma mit eigenem Standard, ungueltig, Logo weg
DO $$ DECLARE n int; r record; v_fehler text; g jsonb; v_dunkel jsonb; BEGIN
  -- I1: 'dunkel' traegt VibeMinds Logo, Schriften und Kopf-/Fusstext (so schreibt es VibeMinds eigene
  -- Uebernahme hinein); ein neues Firmen-Layout darf davon nichts erben
  UPDATE marketing.layout_vorlagen
     SET gestalt = gestalt || '{"logo":"data:image/png;base64,VklCRU1JTkQ=","kopf_text":"VibeMind",
                               "fuss_text":"VibeMind GmbH","schriften":{"anzeige":"oxanium","text":"rajdhani"}}'
   WHERE name = 'dunkel';
  SELECT gestalt INTO v_dunkel FROM marketing.layout_vorlagen WHERE name = 'dunkel';
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.layout_vorlagen WHERE mandant = 'probe_marke'), '13: Probe ohne Layout';
  n := marketing.pult_marke_spiegeln('probe_marke',
         '{"akzent":"#336699","flaeche":"#eef2f7","logo":"data:image/png;base64,iVBORw0KGgo=",
           "schriften":{"anzeige":"playfair","text":"manrope"}}', '2026-10-07 12:00 von probe');
  ASSERT n = 1, format('13a: neues Layout Fassung 1, ist %s', n);
  SELECT * INTO r FROM marketing.layout_vorlagen WHERE mandant = 'probe_marke';
  ASSERT r.name = 'marke-probe-marke' AND r.art = 'layout' AND r.standard AND r.inhaltsart = 'newsletter'
     AND r.status = 'freigegeben' AND r.entschieden_von = 'marke' AND r.fassung = 1,
         format('13b: Layout: %s', row_to_json(r));
  -- 066: der Erbe nimmt dunkel ohne logo, logo_dunkel, schriften, kopf_text, fuss_text (Abzugsliste wie pult_marke_spiegeln)
  ASSERT r.gestalt = (v_dunkel - ARRAY['logo','logo_dunkel','schriften','kopf_text','fuss_text'])
                     || '{"akzent":"#336699","flaeche":"#eef2f7","logo":"data:image/png;base64,iVBORw0KGgo=",
           "schriften":{"anzeige":"playfair","text":"manrope"}}'::jsonb, format('13c: Gestalt: %s', r.gestalt);
  -- ohne Logo und Schriften im Profil: nichts von VibeMind
  n := marketing.pult_marke_spiegeln('probe_marke_aus', '{"akzent":"#336699"}', '2026-10-07 12:00 von probe');
  SELECT gestalt INTO g FROM marketing.layout_vorlagen WHERE name = 'marke-probe-marke-aus';
  ASSERT NOT (g ?| ARRAY['logo','schriften','kopf_text','fuss_text']), format('13c2: nichts geerbt: %s', g);
  ASSERT (SELECT gestalt FROM marketing.layout_fassungen WHERE layout = 'marke-probe-marke' AND fassung = 1) = r.gestalt,
         '13d: Fassung 1 aufgezeichnet';
  SELECT * INTO r FROM marketing.marken_spiegel WHERE mandant = 'probe_marke';
  ASSERT r.stand = '2026-10-07 12:00 von probe' AND r.gespiegelt_am = now() AND r.fehler IS NULL,
         format('13e: Spiegel-Stand: %s', row_to_json(r));
  -- gleiche Werte: keine neue Fassung (Abgleich alle 10 min) und kein Speichern (T1: Muster bleibt)
  UPDATE marketing.layout_vorlagen SET muster_datei = 'muster-probe.png', entschieden_von = 'hand'
   WHERE name = 'marke-probe-marke';
  n := marketing.pult_marke_spiegeln('probe_marke', '{"akzent":"#336699"}', '2026-10-07 12:00 von probe');
  ASSERT n = 1, format('13f: unveraendert, ist %s', n);
  SELECT * INTO r FROM marketing.layout_vorlagen WHERE name = 'marke-probe-marke';
  ASSERT r.muster_datei = 'muster-probe.png' AND r.entschieden_von = 'hand',
         format('13f2: unveraendert nicht gespeichert: %s', row_to_json(r));
  -- neue Werte: neue Fassung, uebrige Schluessel bleiben; null entfernt das Logo
  n := marketing.pult_marke_spiegeln('probe_marke', '{"akzent":"#225588","logo":null}', '2026-10-07 13:00 von probe');
  ASSERT n = 2, format('13g: Fassung 2, ist %s', n);
  SELECT gestalt INTO g FROM marketing.layout_vorlagen WHERE name = 'marke-probe-marke';
  ASSERT g->>'akzent' = '#225588' AND g->>'flaeche' = '#eef2f7' AND NOT (g ? 'logo')
     AND g->'schriften' = '{"anzeige":"playfair","text":"manrope"}'::jsonb, format('13h: %s', g);
  ASSERT (SELECT count(*) FROM marketing.layout_fassungen WHERE layout = 'marke-probe-marke') = 2, '13i: zwei Fassungen';
  ASSERT (SELECT stand FROM marketing.marken_spiegel WHERE mandant = 'probe_marke') = '2026-10-07 13:00 von probe', '13j: Stand';
  -- ungueltig: nichts gespiegelt, Fehler vermerkt, letzter gueltiger Stand bleibt
  n := marketing.pult_marke_spiegeln('probe_marke', '{"schriften":{"anzeige":"comic","text":"manrope"}}', '2026-10-07 14:00 von probe');
  ASSERT n IS NULL, format('13k: ungueltig liefert NULL, ist %s', n);
  SELECT * INTO r FROM marketing.marken_spiegel WHERE mandant = 'probe_marke';
  ASSERT r.stand = '2026-10-07 13:00 von probe' AND r.fehler = 'Layout ungueltig: schriften.anzeige ist keine Schrift aus dem Register',
         format('13l: Fehler vermerkt: %s', row_to_json(r));
  ASSERT (SELECT fassung FROM marketing.layout_vorlagen WHERE name = 'marke-probe-marke') = 2, '13m: Layout unveraendert';
  -- naechster Erfolg loescht den Fehler
  n := marketing.pult_marke_spiegeln('probe_marke', '{"akzent":"#225588"}', '2026-10-07 14:00 von probe');
  ASSERT n = 2 AND (SELECT fehler FROM marketing.marken_spiegel WHERE mandant = 'probe_marke') IS NULL, '13n: Fehler weg';
  -- fremde Schluessel und unbekannte Firma
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_spiegeln('probe_marke', '{"grund":"#000000"}', 'x'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Spiegel kennt nur akzent, flaeche, logo, logo_dunkel, schriften', format('13o: fremder Schluessel: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_spiegeln('gibt_es_nicht', '{"akzent":"#225588"}', 'x'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Unbekannte Firma', format('13p: unbekannte Firma: %s', v_fehler);
  -- Firma mit eigenem Standard-Layout: dort eine neue Fassung, kein zweites Layout
  n := marketing.pult_marke_spiegeln('probe_marke_b', '{"akzent":"#123456"}', '2026-10-07 12:00 von probe');
  ASSERT n = 2, format('13q: Fassung 2 im bestehenden Standard, ist %s', n);
  ASSERT (SELECT gestalt->>'akzent' FROM marketing.layout_vorlagen WHERE name = 'probe-marke-b-std') = '#123456', '13r: gesetzt';
  ASSERT (SELECT count(*) FROM marketing.layout_vorlagen WHERE mandant = 'probe_marke_b') = 1, '13s: kein zweites Layout';
  ASSERT (SELECT erstellt_von FROM marketing.layout_fassungen WHERE layout = 'probe-marke-b-std' AND fassung = 2) = 'marke',
         '13t: Fassung von marke';
END $$;
-- 14) Profil-Hinweise (I3): der Abgleich meldet "Marke.md: ... ungültig" je Firma, auch ohne Spiegel-Aenderung
DO $$ DECLARE r record; v_fehler text; BEGIN
  ASSERT marketing.pult_marke_profil_hinweise('probe_marke', '["Marke.md: akzent ungültig"]'), '14a: gesetzt';
  SELECT * INTO r FROM marketing.marken_spiegel WHERE mandant = 'probe_marke';
  ASSERT r.hinweise = '["Marke.md: akzent ungültig"]'::jsonb AND r.stand = '2026-10-07 14:00 von probe'
     AND r.fehler IS NULL, format('14b: Hinweise ohne Stand/Fehler zu aendern: %s', row_to_json(r));
  -- Firma ohne Spiegelzeile: Zeile entsteht ohne Stand
  ASSERT marketing.pult_marke_profil_hinweise('probe_marke_b', '["Marke.md: logo ungültig"]'), '14c: gesetzt';
  DELETE FROM marketing.marken_spiegel WHERE mandant = 'probe_marke_b';
  ASSERT marketing.pult_marke_profil_hinweise('probe_marke_b', '["Marke.md: logo ungültig"]'), '14c2: neue Zeile';
  SELECT * INTO r FROM marketing.marken_spiegel WHERE mandant = 'probe_marke_b';
  ASSERT r.stand = '' AND r.gespiegelt_am IS NULL AND r.hinweise = '["Marke.md: logo ungültig"]'::jsonb,
         format('14d: %s', row_to_json(r));
  ASSERT marketing.pult_marke_profil_hinweise('probe_marke', '[]'), '14e: leeren';
  ASSERT (SELECT hinweise FROM marketing.marken_spiegel WHERE mandant = 'probe_marke') = '[]'::jsonb, '14f: leer';
  -- ein erfolgreicher Spiegel laesst die Hinweise stehen (sie kommen aus der Datei, nicht aus dem Spiegel)
  PERFORM marketing.pult_marke_profil_hinweise('probe_marke', '["Marke.md: text ungültig"]');
  PERFORM marketing.pult_marke_spiegeln('probe_marke', '{"akzent":"#225589"}', '2026-10-07 15:00 von probe');
  ASSERT (SELECT hinweise FROM marketing.marken_spiegel WHERE mandant = 'probe_marke')
         = '["Marke.md: text ungültig"]'::jsonb, '14g: Spiegel laesst Hinweise';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_profil_hinweise('probe_marke', '{}'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Hinweise muessen ein Array sein', format('14h: kein Array: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_profil_hinweise('probe_marke', (SELECT jsonb_agg(i) FROM generate_series(1, 51) i));
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Hoechstens 50 Hinweise', format('14i: zu viele: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_marke_profil_hinweise('gibt_es_nicht', '[]'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Unbekannte Firma', format('14j: unbekannte Firma: %s', v_fehler);
END $$;
SELECT 'verify_064 ok' AS ergebnis;

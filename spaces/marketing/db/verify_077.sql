-- Nachweise fuer 077, laeuft nur ueber migration_probe (eine Transaktion + ROLLBACK).
-- Eigene Probe-Inhalte (wie verify_075), "in Arbeit" wird direkt gesetzt. In der Probe ist now() konstant.
-- Darf mehrfach in derselben Probe laufen (Idempotenz: 077, verify_077, 077, verify_077).

-- 0) Struktur und Rechte
DO $$ DECLARE v_def text; BEGIN
  ASSERT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'marketing'
                  AND table_name = 'inhalt_fassungen' AND column_name = 'seite' AND data_type = 'jsonb'),
         '0: Spalte inhalt_fassungen.seite jsonb';
  SELECT pg_get_constraintdef(oid) INTO v_def FROM pg_constraint
   WHERE conrelid = 'marketing.inhalt_fassungen'::regclass AND conname = 'inhalt_fassungen_format_check';
  ASSERT v_def LIKE '%seite%', format('0: format_check kennt seite: %s', v_def);
  ASSERT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'marketing.inhalt_fassungen'::regclass
                  AND conname = 'inhalt_fassungen_seite_gueltig'), '0: inhalt_fassungen_seite_gueltig';
  ASSERT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'marketing.inhalt_fassungen'::regclass
                  AND conname = 'inhalt_fassungen_bloecke_gueltig'), '0: inhalt_fassungen_bloecke_gueltig bleibt';
  ASSERT (SELECT provolatile FROM pg_proc WHERE oid = 'marketing.pult_seite_fehler(jsonb)'::regprocedure) = 'i',
         '0: pult_seite_fehler IMMUTABLE';
  ASSERT to_regprocedure('marketing.pult_seite_speichern(uuid, int, text, text, jsonb, text, boolean)') IS NOT NULL,
         '0: Signatur pult_seite_speichern';
  ASSERT to_regprocedure('marketing.pult_collage_anlegen(text, text, text, jsonb, text)') IS NOT NULL,
         '0: Signatur pult_collage_anlegen';
  ASSERT (SELECT count(*) FROM pg_proc WHERE pronamespace = 'marketing'::regnamespace
           AND proname IN ('pult_seite_speichern', 'pult_collage_anlegen', 'pult_seite_fehler')) = 3,
         '0: keine Ueberladungen';
  ASSERT NOT has_function_privilege('public',
           'marketing.pult_seite_speichern(uuid,int,text,text,jsonb,text,boolean)', 'EXECUTE'),
         '0: PUBLIC darf nicht speichern';
  ASSERT NOT has_function_privilege('public', 'marketing.pult_collage_anlegen(text,text,text,jsonb,text)', 'EXECUTE'),
         '0: PUBLIC darf nicht anlegen';
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sales_app') THEN
    ASSERT NOT has_function_privilege('sales_app',
             'marketing.pult_seite_speichern(uuid,int,text,text,jsonb,text,boolean)', 'EXECUTE'),
           '0: sales_app darf nicht speichern';
  END IF;
  ASSERT has_function_privilege(current_user,
           'marketing.pult_seite_speichern(uuid,int,text,text,jsonb,text,boolean)', 'EXECUTE')
     AND has_function_privilege(current_user, 'marketing.pult_collage_anlegen(text,text,text,jsonb,text)', 'EXECUTE'),
         '0: die API-Rolle (Probe-Rolle) darf speichern und anlegen';
END $$;

-- 1) Pruefer: gueltig / ungueltig
DO $$ DECLARE s jsonb; voll jsonb; v_f text; v_viele jsonb; v_gross jsonb; r record; BEGIN
  -- Minimal: je eine Ebene jeder Art, nur Pflichtfelder
  s := '{"version":1,"breite":600,"hoehe":1200,"hintergrund":{"farbe":"#ffffff"},
         "schriften":{"anzeige":"dm-sans","text":"poppins"},
         "ebenen":[
           {"id":"b","art":"bild","x":300,"y":200,"breite":400,"hoehe":300,"quelle":"medien:probe.png"},
           {"id":"f","art":"form","x":300,"y":600,"breite":200,"hoehe":200,"form":"kreis","farbe":"#5b3fd6"},
           {"id":"t","art":"text","x":300,"y":800,"breite":480,"hoehe":120,"text":"Hallo {vorname}",
            "rolle":"TEXT","groesse":17,"farbe":"#333333"},
           {"id":"k","art":"knopf","x":300,"y":1000,"breite":220,"hoehe":48,"text":"Mehr",
            "link":"https://example.com/a?b=1","farbe":"#5b3fd6","textfarbe":"#ffffff"}]}'::jsonb;
  v_f := marketing.pult_seite_fehler(s);
  ASSERT v_f IS NULL, format('1: Minimal-Seite: %s', v_f);
  -- Voll: alle optionalen Felder gesetzt (Ausgabe von seite.pruefen fuer bild/form/text/knopf)
  voll := '{"version":1,"breite":600,"hoehe":400,"hintergrund":{"farbe":"#000000","bild":"medien:grund.jpg"},
            "schriften":{"anzeige":"rajdhani","text":"cormorant"},
            "ebenen":[
              {"id":"b-1","art":"bild","x":-600,"y":-1000,"breite":1,"hoehe":1,"drehung":-180,"deckkraft":0,
               "quelle":"medien:a_b-c.webp","passung":"einpassen","rundung":600,"kreis":true,"rahmen_breite":20,
               "rahmen_farbe":"#AbCdEf","schatten":true},
              {"id":"f_1","art":"form","x":1200,"y":7000,"breite":1200,"hoehe":6000,"drehung":180,"deckkraft":1,
               "form":"rechteck","farbe":"#000000","rundung":0},
              {"id":"t1","art":"text","x":0.5,"y":0.5,"breite":10,"hoehe":10,"drehung":12.5,"deckkraft":0.5,
               "text":"Ü","rolle":"HAND","gewicht":900,"groesse":160,"zeilenabstand":2.5,"buchstabenabstand":-0.1,
               "farbe":"#111111","ausrichtung":"rechts","grossbuchstaben":true,"kursiv":true,"hintergrund":null},
              {"id":"t2","art":"text","x":1,"y":1,"breite":10,"hoehe":10,"text":"x","rolle":"ANZEIGE","gewicht":300,
               "groesse":10,"zeilenabstand":0.8,"buchstabenabstand":0.5,"farbe":"#111111","hintergrund":"#eeeeee"},
              {"id":"k1","art":"knopf","x":300,"y":460,"breite":220,"hoehe":48,"drehung":0,"deckkraft":1,
               "text":"Mehr erfahren","link":"https://example.com","farbe":"#5b3fd6","textfarbe":"#ffffff",
               "rundung":60,"rolle":"ANZEIGE","groesse":40,"gewicht":600}]}'::jsonb;
  v_f := marketing.pult_seite_fehler(voll);
  ASSERT v_f IS NULL, format('1: Voll-Seite: %s', v_f);
  ASSERT marketing.pult_seite_fehler(jsonb_set(s, '{ebenen}', '[]')) IS NULL, '1: ohne Ebenen gueltig';
  ASSERT marketing.pult_seite_fehler(jsonb_set(s, '{hoehe}', '6000')) IS NULL, '1: hoehe 6000';
  ASSERT marketing.pult_seite_fehler(jsonb_set(s, '{hoehe}', '400.0')) IS NULL, '1: hoehe 400.0 ist ganz';

  -- 80 Ebenen gehen, 81 nicht
  SELECT jsonb_agg(jsonb_build_object('id', 'e' || g, 'art', 'form', 'x', 300, 'y', 300, 'breite', 10,
                                      'hoehe', 10, 'form', 'rechteck', 'farbe', '#000000'))
    INTO v_viele FROM generate_series(1, 80) g;
  ASSERT marketing.pult_seite_fehler(jsonb_set(s, '{ebenen}', v_viele)) IS NULL, '1: 80 Ebenen';
  v_viele := v_viele || jsonb_build_array(jsonb_build_object('id', 'e81', 'art', 'form', 'x', 300, 'y', 300,
                                          'breite', 10, 'hoehe', 10, 'form', 'rechteck', 'farbe', '#000000'));
  v_f := marketing.pult_seite_fehler(jsonb_set(s, '{ebenen}', v_viele));
  ASSERT v_f IS NOT NULL, '1: 81 Ebenen';

  -- ueber 256 KB (UTF-8-Bytes wie seite.pruefen): 80 Textebenen mit je 3000 x "ä"
  SELECT jsonb_agg(jsonb_build_object('id', 't' || g, 'art', 'text', 'x', 300, 'y', 300, 'breite', 10,
                                      'hoehe', 10, 'text', repeat('ä', 3000), 'rolle', 'TEXT', 'groesse', 17,
                                      'farbe', '#000000'))
    INTO v_gross FROM generate_series(1, 80) g;
  v_f := marketing.pult_seite_fehler(jsonb_set(s, '{ebenen}', v_gross));
  ASSERT v_f LIKE '%256 KB%', format('1: Groesse: %s', v_f);

  FOR r IN SELECT * FROM (VALUES
      ('kein Objekt', '[]'::jsonb),
      ('breite 599', jsonb_set(s, '{breite}', '599')),
      ('hoehe 6001', jsonb_set(s, '{hoehe}', '6001')),
      ('hoehe 399', jsonb_set(s, '{hoehe}', '399')),
      ('hoehe 500.5', jsonb_set(s, '{hoehe}', '500.5')),
      ('hoehe Text', jsonb_set(s, '{hoehe}', '"500"')),
      ('version 2', jsonb_set(s, '{version}', '2')),
      ('version true', jsonb_set(s, '{version}', 'true')),
      ('Seite unbekannt', s || '{"css":"x"}'),
      ('ebenen fehlt', s - 'ebenen'),
      ('ebenen kein Array', jsonb_set(s, '{ebenen}', '{}')),
      ('Hintergrund ohne farbe', jsonb_set(s, '{hintergrund}', '{}')),
      ('Hintergrund unbekannt', jsonb_set(s, '{hintergrund,x}', '1')),
      ('Hintergrund-Bild https', jsonb_set(s, '{hintergrund,bild}', '"https://x.de/a.png"')),
      ('Schrift nicht im Register', jsonb_set(s, '{schriften,text}', '"comic-sans"')),
      ('Schrift caveat', jsonb_set(s, '{schriften,anzeige}', '"caveat"')),
      ('Schrift fehlt', jsonb_set(s, '{schriften}', '{"anzeige":"dm-sans"}')),
      ('art video', jsonb_set(s, '{ebenen,1,art}', '"video"')),
      ('Quelle https', jsonb_set(s, '{ebenen,0,quelle}', '"https://x.de/a.png"')),
      ('Quelle ..', jsonb_set(s, '{ebenen,0,quelle}', '"medien:a..png"')),
      ('Quelle svg', jsonb_set(s, '{ebenen,0,quelle}', '"medien:a.svg"')),
      ('Link http', jsonb_set(s, '{ebenen,3,link}', '"http://example.com"')),
      ('Link mit Leerzeichen', jsonb_set(s, '{ebenen,3,link}', '"https://a b"')),
      ('Farbe red', jsonb_set(s, '{ebenen,1,farbe}', '"red"')),
      ('Ebene unbekannt', jsonb_set(s, '{ebenen,2,style}', '"color:red"')),
      ('Feld einer anderen Art', jsonb_set(s, '{ebenen,1,quelle}', '"medien:a.png"')),
      ('Pflichtfeld fehlt', jsonb_set(s, '{ebenen,2}', (s #> '{ebenen,2}') - 'groesse')),
      ('id fehlt', jsonb_set(s, '{ebenen,1}', (s #> '{ebenen,1}') - 'id')),
      ('id ungueltig', jsonb_set(s, '{ebenen,1,id}', '"a b"')),
      ('id doppelt', jsonb_set(s, '{ebenen,1,id}', '"b"')),
      ('x zu gross', jsonb_set(s, '{ebenen,0,x}', '1201')),
      ('drehung null', jsonb_set(s, '{ebenen,0,drehung}', 'null')),
      ('deckkraft 1.5', jsonb_set(s, '{ebenen,0,deckkraft}', '1.5')),
      ('kreis Text', jsonb_set(s, '{ebenen,0,kreis}', '"ja"')),
      ('rahmen_breite 21', jsonb_set(s, '{ebenen,0,rahmen_breite}', '21')),
      ('Text leer', jsonb_set(s, '{ebenen,2,text}', '""')),
      ('Text zu lang', jsonb_set(s, '{ebenen,2,text}', to_jsonb(repeat('x', 3001)))),
      ('Rolle', jsonb_set(s, '{ebenen,2,rolle}', '"FETT"')),
      ('gewicht 450.5', jsonb_set(s, '{ebenen,2,gewicht}', '450.5')),
      ('groesse 161', jsonb_set(s, '{ebenen,2,groesse}', '161')),
      ('ausrichtung', jsonb_set(s, '{ebenen,2,ausrichtung}', '"block"')),
      ('Knopftext 61', jsonb_set(s, '{ebenen,3,text}', to_jsonb(repeat('x', 61)))),
      ('Knopf-Rolle HAND', jsonb_set(s, '{ebenen,3,rolle}', '"HAND"')),
      ('Knopf-Rundung 61', jsonb_set(s, '{ebenen,3,rundung}', '61')),
      ('Ebene kein Objekt', jsonb_set(s, '{ebenen,0}', '"bild"'))
    ) t(fall, doc) LOOP
    v_f := marketing.pult_seite_fehler(r.doc);
    ASSERT v_f IS NOT NULL, format('1: ungueltig nicht erkannt: %s', r.fall);
  END LOOP;
  ASSERT marketing.pult_seite_fehler(NULL) IS NOT NULL, '1: NULL';
END $$;

-- 2) Anlegen, Speichern, Sperren, Format-Regeln
DO $$ DECLARE s jsonb; v_i uuid; v_p uuid; v_n int; v_fehler text; f record; v_vorher int; BEGIN
  s := '{"version":1,"breite":600,"hoehe":1200,"hintergrund":{"farbe":"#ffffff","bild":null},
         "schriften":{"anzeige":"dm-sans","text":"dm-sans"},
         "ebenen":[{"id":"t","art":"text","x":300,"y":200,"breite":480,"hoehe":120,"text":"Probe",
                    "rolle":"TEXT","groesse":17,"farbe":"#333333"}]}'::jsonb;

  -- Anlegen
  v_i := marketing.pult_collage_anlegen('vibemind', 'Probe 077', 'Betreff', s, 'probe');
  ASSERT (SELECT art FROM marketing.inhalte WHERE id = v_i) = 'newsletter', '2: art newsletter';
  ASSERT (SELECT status FROM marketing.inhalte WHERE id = v_i) = 'entwurf', '2: status entwurf';
  ASSERT (SELECT mandant FROM marketing.inhalte WHERE id = v_i) = 'vibemind', '2: mandant';
  ASSERT (SELECT titel FROM marketing.inhalte WHERE id = v_i) = 'Probe 077', '2: titel';
  SELECT * INTO f FROM marketing.inhalt_fassungen WHERE inhalt = v_i;
  ASSERT f.fassung = 1 AND f.format = 'seite' AND f.seite = s AND f.bloecke IS NULL AND f.urheber = 'betreiber',
         format('2: Fassung 1: %s/%s/%s', f.fassung, f.format, f.urheber);
  ASSERT f.felder = '{"betreff":"Betreff","vorschautext":""}'::jsonb, format('2: felder: %s', f.felder);
  ASSERT (SELECT count(*) FROM marketing.inhalt_fassungen WHERE inhalt = v_i) = 1, '2: genau eine Fassung';

  -- Anlegen scheitert vollstaendig (kein Inhalt bleibt)
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_collage_anlegen('vibemind', 'Probe 077 X', 'Betreff',
                                               jsonb_set(s, '{breite}', '599'), 'probe');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler IS NOT NULL, '2: ungueltige Seite beim Anlegen';
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.inhalte WHERE titel = 'Probe 077 X'), '2: nichts angelegt';
  -- Ohne Betreff (die API reicht '' durch): der Titel wird Betreff, wie bei pult_inhalt_aus_vorlage (058)
  FOR f IN SELECT b FROM unnest(ARRAY['', '  ', NULL]) b LOOP
    v_p := marketing.pult_collage_anlegen('vibemind', '  Probe 077 ohne Betreff ', f.b, s, 'probe');
    ASSERT (SELECT felder FROM marketing.inhalt_fassungen WHERE inhalt = v_p AND fassung = 1)
           = '{"betreff":"Probe 077 ohne Betreff","vorschautext":""}'::jsonb,
           format('2: ohne Betreff [%s] -> Titel als Betreff', f.b);
  END LOOP;
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_collage_anlegen('vibemind', ' ', 'Betreff', s, 'probe');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Ohne Titel kein Newsletter', format('2: ohne Titel: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_collage_anlegen('gibtsnicht', 'Probe 077 X', 'Betreff', s, 'probe');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Unbekannte Firma', format('2: unbekannte Firma: %s', v_fehler);

  -- Speichern
  v_n := marketing.pult_seite_speichern(v_i, 1, 'Betreff 2', 'Vorschau', jsonb_set(s, '{hoehe}', '1300'),
                                        'betreiber', false);
  ASSERT v_n = 2, format('2: speichern gibt 2: %s', v_n);
  SELECT * INTO f FROM marketing.inhalt_fassungen WHERE inhalt = v_i AND fassung = 2;
  ASSERT f.format = 'seite' AND f.bloecke IS NULL AND (f.seite->>'hoehe')::int = 1300
     AND f.felder = '{"betreff":"Betreff 2","vorschautext":"Vorschau"}'::jsonb AND f.layout = 'dunkel',
         '2: Fassung 2';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_seite_speichern(v_i, 1, 'Betreff', '', s, 'betreiber', false);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE 'Inzwischen gibt es Fassung 2%', format('2: Basis veraltet: %s', v_fehler);
  ASSERT marketing.pult_seite_speichern(v_i, 1, 'Betreff', '', s, 'betreiber', true) = 3, '2: als Kopie';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_seite_speichern(v_i, 3, 'Betreff', '', jsonb_set(s, '{ebenen,0,farbe}', '"red"'),
                                               'betreiber', false);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler IS NOT NULL, '2: ungueltige Seite beim Speichern';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_seite_speichern(gen_random_uuid(), 0, 'Betreff', '', s, 'betreiber', false);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Unbekannter Inhalt', format('2: unbekannt: %s', v_fehler);
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'post', 'Probe 077 P') RETURNING id INTO v_p;
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_seite_speichern(v_p, 0, 'Betreff', '', s, 'betreiber', false);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Seiten gibt es nur für Newsletter', format('2: nur Newsletter: %s', v_fehler);

  -- Sperren: eingereicht
  UPDATE marketing.inhalte SET status = 'eingereicht', eingereichte_fassung = 3, eingereicht_am = now(),
                               eingereicht_von = 'probe' WHERE id = v_i;
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_seite_speichern(v_i, 3, 'Betreff', '', s, 'agent', false);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Liegt zur Freigabe – erst zurückziehen', format('2: eingereicht: %s', v_fehler);
  UPDATE marketing.inhalte SET status = 'entwurf', eingereichte_fassung = NULL, eingereicht_am = NULL,
                               eingereicht_von = NULL WHERE id = v_i;
  -- Sperren: Assistent arbeitet (nur fuer den Betreiber)
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, status, versuche, vergeben_bis)
  VALUES (v_i, 'chat', 'laeuft', 'in_arbeit', 1, now() + interval '5 min');
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_seite_speichern(v_i, 3, 'Betreff', '', s, 'betreiber', false);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Der Assistent arbeitet gerade', format('2: Assistent: %s', v_fehler);
  v_n := marketing.pult_seite_speichern(v_i, 3, 'Betreff', '', s, 'agent', false);
  ASSERT v_n = 4, format('2: agent speichert trotz laufender Runde: %s', v_n);
  ASSERT (SELECT urheber FROM marketing.inhalt_fassungen WHERE inhalt = v_i AND fassung = 4) = 'agent', '2: urheber agent';
  -- Nicht-Entwurf
  UPDATE marketing.inhalte SET status = 'abgelehnt', entschieden_von = 'probe', entschieden_am = now() WHERE id = v_i;
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_seite_speichern(v_i, 4, 'Betreff', '', s, 'agent', false);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Nur Entwuerfe lassen sich bearbeiten', format('2: nur Entwurf: %s', v_fehler);

  -- Format-Regeln direkt an der Tabelle
  SELECT max(fassung) INTO v_vorher FROM marketing.inhalt_fassungen WHERE inhalt = v_i;
  v_fehler := NULL;
  BEGIN
    INSERT INTO marketing.inhalt_fassungen (inhalt, fassung, felder, layout, urheber, format)
    VALUES (v_i, v_vorher + 1, '{}', 'dunkel', 'agent', 'seite');
  EXCEPTION WHEN check_violation THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE '%inhalt_fassungen_format_check%', format('2: seite ohne Dokument: %s', v_fehler);
  v_fehler := NULL;
  BEGIN
    INSERT INTO marketing.inhalt_fassungen (inhalt, fassung, felder, layout, urheber, format, seite)
    VALUES (v_i, v_vorher + 1, '{}', 'dunkel', 'agent', 'felder', s);
  EXCEPTION WHEN check_violation THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE '%inhalt_fassungen_format_check%', format('2: felder mit Seite: %s', v_fehler);
  v_fehler := NULL;
  BEGIN
    INSERT INTO marketing.inhalt_fassungen (inhalt, fassung, felder, layout, urheber, format, seite)
    VALUES (v_i, v_vorher + 1, '{}', 'dunkel', 'agent', 'seite', jsonb_set(s, '{hoehe}', '6001'));
  EXCEPTION WHEN check_violation THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE '%inhalt_fassungen_seite_gueltig%', format('2: ungueltige Seite direkt: %s', v_fehler);
END $$;

-- 3) Unveraendert: Block-Newsletter speichern wie vorher
DO $$ DECLARE d jsonb; v_i uuid; v_fehler text; BEGIN
  d := '{"root":{"type":"EmailLayout","data":{"childrenIds":["t"]}},
         "t":{"type":"Text","data":{"style":{},"props":{"text":"eins","markdown":false}}}}'::jsonb;
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 077 B')
  RETURNING id INTO v_i;
  ASSERT marketing.pult_bloecke_speichern(v_i, 0, 'Probe 077 B', '', d, 'betreiber', false) = 1, '3: Block 1';
  ASSERT marketing.pult_bloecke_speichern(v_i, 1, 'Probe 077 B', '',
           jsonb_set(d, '{t,data,props,text}', '"zwei"'), 'betreiber', false) = 2, '3: Block 2';
  ASSERT (SELECT format = 'bloecke' AND seite IS NULL AND bloecke IS NOT NULL FROM marketing.inhalt_fassungen
           WHERE inhalt = v_i AND fassung = 2), '3: Blockfassung ohne Seite';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bloecke_speichern(v_i, 1, 'Probe 077 B', '', d, 'betreiber', false);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE 'Inzwischen gibt es Fassung 2%', format('3: Block-Basis: %s', v_fehler);
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, status, versuche, vergeben_bis)
  VALUES (v_i, 'chat', 'laeuft', 'in_arbeit', 1, now() + interval '5 min');
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bloecke_speichern(v_i, 2, 'Probe 077 B', '', d, 'betreiber', false);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Der Assistent arbeitet gerade', format('3: Block-Assistent: %s', v_fehler);
  v_fehler := NULL;
  BEGIN
    INSERT INTO marketing.inhalt_fassungen (inhalt, fassung, felder, layout, urheber, format, bloecke)
    VALUES (v_i, 3, '{}', 'dunkel', 'agent', 'bloecke', '{"root":{"type":"Text"}}');
  EXCEPTION WHEN check_violation THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE '%inhalt_fassungen_bloecke_gueltig%', format('3: bloecke_gueltig greift: %s', v_fehler);
END $$;

-- 4) Formatsperre der API (api/pult.py mit_formatsperre): dieselbe Abfrageform mit Literalen. CASE darf den
--    ELSE-Zweig mit der schreibenden Funktion nur bei passendem Format auswerten; "gesperrt" sieht den Stand
--    vor dem Aufruf (Schnappschuss der Abfrage).
DO $$ DECLARE s jsonb; d jsonb; v_c uuid; v_b uuid; v_l uuid; v_r int; v_g boolean;
  c_sperre CONSTANT text := 'SELECT CASE WHEN %1$s THEN NULL ELSE %2$s END, coalesce(%1$s, false)';
  c_fmt CONSTANT text := '(SELECT f.format FROM marketing.inhalt_fassungen f WHERE f.inhalt = %L::uuid '
                         'ORDER BY f.fassung DESC LIMIT 1)';
BEGIN
  s := '{"version":1,"breite":600,"hoehe":1200,"hintergrund":{"farbe":"#ffffff","bild":null},
         "schriften":{"anzeige":"dm-sans","text":"dm-sans"},
         "ebenen":[{"id":"t","art":"text","x":300,"y":200,"breite":480,"hoehe":120,"text":"Probe",
                    "rolle":"TEXT","groesse":17,"farbe":"#333333"}]}'::jsonb;
  d := '{"root":{"type":"EmailLayout","data":{"childrenIds":["t"]}},
         "t":{"type":"Text","data":{"style":{},"props":{"text":"eins","markdown":false}}}}'::jsonb;
  v_c := marketing.pult_collage_anlegen('vibemind', 'Probe 077 Sperre C', 'Betreff', s, 'probe');
  -- Bloecke auf einer Collage: gesperrt, keine neue Fassung
  EXECUTE format(c_sperre, format(c_fmt, v_c) || ' = ''seite''',
                 format('marketing.pult_bloecke_speichern(%L::uuid, 1, %L, %L, %L::jsonb, %L, false)',
                        v_c, 'B', '', d, 'betreiber')) INTO v_r, v_g;
  ASSERT v_r IS NULL AND v_g, format('4: Bloecke auf Collage gesperrt: %s/%s', v_r, v_g);
  ASSERT (SELECT count(*) FROM marketing.inhalt_fassungen WHERE inhalt = v_c) = 1, '4: keine Blockfassung';
  -- Einreichen einer Collage: gesperrt, bleibt Entwurf
  EXECUTE format(c_sperre, format(c_fmt, v_c) || ' = ''seite''',
                 format('marketing.pult_einreichen(%L::uuid, %L)', v_c, 'probe')) INTO v_r, v_g;
  ASSERT v_r IS NULL AND v_g AND (SELECT status FROM marketing.inhalte WHERE id = v_c) = 'entwurf',
         '4: Einreichen einer Collage gesperrt';
  -- Seite auf einem Block-Newsletter: gesperrt
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 077 Sperre B')
  RETURNING id INTO v_b;
  EXECUTE format(c_sperre, format(c_fmt, v_b) || ' = ''seite''',
                 format('marketing.pult_bloecke_speichern(%L::uuid, 0, %L, %L, %L::jsonb, %L, false)',
                        v_b, 'B', '', d, 'betreiber')) INTO v_r, v_g;
  ASSERT v_r = 1 AND NOT v_g, format('4: Bloecke auf Block-Newsletter laufen: %s/%s', v_r, v_g);
  EXECUTE format(c_sperre, format(c_fmt, v_b) || ' <> ''seite''',
                 format('marketing.pult_seite_speichern(%L::uuid, 1, %L, %L, %L::jsonb, %L, false)',
                        v_b, 'B', '', s, 'betreiber')) INTO v_r, v_g;
  ASSERT v_r IS NULL AND v_g, format('4: Seite auf Block-Newsletter gesperrt: %s/%s', v_r, v_g);
  ASSERT (SELECT format FROM marketing.inhalt_fassungen WHERE inhalt = v_b ORDER BY fassung DESC LIMIT 1) = 'bloecke',
         '4: Block-Newsletter bleibt Bloecke';
  -- Seite auf einem Inhalt ohne Fassung: laeuft (Vergleich NULL -> ELSE)
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 077 Sperre L')
  RETURNING id INTO v_l;
  EXECUTE format(c_sperre, format(c_fmt, v_l) || ' <> ''seite''',
                 format('marketing.pult_seite_speichern(%L::uuid, 0, %L, %L, %L::jsonb, %L, false)',
                        v_l, 'B', '', s, 'betreiber')) INTO v_r, v_g;
  ASSERT v_r = 1 AND NOT v_g, format('4: Seite ohne Fassung laeuft: %s/%s', v_r, v_g);
END $$;

SELECT 'verify_077 ok' AS ergebnis;

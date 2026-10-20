-- Nachweise fuer 080, laeuft nur ueber migration_probe (eine Transaktion + ROLLBACK).
-- Eigene Probe-Inhalte (wie verify_077), "in Arbeit" wird direkt gesetzt. In der Probe ist now() konstant.
-- Darf mehrfach in derselben Probe laufen (Idempotenz: 080, verify_080, 080, verify_080): die Probe-Firmen
-- fuer die Kompositionen bekommen je Lauf einen eigenen Namen.

-- 0) Struktur und Rechte
DO $$ BEGIN
  ASSERT to_regprocedure('marketing.pult_chat_fertig_seite(uuid, text, jsonb, jsonb, jsonb, int)') IS NOT NULL,
         '0: Signatur pult_chat_fertig_seite';
  ASSERT to_regprocedure('marketing.pult_chat_stopp_abschliessen_seite(uuid, jsonb, text, int)') IS NOT NULL,
         '0: Signatur pult_chat_stopp_abschliessen_seite';
  ASSERT to_regprocedure('marketing.pult_seite_kompositionen(text, uuid)') IS NOT NULL,
         '0: Signatur pult_seite_kompositionen';
  -- jede bisherige Signatur bleibt aufrufbar
  ASSERT to_regprocedure('marketing.pult_chat_fertig(uuid, text, jsonb, jsonb, jsonb, int)') IS NOT NULL
     AND to_regprocedure('marketing.pult_chat_stopp_abschliessen(uuid, jsonb, text, int)') IS NOT NULL,
         '0: Block-Funktionen bleiben';
  ASSERT to_regprocedure('marketing.pult_bild_auftrag(uuid, text, boolean, text, text)') IS NOT NULL
     AND to_regprocedure('marketing.pult_bild_auftrag(uuid, text, boolean, text, text, int, text)') IS NOT NULL
     AND to_regprocedure('marketing.pult_bild_auftrag(uuid, text, boolean, text, text, int, text, jsonb)') IS NOT NULL,
         '0: pult_bild_auftrag 5/7/8 Argumente';
  ASSERT to_regprocedure('marketing.pult_bild_einsetzen(uuid, jsonb, text)') IS NOT NULL
     AND to_regprocedure('marketing.pult_bild_einsetzen(uuid, jsonb, text, jsonb)') IS NOT NULL,
         '0: pult_bild_einsetzen 3/4 Argumente';
  ASSERT (SELECT count(*) FROM pg_proc WHERE pronamespace = 'marketing'::regnamespace
           AND proname = 'pult_bild_auftrag') = 3, '0: genau drei pult_bild_auftrag';
  ASSERT (SELECT count(*) FROM pg_proc WHERE pronamespace = 'marketing'::regnamespace
           AND proname = 'pult_bild_einsetzen') = 2, '0: genau zwei pult_bild_einsetzen';
  -- die umbenannten Originale
  ASSERT to_regprocedure('marketing._pult_bild_auftrag_vor080(uuid, text, boolean, text, text)') IS NOT NULL
     AND to_regprocedure('marketing._pult_bild_auftrag_vor080(uuid, text, boolean, text, text, int, text)') IS NOT NULL
     AND to_regprocedure('marketing._pult_bild_einsetzen_vor080(uuid, jsonb, text)') IS NOT NULL,
         '0: Originale umbenannt (_vor080)';
  ASSERT (SELECT count(*) FROM pg_proc WHERE pronamespace = 'marketing'::regnamespace
           AND proname IN ('_pult_bild_auftrag_vor080', '_pult_bild_einsetzen_vor080')) = 3,
         '0: genau drei Originale';
  -- Fix-Runde 1: Huellen um pult_chat_fertig, pult_chat_stopp_abschliessen, pult_bild_datei_fehler
  ASSERT to_regprocedure('marketing._pult_chat_fertig_vor080(uuid, text, jsonb, jsonb, jsonb, int)') IS NOT NULL
     AND to_regprocedure('marketing._pult_chat_stopp_abschliessen_vor080(uuid, jsonb, text, int)') IS NOT NULL
     AND to_regprocedure('marketing._pult_bild_datei_fehler_vor080(uuid, text)') IS NOT NULL
     AND to_regprocedure('marketing.pult_bild_datei_fehler(uuid, text)') IS NOT NULL,
         '0: Fix-Runde 1 Originale umbenannt, Huellen da';
  ASSERT (SELECT count(*) FROM pg_proc WHERE pronamespace = 'marketing'::regnamespace
           AND proname IN ('pult_chat_fertig', 'pult_chat_stopp_abschliessen', 'pult_bild_datei_fehler',
                           '_pult_chat_fertig_vor080', '_pult_chat_stopp_abschliessen_vor080',
                           '_pult_bild_datei_fehler_vor080')) = 6, '0: je genau eine Huelle und ein Original';
  ASSERT NOT has_function_privilege('public',
           'marketing.pult_chat_fertig_seite(uuid,text,jsonb,jsonb,jsonb,int)', 'EXECUTE')
     AND NOT has_function_privilege('public',
           'marketing.pult_chat_stopp_abschliessen_seite(uuid,jsonb,text,int)', 'EXECUTE')
     AND NOT has_function_privilege('public', 'marketing.pult_seite_kompositionen(text,uuid)', 'EXECUTE'),
         '0: PUBLIC darf die neuen Funktionen nicht';
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sales_app') THEN
    ASSERT NOT has_function_privilege('sales_app',
             'marketing.pult_chat_fertig_seite(uuid,text,jsonb,jsonb,jsonb,int)', 'EXECUTE'),
           '0: sales_app darf nicht speichern';
  END IF;
  ASSERT has_function_privilege(current_user,
           'marketing.pult_chat_fertig_seite(uuid,text,jsonb,jsonb,jsonb,int)', 'EXECUTE')
     AND has_function_privilege(current_user, 'marketing.pult_seite_kompositionen(text,uuid)', 'EXECUTE'),
         '0: die API-Rolle (Probe-Rolle) darf';
END $$;

-- 1) Fertig und Stopp-Abschluss fuer Seiten
DO $$ DECLARE s jsonb; s2 jsonb; v_c uuid; a uuid; j jsonb; f record; r record; v_fehler text; v_n int; BEGIN
  s := '{"version":1,"breite":600,"hoehe":1200,"hintergrund":{"farbe":"#ffffff","bild":null},
         "schriften":{"anzeige":"dm-sans","text":"dm-sans"},
         "ebenen":[{"id":"held","art":"bild","x":300,"y":200,"breite":400,"hoehe":300,
                    "quelle":"medien:platzhalter-4x3.png"},
                   {"id":"t","art":"text","x":300,"y":600,"breite":480,"hoehe":120,"text":"Probe",
                    "rolle":"TEXT","groesse":17,"farbe":"#333333"}]}'::jsonb;
  s2 := jsonb_set(s, '{hoehe}', '1300');
  v_c := marketing.pult_collage_anlegen('vibemind', 'Probe 080 Fertig', 'Betreff 080', s, 'probe');

  -- fertig speichert eine neue Seiten-Fassung mit urheber agent
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, status, versuche, vergeben_bis, fassung_vorher)
  VALUES (v_c, 'chat', 'Bitte hoeher', 'in_arbeit', 1, now() + interval '5 min', 1) RETURNING id INTO a;
  j := marketing.pult_chat_fertig_seite(a, 'Erledigt', s2, '["Hinweis eins"]', '{"notiz":"n"}', 1);
  ASSERT j = '{"fassung":2}'::jsonb, format('1a: Rueckgabe: %s', j);
  SELECT * INTO f FROM marketing.inhalt_fassungen WHERE inhalt = v_c AND fassung = 2;
  ASSERT f.format = 'seite' AND f.seite = s2 AND f.bloecke IS NULL AND f.urheber = 'agent'
     AND f.felder = '{"betreff":"Betreff 080","vorschautext":""}'::jsonb,
         format('1a: Fassung 2: %s/%s/%s', f.format, f.urheber, f.felder);
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = a;
  ASSERT r.status = 'fertig' AND r.antwort = 'Erledigt' AND r.hinweise = '["Hinweis eins"]'::jsonb
     AND r.ergebnis = '{"notiz":"n"}'::jsonb AND r.fassung_vorher = 1 AND r.fassung_nachher = 2
     AND r.vergeben_bis IS NULL AND r.schritt = '' AND r.zwischenstand IS NULL,
         format('1a: Auftrag: %s', row_to_json(r));

  -- veraltete Basis: dieselbe Ausnahme wie im Block-Weg (die API macht daraus {"status":"veraltet"})
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, status, versuche, vergeben_bis, fassung_vorher)
  VALUES (v_c, 'chat', 'Noch was', 'in_arbeit', 1, now() + interval '5 min', 1) RETURNING id INTO a;
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_fertig_seite(a, 'x', s, '[]', '{}', 1);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE 'Inzwischen gibt es Fassung 2%', format('1b: veraltet: %s', v_fehler);
  ASSERT (SELECT status FROM marketing.chat_auftraege WHERE id = a) = 'in_arbeit', '1b: Auftrag bleibt in Arbeit';
  ASSERT (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt = v_c) = 2, '1b: keine neue Fassung';
  -- Basis unter fassung_vorher
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_fertig_seite(a, 'x', s, '[]', '{}', 0);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Basis-Fassung 0 passt nicht zu diesem Auftrag', format('1b: Basis 0: %s', v_fehler);
  -- ungueltige Seite wird nicht gespeichert
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_fertig_seite(a, 'x', jsonb_set(s, '{breite}', '599'), '[]', '{}', 2);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler IS NOT NULL, '1b: ungueltige Seite';
  ASSERT (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt = v_c) = 2, '1b: nichts gespeichert';
  -- Hinweise kein Array
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_fertig_seite(a, 'x', s, '{}', '{}', 2);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Hinweise muessen ein Array sein', format('1b: Hinweise: %s', v_fehler);
  -- ohne Seite: fertig ohne Fassung (wie der Block-Weg ohne Bloecke)
  j := marketing.pult_chat_fertig_seite(a, 'Nur Antwort', NULL, NULL, NULL, NULL);
  ASSERT j = '{"fassung":null}'::jsonb, format('1c: ohne Seite: %s', j);
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = a;
  ASSERT r.status = 'fertig' AND r.fassung_vorher = 1 AND r.fassung_nachher IS NULL AND r.hinweise = '[]'::jsonb,
         format('1c: Auftrag: %s', row_to_json(r));
  -- nicht mehr in Arbeit
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_fertig_seite(a, 'x', s, '[]', '{}', 2);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Auftrag ist nicht (mehr) in Arbeit', format('1c: fertig: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_fertig_seite(gen_random_uuid(), 'x', s, '[]', '{}', 2);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Unbekannter Auftrag', format('1c: unbekannt: %s', v_fehler);
  -- gestoppt
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, status, versuche, vergeben_bis, fassung_vorher, stopp)
  VALUES (v_c, 'chat', 'Stopp', 'in_arbeit', 1, now() + interval '5 min', 2, 'behalten') RETURNING id INTO a;
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_fertig_seite(a, 'x', s, '[]', '{}', 2);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Auftrag wurde gestoppt', format('1d: gestoppt: %s', v_fehler);

  -- Stopp-Abschluss "behalten" speichert
  UPDATE marketing.chat_auftraege SET schritt_nr = 2, ergebnis = '{"x":1}' WHERE id = a;
  j := marketing.pult_chat_stopp_abschliessen_seite(a, s, 'Bis hier', 2);
  ASSERT j = '{"status":"fertig","fassung":3}'::jsonb, format('1e: Stopp behalten: %s', j);
  SELECT * INTO f FROM marketing.inhalt_fassungen WHERE inhalt = v_c AND fassung = 3;
  ASSERT f.format = 'seite' AND f.seite = s AND f.urheber = 'agent', '1e: Fassung 3';
  SELECT * INTO r FROM marketing.chat_auftraege WHERE id = a;
  ASSERT r.status = 'fertig' AND r.antwort = 'Gestoppt nach Schritt 2 – bisherige Schritte übernommen'
     AND r.hinweise = '["Bis hier"]'::jsonb AND r.ergebnis = '{"x":1,"notiz":"gestoppt nach Schritt 2"}'::jsonb
     AND r.fassung_vorher = 2 AND r.fassung_nachher = 3, format('1e: Auftrag: %s', row_to_json(r));
  -- schon abgeschlossen: Status und Fassung zurueck
  j := marketing.pult_chat_stopp_abschliessen_seite(a, s, '', 3);
  ASSERT j = '{"status":"fertig","fassung":3}'::jsonb, format('1e: zweiter Aufruf: %s', j);
  -- Stopp "verwerfen" speichert nichts
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, status, versuche, vergeben_bis, fassung_vorher, stopp)
  VALUES (v_c, 'chat', 'Weg', 'in_arbeit', 1, now() + interval '5 min', 3, 'verwerfen') RETURNING id INTO a;
  j := marketing.pult_chat_stopp_abschliessen_seite(a, s2, NULL, 3);
  ASSERT j = '{"status":"fehler"}'::jsonb, format('1f: Stopp verwerfen: %s', j);
  ASSERT (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt = v_c) = 3, '1f: nichts gespeichert';
  ASSERT (SELECT antwort FROM marketing.chat_auftraege WHERE id = a) = 'Gestoppt – nichts übernommen', '1f: Antwort';
  -- nicht gestoppt
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, status, versuche, vergeben_bis, fassung_vorher)
  VALUES (v_c, 'chat', 'Laeuft', 'in_arbeit', 1, now() + interval '5 min', 3) RETURNING id INTO a;
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_stopp_abschliessen_seite(a, s, '', 3);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Auftrag wurde nicht gestoppt', format('1g: nicht gestoppt: %s', v_fehler);
  -- veraltete Basis beim Stopp
  UPDATE marketing.chat_auftraege SET stopp = 'behalten', stopp_am = now() WHERE id = a;
  v_n := marketing.pult_seite_speichern(v_c, 3, 'Betreff 080', '', s2, 'agent', false);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_stopp_abschliessen_seite(a, s, '', 3);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE format('Inzwischen gibt es Fassung %s%%', v_n), format('1g: Stopp veraltet: %s', v_fehler);
  -- Seite auf einem Block-Newsletter: gesperrt
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 080 Block F')
  RETURNING id INTO v_c;
  PERFORM marketing.pult_bloecke_speichern(v_c, 0, 'B', '',
    '{"root":{"type":"EmailLayout","data":{"childrenIds":["t"]}},
      "t":{"type":"Text","data":{"style":{},"props":{"text":"eins","markdown":false}}}}'::jsonb, 'betreiber', false);
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, status, versuche, vergeben_bis, fassung_vorher)
  VALUES (v_c, 'chat', 'x', 'in_arbeit', 1, now() + interval '5 min', 1) RETURNING id INTO a;
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_fertig_seite(a, 'x', s, '[]', '{}', 1);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Dieser Newsletter ist keine Collage', format('1h: Seite auf Block: %s', v_fehler);
END $$;

-- 2) Bildauftraege und Einsetzen auf Bild-Ebenen
DO $$ DECLARE s jsonb; v_c uuid; bi uuid; b7 uuid; b8 uuid; bf uuid; j jsonb; r record; v_fehler text; v_n int;
BEGIN
  s := '{"version":1,"breite":600,"hoehe":1200,"hintergrund":{"farbe":"#ffffff"},
         "schriften":{"anzeige":"dm-sans","text":"dm-sans"},
         "ebenen":[{"id":"held","art":"bild","x":300,"y":200,"breite":400,"hoehe":300,
                    "quelle":"medien:platzhalter-4x3.png"},
                   {"id":"foto","art":"bild","x":300,"y":600,"breite":200,"hoehe":200,"quelle":"medien:probe-foto.jpg"},
                   {"id":"t","art":"text","x":300,"y":900,"breite":480,"hoehe":120,"text":"Probe",
                    "rolle":"TEXT","groesse":17,"farbe":"#333333"}]}'::jsonb;
  v_c := marketing.pult_collage_anlegen('vibemind', 'Probe 080 Bild', 'Betreff', s, 'probe');

  -- 5 Argumente: Bild-Ebene
  bi := marketing.pult_bild_auftrag(v_c, 'held', false, 'Ein Team', 'agent');
  SELECT * INTO r FROM marketing.bild_auftraege WHERE id = bi;
  ASSERT r.platz = 'held' AND r.status = 'offen' AND r.grund_fassung = 1 AND r.hinweis = 'Ein Team'
     AND r.urheber = 'agent', format('2a: Auftrag: %s', row_to_json(r));
  -- Text-Ebene, unbekannte Ebene, ohne Ebene: Ausnahme mit deutschem Grund
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bild_auftrag(v_c, 't', false, '', 'agent'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Ebene t ist keine Bild-Ebene', format('2b: Text-Ebene: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bild_auftrag(v_c, 'gibtsnicht', false, '', 'agent');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Ebene gibtsnicht gibt es in der gespeicherten Fassung nicht – erst speichern',
         format('2b: unbekannte Ebene: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bild_auftrag(v_c, NULL, true, '', 'system'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Bei einer Collage braucht der Bildauftrag eine Bild-Ebene', format('2b: ohne Ebene: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bild_auftrag(v_c, 'held', false, '', 'robot'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Urheber muss system, mensch oder agent sein', format('2b: Urheber: %s', v_fehler);
  -- 7 Argumente: aendern braucht ein echtes Bild in der Ebene
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bild_auftrag(v_c, 'held', false, '', 'agent', 55, 'aendern');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Hier ist noch kein Bild – nimm bild_erzeugen', format('2c: aendern Platzhalter: %s', v_fehler);
  b7 := marketing.pult_bild_auftrag(v_c, 'foto', false, 'heller', 'agent', 100, 'aendern');
  SELECT * INTO r FROM marketing.bild_auftraege WHERE id = b7;
  ASSERT r.platz = 'foto' AND r.modus = 'aendern' AND r.staerke = 100, format('2c: aendern: %s/%s', r.modus, r.staerke);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bild_auftrag(v_c, 't', false, '', 'agent', 55, 'neu');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Ebene t ist keine Bild-Ebene', format('2c: 7-arg Text-Ebene: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bild_auftrag(v_c, 'foto', false, '', 'agent', 101, 'neu');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Staerke muss 0 bis 100 sein', format('2c: Staerke: %s', v_fehler);
  -- 8 Argumente: Vorlagen; ersetzt den offenen Auftrag derselben Ebene
  b8 := marketing.pult_bild_auftrag(v_c, 'foto', false, '', 'agent', 100, 'neu', '["medien:a.jpg"]');
  SELECT * INTO r FROM marketing.bild_auftraege WHERE id = b8;
  ASSERT r.modus = 'neu' AND r.vorlagen = '["medien:a.jpg"]'::jsonb, format('2d: Vorlagen: %s', row_to_json(r));
  ASSERT (SELECT status FROM marketing.bild_auftraege WHERE id = b7) = 'verworfen', '2d: alter Auftrag verworfen';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bild_auftrag(v_c, 'gibtsnicht', false, '', 'agent', 100, 'neu', '[]');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE 'Ebene gibtsnicht gibt es%', format('2d: 8-arg unbekannte Ebene: %s', v_fehler);

  -- Einsetzen: setzt quelle der Ebene, speichert als agent
  UPDATE marketing.bild_auftraege SET status = 'in_arbeit', versuche = 1, vergeben_bis = now() + interval '5 min'
   WHERE id = bi;
  j := marketing.pult_bild_einsetzen(bi, '{"held":"medien:nl-0123abcd-held.jpg"}', '');
  ASSERT j = '{"fassung":2,"eingesetzt":["held"],"uebersprungen":[]}'::jsonb, format('2e: einsetzen: %s', j);
  SELECT * INTO r FROM marketing.inhalt_fassungen WHERE inhalt = v_c AND fassung = 2;
  ASSERT r.format = 'seite' AND r.urheber = 'agent' AND r.seite #>> '{ebenen,0,quelle}' = 'medien:nl-0123abcd-held.jpg'
     AND r.seite #>> '{ebenen,1,quelle}' = 'medien:probe-foto.jpg' AND r.seite - 'ebenen' = s - 'ebenen'
     AND r.felder = '{"betreff":"Betreff","vorschautext":""}'::jsonb,
         format('2e: Fassung 2: %s', r.seite);
  SELECT * INTO r FROM marketing.bild_auftraege WHERE id = bi;
  ASSERT r.status = 'fertig' AND r.vergeben_bis IS NULL AND r.ergebnis = '{"held":"medien:nl-0123abcd-held.jpg"}'::jsonb,
         format('2e: Auftrag: %s', r.status);
  -- falscher Name / fremde Ebene
  UPDATE marketing.bild_auftraege SET status = 'in_arbeit', vergeben_bis = now() + interval '5 min' WHERE id = b8;
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bild_einsetzen(b8, '{"foto":"medien:boese.jpg"}', '');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Ungueltiger Bildname fuer foto', format('2f: Bildname: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bild_einsetzen(b8, '{"held":"medien:nl-0123abcd-held.jpg"}', '');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Platz held gehoert nicht zu diesem Auftrag', format('2f: fremde Ebene: %s', v_fehler);
  -- inzwischen belegt (der Betreiber hat ein anderes Bild gesetzt): uebersprungen, fehler, keine Fassung; 4 Argumente
  v_n := marketing.pult_seite_speichern(v_c, 2, 'Betreff', '',
           jsonb_set((SELECT seite FROM marketing.inhalt_fassungen WHERE inhalt = v_c AND fassung = 2),
                     '{ebenen,1,quelle}', '"medien:anders.jpg"'), 'betreiber', false);
  j := marketing.pult_bild_einsetzen(b8, '{"foto":"medien:nl-0123abcd-foto.jpg"}', 'Probe', '{"ms":5}');
  ASSERT j = '{"fassung":null,"eingesetzt":[],"uebersprungen":["foto: Platz inzwischen belegt"]}'::jsonb,
         format('2g: belegt: %s', j);
  SELECT * INTO r FROM marketing.bild_auftraege WHERE id = b8;
  ASSERT r.status = 'fehler' AND r.befund = 'Probe; foto: Platz inzwischen belegt' AND r.messung = '{"ms":5}'::jsonb,
         format('2g: Auftrag: %s / %s', r.status, r.befund);
  ASSERT (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt = v_c) = v_n, '2g: keine neue Fassung';
  -- Ebene inzwischen geloescht (oder zur Text-Ebene geworden): uebersprungen
  bf := marketing.pult_bild_auftrag(v_c, 'foto', false, '', 'agent');
  UPDATE marketing.bild_auftraege SET status = 'in_arbeit', vergeben_bis = now() + interval '5 min' WHERE id = bf;
  v_n := marketing.pult_seite_speichern(v_c, v_n, 'Betreff', '',
           jsonb_set(s, '{ebenen}', (s->'ebenen') - 1), 'betreiber', false);
  j := marketing.pult_bild_einsetzen(bf, '{"foto":"medien:nl-0123abcd-foto.jpg"}', '');
  ASSERT j = '{"fassung":null,"eingesetzt":[],"uebersprungen":["foto: Platz gibt es nicht mehr"]}'::jsonb,
         format('2h: Ebene weg: %s', j);
  -- entschieden: keine neuen Bildauftraege (laufende verwirft der Status-Trigger aus 056)
  bf := marketing.pult_bild_auftrag(v_c, 'held', false, '', 'agent', 55, 'ueberarbeiten');
  UPDATE marketing.bild_auftraege SET status = 'in_arbeit', vergeben_bis = now() + interval '5 min' WHERE id = bf;
  UPDATE marketing.inhalte SET status = 'abgelehnt', entschieden_von = 'probe', entschieden_am = now() WHERE id = v_c;
  ASSERT (SELECT status FROM marketing.bild_auftraege WHERE id = bf) = 'verworfen', '2i: Auftrag verworfen';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bild_auftrag(v_c, 'held', false, '', 'agent'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Schon entschieden - keine neuen Bilder', format('2i: kein Auftrag: %s', v_fehler);
END $$;

-- 2b) Einsetzen ueber eine unveraenderte echte Bild-Ebene
DO $$ DECLARE s jsonb; v_c uuid; bf uuid; j jsonb; BEGIN
  s := '{"version":1,"breite":600,"hoehe":1200,"hintergrund":{"farbe":"#ffffff"},
         "schriften":{"anzeige":"dm-sans","text":"dm-sans"},
         "ebenen":[{"id":"foto","art":"bild","x":300,"y":600,"breite":200,"hoehe":200,"quelle":"medien:probe-foto.jpg"}]}'::jsonb;
  v_c := marketing.pult_collage_anlegen('vibemind', 'Probe 080 Bild 2', 'Betreff', s, 'probe');
  bf := marketing.pult_bild_auftrag(v_c, 'foto', false, '', 'agent', 55, 'aendern');
  UPDATE marketing.bild_auftraege SET status = 'in_arbeit', vergeben_bis = now() + interval '5 min' WHERE id = bf;
  j := marketing.pult_bild_einsetzen(bf, '{"foto":"medien:nl-0123abcd-foto-frei.png"}', '');
  ASSERT j = '{"fassung":2,"eingesetzt":["foto"],"uebersprungen":[]}'::jsonb, format('2j: unveraendert: %s', j);
  ASSERT (SELECT seite #>> '{ebenen,0,quelle}' FROM marketing.inhalt_fassungen WHERE inhalt = v_c AND fassung = 2)
         = 'medien:nl-0123abcd-foto-frei.png', '2j: quelle gesetzt';
END $$;

-- 3) Kompositionen: hoechstens 5, neueste zuerst, ohne den eigenen, nur dieselbe Firma, nur Collagen
DO $$ DECLARE s jsonb; v_m text; v_m2 text; v_ids uuid[] := ARRAY[]::uuid[]; v_i uuid; v_b uuid; v_x uuid;
  j jsonb; g int; v_neu jsonb;
BEGIN
  v_m := 'p080_' || substr(md5(random()::text || clock_timestamp()::text), 1, 10);
  v_m2 := v_m || 'b';
  INSERT INTO marketing.mandanten (id, name, aktiv) VALUES (v_m, 'Probe 080', false), (v_m2, 'Probe 080 B', false);
  s := '{"version":1,"breite":600,"hoehe":1200,"hintergrund":{"farbe":"#ffffff"},
         "schriften":{"anzeige":"dm-sans","text":"dm-sans"},"ebenen":[]}'::jsonb;
  ASSERT marketing.pult_seite_kompositionen(v_m, NULL) = '[]'::jsonb, '3: ohne Collagen leer';
  -- 7 Collagen, die i-te ist i Minuten alt (1 = neueste)
  FOR g IN 1..7 LOOP
    v_i := marketing.pult_collage_anlegen(v_m, 'Probe 080 K' || g, 'B', jsonb_set(s, '{hoehe}', to_jsonb(400 + g)), 'probe');
    UPDATE marketing.inhalte SET erstellt_am = now() - make_interval(mins => g) WHERE id = v_i;
    v_ids := v_ids || v_i;
  END LOOP;
  -- die neueste Fassung zaehlt
  v_neu := jsonb_set(s, '{hoehe}', '999');
  PERFORM marketing.pult_seite_speichern(v_ids[2], 1, 'B', '', v_neu, 'betreiber', false);
  -- Block-Newsletter (neuer als alle) und Collage einer anderen Firma zaehlen nicht
  INSERT INTO marketing.inhalte (mandant, art, titel, erstellt_am) VALUES (v_m, 'newsletter', 'Probe 080 KB', now())
  RETURNING id INTO v_b;
  PERFORM marketing.pult_bloecke_speichern(v_b, 0, 'B', '',
    '{"root":{"type":"EmailLayout","data":{"childrenIds":["t"]}},
      "t":{"type":"Text","data":{"style":{},"props":{"text":"eins","markdown":false}}}}'::jsonb, 'betreiber', false);
  v_x := marketing.pult_collage_anlegen(v_m2, 'Probe 080 KX', 'B', s, 'probe');

  j := marketing.pult_seite_kompositionen(v_m, v_ids[1]);
  ASSERT jsonb_typeof(j) = 'array' AND jsonb_array_length(j) = 5, format('3a: fuenf Eintraege: %s', jsonb_array_length(j));
  ASSERT (SELECT array_agg((e->>'inhalt')::uuid ORDER BY o) FROM jsonb_array_elements(j) WITH ORDINALITY t(e, o))
         = v_ids[2:6], '3a: die neuesten ohne den eigenen, neueste zuerst';
  ASSERT j->0->'seite' = v_neu, format('3a: neueste Fassung: %s', j->0->'seite');
  ASSERT j->1->'seite' = jsonb_set(s, '{hoehe}', '403'), '3a: Seite der dritten';
  ASSERT (SELECT bool_and(e ?& ARRAY['inhalt', 'seite'] AND (SELECT count(*) FROM jsonb_object_keys(e)) = 2)
            FROM jsonb_array_elements(j) e), '3a: Form {inhalt, seite}';
  ASSERT NOT EXISTS (SELECT 1 FROM jsonb_array_elements(j) e WHERE (e->>'inhalt')::uuid IN (v_b, v_x)),
         '3a: keine Bloecke, keine fremde Firma';
  j := marketing.pult_seite_kompositionen(v_m, NULL);
  ASSERT (SELECT array_agg((e->>'inhalt')::uuid ORDER BY o) FROM jsonb_array_elements(j) WITH ORDINALITY t(e, o))
         = v_ids[1:5], '3b: ohne Ausnahme die fuenf neuesten';
  j := marketing.pult_seite_kompositionen(v_m2, v_x);
  ASSERT j = '[]'::jsonb, format('3c: andere Firma ohne die eigene leer: %s', j);
  ASSERT marketing.pult_seite_kompositionen('gibtsnicht', NULL) = '[]'::jsonb, '3d: unbekannte Firma leer';
END $$;

-- 4) Block-Wege unveraendert
DO $$ DECLARE d jsonb; v_i uuid; a uuid; bi uuid; j jsonb; v_fehler text; r record; BEGIN
  d := '{"root":{"type":"EmailLayout","data":{"childrenIds":["held","t"]}},
         "held":{"type":"Image","data":{"style":{"padding":{"top":0,"bottom":0,"left":0,"right":0}},"props":{"url":"medien:platzhalter-2x1.png","alt":"Team","width":600,"height":300}}},
         "t":{"type":"Text","data":{"style":{},"props":{"text":"eins","markdown":false}}}}'::jsonb;
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 080 Block')
  RETURNING id INTO v_i;
  PERFORM marketing.pult_bloecke_speichern(v_i, 0, 'Probe 080 Block', '', d, 'betreiber', false);
  -- pult_chat_fertig
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, status, versuche, vergeben_bis, fassung_vorher)
  VALUES (v_i, 'chat', 'x', 'in_arbeit', 1, now() + interval '5 min', 1) RETURNING id INTO a;
  j := marketing.pult_chat_fertig(a, 'ok', jsonb_set(d, '{t,data,props,text}', '"zwei"'), '[]', '{}', 1);
  ASSERT j = '{"fassung":2}'::jsonb, format('4a: Block fertig: %s', j);
  ASSERT (SELECT format = 'bloecke' AND urheber = 'agent' FROM marketing.inhalt_fassungen WHERE inhalt = v_i AND fassung = 2),
         '4a: Blockfassung';
  -- Bildauftrag: 5, 7 und 8 Argumente, alle Plaetze, Fehler wie vorher
  bi := marketing.pult_bild_auftrag(v_i, NULL, true, '', 'system');
  ASSERT (SELECT platz IS NULL AND nur_leere AND grund_fassung = 2 FROM marketing.bild_auftraege WHERE id = bi),
         '4b: alle leeren Plaetze';
  bi := marketing.pult_bild_auftrag(v_i, 'held', false, '', 'mensch', 55, 'ueberarbeiten', '[]');
  ASSERT (SELECT platz = 'held' AND modus = 'ueberarbeiten' FROM marketing.bild_auftraege WHERE id = bi), '4b: 8-arg';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bild_auftrag(v_i, 't', false, '', 'mensch'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Bildplatz t gibt es in der gespeicherten Fassung nicht - erst speichern', format('4b: Platz: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bild_auftrag(v_i, 'held', false, '', 'mensch', 55, 'aendern');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Hier ist noch kein Bild – nimm bild_erzeugen', format('4b: aendern: %s', v_fehler);
  -- Einsetzen in den Block
  UPDATE marketing.bild_auftraege SET status = 'in_arbeit', vergeben_bis = now() + interval '5 min' WHERE id = bi;
  j := marketing.pult_bild_einsetzen(bi, '{"held":"medien:nl-0123abcd-held.jpg"}', '', '{}');
  ASSERT j = '{"fassung":3,"eingesetzt":["held"],"uebersprungen":[]}'::jsonb, format('4c: Block einsetzen: %s', j);
  SELECT * INTO r FROM marketing.inhalt_fassungen WHERE inhalt = v_i AND fassung = 3;
  ASSERT r.format = 'bloecke' AND r.bloecke #>> '{held,data,props,url}' = 'medien:nl-0123abcd-held.jpg'
     AND r.seite IS NULL, '4c: Blockfassung mit Bild';
  -- Text-Fassung (format felder): Bildauftrag wie vorher abgelehnt
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 080 Leer')
  RETURNING id INTO v_i;
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_bild_auftrag(v_i, 'held', false, '', 'mensch'); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Dieser Newsletter ist nicht im Editor-Format', format('4d: ohne Fassung: %s', v_fehler);
END $$;

-- 5) Fix-Runde 1: Bloecke nie ueber eine Collage
DO $$ DECLARE s jsonb; d jsonb; v_c uuid; v_i uuid; a uuid; j jsonb; v_fehler text; r record; BEGIN
  s := '{"version":1,"breite":600,"hoehe":1200,"hintergrund":{"farbe":"#ffffff","bild":null},
         "schriften":{"anzeige":"dm-sans","text":"dm-sans"},"ebenen":[]}'::jsonb;
  d := '{"root":{"type":"EmailLayout","data":{"childrenIds":["t"]}},
         "t":{"type":"Text","data":{"style":{},"props":{"text":"eins","markdown":false}}}}'::jsonb;
  v_c := marketing.pult_collage_anlegen('vibemind', 'Probe 080 Schutz', 'B', s, 'probe');
  -- fertig mit Bloecken auf einer Collage: Ausnahme, keine Fassung, Auftrag bleibt in Arbeit
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, status, versuche, vergeben_bis, fassung_vorher)
  VALUES (v_c, 'chat', 'x', 'in_arbeit', 1, now() + interval '5 min', 1) RETURNING id INTO a;
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_fertig(a, 'x', d, '[]', '{}', 1); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Das ist ein Collage-Newsletter – Blöcke werden nicht übernommen', format('5a: fertig: %s', v_fehler);
  ASSERT (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt = v_c) = 1, '5a: keine Blockfassung';
  ASSERT (SELECT format FROM marketing.inhalt_fassungen WHERE inhalt = v_c AND fassung = 1) = 'seite', '5a: Collage bleibt';
  ASSERT (SELECT status FROM marketing.chat_auftraege WHERE id = a) = 'in_arbeit', '5a: Auftrag bleibt in Arbeit';
  -- ohne Bloecke laeuft das Original (fertig ohne Fassung)
  j := marketing.pult_chat_fertig(a, 'Nur Antwort', NULL, NULL, NULL, NULL);
  ASSERT j = '{"fassung":null}'::jsonb, format('5b: ohne Bloecke: %s', j);
  -- Stopp behalten mit Bloecken: Ausnahme; mit NULL schliesst er ab (der Rueckfall der API)
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, status, versuche, vergeben_bis, fassung_vorher, stopp)
  VALUES (v_c, 'chat', 'Stopp', 'in_arbeit', 1, now() + interval '5 min', 1, 'behalten') RETURNING id INTO a;
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_chat_stopp_abschliessen(a, d, '', 1); EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Das ist ein Collage-Newsletter – Blöcke werden nicht übernommen', format('5c: Stopp: %s', v_fehler);
  ASSERT (SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt = v_c) = 1, '5c: keine Blockfassung';
  j := marketing.pult_chat_stopp_abschliessen(a, NULL, 'Zwischenstand nicht übernommen', NULL);
  ASSERT j = '{"status":"fehler"}'::jsonb, format('5c: mit NULL: %s', j);
  -- schon abgeschlossen: Endstand wie bisher, auch mit Bloecken (nichts wuerde gespeichert)
  j := marketing.pult_chat_stopp_abschliessen(a, d, '', 1);
  ASSERT j = '{"status":"fehler"}'::jsonb, format('5d: Endstand: %s', j);
  -- Block-Newsletter: Stopp behalten mit Bloecken speichert wie bisher
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 080 Schutz Block')
  RETURNING id INTO v_i;
  PERFORM marketing.pult_bloecke_speichern(v_i, 0, 'B', '', d, 'betreiber', false);
  INSERT INTO marketing.chat_auftraege (inhalt, art, nachricht, status, versuche, vergeben_bis, fassung_vorher, stopp)
  VALUES (v_i, 'chat', 'Stopp', 'in_arbeit', 1, now() + interval '5 min', 1, 'behalten') RETURNING id INTO a;
  j := marketing.pult_chat_stopp_abschliessen(a, jsonb_set(d, '{t,data,props,text}', '"zwei"'), '', 1);
  ASSERT j = '{"status":"fertig","fassung":2}'::jsonb, format('5e: Block-Stopp: %s', j);
  SELECT * INTO r FROM marketing.inhalt_fassungen WHERE inhalt = v_i AND fassung = 2;
  ASSERT r.format = 'bloecke' AND r.bloecke #>> '{t,data,props,text}' = 'zwei', '5e: Blockfassung';
END $$;

-- 6) Fix-Runde 1: Dateipruefung des Bild-Arbeiters fuer Bild-Ebenen
DO $$ DECLARE s jsonb; d jsonb; v_c uuid; v_i uuid; b uuid; ba uuid; v_n int; BEGIN
  s := '{"version":1,"breite":600,"hoehe":1200,"hintergrund":{"farbe":"#ffffff","bild":null},
         "schriften":{"anzeige":"dm-sans","text":"dm-sans"},
         "ebenen":[{"id":"held","art":"bild","x":300,"y":200,"breite":400,"hoehe":300,
                    "quelle":"medien:platzhalter-4x3.png"},
                   {"id":"t","art":"text","x":300,"y":600,"breite":480,"hoehe":120,"text":"Probe",
                    "rolle":"TEXT","groesse":17,"farbe":"#333333"}]}'::jsonb;
  v_c := marketing.pult_collage_anlegen('vibemind', 'Probe 080 Datei', 'B', s, 'probe');
  b := marketing.pult_bild_auftrag(v_c, 'held', false, '', 'agent');
  ASSERT marketing.pult_bild_datei_fehler(b, 'held') = 'Auftrag ist nicht (mehr) in Arbeit', '6a: offen';
  UPDATE marketing.bild_auftraege SET status = 'in_arbeit', versuche = 1, vergeben_bis = now() + interval '5 min'
   WHERE id = b;
  ASSERT marketing.pult_bild_datei_fehler(b, 'held') IS NULL, '6a: Bild-Ebene gilt';
  ASSERT marketing.pult_bild_datei_fehler(b, 't') = 'Platz gehoert nicht zu diesem Auftrag', '6a: fremder Platz';
  ASSERT marketing.pult_bild_datei_fehler(gen_random_uuid(), 'held') = 'Unbekannter Auftrag', '6a: unbekannt';
  -- Auftrag fuer alle Plaetze (platz NULL, direkt angelegt): Text-Ebene und unbekannte Ebene sind keine Bildplaetze
  INSERT INTO marketing.bild_auftraege (inhalt, platz, nur_leere, hinweis, grund_fassung, urheber, status,
                                        versuche, vergeben_bis)
  VALUES (v_c, NULL, false, '', 1, 'system', 'in_arbeit', 1, now() + interval '5 min') RETURNING id INTO ba;
  ASSERT marketing.pult_bild_datei_fehler(ba, 'held') IS NULL, '6b: Bild-Ebene';
  ASSERT marketing.pult_bild_datei_fehler(ba, 't') = 'Bildplatz gibt es nicht', '6b: Text-Ebene';
  ASSERT marketing.pult_bild_datei_fehler(ba, 'weg') = 'Bildplatz gibt es nicht', '6b: unbekannte Ebene';
  ASSERT marketing.pult_bild_datei_fehler(ba, NULL) = 'Bildplatz gibt es nicht', '6b: ohne Platz';
  -- Mass 0: laesst schon die Seitenpruefung nicht zu; falls doch gespeichert, ist es kein Bildplatz
  v_n := NULL;
  BEGIN v_n := marketing.pult_seite_speichern(v_c, 1, 'B', '', jsonb_set(s, '{ebenen,0,breite}', '0'), 'betreiber', false);
  EXCEPTION WHEN OTHERS THEN v_n := NULL; END;
  IF v_n IS NOT NULL THEN
    ASSERT marketing.pult_bild_datei_fehler(ba, 'held') = 'Bildplatz gibt es nicht', '6c: Breite 0';
  ELSE
    RAISE NOTICE '6c: Breite 0 lehnt schon pult_seite_speichern ab';
  END IF;
  -- abgelaufene Vergabe
  UPDATE marketing.bild_auftraege SET vergeben_bis = now() - interval '1 min' WHERE id = ba;
  ASSERT marketing.pult_bild_datei_fehler(ba, 'held') = 'Auftrag ist nicht (mehr) in Arbeit', '6d: abgelaufen';
  -- Block-Newsletter: das Original (Bildplatz in den Bloecken)
  d := '{"root":{"type":"EmailLayout","data":{"childrenIds":["held","t"]}},
         "held":{"type":"Image","data":{"style":{},"props":{"url":"medien:platzhalter-2x1.png","alt":"x","width":600,"height":300}}},
         "t":{"type":"Text","data":{"style":{},"props":{"text":"eins","markdown":false}}}}'::jsonb;
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 080 Datei Block')
  RETURNING id INTO v_i;
  PERFORM marketing.pult_bloecke_speichern(v_i, 0, 'B', '', d, 'betreiber', false);
  b := marketing.pult_bild_auftrag(v_i, NULL, false, '', 'system');
  UPDATE marketing.bild_auftraege SET status = 'in_arbeit', versuche = 1, vergeben_bis = now() + interval '5 min'
   WHERE id = b;
  ASSERT marketing.pult_bild_datei_fehler(b, 'held') IS NULL, '6e: Block-Bildplatz';
  ASSERT marketing.pult_bild_datei_fehler(b, 't') = 'Bildplatz gibt es nicht', '6e: Textblock';
END $$;

SELECT 'verify_080 ok' AS ergebnis;

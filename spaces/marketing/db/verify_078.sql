-- Nachweise fuer 078 (Marken-Chat: Quellen, Rueckgaengig), nur ueber migration_probe (eine Transaktion +
-- ROLLBACK). Probe-Firmen probe_078 (Rueckgaengig), probe_078_b (Quellen), probe_078_d (nicht direkt),
-- probe_078_e (Assistent arbeitet), probe_078_f (toter Uebernahme-Auftrag). Jeder Fall wirft 'verify_078: ...', wenn er scheitert.
CREATE TEMP TABLE _p078 ON COMMIT DROP AS SELECT NULL::text AS k, NULL::uuid AS id LIMIT 0;

DO $$ BEGIN
  INSERT INTO marketing.mandanten (id, name, aktiv) VALUES
    ('probe_078', 'Probe 078', true), ('probe_078_b', 'Probe 078 B', true),
    ('probe_078_d', 'Probe 078 D', true), ('probe_078_e', 'Probe 078 E', true),
    ('probe_078_f', 'Probe 078 F', true);
END $$;

-- (a) Spalte quellen existiert, der Check nimmt 12 Quellen und lehnt 13 ab
DO $$ DECLARE a uuid; geworfen boolean := false;
  q jsonb := '{"titel":"t","url":"https://example.org","art":"gelesen"}';
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                  WHERE table_schema = 'marketing' AND table_name = 'marken_auftraege'
                    AND column_name = 'quellen' AND data_type = 'jsonb') THEN
    RAISE EXCEPTION 'verify_078: Spalte marken_auftraege.quellen fehlt'; END IF;
  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, status)
  VALUES ('probe_078_b', 'chat', 'a', 'fertig') RETURNING id INTO a;
  UPDATE marketing.marken_auftraege
     SET quellen = (SELECT jsonb_agg(q) FROM generate_series(1, 12)) WHERE id = a;
  BEGIN
    UPDATE marketing.marken_auftraege
       SET quellen = (SELECT jsonb_agg(q) FROM generate_series(1, 13)) WHERE id = a;
  EXCEPTION WHEN check_violation THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_078: 13 Quellen muessen am Check scheitern'; END IF;
  geworfen := false;
  BEGIN
    UPDATE marketing.marken_auftraege SET quellen = '{"a":1}' WHERE id = a;
  EXCEPTION WHEN check_violation THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_078: quellen als Objekt muss am Check scheitern'; END IF;
END $$;

-- (b) pult_marke_quellen: in_arbeit mit gueltiger Sperre -> true und gespeichert; fertig, abgelaufen,
-- unbekannt -> false; ungueltige Quellen werfen (auch bei fertigem Auftrag: die Pruefung kommt zuerst)
DO $$ DECLARE ok uuid; fertig uuid; geworfen boolean;
  q jsonb := '[{"titel":"Startseite","url":"https://example.org/","art":"gelesen"},
               {"titel":"Treffer","url":"http://example.com/x","art":"websuche"}]';
BEGIN
  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, status, vergeben_bis)
  VALUES ('probe_078_b', 'chat', 'b', 'in_arbeit', now() + interval '5 minutes') RETURNING id INTO ok;
  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, status)
  VALUES ('probe_078_b', 'chat', 'c', 'fertig') RETURNING id INTO fertig;
  IF NOT marketing.pult_marke_quellen(ok, q) THEN
    RAISE EXCEPTION 'verify_078: in_arbeit mit Sperre muss true geben'; END IF;
  IF (SELECT quellen FROM marketing.marken_auftraege WHERE id = ok) IS DISTINCT FROM q THEN
    RAISE EXCEPTION 'verify_078: Quellen nicht gespeichert'; END IF;
  IF marketing.pult_marke_quellen(fertig, q) THEN
    RAISE EXCEPTION 'verify_078: fertiger Auftrag darf nicht schreiben'; END IF;
  IF (SELECT quellen FROM marketing.marken_auftraege WHERE id = fertig) IS NOT NULL THEN
    RAISE EXCEPTION 'verify_078: fertiger Auftrag wurde doch beschrieben'; END IF;
  IF marketing.pult_marke_quellen(gen_random_uuid(), q) THEN
    RAISE EXCEPTION 'verify_078: unbekannter Auftrag darf nicht schreiben'; END IF;
  -- leere Liste ist gueltig (keine Quellen)
  IF NOT marketing.pult_marke_quellen(ok, '[]') THEN
    RAISE EXCEPTION 'verify_078: leere Liste muss gehen'; END IF;
  -- genau an den Grenzen geht es: 12 Quellen, Titel 120, URL 500
  IF NOT marketing.pult_marke_quellen(ok, (SELECT jsonb_agg(jsonb_build_object(
       'titel', repeat('t', 120), 'url', 'https://e.org/' || repeat('p', 486), 'art', 'websuche'))
       FROM generate_series(1, 12))) THEN
    RAISE EXCEPTION 'verify_078: Grenzwerte muessen gehen'; END IF;

  geworfen := false;
  BEGIN PERFORM marketing.pult_marke_quellen(fertig, '[{"titel":"x","url":"javascript:x","art":"gelesen"}]');
  EXCEPTION WHEN others THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_078: url javascript:x muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.pult_marke_quellen(ok, jsonb_build_array(jsonb_build_object(
          'titel', repeat('t', 121), 'url', 'https://example.org', 'art', 'gelesen')));
  EXCEPTION WHEN others THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_078: Titel > 120 muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.pult_marke_quellen(ok, jsonb_build_array(jsonb_build_object(
          'titel', 't', 'url', 'https://e.org/' || repeat('p', 487), 'art', 'gelesen')));
  EXCEPTION WHEN others THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_078: URL > 500 muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.pult_marke_quellen(ok, '[{"titel":"t","url":"https://e.org","art":"geraten"}]');
  EXCEPTION WHEN others THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_078: unbekannte art muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.pult_marke_quellen(ok, '[{"titel":"t","url":"https://e.org","art":"gelesen","x":1}]');
  EXCEPTION WHEN others THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_078: fremder Schluessel muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.pult_marke_quellen(ok, '{"titel":"t"}');
  EXCEPTION WHEN others THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_078: Quellen als Objekt muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.pult_marke_quellen(ok, NULL);
  EXCEPTION WHEN others THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_078: Quellen NULL muss werfen'; END IF;
  geworfen := false;
  BEGIN PERFORM marketing.pult_marke_quellen(ok, (SELECT jsonb_agg(jsonb_build_object(
          'titel', 't', 'url', 'https://e.org', 'art', 'gelesen')) FROM generate_series(1, 13)));
  EXCEPTION WHEN others THEN geworfen := true; END;
  IF NOT geworfen THEN RAISE EXCEPTION 'verify_078: 13 Quellen muessen werfen'; END IF;

  -- Fix-Runde 1: Nicht-Objekte, fehlender Schluessel, Titel keine Zeichenkette, leerer Titel
  DECLARE schlecht jsonb; BEGIN
    FOREACH schlecht IN ARRAY ARRAY[
        '["x"]', '[null]', '[1]', '[[]]',
        '[{"titel":"t","url":"https://e.org"}]',
        '[{"url":"https://e.org","art":"gelesen"}]',
        '[{"titel":7,"url":"https://e.org","art":"gelesen"}]',
        '[{"titel":null,"url":"https://e.org","art":"gelesen"}]',
        '[{"titel":"","url":"https://e.org","art":"gelesen"}]',
        '[{"titel":"   ","url":"https://e.org","art":"gelesen"}]',
        '[{"titel":" \t\n ","url":"https://e.org","art":"gelesen"}]',
        '[{"titel":"t","url":5,"art":"gelesen"}]',
        -- Abschluss-Review I2: Zugangsdaten in der Adresse, Leer- und Steuerzeichen irgendwo in der url
        '[{"titel":"t","url":"https://user:pw@e.org/","art":"gelesen"}]',
        '[{"titel":"t","url":"https://e.org@boese.example/a","art":"gelesen"}]',
        '[{"titel":"t","url":"http://@e.org","art":"gelesen"}]',
        '[{"titel":"t","url":"https://e.org /a","art":"gelesen"}]',
        '[{"titel":"t","url":"https://e.org/a\tb","art":"gelesen"}]',
        '[{"titel":"t","url":"https://e.org/a\nb","art":"gelesen"}]',
        '[{"titel":"t","url":"https://e.org/a\rb","art":"gelesen"}]',
        '[{"titel":"t","url":"https://e.org/a\u0001b","art":"gelesen"}]',
        '[{"titel":"t","url":"https://e.org/a\u007fb","art":"gelesen"}]',
        '[{"titel":"t","url":"https://e.org/a\u0085b","art":"gelesen"}]',
        '[{"titel":"t","url":"https://e.org/a\u00a0b","art":"gelesen"}]',
        '[{"titel":"t","url":"https://e.org/a\u2028b","art":"gelesen"}]'
      ]::jsonb[] LOOP
      geworfen := false;
      BEGIN PERFORM marketing.pult_marke_quellen(ok, schlecht);
      EXCEPTION WHEN others THEN geworfen := true; END;
      IF NOT geworfen THEN RAISE EXCEPTION 'verify_078: Quellen % muessen werfen', schlecht; END IF;
    END LOOP;
  END;

  -- '@' im Pfad, in der Suche oder im Anker ist keine Zugangsangabe und bleibt gueltig
  IF NOT marketing.pult_marke_quellen(ok, '[{"titel":"t","url":"https://e.org/a@b","art":"gelesen"},
       {"titel":"t","url":"https://e.org?mail=a@b","art":"gelesen"},
       {"titel":"t","url":"https://e.org#a@b","art":"websuche"}]') THEN
    RAISE EXCEPTION 'verify_078: @ hinter der Adresse muss gehen'; END IF;

  -- Sperre abgelaufen -> false
  UPDATE marketing.marken_auftraege SET vergeben_bis = now() - interval '1 second' WHERE id = ok;
  IF marketing.pult_marke_quellen(ok, q) THEN
    RAISE EXCEPTION 'verify_078: abgelaufene Sperre darf nicht schreiben'; END IF;
END $$;

-- (c) test_rueckgaengig_nur_fuer_den_juengsten: zwei direkt angenommene Vorschlaege A (aelter) und B (juenger),
-- danach ein offener, nicht direkter Vorschlag C und ein wartender Wissens-Lauf (blockiert nicht)
DO $$ DECLARE auf uuid; va uuid; vb uuid; vc uuid; BEGIN
  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, status, erstellt_am)
  VALUES ('probe_078', 'chat', 'Ton direkt', 'fertig', now() - interval '3 hours') RETURNING id INTO auf;
  INSERT INTO marketing.marken_vorschlaege (mandant, auftrag, vorschlag, status, erstellt_am,
                                            entschieden_von, entschieden_am)
  VALUES ('probe_078', auf,
          '{"abschnitte":{"Ton":"Ton A"},"direkt":true,
            "aenderungen":[{"feld":"Ton","vorher":"Ton alt","nachher":"Ton A"}]}',
          'angenommen', now() - interval '3 hours', 'Agent (direkt)', now() - interval '3 hours')
  RETURNING id INTO va;
  UPDATE marketing.marken_auftraege SET vorschlag = va WHERE id = auf;
  INSERT INTO _p078 VALUES ('auftrag_a', auf), ('a', va);

  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, status, erstellt_am)
  VALUES ('probe_078', 'chat', 'Woerter direkt', 'fertig', now() - interval '2 hours') RETURNING id INTO auf;
  INSERT INTO marketing.marken_vorschlaege (mandant, auftrag, vorschlag, status, erstellt_am,
                                            entschieden_von, entschieden_am)
  VALUES ('probe_078', auf,
          '{"abschnitte":{"Wörter":"Wir sagen du","Ton":"Ton B"},"direkt":true,
            "aenderungen":[{"feld":"Wörter","vorher":"","nachher":"Wir sagen du"},
                           {"feld":"Ton","vorher":"Ton A","nachher":"Ton B"}]}',
          'angenommen', now() - interval '2 hours', 'Agent (direkt)', now() - interval '2 hours')
  RETURNING id INTO vb;
  UPDATE marketing.marken_auftraege SET vorschlag = vb WHERE id = auf;
  INSERT INTO _p078 VALUES ('auftrag_b', auf), ('b', vb);

  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, status, erstellt_am)
  VALUES ('probe_078', 'chat', 'Farbe?', 'fertig', now() - interval '1 hour') RETURNING id INTO auf;
  INSERT INTO marketing.marken_vorschlaege (mandant, auftrag, vorschlag, erstellt_am)
  VALUES ('probe_078', auf, '{"akzent":"#112233","direkt":false}', now() - interval '1 hour')
  RETURNING id INTO vc;
  UPDATE marketing.marken_auftraege SET vorschlag = vc WHERE id = auf;
  INSERT INTO _p078 VALUES ('c', vc);

  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, kontext)
  VALUES ('probe_078', 'wissen', 'Wissen', '{"seit": 0}');
END $$;

DO $$ DECLARE va uuid := (SELECT id FROM _p078 WHERE k = 'a'); vb uuid := (SELECT id FROM _p078 WHERE k = 'b');
  vc uuid := (SELECT id FROM _p078 WHERE k = 'c'); auf_b uuid := (SELECT id FROM _p078 WHERE k = 'auftrag_b');
  msg text; uid uuid; u marketing.marken_auftraege; n marketing.marken_vorschlaege;
BEGIN
  -- Rueckgaengig an A: B ist neuer -> abgewiesen, nichts angelegt
  msg := NULL;
  BEGIN PERFORM marketing.pult_marke_rueckgaengig(va, 'Felix', 'probe_078');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS NULL OR position('neuere Änderung' IN msg) = 0 THEN
    RAISE EXCEPTION 'verify_078: Rueckgaengig an A muss "neuere Änderung" werfen, war: %', msg; END IF;
  IF (SELECT count(*) FROM marketing.marken_vorschlaege WHERE mandant = 'probe_078') <> 3 THEN
    RAISE EXCEPTION 'verify_078: abgewiesenes Rueckgaengig hat etwas angelegt'; END IF;

  -- falsche Firma (veralteter Tab)
  msg := NULL;
  BEGIN PERFORM marketing.pult_marke_rueckgaengig(vb, 'Felix', 'probe_078_e');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS NULL OR position('anderen Firma' IN msg) = 0 THEN
    RAISE EXCEPTION 'verify_078: fremde Firma muss werfen, war: %', msg; END IF;

  -- ohne Namen
  msg := NULL;
  BEGIN PERFORM marketing.pult_marke_rueckgaengig(vb, '  ', 'probe_078');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS NULL THEN RAISE EXCEPTION 'verify_078: Rueckgaengig ohne Namen muss werfen'; END IF;

  -- Rueckgaengig an B: neuer angenommener Vorschlag mit vertauschten Aenderungen + Uebernahme-Auftrag
  uid := marketing.pult_marke_rueckgaengig(vb, ' Felix ', 'probe_078');
  SELECT * INTO u FROM marketing.marken_auftraege WHERE id = uid;
  IF NOT FOUND OR u.art <> 'uebernehmen' OR u.status <> 'offen' OR u.mandant <> 'probe_078'
     OR u.vorschlag IS NULL THEN
    RAISE EXCEPTION 'verify_078: Uebernahme-Auftrag fehlt oder falsch: %', row_to_json(u); END IF;
  SELECT * INTO n FROM marketing.marken_vorschlaege WHERE id = u.vorschlag;
  IF n.status <> 'angenommen' OR n.entschieden_von <> 'Felix' OR n.entschieden_am IS NULL THEN
    RAISE EXCEPTION 'verify_078: neuer Vorschlag nicht angenommen: %', row_to_json(n); END IF;
  IF n.auftrag <> auf_b THEN
    RAISE EXCEPTION 'verify_078: neuer Vorschlag muss den Auftrag von B tragen'; END IF;
  IF n.id = vb THEN RAISE EXCEPTION 'verify_078: Rueckgaengig muss einen NEUEN Vorschlag anlegen'; END IF;
  IF n.vorschlag IS DISTINCT FROM jsonb_build_object(
       'abschnitte', '{"Wörter":"","Ton":"Ton A"}'::jsonb,
       'aenderungen', '[{"feld":"Wörter","vorher":"Wir sagen du","nachher":""},
                        {"feld":"Ton","vorher":"Ton B","nachher":"Ton A"}]'::jsonb,
       'direkt', true, 'rueckgaengig_von', vb) THEN
    RAISE EXCEPTION 'verify_078: Inhalt des Rueckgaengig-Vorschlags falsch: %', n.vorschlag; END IF;
  -- B selbst bleibt angenommen; der offene, veraltete Vorschlag C ist ersetzt
  IF (SELECT status FROM marketing.marken_vorschlaege WHERE id = vb) <> 'angenommen' THEN
    RAISE EXCEPTION 'verify_078: B muss angenommen bleiben'; END IF;
  IF (SELECT status FROM marketing.marken_vorschlaege WHERE id = vc) <> 'ersetzt' THEN
    RAISE EXCEPTION 'verify_078: offener Vorschlag C muss ersetzt sein'; END IF;

  -- jetzt ist der Rueckgaengig-Vorschlag der juengste: B ist nicht mehr rueckgaengig zu machen
  msg := NULL;
  BEGIN PERFORM marketing.pult_marke_rueckgaengig(vb, 'Felix', 'probe_078');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS NULL OR position('neuere Änderung' IN msg) = 0 THEN
    RAISE EXCEPTION 'verify_078: zweites Rueckgaengig an B muss "neuere Änderung" werfen, war: %', msg; END IF;

  -- schlaegt die Uebernahme fehl, wird der Rueckgaengig-Vorschlag wieder offen (064-Regel) - ohne Konflikt
  -- mit dem Index "ein offener Vorschlag je Firma", weil C ersetzt ist
  UPDATE marketing.marken_auftraege SET status = 'in_arbeit', vergeben_bis = now() + interval '5 minutes'
   WHERE id = uid;
  PERFORM marketing.pult_marke_zurueck(uid, 'PC weg');
  IF (SELECT status FROM marketing.marken_vorschlaege WHERE id = n.id) <> 'offen' THEN
    RAISE EXCEPTION 'verify_078: nach Fehlschlag muss der Rueckgaengig-Vorschlag offen sein'; END IF;
END $$;

-- (d) Rueckgaengig an einem nicht direkten Vorschlag (direkt false, direkt fehlt, direkt als Text) wirft
DO $$ DECLARE auf uuid; v uuid; msg text; inhalt jsonb; BEGIN
  FOREACH inhalt IN ARRAY ARRAY[
      '{"abschnitte":{"Ton":"x"},"direkt":false,"aenderungen":[{"feld":"Ton","vorher":"a","nachher":"x"}]}',
      '{"abschnitte":{"Ton":"x"},"aenderungen":[{"feld":"Ton","vorher":"a","nachher":"x"}]}',
      '{"abschnitte":{"Ton":"x"},"direkt":"true","aenderungen":[{"feld":"Ton","vorher":"a","nachher":"x"}]}'
    ]::jsonb[] LOOP
    INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, status)
    VALUES ('probe_078_d', 'chat', 'd', 'fertig') RETURNING id INTO auf;
    INSERT INTO marketing.marken_vorschlaege (mandant, auftrag, vorschlag, status, entschieden_von, entschieden_am)
    VALUES ('probe_078_d', auf, inhalt, 'angenommen', 'Felix', now()) RETURNING id INTO v;
    msg := NULL;
    BEGIN PERFORM marketing.pult_marke_rueckgaengig(v, 'Felix', 'probe_078_d');
    EXCEPTION WHEN others THEN msg := SQLERRM; END;
    IF msg IS NULL OR position('direkt' IN msg) = 0 THEN
      RAISE EXCEPTION 'verify_078: nicht direkter Vorschlag % muss werfen, war: %', inhalt, msg; END IF;
    DELETE FROM marketing.marken_vorschlaege WHERE id = v;
  END LOOP;
  -- direkt, aber nicht angenommen (offen) wirft ebenso
  INSERT INTO marketing.marken_vorschlaege (mandant, auftrag, vorschlag)
  VALUES ('probe_078_d', auf,
          '{"abschnitte":{"Ton":"x"},"direkt":true,"aenderungen":[{"feld":"Ton","vorher":"a","nachher":"x"}]}')
  RETURNING id INTO v;
  msg := NULL;
  BEGIN PERFORM marketing.pult_marke_rueckgaengig(v, 'Felix', 'probe_078_d');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS NULL THEN RAISE EXCEPTION 'verify_078: offener Vorschlag darf nicht rueckgaengig gehen'; END IF;
  -- direkt + angenommen, aber ohne gueltige Aenderungsliste wirft
  UPDATE marketing.marken_vorschlaege
     SET status = 'angenommen', entschieden_von = 'Felix', entschieden_am = now(),
         vorschlag = '{"abschnitte":{"Ton":"x"},"direkt":true,"aenderungen":[{"feld":"Ton","vorher":1}]}'
   WHERE id = v;
  msg := NULL;
  BEGIN PERFORM marketing.pult_marke_rueckgaengig(v, 'Felix', 'probe_078_d');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS NULL OR position('Änderungsliste' IN msg) = 0 THEN
    RAISE EXCEPTION 'verify_078: kaputte Aenderungsliste muss werfen, war: %', msg; END IF;
  -- unbekannter Vorschlag
  msg := NULL;
  BEGIN PERFORM marketing.pult_marke_rueckgaengig(gen_random_uuid(), 'Felix', 'probe_078_d');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS NULL THEN RAISE EXCEPTION 'verify_078: unbekannter Vorschlag muss werfen'; END IF;
END $$;

-- (e) Rueckgaengig, waehrend ein Chat-Auftrag offen ist -> 'Der Assistent arbeitet gerade', nichts angelegt
DO $$ DECLARE auf uuid; v uuid; msg text; BEGIN
  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, status, erstellt_am)
  VALUES ('probe_078_e', 'chat', 'e', 'fertig', now() - interval '1 hour') RETURNING id INTO auf;
  INSERT INTO marketing.marken_vorschlaege (mandant, auftrag, vorschlag, status, erstellt_am,
                                            entschieden_von, entschieden_am)
  VALUES ('probe_078_e', auf,
          '{"abschnitte":{"Ton":"x"},"direkt":true,"aenderungen":[{"feld":"Ton","vorher":"a","nachher":"x"}]}',
          'angenommen', now() - interval '1 hour', 'Agent (direkt)', now() - interval '1 hour')
  RETURNING id INTO v;
  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht) VALUES ('probe_078_e', 'chat', 'laeuft');
  msg := NULL;
  BEGIN PERFORM marketing.pult_marke_rueckgaengig(v, 'Felix', 'probe_078_e');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS DISTINCT FROM 'Der Assistent arbeitet gerade' THEN
    RAISE EXCEPTION 'verify_078: offener Chat muss "Der Assistent arbeitet gerade" werfen, war: %', msg; END IF;
  IF (SELECT count(*) FROM marketing.marken_vorschlaege WHERE mandant = 'probe_078_e') <> 1 THEN
    RAISE EXCEPTION 'verify_078: abgewiesenes Rueckgaengig hat etwas angelegt'; END IF;
END $$;

-- (f) Fix-Runde 1: angenommener direkter Vorschlag, dessen Uebernahme-Auftrag tot ist (Sperre abgelaufen,
-- versuche >= 2). Das Aufraeumen setzt ihn auf fehler und den Vorschlag wieder offen - Rueckgaengig muss
-- deshalb werfen, und das Original darf nie 'ersetzt' werden.
DO $$ DECLARE auf uuid; v uuid; msg text; BEGIN
  INSERT INTO marketing.marken_auftraege (mandant, art, nachricht, status, erstellt_am)
  VALUES ('probe_078_f', 'chat', 'f', 'fertig', now() - interval '1 hour') RETURNING id INTO auf;
  INSERT INTO marketing.marken_vorschlaege (mandant, auftrag, vorschlag, status, erstellt_am,
                                            entschieden_von, entschieden_am)
  VALUES ('probe_078_f', auf,
          '{"abschnitte":{"Ton":"x"},"direkt":true,"aenderungen":[{"feld":"Ton","vorher":"a","nachher":"x"}]}',
          'angenommen', now() - interval '1 hour', 'Agent (direkt)', now() - interval '1 hour')
  RETURNING id INTO v;
  INSERT INTO marketing.marken_auftraege (mandant, art, vorschlag, status, versuche, vergeben_bis, erstellt_am)
  VALUES ('probe_078_f', 'uebernehmen', v, 'in_arbeit', 2, now() - interval '1 minute', now() - interval '50 minutes');
  msg := NULL;
  BEGIN PERFORM marketing.pult_marke_rueckgaengig(v, 'Felix', 'probe_078_f');
  EXCEPTION WHEN others THEN msg := SQLERRM; END;
  IF msg IS NULL THEN
    RAISE EXCEPTION 'verify_078: Rueckgaengig mit totem Uebernahme-Auftrag muss werfen'; END IF;
  IF (SELECT status FROM marketing.marken_vorschlaege WHERE id = v) = 'ersetzt' THEN
    RAISE EXCEPTION 'verify_078: Original darf nie ersetzt werden'; END IF;
  IF (SELECT count(*) FROM marketing.marken_vorschlaege WHERE mandant = 'probe_078_f') <> 1 THEN
    RAISE EXCEPTION 'verify_078: abgewiesenes Rueckgaengig hat etwas angelegt'; END IF;
END $$;

DO $$ BEGIN RAISE NOTICE 'verify_078: ok'; END $$;
SELECT 'verify_078: ok' AS ergebnis;

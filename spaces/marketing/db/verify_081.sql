-- Nachweise fuer 081, laeuft nur ueber migration_probe (eine Transaktion + ROLLBACK).
-- Eigene Probe-Inhalte (wie verify_077/080). In der Probe ist now() konstant.
-- Darf mehrfach in derselben Probe laufen (Idempotenz: 081, verify_081, 081, verify_081): jeder Lauf legt neue
-- Probe-Inhalte an (der Titel wird bei Bedarf durchnummeriert, 079).

-- 0) Struktur und Rechte
DO $$ DECLARE v_def text; BEGIN
  ASSERT to_regclass('marketing.seite_mailfassungen') IS NOT NULL, '0: Tabelle seite_mailfassungen';
  ASSERT (SELECT array_agg(column_name::text ORDER BY ordinal_position) FROM information_schema.columns
           WHERE table_schema = 'marketing' AND table_name = 'seite_mailfassungen')
         = ARRAY['inhalt', 'fassung', 'html_versand', 'html_vorschau_mjml', 'text', 'medien', 'bytes', 'erstellt_am'],
         '0: Spalten';
  SELECT pg_get_constraintdef(oid) INTO v_def FROM pg_constraint
   WHERE conrelid = 'marketing.seite_mailfassungen'::regclass AND contype = 'p';
  ASSERT v_def = 'PRIMARY KEY (inhalt, fassung)', format('0: PK: %s', v_def);
  ASSERT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'marketing.seite_mailfassungen'::regclass
                  AND contype = 'f' AND confrelid = 'marketing.inhalt_fassungen'::regclass AND confdeltype = 'c'),
         '0: FK auf inhalt_fassungen mit ON DELETE CASCADE';
  ASSERT to_regprocedure('marketing.pult_seite_mail_speichern(uuid, int, text, text, text, jsonb, int)') IS NOT NULL,
         '0: Signatur pult_seite_mail_speichern';
  ASSERT to_regprocedure('marketing.pult_seite_mail_lesen(uuid, int)') IS NOT NULL, '0: Signatur pult_seite_mail_lesen';
  ASSERT to_regprocedure('marketing.pult_seite_einreichen(uuid, int, text)') IS NOT NULL,
         '0: Signatur pult_seite_einreichen';
  ASSERT (SELECT count(*) FROM pg_proc WHERE pronamespace = 'marketing'::regnamespace
           AND proname IN ('pult_seite_mail_speichern', 'pult_seite_mail_lesen', 'pult_seite_einreichen')) = 3,
         '0: keine Ueberladungen';
  ASSERT to_regprocedure('marketing.pult_einreichen(uuid, text)') IS NOT NULL, '0: pult_einreichen bleibt';
  ASSERT NOT has_function_privilege('public',
           'marketing.pult_seite_mail_speichern(uuid,int,text,text,text,jsonb,int)', 'EXECUTE')
     AND NOT has_function_privilege('public', 'marketing.pult_seite_mail_lesen(uuid,int)', 'EXECUTE')
     AND NOT has_function_privilege('public', 'marketing.pult_seite_einreichen(uuid,int,text)', 'EXECUTE'),
         '0: PUBLIC darf nichts';
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sales_app') THEN
    ASSERT NOT has_function_privilege('sales_app',
             'marketing.pult_seite_mail_speichern(uuid,int,text,text,text,jsonb,int)', 'EXECUTE')
       AND NOT has_function_privilege('sales_app', 'marketing.pult_seite_einreichen(uuid,int,text)', 'EXECUTE'),
           '0: sales_app darf nicht';
  END IF;
  ASSERT has_function_privilege(current_user,
           'marketing.pult_seite_mail_speichern(uuid,int,text,text,text,jsonb,int)', 'EXECUTE')
     AND has_function_privilege(current_user, 'marketing.pult_seite_mail_lesen(uuid,int)', 'EXECUTE')
     AND has_function_privilege(current_user, 'marketing.pult_seite_einreichen(uuid,int,text)', 'EXECUTE'),
         '0: die API-Rolle (Probe-Rolle) darf';
END $$;

-- 1) Speichern einmal je (Inhalt, Fassung), Lesen, Ablehnungen; 2) Einreichen; 3) Loeschen raeumt mit ab
DO $$ DECLARE s jsonb; d jsonb; v_c uuid; v_b uuid; v_l uuid; j jsonb; m jsonb; v_fehler text; v_n int; BEGIN
  s := '{"version":1,"breite":600,"hoehe":1200,"hintergrund":{"farbe":"#ffffff","bild":null},
         "schriften":{"anzeige":"dm-sans","text":"dm-sans"},
         "ebenen":[{"id":"b","art":"bild","x":300,"y":200,"breite":400,"hoehe":300,"quelle":"medien:probe.png"},
                   {"id":"t","art":"text","x":300,"y":600,"breite":480,"hoehe":120,"text":"Hallo {vorname}",
                    "rolle":"TEXT","groesse":17,"farbe":"#333333"}]}'::jsonb;
  v_c := marketing.pult_collage_anlegen('vibemind', 'Probe 081', 'Betreff 081', s, 'probe');
  m := jsonb_build_array(jsonb_build_object('name', 'ms-' || left(v_c::text, 8) || '-1-01.jpg',
                                            'cid', 'ms-' || left(v_c::text, 8) || '-1-01.jpg', 'sw', false));

  -- 1a) ohne Mail-Fassung: lesen NULL
  ASSERT marketing.pult_seite_mail_lesen(v_c, 1) IS NULL, '1a: lesen ohne Zeile ist NULL';
  -- 1b) speichern liefert die Zeile
  j := marketing.pult_seite_mail_speichern(v_c, 1, '<html>cid</html>', '<mjml>v</mjml>', 'Text', m, 1234);
  ASSERT j->>'html_versand' = '<html>cid</html>' AND j->>'html_vorschau_mjml' = '<mjml>v</mjml>'
     AND j->>'text' = 'Text' AND j->'medien' = m AND (j->>'bytes')::int = 1234 AND (j->>'fassung')::int = 1
     AND (j->>'inhalt')::uuid = v_c, format('1b: Rueckgabe %s', j);
  -- 1c) ein zweites Speichern aendert nichts und liefert die erste Zeile (ON CONFLICT DO NOTHING)
  j := marketing.pult_seite_mail_speichern(v_c, 1, '<html>zwei</html>', '<mjml>zwei</mjml>', 'Zwei', '[]', 0);
  ASSERT j->>'html_versand' = '<html>cid</html>' AND j->'medien' = m, format('1c: erste Zeile bleibt: %s', j);
  ASSERT (SELECT count(*) FROM marketing.seite_mailfassungen WHERE inhalt = v_c) = 1, '1c: genau eine Zeile';
  ASSERT marketing.pult_seite_mail_lesen(v_c, 1) = j, '1c: lesen = gespeichert';

  -- 1d) Ablehnungen: unbekannte Fassung, Block-Newsletter, zu gross, fremde Mediennamen, leeres HTML
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_seite_mail_speichern(v_c, 9, 'h', 'm', 't', '[]', 0);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Unbekannte Fassung', format('1d: unbekannte Fassung: %s', v_fehler);
  d := '{"root":{"type":"EmailLayout","data":{"childrenIds":["t"]}},
         "t":{"type":"Text","data":{"style":{},"props":{"text":"eins","markdown":false}}}}'::jsonb;
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 081 Bloecke')
  RETURNING id INTO v_b;
  PERFORM marketing.pult_bloecke_speichern(v_b, 0, 'Probe 081', '', d, 'betreiber', false);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_seite_mail_speichern(v_b, 1, 'h', 'm', 't', '[]', 0);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Das ist kein Collage-Newsletter', format('1d: Bloecke: %s', v_fehler);
  PERFORM marketing.pult_seite_speichern(v_c, 1, 'Betreff 081', '', s, 'betreiber', false);   -- Fassung 2
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_seite_mail_speichern(v_c, 2, 'h', 'm', 't', '[]', 8388609);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler LIKE 'Die Mail wäre zu groß%', format('1d: zu gross: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_seite_mail_speichern(v_c, 2, 'h', 'm', 't', '[{"name":"gs-0123456789ab.jpg"}]', 1);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'medien: nur Bildstücke ms-…jpg', format('1d: fremder Name: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_seite_mail_speichern(v_c, 2, '', 'm', 't', '[]', 0);
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Die Mail-Fassung ist leer', format('1d: leer: %s', v_fehler);
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.seite_mailfassungen WHERE inhalt = v_c AND fassung = 2),
         '1d: nichts gespeichert';

  -- 2a) Einreichen: veraltete Fassung (1 statt 2)
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_seite_einreichen(v_c, 1, 'probe');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Inzwischen gibt es Fassung 2 – bitte neu laden', format('2a: veraltet: %s', v_fehler);
  -- 2b) ohne Mail-Fassung der neuesten
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_seite_einreichen(v_c, 2, 'probe');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Die Mail-Fassung fehlt – bitte erneut einreichen', format('2b: ohne Mail: %s', v_fehler);
  ASSERT (SELECT status FROM marketing.inhalte WHERE id = v_c) = 'entwurf', '2b: bleibt Entwurf';
  -- 2c) Block-Newsletter und unbekannter Inhalt
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_seite_einreichen(v_b, 1, 'probe');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Das ist kein Collage-Newsletter', format('2c: Bloecke: %s', v_fehler);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_seite_einreichen(gen_random_uuid(), 1, 'probe');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Unbekannter Inhalt', format('2c: unbekannt: %s', v_fehler);
  -- 2d) mit Mail-Fassung: pult_einreichen laeuft (seine Regeln gelten weiter, z. B. der Name)
  m := jsonb_build_array(jsonb_build_object('name', 'ms-' || left(v_c::text, 8) || '-2-01-2.jpg',
                                            'cid', 'ms-' || left(v_c::text, 8) || '-2-01-2.jpg', 'sw', false));
  PERFORM marketing.pult_seite_mail_speichern(v_c, 2, '<html/>', '<mjml/>', 't', m, 10);
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_seite_einreichen(v_c, 2, '  ');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Ohne Namen kein Einreichen', format('2d: Name: %s', v_fehler);
  v_n := marketing.pult_seite_einreichen(v_c, 2, 'probe');
  ASSERT v_n = 2, format('2d: eingereicht %s', v_n);
  ASSERT (SELECT status = 'eingereicht' AND eingereichte_fassung = 2 FROM marketing.inhalte WHERE id = v_c),
         '2d: Status eingereicht, Fassung 2';
  v_fehler := NULL;
  BEGIN PERFORM marketing.pult_seite_einreichen(v_c, 2, 'probe');
  EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler = 'Liegt schon zur Freigabe', format('2d: doppelt: %s', v_fehler);
  -- 2e) die Freigabe laeuft unveraendert ueber pult_entscheiden; die Mail-Fassung bleibt lesbar
  PERFORM marketing.pult_entscheiden(v_c, 2, 'freigeben', 'probe', NULL);
  ASSERT (SELECT status FROM marketing.inhalte WHERE id = v_c) = 'freigegeben', '2e: freigegeben';
  ASSERT marketing.pult_seite_mail_lesen(v_c, 2)->'medien' = m, '2e: Mail-Fassung der Freigabe';

  -- 3) Ein Entwurf mit Mail-Fassung laesst sich loeschen (CASCADE), die Mail-Fassung geht mit
  v_l := marketing.pult_collage_anlegen('vibemind', 'Probe 081 Loeschen', 'Betreff', s, 'probe');
  PERFORM marketing.pult_seite_mail_speichern(v_l, 1, '<html/>', '<mjml/>', 't', '[]', 0);
  PERFORM marketing.pult_inhalt_loeschen(v_l, 'vibemind', 'probe');
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.seite_mailfassungen WHERE inhalt = v_l), '3: Mail-Fassung geloescht';
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.inhalte WHERE id = v_l), '3: Inhalt geloescht';
END $$;

SELECT 'verify_081 ok' AS ergebnis;

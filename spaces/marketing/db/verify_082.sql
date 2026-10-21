-- Nachweise fuer 082, laeuft nur ueber migration_probe (eine Transaktion + ROLLBACK).
-- Jeder Abschnitt mit Probe-Daten endet mit der Ausnahme P0V82, die sein eigener Handler faengt: so rollt der
-- Abschnitt seine Probe-Vorlagen und -Inhalte selbst zurueck (Vorlagen-Fassungen lassen sich nicht loeschen), und
-- spaetere Verify-Dateien in derselben Probe (058/068/077/081) sehen den Bestand unveraendert. ASSERT-Fehler
-- (P0004) und unerwartete Fehler fallen durch. Darf mehrfach in derselben Probe laufen (082, verify, 082, verify).

CREATE OR REPLACE FUNCTION pg_temp.v082_seite(p_titel text DEFAULT 'Probe') RETURNS jsonb
LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_build_object(
    'version', 1, 'breite', 600, 'hoehe', 1200, 'hintergrund', jsonb_build_object('farbe', '#ffffff'),
    'schriften', jsonb_build_object('anzeige', 'dm-sans', 'text', 'poppins'),
    'ebenen', jsonb_build_array(
      jsonb_build_object('id','logo','art','bild','x',300,'y',100,'breite',200,'hoehe',80,'quelle','medien:probe.png'),
      jsonb_build_object('id','titel','art','text','x',300,'y',400,'breite',480,'hoehe',120,'text',p_titel,
                         'rolle','ANZEIGE','groesse',40,'farbe','#222222'),
      jsonb_build_object('id','k','art','knopf','x',300,'y',1000,'breite',220,'hoehe',48,'text','Mehr',
                         'link','https://example.com','farbe','#5b3fd6','textfarbe','#ffffff')));
$$;

-- 0) Struktur und Rechte
DO $$ DECLARE v_def text; t text; f text; BEGIN
  FOREACH t IN ARRAY ARRAY['newsletter_vorlagen', 'newsletter_vorlagen_fassungen'] LOOP
    ASSERT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'marketing' AND table_name = t
                    AND column_name = 'format' AND data_type = 'text' AND is_nullable = 'NO'
                    AND column_default LIKE '''bloecke''%'), format('0: %s.format text NOT NULL DEFAULT bloecke', t);
    ASSERT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'marketing' AND table_name = t
                    AND column_name = 'seite' AND data_type = 'jsonb'), format('0: %s.seite jsonb', t);
    ASSERT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'marketing' AND table_name = t
                    AND column_name = 'rollen' AND data_type = 'jsonb'), format('0: %s.rollen jsonb', t);
    ASSERT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'marketing' AND table_name = t
                    AND column_name = 'bloecke' AND is_nullable = 'YES'), format('0: %s.bloecke NULL-faehig', t);
    SELECT pg_get_constraintdef(oid) INTO v_def FROM pg_constraint
     WHERE conrelid = ('marketing.' || t)::regclass AND conname = t || '_bloecke_gueltig';
    ASSERT v_def LIKE '%format <> ''bloecke''%' AND v_def LIKE '%marketing.pult_bloecke_fehler(bloecke)%',
           format('0: %s Block-CHECK nur bei bloecke: %s', t, v_def);
    SELECT pg_get_constraintdef(oid) INTO v_def FROM pg_constraint
     WHERE conrelid = ('marketing.' || t)::regclass AND conname = t || '_seite_gueltig';
    ASSERT v_def LIKE '%format <> ''seite''%' AND v_def LIKE '%marketing.pult_seite_fehler(seite)%',
           format('0: %s Seiten-CHECK: %s', t, v_def);
    SELECT pg_get_constraintdef(oid) INTO v_def FROM pg_constraint
     WHERE conrelid = ('marketing.' || t)::regclass AND conname = t || '_format_check';
    ASSERT v_def LIKE '%seite%' AND v_def LIKE '%rollen%', format('0: %s Format-Regel: %s', t, v_def);
  END LOOP;
  -- Freigabe sichtbar: wer und wann
  ASSERT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'marketing'
                  AND table_name = 'newsletter_vorlagen' AND column_name = 'entschieden_von' AND data_type = 'text'),
         '0: newsletter_vorlagen.entschieden_von text';
  ASSERT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'marketing'
                  AND table_name = 'newsletter_vorlagen' AND column_name = 'entschieden_am'
                  AND data_type = 'timestamp with time zone'), '0: newsletter_vorlagen.entschieden_am timestamptz';
  -- Fassungen bleiben unveraenderlich (075 hat nur inhalt_fassungen umgestellt)
  ASSERT EXISTS (SELECT 1 FROM pg_trigger tg JOIN pg_proc p ON p.oid = tg.tgfoid
                  WHERE tg.tgrelid = 'marketing.newsletter_vorlagen_fassungen'::regclass
                    AND tg.tgname = 'trg_vorlage_fassung_unveraenderlich' AND p.proname = '_fassung_unveraenderlich'
                    AND tg.tgenabled = 'O'), '0: Vorlagen-Fassungen unveraenderlich';
  ASSERT (SELECT provolatile FROM pg_proc WHERE oid = 'marketing.pult_hat_collage_vorlage(text)'::regprocedure) = 's',
         '0: pult_hat_collage_vorlage STABLE';
  FOREACH f IN ARRAY ARRAY['marketing.pult_vorlage_seite_speichern(text,text,jsonb,jsonb,text,text,boolean)',
                           'marketing.pult_vorlage_entscheiden(text,text,text)',
                           'marketing.pult_collage_aus_vorlage(text,text,text,jsonb,text)',
                           'marketing.pult_hat_collage_vorlage(text)'] LOOP
    ASSERT to_regprocedure(f) IS NOT NULL, format('0: Signatur %s', f);
    ASSERT NOT has_function_privilege('public', f, 'EXECUTE'), format('0: PUBLIC darf nicht %s', f);
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sales_app') THEN
      ASSERT NOT has_function_privilege('sales_app', f, 'EXECUTE'), format('0: sales_app darf nicht %s', f);
    END IF;
    ASSERT has_function_privilege(current_user, f, 'EXECUTE'), format('0: API-Rolle darf %s', f);
  END LOOP;
  ASSERT (SELECT count(*) FROM pg_proc WHERE pronamespace = 'marketing'::regnamespace
           AND proname IN ('pult_vorlage_seite_speichern', 'pult_vorlage_entscheiden', 'pult_collage_aus_vorlage',
                           'pult_hat_collage_vorlage')) = 4, '0: keine Ueberladungen';
  ASSERT (SELECT count(*) FROM pg_proc WHERE pronamespace = 'marketing'::regnamespace
           AND proname = 'pult_inhalt_aus_vorlage') = 2, '0: pult_inhalt_aus_vorlage weiter mit 3 und 5 Argumenten';
END $$;

-- 1) Bestehende Zeilen bleiben gueltig (laeuft vor allen Probe-Daten)
DO $$ BEGIN
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.newsletter_vorlagen
                      WHERE format = 'bloecke' AND (seite IS NOT NULL OR rollen IS NOT NULL OR bloecke IS NULL
                                                    OR marketing.pult_bloecke_fehler(bloecke) IS NOT NULL)),
         '1: alle Block-Vorlagen gueltig';
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.newsletter_vorlagen_fassungen
                      WHERE format = 'bloecke' AND (seite IS NOT NULL OR rollen IS NOT NULL OR bloecke IS NULL
                                                    OR marketing.pult_bloecke_fehler(bloecke) IS NOT NULL)),
         '1: alle Block-Vorlagen-Fassungen gueltig';
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.newsletter_vorlagen
                      WHERE format = 'seite' AND marketing.pult_seite_fehler(seite) IS NOT NULL),
         '1: alle Seiten-Vorlagen gueltig';
  -- Stand von vorher: jede Vorlage ohne Seite ist eine Block-Vorlage (die Vorgabe hat den Bestand erfasst)
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.newsletter_vorlagen WHERE seite IS NULL AND format <> 'bloecke'),
         '1: Bestand ist bloecke';
  ASSERT EXISTS (SELECT 1 FROM marketing.newsletter_vorlagen WHERE name = 'leer' AND format = 'bloecke'),
         '1: Vorlage leer ist Block-Vorlage';
END $$;

-- 2) Seiten-Vorlage speichern und Fassung zaehlen
DO $$ DECLARE v_n int; v jsonb; v_fehler text; r record; BEGIN
  BEGIN
    v_n := marketing.pult_vorlage_seite_speichern('probe-082-a', 'Probe A', pg_temp.v082_seite(),
             '{"farben":{"#5b3fd6":"akzent"},"ebenen":{"logo":"logo"}}', 'probe', 'vorschlag', true);
    ASSERT v_n = 1, format('2: erste Fassung = 1, ist %s', v_n);
    SELECT * INTO r FROM marketing.newsletter_vorlagen WHERE name = 'probe-082-a';
    ASSERT r.format = 'seite' AND r.bloecke IS NULL AND r.seite = pg_temp.v082_seite() AND r.status = 'vorschlag'
       AND r.fuer_alle AND r.fassung = 1 AND r.beschreibung = 'Probe A' AND r.erstellt_von = 'probe'
       AND r.mandant = 'vibemind' AND r.rollen->'ebenen'->>'logo' = 'logo', '2: Kopfzeile';
    ASSERT (SELECT count(*) FROM marketing.newsletter_vorlagen_fassungen
             WHERE vorlage = 'probe-082-a' AND fassung = 1 AND format = 'seite' AND bloecke IS NULL
               AND seite = pg_temp.v082_seite() AND rollen ? 'farben' AND erstellt_von = 'probe') = 1, '2: Fassung 1';
    -- zweite Fassung: Rollen NULL -> {}, fuer_alle NULL laesst stehen, Beschreibung NULL laesst stehen, der
    -- Status einer bestehenden Vorlage bleibt (nur neue bekommen p_status)
    v_n := marketing.pult_vorlage_seite_speichern('probe-082-a', NULL, pg_temp.v082_seite('Zwei'), NULL, 'probe2',
                                                  'freigegeben', NULL);
    ASSERT v_n = 2, format('2: zweite Fassung = 2, ist %s', v_n);
    SELECT * INTO r FROM marketing.newsletter_vorlagen WHERE name = 'probe-082-a';
    ASSERT r.fassung = 2 AND r.fuer_alle AND r.beschreibung = 'Probe A' AND r.rollen = '{}'::jsonb
       AND r.status = 'vorschlag' AND r.seite = pg_temp.v082_seite('Zwei') AND r.erstellt_von = 'probe',
           '2: Kopf nach Fassung 2';
    -- eine Entscheidung uebersteht den naechsten Generator-Lauf (Status und wer/wann bleiben)
    PERFORM marketing.pult_vorlage_entscheiden('probe-082-a', 'zurueckziehen', 'Felix');
    PERFORM marketing.pult_vorlage_seite_speichern('probe-082-a', NULL, pg_temp.v082_seite('Drei'), NULL, 'probe',
                                                   'vorschlag', NULL);
    SELECT * INTO r FROM marketing.newsletter_vorlagen WHERE name = 'probe-082-a';
    ASSERT r.status = 'zurueckgezogen' AND r.entschieden_von = 'Felix' AND r.entschieden_am IS NOT NULL
       AND r.fassung = 3 AND r.seite = pg_temp.v082_seite('Drei'), '2: Entscheidung bleibt beim erneuten Speichern';
    -- ungueltiger Status wird auch fuer bestehende Vorlagen abgelehnt
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_vorlage_seite_speichern('probe-082-a', NULL, pg_temp.v082_seite(), NULL, 'probe',
                                                         'entwurf', NULL);
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler = 'Status muss vorschlag, freigegeben oder zurueckgezogen sein',
           format('2: Status geprueft: %s', v_fehler);
    ASSERT (SELECT count(*) FROM marketing.newsletter_vorlagen_fassungen WHERE vorlage = 'probe-082-a') = 3,
           '2: drei Fassungen';
    ASSERT (SELECT seite FROM marketing.newsletter_vorlagen_fassungen WHERE vorlage = 'probe-082-a' AND fassung = 1)
           = pg_temp.v082_seite(), '2: Fassung 1 unveraendert';
    -- fuer_alle false setzt zurueck
    PERFORM marketing.pult_vorlage_seite_speichern('probe-082-a', NULL, pg_temp.v082_seite(), NULL, 'probe',
                                                   'vorschlag', false);
    ASSERT NOT (SELECT fuer_alle FROM marketing.newsletter_vorlagen WHERE name = 'probe-082-a'), '2: fuer_alle false';
    -- neue Vorlage ohne fuer_alle: false
    PERFORM marketing.pult_vorlage_seite_speichern('probe-082-b', '', pg_temp.v082_seite(), NULL, 'probe',
                                                   'vorschlag', NULL);
    ASSERT NOT (SELECT fuer_alle FROM marketing.newsletter_vorlagen WHERE name = 'probe-082-b'), '2: Vorgabe false';
    -- Fassungen bleiben unveraenderlich
    v_fehler := NULL;
    BEGIN UPDATE marketing.newsletter_vorlagen_fassungen SET rollen = '{"x":1}' WHERE vorlage = 'probe-082-a';
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler LIKE 'Fassungen sind unveraenderlich%', format('2: Fassung unveraenderlich: %s', v_fehler);
    -- eine Block-Vorlage wird nicht zur Seite
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_vorlage_seite_speichern('leer', '', pg_temp.v082_seite(), NULL, 'probe',
                                                         'vorschlag', NULL);
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler = 'Vorlage leer ist eine Block-Vorlage – bitte einen anderen Namen wählen',
           format('2: Block-Vorlage bleibt: %s', v_fehler);
    ASSERT (SELECT format = 'bloecke' AND seite IS NULL FROM marketing.newsletter_vorlagen WHERE name = 'leer'),
           '2: leer unveraendert';
    -- und eine Seiten-Vorlage nicht zum Block (pult_vorlage_speichern scheitert fail-closed)
    SELECT bloecke INTO v FROM marketing.newsletter_vorlagen WHERE name = 'leer';
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_vorlage_speichern('probe-082-a', '', v, 'probe', 'vorschlag');
    EXCEPTION WHEN check_violation THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler LIKE '%newsletter_vorlagen_format_check%', format('2: Seite bleibt Seite: %s', v_fehler);
    ASSERT (SELECT count(*) FROM marketing.newsletter_vorlagen_fassungen WHERE vorlage = 'probe-082-a') = 4,
           '2: keine Block-Fassung unter der Seiten-Vorlage';
    RAISE EXCEPTION USING ERRCODE = 'P0V82', MESSAGE = 'verify_082 zurueck';
  EXCEPTION WHEN SQLSTATE 'P0V82' THEN NULL;
  END;
END $$;

-- 3) Ungueltige Seite abgelehnt
DO $$ DECLARE v_fehler text; v jsonb; BEGIN
  BEGIN
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_vorlage_seite_speichern('probe-082-c', '', jsonb_set(pg_temp.v082_seite(),
                    '{breite}', '599'), NULL, 'probe', 'vorschlag', true);
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler LIKE 'Vorlage ungültig: %', format('3: Breite 599: %s', v_fehler);
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_vorlage_seite_speichern('probe-082-c', '', NULL, NULL, 'probe', 'vorschlag', true);
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler LIKE 'Vorlage ungültig: %', format('3: ohne Seite: %s', v_fehler);
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_vorlage_seite_speichern('probe-082-c', '', pg_temp.v082_seite(), '[1]', 'probe',
                                                         'vorschlag', true);
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler = 'Rollen müssen ein JSON-Objekt sein', format('3: Rollen als Liste: %s', v_fehler);
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_vorlage_seite_speichern('probe-082-c', '', pg_temp.v082_seite(), NULL, 'probe',
                                                         'entwurf', true);
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler LIKE 'Status muss%', format('3: Status: %s', v_fehler);
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_vorlage_seite_speichern('probe-082-c', '', pg_temp.v082_seite(), NULL, ' ',
                                                         'vorschlag', true);
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler = 'Ohne Urheber keine Vorlage', format('3: ohne Urheber: %s', v_fehler);
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_vorlage_seite_speichern('Probe_082', '', pg_temp.v082_seite(), NULL, 'probe',
                                                         'vorschlag', true);
    EXCEPTION WHEN check_violation THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler IS NOT NULL, '3: Name nach dem Muster der Tabelle';
    ASSERT NOT EXISTS (SELECT 1 FROM marketing.newsletter_vorlagen WHERE name IN ('probe-082-c', 'Probe_082')),
           '3: nichts angelegt';
    -- Die CHECKs halten auch direkte Schreiber
    v_fehler := NULL;
    BEGIN INSERT INTO marketing.newsletter_vorlagen (name, format, seite, erstellt_von)
          VALUES ('probe-082-d', 'seite', jsonb_set(pg_temp.v082_seite(), '{breite}', '599'), 'probe');
    EXCEPTION WHEN check_violation THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler LIKE '%newsletter_vorlagen_seite_gueltig%', format('3: CHECK Seite: %s', v_fehler);
    v_fehler := NULL;
    BEGIN INSERT INTO marketing.newsletter_vorlagen_fassungen (vorlage, fassung, format, seite, erstellt_von)
          VALUES ('leer', 999, 'seite', jsonb_set(pg_temp.v082_seite(), '{breite}', '599'), 'probe');
    EXCEPTION WHEN check_violation THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler LIKE '%newsletter_vorlagen_fassungen_seite_gueltig%', format('3: CHECK Fassung Seite: %s', v_fehler);
    SELECT bloecke INTO v FROM marketing.newsletter_vorlagen WHERE name = 'leer';
    v_fehler := NULL;
    BEGIN INSERT INTO marketing.newsletter_vorlagen (name, format, bloecke, seite, erstellt_von)
          VALUES ('probe-082-d', 'seite', v, pg_temp.v082_seite(), 'probe');
    EXCEPTION WHEN check_violation THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler LIKE '%newsletter_vorlagen_format_check%', format('3: Seite mit Bloecken: %s', v_fehler);
    v_fehler := NULL;
    BEGIN INSERT INTO marketing.newsletter_vorlagen (name, erstellt_von) VALUES ('probe-082-d', 'probe');
    EXCEPTION WHEN check_violation THEN v_fehler := SQLERRM; END;
    -- hier verletzen Format-Regel und Block-CHECK zugleich; Postgres meldet den ersten nach Namen
    ASSERT v_fehler LIKE '%newsletter_vorlagen_format_check%' OR v_fehler LIKE '%newsletter_vorlagen_bloecke_gueltig%',
           format('3: Block ohne Bloecke: %s', v_fehler);
    v_fehler := NULL;
    BEGIN INSERT INTO marketing.newsletter_vorlagen (name, bloecke, rollen, erstellt_von)
          VALUES ('probe-082-d', v, '{}', 'probe');
    EXCEPTION WHEN check_violation THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler LIKE '%newsletter_vorlagen_format_check%', format('3: Rollen nur bei Seiten: %s', v_fehler);
    v_fehler := NULL;
    BEGIN INSERT INTO marketing.newsletter_vorlagen (name, format, seite, rollen, erstellt_von)
          VALUES ('probe-082-d', 'seite', pg_temp.v082_seite(), '"x"', 'probe');
    EXCEPTION WHEN check_violation THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler LIKE '%newsletter_vorlagen_format_check%', format('3: Rollen als Text: %s', v_fehler);
    v_fehler := NULL;
    BEGIN INSERT INTO marketing.newsletter_vorlagen (name, format, bloecke, erstellt_von)
          VALUES ('probe-082-d', 'felder', v, 'probe');
    EXCEPTION WHEN check_violation THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler LIKE '%newsletter_vorlagen_format_check%', format('3: unbekanntes Format: %s', v_fehler);
    RAISE EXCEPTION USING ERRCODE = 'P0V82', MESSAGE = 'verify_082 zurueck';
  EXCEPTION WHEN SQLSTATE 'P0V82' THEN NULL;
  END;
END $$;

-- 4) Block-Vorlage unveraendert speicherbar, Block-Kette nur fuer Block-Vorlagen
DO $$ DECLARE v jsonb; v_n int; v_id uuid; v_fehler text; BEGIN
  BEGIN
    SELECT bloecke INTO v FROM marketing.newsletter_vorlagen WHERE name = 'leer';
    v_n := marketing.pult_vorlage_speichern('probe-082-block', 'Block', v, 'probe', 'vorschlag');
    ASSERT v_n = 1, '4: Block-Vorlage Fassung 1';
    v_n := marketing.pult_vorlage_speichern('probe-082-block', 'Block', v, 'probe', 'freigegeben');
    ASSERT v_n = 2, '4: Block-Vorlage Fassung 2';
    ASSERT (SELECT format = 'bloecke' AND bloecke = v AND seite IS NULL AND rollen IS NULL AND status = 'freigegeben'
              FROM marketing.newsletter_vorlagen WHERE name = 'probe-082-block'), '4: Kopf bloecke';
    ASSERT (SELECT count(*) FROM marketing.newsletter_vorlagen_fassungen WHERE vorlage = 'probe-082-block'
             AND format = 'bloecke' AND bloecke = v AND seite IS NULL) = 2, '4: zwei Block-Fassungen';
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_vorlage_speichern('probe-082-block', 'Block', '{"root":{}}', 'probe', 'vorschlag');
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler LIKE 'Vorlage ungueltig: %', format('4: ungueltige Bloecke: %s', v_fehler);
    -- Neu aus Block-Vorlage (5 Argumente) wie bisher
    v_id := marketing.pult_inhalt_aus_vorlage('probe-082-block', 'Probe 082 Block', 'vibemind', v, NULL);
    ASSERT (SELECT format = 'bloecke' AND bloecke = v FROM marketing.inhalt_fassungen WHERE inhalt = v_id AND fassung = 1),
           '4: Block-Newsletter aus Block-Vorlage';
    -- aber nicht unter dem Namen einer Seiten-Vorlage
    PERFORM marketing.pult_vorlage_seite_speichern('probe-082-s', '', pg_temp.v082_seite(), NULL, 'probe',
                                                   'freigegeben', true);
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_inhalt_aus_vorlage('probe-082-s', 'Probe 082 X', 'vibemind', v, NULL);
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler = 'Vorlage probe-082-s gibt es nicht oder sie ist nicht freigegeben',
           format('4: 5 Argumente lehnen Seiten-Vorlage ab: %s', v_fehler);
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_inhalt_aus_vorlage('probe-082-s', 'Probe 082 X', 'vibemind');
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler IS NOT NULL, '4: 3 Argumente lehnen Seiten-Vorlage ab';
    ASSERT NOT EXISTS (SELECT 1 FROM marketing.inhalte WHERE titel LIKE 'Probe 082 X%'), '4: kein Inhalt angelegt';
    RAISE EXCEPTION USING ERRCODE = 'P0V82', MESSAGE = 'verify_082 zurueck';
  EXCEPTION WHEN SQLSTATE 'P0V82' THEN NULL;
  END;
END $$;

-- 5) Entscheiden in allen Uebergaengen
DO $$ DECLARE v_s text; v_fehler text; v jsonb; BEGIN
  BEGIN
    PERFORM marketing.pult_vorlage_seite_speichern('probe-082-e', '', pg_temp.v082_seite(), NULL, 'probe',
                                                   'vorschlag', true);
    ASSERT (SELECT entschieden_von IS NULL AND entschieden_am IS NULL FROM marketing.newsletter_vorlagen
             WHERE name = 'probe-082-e'), '5: Vorschlag ohne Entscheidung';
    -- vorschlag -> freigeben
    v_s := marketing.pult_vorlage_entscheiden('probe-082-e', 'freigeben', '  betreiber ');
    ASSERT v_s = 'freigegeben' AND (SELECT status FROM marketing.newsletter_vorlagen WHERE name = 'probe-082-e')
           = 'freigegeben', '5: vorschlag -> freigegeben';
    ASSERT (SELECT entschieden_von = 'betreiber' AND entschieden_am = now() FROM marketing.newsletter_vorlagen
             WHERE name = 'probe-082-e'), '5: wer und wann gespeichert';
    ASSERT (SELECT fassung FROM marketing.newsletter_vorlagen WHERE name = 'probe-082-e') = 1
       AND (SELECT count(*) FROM marketing.newsletter_vorlagen_fassungen WHERE vorlage = 'probe-082-e') = 1,
           '5: Entscheiden legt keine Fassung an';
    -- freigegeben -> freigeben: Fehler
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_vorlage_entscheiden('probe-082-e', 'freigeben', 'betreiber');
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler = 'Vorlage probe-082-e ist schon freigegeben', format('5: doppelt freigeben: %s', v_fehler);
    -- freigegeben -> zurueckziehen
    v_s := marketing.pult_vorlage_entscheiden('probe-082-e', 'zurueckziehen', 'Felix');
    ASSERT v_s = 'zurueckgezogen' AND (SELECT status FROM marketing.newsletter_vorlagen WHERE name = 'probe-082-e')
           = 'zurueckgezogen', '5: freigegeben -> zurueckgezogen';
    ASSERT (SELECT entschieden_von FROM marketing.newsletter_vorlagen WHERE name = 'probe-082-e') = 'Felix',
           '5: letzte Entscheidung zaehlt';
    -- zurueckgezogen -> beides: Fehler
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_vorlage_entscheiden('probe-082-e', 'freigeben', 'betreiber');
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler = 'Vorlage probe-082-e ist zurückgezogen – entscheiden geht nur bei Vorschlägen und '
                      'freigegebenen Vorlagen', format('5: zurueckgezogen -> freigeben: %s', v_fehler);
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_vorlage_entscheiden('probe-082-e', 'zurueckziehen', 'betreiber');
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler LIKE 'Vorlage probe-082-e ist zurückgezogen%', format('5: zurueckgezogen -> zurueckziehen: %s', v_fehler);
    ASSERT (SELECT entschieden_von FROM marketing.newsletter_vorlagen WHERE name = 'probe-082-e') = 'Felix',
           '5: abgelehnte Entscheidung aendert wer/wann nicht';
    ASSERT (SELECT status FROM marketing.newsletter_vorlagen WHERE name = 'probe-082-e') = 'zurueckgezogen',
           '5: bleibt zurueckgezogen';
    -- vorschlag -> zurueckziehen
    PERFORM marketing.pult_vorlage_seite_speichern('probe-082-f', '', pg_temp.v082_seite(), NULL, 'probe',
                                                   'vorschlag', true);
    v_s := marketing.pult_vorlage_entscheiden('probe-082-f', 'zurueckziehen', 'betreiber');
    ASSERT v_s = 'zurueckgezogen', '5: vorschlag -> zurueckgezogen';
    -- auch Block-Vorlagen
    SELECT bloecke INTO v FROM marketing.newsletter_vorlagen WHERE name = 'leer';
    PERFORM marketing.pult_vorlage_speichern('probe-082-g', '', v, 'probe', 'vorschlag');
    ASSERT marketing.pult_vorlage_entscheiden('probe-082-g', 'freigeben', 'betreiber') = 'freigegeben',
           '5: Block-Vorlage freigeben';
    -- Eingaben
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_vorlage_entscheiden('probe-082-g', 'loeschen', 'betreiber');
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler = 'Urteil muss freigeben oder zurueckziehen sein', format('5: Urteil: %s', v_fehler);
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_vorlage_entscheiden('probe-082-g', NULL, 'betreiber');
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler = 'Urteil muss freigeben oder zurueckziehen sein', format('5: Urteil NULL: %s', v_fehler);
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_vorlage_entscheiden('probe-082-gibtsnicht', 'freigeben', 'betreiber');
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler = 'Vorlage probe-082-gibtsnicht gibt es nicht', format('5: unbekannt: %s', v_fehler);
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_vorlage_entscheiden('probe-082-g', 'zurueckziehen', '');
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler = 'Ohne Namen keine Entscheidung', format('5: ohne Namen: %s', v_fehler);
    RAISE EXCEPTION USING ERRCODE = 'P0V82', MESSAGE = 'verify_082 zurueck';
  EXCEPTION WHEN SQLSTATE 'P0V82' THEN NULL;
  END;
END $$;

-- 6) Aus Vorlage nur bei freigegeben (eigene oder fuer_alle, nur Seiten-Vorlagen)
DO $$ DECLARE v_id uuid; v_fehler text; r record; v_gefuellt jsonb; BEGIN
  BEGIN
    INSERT INTO marketing.mandanten (id, name, aktiv) VALUES ('probe_082_a', 'Probe 082 A', false),
                                                             ('probe_082_b', 'Probe 082 B', false);
    PERFORM marketing.pult_vorlage_seite_speichern('probe-082-h', '', pg_temp.v082_seite(), NULL, 'probe',
                                                   'vorschlag', false);
    UPDATE marketing.newsletter_vorlagen SET mandant = 'probe_082_a' WHERE name = 'probe-082-h';
    -- die API liefert die schon gefuellte Seite; sie wird gespeichert, nicht die Vorlage
    v_gefuellt := pg_temp.v082_seite('Gefüllt für A');
    -- vorschlag: nein
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_collage_aus_vorlage('probe-082-h', 'Probe 082 H', 'probe_082_a', v_gefuellt, 'probe');
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler = 'Vorlage probe-082-h gibt es nicht oder sie ist nicht freigegeben',
           format('6: Vorschlag: %s', v_fehler);
    -- freigegeben, eigene Firma: ja
    PERFORM marketing.pult_vorlage_entscheiden('probe-082-h', 'freigeben', 'betreiber');
    v_id := marketing.pult_collage_aus_vorlage('probe-082-h', 'Probe 082 H', 'probe_082_a', v_gefuellt, 'probe');
    SELECT i.mandant, i.art, i.titel, i.status, f.fassung, f.format, f.seite, f.bloecke, f.felder, f.urheber
      INTO r FROM marketing.inhalte i JOIN marketing.inhalt_fassungen f ON f.inhalt = i.id WHERE i.id = v_id;
    ASSERT r.mandant = 'probe_082_a' AND r.art = 'newsletter' AND r.titel = 'Probe 082 H' AND r.status = 'entwurf'
       AND r.fassung = 1 AND r.format = 'seite' AND r.seite = v_gefuellt AND r.bloecke IS NULL
       AND r.felder->>'betreff' = 'Probe 082 H' AND r.urheber = 'betreiber', format('6: angelegt: %s', row_to_json(r));
    ASSERT (SELECT count(*) FROM marketing.inhalt_fassungen WHERE inhalt = v_id) = 1, '6: genau eine Fassung';
    -- fremde Firma, nicht fuer alle: nein
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_collage_aus_vorlage('probe-082-h', 'Probe 082 X', 'probe_082_b', v_gefuellt, 'probe');
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler = 'Vorlage probe-082-h gibt es nicht oder sie ist nicht freigegeben',
           format('6: fremde Firma: %s', v_fehler);
    -- fuer alle: ja
    UPDATE marketing.newsletter_vorlagen SET fuer_alle = true WHERE name = 'probe-082-h';
    v_id := marketing.pult_collage_aus_vorlage('probe-082-h', 'Probe 082 B', 'probe_082_b', v_gefuellt, 'probe');
    ASSERT (SELECT mandant FROM marketing.inhalte WHERE id = v_id) = 'probe_082_b', '6: fuer alle';
    -- ungueltige gefuellte Seite: nichts bleibt
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_collage_aus_vorlage('probe-082-h', 'Probe 082 X', 'probe_082_a',
                    jsonb_set(v_gefuellt, '{breite}', '599'), 'probe');
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler IS NOT NULL, '6: ungueltige Seite abgelehnt';
    -- ohne Titel, unbekannte Firma
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_collage_aus_vorlage('probe-082-h', ' ', 'probe_082_a', v_gefuellt, 'probe');
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler = 'Ohne Titel kein Newsletter', format('6: ohne Titel: %s', v_fehler);
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_collage_aus_vorlage('probe-082-h', 'Probe 082 X', 'probe_082_gibtsnicht',
                    v_gefuellt, 'probe');
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler = 'Unbekannte Firma', format('6: unbekannte Firma: %s', v_fehler);
    -- Block-Vorlage: keine Collage-Vorlage
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_collage_aus_vorlage('leer', 'Probe 082 X', 'vibemind', v_gefuellt, 'probe');
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler = 'Vorlage leer ist keine Collage-Vorlage', format('6: Block-Vorlage: %s', v_fehler);
    -- zurueckgezogen: nein
    PERFORM marketing.pult_vorlage_entscheiden('probe-082-h', 'zurueckziehen', 'betreiber');
    v_fehler := NULL;
    BEGIN PERFORM marketing.pult_collage_aus_vorlage('probe-082-h', 'Probe 082 X', 'probe_082_a', v_gefuellt, 'probe');
    EXCEPTION WHEN OTHERS THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler = 'Vorlage probe-082-h gibt es nicht oder sie ist nicht freigegeben',
           format('6: zurueckgezogen: %s', v_fehler);
    ASSERT NOT EXISTS (SELECT 1 FROM marketing.inhalte WHERE titel LIKE 'Probe 082 X%'), '6: kein Fehlversuch angelegt';
    RAISE EXCEPTION USING ERRCODE = 'P0V82', MESSAGE = 'verify_082 zurueck';
  EXCEPTION WHEN SQLSTATE 'P0V82' THEN NULL;
  END;
END $$;

-- 7) pult_hat_collage_vorlage
DO $$ DECLARE v jsonb; BEGIN
  BEGIN
    INSERT INTO marketing.mandanten (id, name, aktiv) VALUES ('probe_082_a', 'Probe 082 A', false),
                                                             ('probe_082_b', 'Probe 082 B', false);
    -- vorhandene Seiten-Vorlagen fuer den Abschnitt beiseite stellen (rollt mit zurueck)
    UPDATE marketing.newsletter_vorlagen SET status = 'zurueckgezogen' WHERE format = 'seite';
    ASSERT NOT marketing.pult_hat_collage_vorlage('probe_082_a'), '7: ohne Seiten-Vorlagen false';
    -- freigegebene Block-Vorlage fuer alle zaehlt nicht
    SELECT bloecke INTO v FROM marketing.newsletter_vorlagen WHERE name = 'leer';
    PERFORM marketing.pult_vorlage_speichern('probe-082-i', '', v, 'probe', 'freigegeben');
    UPDATE marketing.newsletter_vorlagen SET fuer_alle = true WHERE name = 'probe-082-i';
    ASSERT NOT marketing.pult_hat_collage_vorlage('probe_082_a'), '7: Block-Vorlage zaehlt nicht';
    -- Vorschlag zaehlt nicht
    PERFORM marketing.pult_vorlage_seite_speichern('probe-082-j', '', pg_temp.v082_seite(), NULL, 'probe',
                                                   'vorschlag', false);
    UPDATE marketing.newsletter_vorlagen SET mandant = 'probe_082_a' WHERE name = 'probe-082-j';
    ASSERT NOT marketing.pult_hat_collage_vorlage('probe_082_a'), '7: Vorschlag zaehlt nicht';
    -- freigegeben, eigene Firma
    PERFORM marketing.pult_vorlage_entscheiden('probe-082-j', 'freigeben', 'betreiber');
    ASSERT marketing.pult_hat_collage_vorlage('probe_082_a'), '7: eigene freigegebene zaehlt';
    ASSERT NOT marketing.pult_hat_collage_vorlage('probe_082_b'), '7: fremde zaehlt nicht';
    -- fuer alle
    UPDATE marketing.newsletter_vorlagen SET fuer_alle = true WHERE name = 'probe-082-j';
    ASSERT marketing.pult_hat_collage_vorlage('probe_082_b'), '7: fuer alle zaehlt';
    -- zurueckgezogen zaehlt nicht mehr
    PERFORM marketing.pult_vorlage_entscheiden('probe-082-j', 'zurueckziehen', 'betreiber');
    ASSERT NOT marketing.pult_hat_collage_vorlage('probe_082_a') AND NOT marketing.pult_hat_collage_vorlage('probe_082_b'),
           '7: zurueckgezogen zaehlt nicht';
    ASSERT NOT marketing.pult_hat_collage_vorlage(NULL), '7: NULL-Firma false';
    RAISE EXCEPTION USING ERRCODE = 'P0V82', MESSAGE = 'verify_082 zurueck';
  EXCEPTION WHEN SQLSTATE 'P0V82' THEN NULL;
  END;
END $$;

-- 8) Abschnitte haben aufgeraeumt
DO $$ BEGIN
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.newsletter_vorlagen WHERE name LIKE 'probe-082-%'),
         '8: keine Probe-Vorlagen uebrig';
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.mandanten WHERE id LIKE 'probe_082_%'), '8: keine Probe-Firmen uebrig';
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.inhalte WHERE titel LIKE 'Probe 082%'), '8: keine Probe-Inhalte uebrig';
END $$;

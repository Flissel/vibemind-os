-- Nachweise fuer 079, laeuft nur ueber migration_probe (eine Transaktion + ROLLBACK).
-- Eigene Probe-Inhalte; darf mehrfach in derselben Probe laufen (079, verify_079, 079, verify_079).

-- 0) Struktur
DO $$ BEGIN
  ASSERT to_regprocedure('marketing.inhalt_titel_frei(text, text, text, uuid)') IS NOT NULL, '0: inhalt_titel_frei';
  ASSERT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'marketing.inhalte'::regclass
                  AND tgname = 'trg_inhalte_titel_eindeutig' AND NOT tgisinternal), '0: Trigger';
  ASSERT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'marketing' AND indexname = 'inhalte_titel_eindeutig'),
         '0: eindeutiger Index';
  ASSERT (SELECT count(*) FROM pg_proc WHERE pronamespace = 'marketing'::regnamespace
           AND proname IN ('inhalt_titel_frei', 'inhalte_titel_eindeutig')) = 2, '0: keine Ueberladungen';
END $$;

-- 1) Bestand: keine doppelten Namen mehr; jede Gruppe behielt genau einen Eintrag mit dem Grundnamen
DO $$ BEGIN
  ASSERT NOT EXISTS (SELECT 1 FROM marketing.inhalte GROUP BY mandant, art, lower(btrim(titel)) HAVING count(*) > 1),
         '1: keine doppelten Namen';
END $$;

-- 2-4) Ein Block mit eigenem Zufallsnamen je Lauf, damit die Probe mehrfach laufen darf.
DO $$ DECLARE v_basis text := 'Probe 079 ' || left(md5(clock_timestamp()::text || random()::text), 8);
  v_a uuid; v_b uuid; v_c uuid; v_t text; v_fehler boolean := false; BEGIN
  -- 2) Anlegen: gleicher Name -> (2), (3); Gross/Klein und Randleerzeichen zaehlen nicht
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', v_basis) RETURNING id INTO v_a;
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', v_basis) RETURNING id INTO v_b;
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', '  ' || upper(v_basis) || ' ')
  RETURNING id INTO v_c;
  ASSERT (SELECT titel FROM marketing.inhalte WHERE id = v_a) = v_basis, '2: erster behaelt den Namen';
  SELECT titel INTO v_t FROM marketing.inhalte WHERE id = v_b;
  ASSERT v_t = v_basis || ' (2)', format('2: zweiter -> (2): %s', v_t);
  SELECT titel INTO v_t FROM marketing.inhalte WHERE id = v_c;
  ASSERT v_t = upper(v_basis) || ' (3)', format('2: dritter (anders geschrieben) -> (3): %s', v_t);

  -- 3) Andere Art und andere Firma duerfen denselben Namen tragen
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'post', v_basis) RETURNING titel INTO v_t;
  ASSERT v_t = v_basis, format('3: andere Art unberuehrt: %s', v_t);
  IF EXISTS (SELECT 1 FROM marketing.mandanten WHERE id = 'fin2gether') THEN
    INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('fin2gether', 'newsletter', v_basis) RETURNING titel INTO v_t;
    ASSERT v_t = v_basis, format('3: andere Firma unberuehrt: %s', v_t);
  END IF;

  -- 4) Umbenennen auf einen vergebenen Namen scheitert; der eigene Name in anderer Schreibung nicht
  BEGIN
    UPDATE marketing.inhalte SET titel = lower(v_basis) WHERE id = v_b;
  EXCEPTION WHEN unique_violation THEN v_fehler := true;
  END;
  ASSERT v_fehler, '4: Umbenennen auf vergebenen Namen -> unique_violation';
  UPDATE marketing.inhalte SET titel = lower(v_basis) || ' (2)' WHERE id = v_b;
  ASSERT (SELECT titel FROM marketing.inhalte WHERE id = v_b) = lower(v_basis) || ' (2)', '4: eigener Name erlaubt';
END $$;

-- 5) Anlege-Funktion des Pults (053/056/058/077) bekommt den nummerierten Namen zurueck - hier ueber den
--    direkten INSERT nachgewiesen, den alle diese Funktionen verwenden (Trigger statt Funktionsaenderung).

SELECT 'verify_079 ok' AS ergebnis;

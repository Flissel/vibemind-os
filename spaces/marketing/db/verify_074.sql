-- Nachweise fuer 074, laeuft nur ueber migration_probe (eine Transaktion + ROLLBACK).
DO $$ DECLARE a uuid; b uuid; c uuid; j jsonb; BEGIN
  ASSERT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'marketing'
                  AND table_name = 'ton_auftraege' AND column_name = 'angelegt_von'), 'Spalte angelegt_von fehlt';
  -- Alte 4-Argument-Form bleibt nutzbar und ergibt 'agent'.
  a := marketing.ton_auftrag_anlegen('vibemind', 'Probe 074', 'de', '[{"sprecher":"Anna","text":"Hallo"}]'::jsonb);
  ASSERT (SELECT angelegt_von FROM marketing.ton_auftraege WHERE id = a) = 'agent', 'Standard agent';
  b := marketing.ton_auftrag_anlegen('vibemind', 'Probe 074b', 'de', '[{"sprecher":"Anna","text":"Hallo"}]'::jsonb, 'betreiber');
  ASSERT (SELECT angelegt_von FROM marketing.ton_auftraege WHERE id = b) = 'betreiber', 'Urheber betreiber';
  BEGIN
    PERFORM marketing.ton_auftrag_anlegen('vibemind', 'x', 'de', '[{"sprecher":"A","text":"x"}]'::jsonb, 'fremd');
    RAISE EXCEPTION 'unbekannter Urheber haette scheitern muessen';
  EXCEPTION WHEN check_violation THEN NULL; END;
  -- Abbrechen aus offen, zweites Mal wirft.
  ASSERT marketing.ton_abbrechen(a) = 'abgebrochen', 'abbrechen aus offen';
  ASSERT (SELECT befund FROM marketing.ton_auftraege WHERE id = a) = 'Im Pult abgebrochen', 'Befund';
  BEGIN
    PERFORM marketing.ton_abbrechen(a);
    RAISE EXCEPTION 'zweimal abbrechen haette scheitern muessen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'Auftrag ist nicht mehr offen%' THEN RAISE; END IF;
  END;
  -- Abgebrochene werden nicht abgeholt; abschliessen auf abgebrochen wirkt nicht.
  ASSERT marketing.ton_abschliessen(a, NULL, NULL, 'x') IS NULL, 'abschliessen auf abgebrochen = NULL';
  -- In Arbeit abbrechbar, vergeben_bis wird NULL.
  UPDATE marketing.ton_auftraege SET status = 'in_arbeit', vergeben_bis = now() + interval '5 minutes' WHERE id = b;
  ASSERT marketing.ton_abbrechen(b) = 'abgebrochen', 'abbrechen aus in_arbeit';
  ASSERT (SELECT vergeben_bis FROM marketing.ton_auftraege WHERE id = b) IS NULL, 'Frist geloescht';
  -- Fertig ist nicht abbrechbar.
  c := marketing.ton_auftrag_anlegen('vibemind', 'Probe 074c', 'de', '[{"sprecher":"Anna","text":"Hallo"}]'::jsonb);
  UPDATE marketing.ton_auftraege SET status = 'fertig', datei = 'pod-0123abcd-probe-074.mp3' WHERE id = c;
  BEGIN
    PERFORM marketing.ton_abbrechen(c);
    RAISE EXCEPTION 'fertig abbrechen haette scheitern muessen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'Auftrag ist nicht mehr offen%' THEN RAISE; END IF;
  END;
  -- Rechte.
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sales_app') THEN
    ASSERT NOT has_function_privilege('sales_app', 'marketing.ton_abbrechen(uuid)', 'EXECUTE'), 'sales_app bricht nicht ab';
    ASSERT NOT has_function_privilege('sales_app',
      'marketing.ton_auftrag_anlegen(text,text,text,jsonb,text)', 'EXECUTE'), 'sales_app legt nichts an';
  END IF;
  RAISE NOTICE 'verify_074 ok';
END $$;

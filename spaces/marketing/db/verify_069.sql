-- Nachweise fuer 069, laeuft nur ueber migration_probe (eine Transaktion + ROLLBACK).
DO $$ DECLARE a jsonb; b jsonb; v text; BEGIN
  INSERT INTO marketing.ton_auftraege (mandant, titel, skript)
  VALUES ('vibemind', 'Probe 069', '[{"sprecher":"A","text":"Hallo"}]'::jsonb);
  a := marketing.ton_naechster('15 minutes');
  ASSERT a ->> 'titel' = 'Probe 069', 'naechster liefert den offenen Auftrag';
  ASSERT (SELECT status FROM marketing.ton_auftraege WHERE id = (a->>'id')::uuid) = 'in_arbeit', 'in Arbeit';
  b := marketing.ton_naechster('15 minutes');
  ASSERT b IS NULL OR b->>'id' <> a->>'id', 'derselbe Auftrag nicht zweimal vergeben';
  ASSERT marketing.ton_verlaengern((a->>'id')::uuid, '15 minutes'), 'verlaengern geht';
  v := marketing.ton_abschliessen((a->>'id')::uuid, 'pod-' || left(a->>'id', 8) || '-probe-069.mp3', 12.5, '');
  ASSERT v = 'fertig', 'abschliessen fertig';
  ASSERT marketing.ton_abschliessen((a->>'id')::uuid, NULL, NULL, 'x') IS NULL, 'zweiter Abschluss wirkungslos';
  BEGIN
    INSERT INTO marketing.ton_auftraege (mandant, titel, skript) VALUES ('vibemind', 'x', '[]'::jsonb);
    RAISE EXCEPTION 'leeres Skript haette scheitern muessen';
  EXCEPTION WHEN check_violation THEN NULL; END;
  BEGIN
    UPDATE marketing.ton_auftraege SET datei = '../boese.mp3' WHERE id = (a->>'id')::uuid;
    RAISE EXCEPTION 'boeser Dateiname haette scheitern muessen';
  EXCEPTION WHEN check_violation THEN NULL; END;
  RAISE NOTICE 'verify_069 ok';
END $$;

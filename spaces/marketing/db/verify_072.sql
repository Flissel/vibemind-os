-- Nachweise fuer 072, laeuft nur ueber migration_probe (eine Transaktion + ROLLBACK).
DO $$ DECLARE a uuid; b uuid; j jsonb; n int; BEGIN
  a := marketing.video_auftrag_anlegen('vibemind', 'bilder', ' Herbst-Reel ', '{"thema":"bAV"}', 'agent');
  ASSERT (SELECT status FROM marketing.video_auftraege WHERE id = a) = 'offen', 'neu -> offen';
  ASSERT (SELECT titel FROM marketing.video_auftraege WHERE id = a) = 'Herbst-Reel', 'Titel getrimmt';
  -- Abholen: genau einmal, mit Lease.
  j := marketing.video_naechster('10 minutes');
  ASSERT (j->>'id')::uuid = a AND (j->>'versuch')::int = 1, 'erster Abruf liefert den Auftrag';
  ASSERT marketing.video_naechster('10 minutes') IS NULL, 'kein Doppelabruf in der Frist';
  ASSERT marketing.video_verlaengern(a, '10 minutes'), 'verlaengern in Arbeit';
  ASSERT marketing.video_notiz(a, 'drei Bilder, zoom_rein'), 'Notiz';
  ASSERT NOT marketing.video_notiz(a, '   '), 'leere Notiz nicht';
  ASSERT jsonb_array_length((SELECT protokoll FROM marketing.video_auftraege WHERE id = a)) = 1, 'ein Eintrag';
  ASSERT marketing.video_abschliessen(a, 'vid-0123abcd-herbst.mp4', 24.5, 9000000, 'exp1', '') = 'fertig', 'fertig';
  ASSERT NOT marketing.video_verlaengern(a, '10 minutes'), 'fertig ist nicht mehr in Arbeit';
  -- Fehlerpfad ohne Datei.
  b := marketing.video_auftrag_anlegen('vibemind', 'material', 'Kurz', '{}', 'betreiber');
  PERFORM marketing.video_naechster('10 minutes');
  ASSERT marketing.video_abschliessen(b, NULL, NULL, NULL, NULL, 'Laura 503') = 'fehler', 'fehler';
  -- Abbrechen.
  b := marketing.video_auftrag_anlegen('vibemind', 'material', 'Weg', '{}', 'betreiber');
  ASSERT marketing.video_abbrechen(b) = 'abgebrochen', 'abbrechen aus offen';
  BEGIN
    PERFORM marketing.video_abbrechen(b);
    RAISE EXCEPTION 'zweimal abbrechen haette scheitern muessen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'Auftrag ist nicht mehr offen%' THEN RAISE; END IF;
  END;
  -- Formen.
  BEGIN
    PERFORM marketing.video_auftrag_anlegen('vibemind', 'film', 'x', '{}', 'agent');
    RAISE EXCEPTION 'unbekannte Art haette scheitern muessen';
  EXCEPTION WHEN check_violation THEN NULL; END;
  BEGIN
    PERFORM marketing.video_auftrag_anlegen('gibtsnicht', 'bilder', 'x', '{}', 'agent');
    RAISE EXCEPTION 'unbekannte Firma haette scheitern muessen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'Unbekannte firma%' THEN RAISE; END IF;
  END;
  -- Rechte: niemand ausser dem Eigentuemer.
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sales_app') THEN
    ASSERT NOT has_function_privilege('sales_app',
      'marketing.video_auftrag_anlegen(text,text,text,jsonb,text)', 'EXECUTE'), 'sales_app legt nichts an';
    ASSERT NOT has_function_privilege('sales_app', 'marketing.video_naechster(interval)', 'EXECUTE'),
      'sales_app holt nichts ab';
  END IF;
  RAISE NOTICE 'verify_072 ok';
END $$;

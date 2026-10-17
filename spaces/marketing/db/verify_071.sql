-- Nachweise fuer 071, laeuft nur ueber migration_probe (eine Transaktion + ROLLBACK).
DO $$ DECLARE a uuid; b uuid; r record; n int; BEGIN
  SELECT (row_to_json(t)::jsonb->>'id')::uuid INTO a FROM (
    SELECT marketing.plan_anlegen('vibemind', current_date + 2, 'newsletter', ' Herbst ', '', NULL, 'agent') AS id) t;
  ASSERT (SELECT status FROM marketing.kampagnen_plan WHERE id = a) = 'vorschlag', 'Agent -> vorschlag';
  b := marketing.plan_anlegen('vibemind', current_date + 3, 'podcast', 'Folge 1', 'Notiz', NULL, 'betreiber');
  ASSERT (SELECT status FROM marketing.kampagnen_plan WHERE id = b) = 'geplant', 'Betreiber -> geplant';
  ASSERT marketing.plan_aendern(a, 'geplant', NULL, NULL, NULL, NULL) = 'geplant', 'Vorschlag bestaetigt';
  ASSERT marketing.plan_aendern(b, NULL, current_date + 5, NULL, NULL, NULL) = 'verschoben', 'neues Datum -> verschoben';
  SELECT count(*) INTO n FROM marketing.plan_anstehend(7) WHERE id IN (a, b);
  ASSERT n = 2, 'beide anstehend';
  ASSERT marketing.plan_aendern(a, 'erledigt', NULL, NULL, NULL, NULL) = 'erledigt', 'erledigt';
  SELECT count(*) INTO n FROM marketing.plan_anstehend(7) WHERE id = a;
  ASSERT n = 0, 'erledigt nicht mehr anstehend';
  BEGIN
    PERFORM marketing.plan_anlegen('vibemind', current_date + 1, 'fax', 'x', '', NULL, 'agent');
    RAISE EXCEPTION 'unbekannte Art haette scheitern muessen';
  EXCEPTION WHEN check_violation THEN NULL; END;
  BEGIN
    PERFORM marketing.plan_aendern(b, 'vorschlag', NULL, NULL, NULL, NULL);
    RAISE EXCEPTION 'zurueck auf vorschlag haette scheitern muessen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'status muss%' THEN RAISE; END IF;
  END;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sales_app') THEN
    ASSERT has_function_privilege('sales_app', 'marketing.plan_anstehend(int)', 'EXECUTE'), 'sales_app darf lesen';
    ASSERT NOT has_function_privilege('sales_app', 'marketing.plan_anlegen(text,date,text,text,text,uuid,text)',
                                      'EXECUTE'), 'sales_app darf nicht anlegen';
    ASSERT NOT has_function_privilege('sales_app', 'marketing.ton_auftrag_anlegen(text,text,text,jsonb)', 'EXECUTE'),
           'sales_app darf keine Podcast-Auftraege anlegen';
  END IF;
  RAISE NOTICE 'verify_071 ok';
END $$;

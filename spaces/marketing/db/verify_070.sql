-- Nachweise fuer 070, laeuft nur ueber migration_probe (eine Transaktion + ROLLBACK).
DO $$ DECLARE v uuid; r jsonb; BEGIN
  -- genau so, wie die API liest: in einer Unterabfrage
  SELECT row_to_json(t)::jsonb INTO r FROM (
    SELECT marketing.ton_auftrag_anlegen('vibemind', ' Probe 070 ', 'de',
                                         '[{"sprecher":"A","text":"Hallo"}]'::jsonb) AS id) t;
  v := (r->>'id')::uuid;
  ASSERT (SELECT titel FROM marketing.ton_auftraege WHERE id = v) = 'Probe 070', 'angelegt, Titel getrimmt';
  ASSERT (SELECT status FROM marketing.ton_auftraege WHERE id = v) = 'offen', 'offen';
  BEGIN
    PERFORM marketing.ton_auftrag_anlegen('gibtsnicht', 'x', 'de', '[{"sprecher":"A","text":"x"}]'::jsonb);
    RAISE EXCEPTION 'unbekannte Firma haette scheitern muessen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'Unbekannte firma' THEN RAISE; END IF;
  END;
  RAISE NOTICE 'verify_070 ok';
END $$;

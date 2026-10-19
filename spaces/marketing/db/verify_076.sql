-- Nachweise fuer 076, laeuft nur ueber migration_probe (eine Transaktion + ROLLBACK).

DO $$ DECLARE v_n int; BEGIN
  SELECT count(*) INTO v_n FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = 'marketing' AND t.tgname LIKE 'trg_emit_sync_%';
  IF v_n <> 0 THEN RAISE EXCEPTION 'verify_076: noch % Sync-Trigger', v_n; END IF;

  IF to_regclass('marketing.sync_outbox') IS NOT NULL THEN
    RAISE EXCEPTION 'verify_076: sync_outbox existiert noch';
  END IF;
  IF to_regprocedure('marketing.emit_sync_event()') IS NOT NULL
     OR to_regprocedure('marketing.mark_outbox_applied(uuid[])') IS NOT NULL
     OR to_regprocedure('marketing._resolve_handle(text,jsonb)') IS NOT NULL THEN
    RAISE EXCEPTION 'verify_076: Sync-Funktion existiert noch';
  END IF;

  -- Schreiben in die frueher beobachteten Tabellen geht weiter, ohne Outbox.
  INSERT INTO marketing.accounts (handle, display_name) VALUES ('probe_076', 'Probe 076');
  UPDATE marketing.accounts SET niche = 'probe' WHERE handle = 'probe_076';
  DELETE FROM marketing.accounts WHERE handle = 'probe_076';
  RAISE NOTICE 'verify_076: ok';
END $$;

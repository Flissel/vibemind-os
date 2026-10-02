-- Nachweise fuer 059, nur ueber migration_probe (Transaktion + ROLLBACK).
DO $$ DECLARE v_inhalt uuid; v_id uuid; v_modus text; BEGIN
  ASSERT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'bild_auftraege_modus_check'
                 AND pg_get_constraintdef(oid) LIKE '%freistellen%'), 'Modus-Check kennt freistellen';
  SELECT id INTO v_inhalt FROM marketing.inhalte WHERE art = 'newsletter' AND status = 'entwurf' LIMIT 1;
  IF v_inhalt IS NOT NULL THEN
    v_id := marketing.pult_bild_auftrag(v_inhalt, 'kopf_bild', false, '', 'mensch', 100, 'freistellen');
    SELECT modus INTO v_modus FROM marketing.bild_auftraege WHERE id = v_id;
    ASSERT v_modus = 'freistellen', 'Staerke 100 macht aus freistellen kein neu';
    BEGIN
      PERFORM marketing.pult_bild_auftrag(v_inhalt, NULL, false, '', 'mensch', 0, 'freistellen');
      ASSERT false, 'freistellen ohne Platz muss scheitern';
    EXCEPTION WHEN raise_exception THEN NULL; END;
  END IF;
END $$;
SELECT 'verify_059 ok' AS ergebnis;

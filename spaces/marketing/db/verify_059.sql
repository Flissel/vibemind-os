-- Nachweise fuer 059, nur ueber migration_probe (Transaktion + ROLLBACK).
DO $$ DECLARE v_inhalt uuid; v_platz text; v_id uuid; v_modus text; BEGIN
  ASSERT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'bild_auftraege_modus_check'
                 AND pg_get_constraintdef(oid) LIKE '%freistellen%'), 'Modus-Check kennt freistellen';
  -- Find newest fassung of a draft newsletter and first image slot in it
  SELECT i.id, block.key
  INTO v_inhalt, v_platz
  FROM marketing.inhalte i
  JOIN marketing.inhalt_fassungen f ON i.id = f.inhalt AND f.fassung = (
      SELECT max(fassung) FROM marketing.inhalt_fassungen WHERE inhalt = i.id
    )
  CROSS JOIN LATERAL jsonb_each(f.bloecke) AS block(key, value)
  WHERE i.art = 'newsletter'
    AND i.status = 'entwurf'
    AND f.bloecke IS NOT NULL
    AND marketing._bild_ist_platz(block.value)
  LIMIT 1;
  IF v_inhalt IS NOT NULL THEN
    v_id := marketing.pult_bild_auftrag(v_inhalt, v_platz, false, '', 'mensch', 100, 'freistellen');
    SELECT modus INTO v_modus FROM marketing.bild_auftraege WHERE id = v_id;
    ASSERT v_modus = 'freistellen', 'Staerke 100 macht aus freistellen kein neu';
    BEGIN
      PERFORM marketing.pult_bild_auftrag(v_inhalt, NULL, false, '', 'mensch', 0, 'freistellen');
      ASSERT false, 'freistellen ohne Platz muss scheitern';
    EXCEPTION WHEN raise_exception THEN NULL; END;
  ELSE
    RAISE NOTICE 'Keine Entwurfs-Newsletter mit Bildplaetzen gefunden - Assertions uebersprungen';
  END IF;
END $$;
SELECT 'verify_059 ok' AS ergebnis;

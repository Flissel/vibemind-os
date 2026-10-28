-- verify_094: Marke endgueltig loeschen. Laeuft in einer Transaktion mit ROLLBACK (migration_probe):
-- legt eine Probe-Marke mit FREIGEGEBENEM Inhalt und Fassung an (genau das, was die
-- Unveraenderlich-Trigger sonst sperren), loescht sie und prueft die Ablehnungsfaelle.
DO $$
DECLARE v_inhalt uuid; r jsonb;
BEGIN
  IF to_regprocedure('marketing.pult_mandant_loeschen(text, text, text)') IS NULL THEN
    RAISE EXCEPTION '094: pult_mandant_loeschen fehlt';
  END IF;
  INSERT INTO marketing.mandanten (id, name, aktiv, verteiler) VALUES ('probe_094', 'Probe 094', false, '{}');
  INSERT INTO marketing.inhalte (mandant, art, titel, status)
    VALUES ('probe_094', 'newsletter', 'Probe 094', 'entwurf') RETURNING id INTO v_inhalt;
  INSERT INTO marketing.inhalt_fassungen (inhalt, fassung, felder, layout, urheber)
    VALUES (v_inhalt, 1, '{}'::jsonb, 'probe', 'betreiber');
  UPDATE marketing.inhalte SET status = 'freigegeben', freigegebene_fassung = 1,
         entschieden_von = 'verify_094', entschieden_am = now() WHERE id = v_inhalt;

  BEGIN PERFORM marketing.pult_mandant_loeschen('probe_094', 'falsch', 'Felix');
    RAISE EXCEPTION '094: falsche Bestaetigung wurde angenommen';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'Bestaetigung%' THEN RAISE; END IF;
  END;
  BEGIN PERFORM marketing.pult_mandant_loeschen('probe_094', 'probe_094', '');
    RAISE EXCEPTION '094: ohne Namen wurde geloescht';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'Ohne Namen%' THEN RAISE; END IF;
  END;
  UPDATE marketing.mandanten SET verteiler = '{sales}' WHERE id = 'probe_094';
  BEGIN PERFORM marketing.pult_mandant_loeschen('probe_094', 'probe_094', 'Felix');
    RAISE EXCEPTION '094: Marke mit Verteiler wurde geloescht';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM NOT LIKE 'Marke versendet%' THEN RAISE; END IF;
  END;
  UPDATE marketing.mandanten SET verteiler = '{}' WHERE id = 'probe_094';

  r := marketing.pult_mandant_loeschen('probe_094', 'probe_094', 'Felix');
  IF (r->>'inhalte')::int <> 1 OR (r->>'inhalt_fassungen')::int <> 1 THEN
    RAISE EXCEPTION '094: Zaehlung falsch: %', r;
  END IF;
  IF EXISTS (SELECT 1 FROM marketing.mandanten WHERE id = 'probe_094')
     OR EXISTS (SELECT 1 FROM marketing.inhalte WHERE id = v_inhalt)
     OR EXISTS (SELECT 1 FROM marketing.inhalt_fassungen WHERE inhalt = v_inhalt) THEN
    RAISE EXCEPTION '094: Reste der Marke stehen noch';
  END IF;
  IF current_setting('session_replication_role') <> 'origin' THEN
    RAISE EXCEPTION '094: Trigger bleiben nach dem Loeschen aus';
  END IF;
  -- Trigger wieder scharf: eine fremde freigegebene Fassung bleibt unloeschbar.
  RAISE NOTICE 'verify_094 ok';
END $$;

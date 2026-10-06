-- Nachweise fuer 062, laeuft nur ueber migration_probe (eine Transaktion + ROLLBACK).

-- 0) Struktur
DO $$ BEGIN
  ASSERT (SELECT count(*) FROM information_schema.columns
           WHERE table_schema = 'marketing' AND table_name = 'medien_mandant'
             AND column_name IN ('dateiname','mandant','geaendert_am')) = 3, '0: Spalten';
  ASSERT (SELECT is_nullable FROM information_schema.columns
           WHERE table_schema = 'marketing' AND table_name = 'medien_mandant'
             AND column_name = 'mandant') = 'YES', '0: mandant darf NULL sein';
  ASSERT to_regclass('marketing.medien_mandant_mandant_idx') IS NOT NULL, '0: Index';
  ASSERT (SELECT aktiv FROM marketing.mandanten WHERE id = 'fin2gether'), '0: fin2gether aktiv';
END $$;

DO $$ DECLARE v_fehler text; v_m text; v_n int;
BEGIN
  -- 1) Fremdschluessel
  v_fehler := NULL;
  BEGIN INSERT INTO marketing.medien_mandant (dateiname, mandant) VALUES ('a.jpg', 'gibtsnicht');
  EXCEPTION WHEN foreign_key_violation THEN v_fehler := SQLERRM; END;
  ASSERT v_fehler IS NOT NULL, '1: unbekannter Mandant muss scheitern';

  -- 2) CHECK: Pfadtrenner, zwei Punkte, Backslash (echt: 'a\b.jpg' unter standard_conforming_strings),
  --    Ruecktaste (E'a\b.jpg'), Anfuehrungszeichen, Zeilenumbruch, leer, zu lang
  v_n := 0;
  FOREACH v_m IN ARRAY ARRAY['a/b.jpg', '..x.jpg', 'a..b.jpg', 'a\b.jpg', E'a\b.jpg', 'a"b.jpg', E'a\nb.jpg', '', repeat('x', 201)] LOOP
    v_fehler := NULL;
    BEGIN INSERT INTO marketing.medien_mandant (dateiname, mandant) VALUES (v_m, 'vibemind');
    EXCEPTION WHEN check_violation THEN v_fehler := SQLERRM; END;
    ASSERT v_fehler IS NOT NULL, format('2: Dateiname muss scheitern: %L', v_m);
    v_n := v_n + 1;
  END LOOP;
  ASSERT v_n = 9, '2: alle Faelle geprueft';

  -- 3) gueltige Namen, NULL-Mandant erlaubt
  INSERT INTO marketing.medien_mandant (dateiname, mandant) VALUES ('logo-fin2gether-0123456789.png', 'fin2gether');
  INSERT INTO marketing.medien_mandant (dateiname, mandant) VALUES ('gemeinsam.jpg', NULL);
  ASSERT (SELECT mandant IS NULL FROM marketing.medien_mandant WHERE dateiname = 'gemeinsam.jpg'), '3: NULL-Mandant';

  -- 4) Umsetzen per ON CONFLICT
  INSERT INTO marketing.medien_mandant (dateiname, mandant) VALUES ('gemeinsam.jpg', 'vibemind')
  ON CONFLICT (dateiname) DO UPDATE SET mandant = EXCLUDED.mandant, geaendert_am = now();
  ASSERT (SELECT mandant FROM marketing.medien_mandant WHERE dateiname = 'gemeinsam.jpg') = 'vibemind', '4: umgesetzt';
  ASSERT (SELECT count(*) FROM marketing.medien_mandant WHERE dateiname = 'gemeinsam.jpg') = 1, '4: keine Dublette';
  INSERT INTO marketing.medien_mandant (dateiname, mandant) VALUES ('gemeinsam.jpg', NULL)
  ON CONFLICT (dateiname) DO UPDATE SET mandant = EXCLUDED.mandant, geaendert_am = now();
  ASSERT (SELECT mandant IS NULL FROM marketing.medien_mandant WHERE dateiname = 'gemeinsam.jpg'), '4: zurueck auf Gemeinsam';
END $$;
SELECT 'verify_062 ok' AS ergebnis;

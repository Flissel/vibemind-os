-- Nachweise fuer 073, nur ueber migration_probe (eine Transaktion + ROLLBACK).
-- Eigener Probe-Newsletter mit einem leeren Platz (held, Platzhalter) und einem echten Bild (foto).
DROP TABLE IF EXISTS pg_temp._p073;   -- zweimal in derselben Probe (073 erneut eingespielt)
CREATE TEMP TABLE _p073 ON COMMIT DROP AS SELECT NULL::uuid AS id LIMIT 0;

DO $$ DECLARE v_i uuid; d jsonb; BEGIN
  d := '{"root":{"type":"EmailLayout","data":{"childrenIds":["held","foto","t"]}},
         "held":{"type":"Image","data":{"style":{},"props":{"url":"medien:platzhalter-2x1.png","alt":"Team","width":600,"height":300}}},
         "foto":{"type":"Image","data":{"style":{},"props":{"url":"medien:probe-073.jpg","alt":"Foto","width":600,"height":300}}},
         "t":{"type":"Text","data":{"style":{},"props":{"text":"eins","markdown":false}}}}'::jsonb;
  INSERT INTO marketing.inhalte (mandant, art, titel) VALUES ('vibemind', 'newsletter', 'Probe 073') RETURNING id INTO v_i;
  PERFORM marketing.pult_bloecke_speichern(v_i, 0, 'Probe 073', '', d, 'betreiber', false);
  INSERT INTO _p073 VALUES (v_i);
END $$;

-- 0) Struktur
DO $$ BEGIN
  ASSERT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'marketing'
                  AND table_name = 'bild_auftraege' AND column_name = 'vorlagen' AND data_type = 'jsonb'
                  AND is_nullable = 'NO'), '0: Spalte vorlagen';
  ASSERT to_regprocedure('marketing.pult_bild_auftrag(uuid, text, boolean, text, text, int, text, jsonb)') IS NOT NULL,
         '0: 8-arg-Huelle';
  ASSERT to_regprocedure('marketing.pult_bild_auftrag(uuid, text, boolean, text, text, int, text)') IS NOT NULL,
         '0: 7-arg-Huelle';
  ASSERT to_regprocedure('marketing._bild_platz_leer(text)') IS NOT NULL, '0: _bild_platz_leer aus 056';
  ASSERT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'bild_auftraege_modus_check'
                 AND pg_get_constraintdef(oid) LIKE '%aendern%' AND pg_get_constraintdef(oid) LIKE '%freistellen%'
                 AND pg_get_constraintdef(oid) LIKE '%ueberarbeiten%'), '0: Modus-Check kennt aendern';
  ASSERT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'bild_auftraege_vorlagen_check'), '0: Vorlagen-Check';
END $$;

-- 1) neu mit 3 Vorlagen
DO $$ DECLARE v_i uuid := (SELECT id FROM _p073); v_id uuid; r record; BEGIN
  v_id := marketing.pult_bild_auftrag(v_i, 'held', false, 'Team am Tisch', 'agent', 100, 'neu',
                                      '["a.jpg","b.png","c.jpg"]'::jsonb);
  SELECT modus, vorlagen INTO r FROM marketing.bild_auftraege WHERE id = v_id;
  ASSERT r.modus = 'neu', format('1: Modus neu: %s', r.modus);
  ASSERT r.vorlagen = '["a.jpg","b.png","c.jpg"]'::jsonb, format('1: genau die 3 Vorlagen: %s', r.vorlagen);
END $$;

-- 2) 4 Vorlagen, doppelte, Zahl statt Text, Text statt Liste -> Fehler
DO $$ DECLARE v_i uuid := (SELECT id FROM _p073); v jsonb; v_msg text; BEGIN
  FOREACH v IN ARRAY ARRAY['["a.jpg","b.jpg","c.jpg","d.jpg"]'::jsonb, '["a.jpg","a.jpg"]'::jsonb, '[1]'::jsonb,
                         '"a.jpg"'::jsonb] LOOP
    v_msg := NULL;
    BEGIN
      PERFORM marketing.pult_bild_auftrag(v_i, 'held', false, '', 'agent', 100, 'neu', v);
    EXCEPTION WHEN raise_exception THEN v_msg := SQLERRM; END;
    ASSERT v_msg = 'Vorlagen: hoechstens 3 verschiedene Mediennamen', format('2: %s abgelehnt: %s', v, v_msg);
  END LOOP;
END $$;

-- 3) aendern auf echtes Bild: 2 Vorlagen gehen, 3 nicht; Staerke 100 macht kein neu
DO $$ DECLARE v_i uuid := (SELECT id FROM _p073); v_id uuid; r record; v_msg text; BEGIN
  v_id := marketing.pult_bild_auftrag(v_i, 'foto', false, 'Himmel abendrot', 'agent', 100, 'aendern',
                                      '["a.jpg","b.jpg"]'::jsonb);
  SELECT modus, vorlagen, staerke INTO r FROM marketing.bild_auftraege WHERE id = v_id;
  ASSERT r.modus = 'aendern', format('3: Modus aendern: %s', r.modus);
  ASSERT r.vorlagen = '["a.jpg","b.jpg"]'::jsonb, format('3: Vorlagen: %s', r.vorlagen);
  BEGIN
    PERFORM marketing.pult_bild_auftrag(v_i, 'foto', false, '', 'agent', 55, 'aendern', '["a.jpg","b.jpg","c.jpg"]'::jsonb);
  EXCEPTION WHEN raise_exception THEN v_msg := SQLERRM; END;
  ASSERT v_msg LIKE 'Aendern: hoechstens 2 Vorlagen%', format('3: 3 Vorlagen bei aendern: %s', v_msg);
  v_msg := NULL;
  BEGIN
    PERFORM marketing.pult_bild_auftrag(v_i, NULL, false, '', 'agent', 55, 'aendern', '[]'::jsonb);
  EXCEPTION WHEN raise_exception THEN v_msg := SQLERRM; END;
  ASSERT v_msg = 'Aendern braucht einen Bildplatz', format('3: aendern ohne Platz: %s', v_msg);
END $$;

-- 4) aendern auf leeren Platz (Platzhalter)
DO $$ DECLARE v_i uuid := (SELECT id FROM _p073); v_msg text; BEGIN
  BEGIN
    PERFORM marketing.pult_bild_auftrag(v_i, 'held', false, '', 'agent', 55, 'aendern', '[]'::jsonb);
  EXCEPTION WHEN raise_exception THEN v_msg := SQLERRM; END;
  ASSERT v_msg = 'Hier ist noch kein Bild – nimm bild_erzeugen', format('4: leerer Platz: %s', v_msg);
  v_msg := NULL;
  BEGIN
    PERFORM marketing.pult_bild_auftrag(v_i, 'held', false, '', 'agent', 55, 'aendern');
  EXCEPTION WHEN raise_exception THEN v_msg := SQLERRM; END;
  ASSERT v_msg = 'Hier ist noch kein Bild – nimm bild_erzeugen', format('4: leerer Platz (7-arg): %s', v_msg);
END $$;

-- 5) freistellen mit Vorlage
DO $$ DECLARE v_i uuid := (SELECT id FROM _p073); v_msg text; BEGIN
  BEGIN
    PERFORM marketing.pult_bild_auftrag(v_i, 'foto', false, '', 'mensch', 0, 'freistellen', '["a.jpg"]'::jsonb);
  EXCEPTION WHEN raise_exception THEN v_msg := SQLERRM; END;
  ASSERT v_msg = 'Freistellen nimmt keine Vorlagen', format('5: freistellen mit Vorlage: %s', v_msg);
END $$;

-- 6) alte Zeilen im Modus ueberarbeiten bleiben gueltig
DO $$ DECLARE v_i uuid := (SELECT id FROM _p073); v_id uuid; BEGIN
  v_id := marketing.pult_bild_auftrag(v_i, 'foto', false, '', 'mensch', 30, 'ueberarbeiten');
  ASSERT (SELECT modus FROM marketing.bild_auftraege WHERE id = v_id) = 'ueberarbeiten', '6: ueberarbeiten anlegen';
  UPDATE marketing.bild_auftraege SET modus = 'neu' WHERE id = v_id;
  UPDATE marketing.bild_auftraege SET modus = 'ueberarbeiten' WHERE id = v_id;
  ASSERT (SELECT modus FROM marketing.bild_auftraege WHERE id = v_id) = 'ueberarbeiten', '6: ueberarbeiten setzbar';
END $$;

-- 7) 7-arg ohne Vorlagen geht weiter, vorlagen = []
DO $$ DECLARE v_i uuid := (SELECT id FROM _p073); v_id uuid; r record; BEGIN
  v_id := marketing.pult_bild_auftrag(v_i, 'held', false, '', 'mensch', 100, 'ueberarbeiten');
  SELECT modus, vorlagen INTO r FROM marketing.bild_auftraege WHERE id = v_id;
  ASSERT r.modus = 'neu' AND r.vorlagen = '[]'::jsonb, format('7: 7-arg: %s|%s', r.modus, r.vorlagen);
  v_id := marketing.pult_bild_auftrag(v_i, 'held', false, '', 'agent', 55, 'neu', NULL);
  ASSERT (SELECT vorlagen FROM marketing.bild_auftraege WHERE id = v_id) = '[]'::jsonb, '7: 8-arg mit NULL';
END $$;

DO $$ BEGIN RAISE NOTICE 'verify_073 ok'; END $$;
SELECT 'verify_073 ok' AS ergebnis;
